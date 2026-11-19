"""Abonnements par appareil et file durable de notifications, sans données de note."""

import base64
from hashlib import sha256
import json
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec

from gaps_scraper.common.storage import canonical
from gaps_scraper.common.storage import database


def initialize_push(path):
    with database(path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS push_config (name TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
                payload TEXT NOT NULL, start_sequence INTEGER NOT NULL, active INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS push_outbox (
                id INTEGER PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES push_subscriptions(id),
                event_sequence INTEGER NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt INTEGER NOT NULL, claimed_at INTEGER,
                UNIQUE(subscription_id,event_sequence));
            CREATE TABLE IF NOT EXISTS push_vapid_headers (
                audience TEXT PRIMARY KEY,headers TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS push_outbox_due ON push_outbox(status,next_attempt);
        ''')


def safe_endpoint(endpoint):
    parsed=urlsplit(endpoint)
    host=parsed.hostname or ''
    accepted=(host.endswith('.push.apple.com') or host=='fcm.googleapis.com'
              or host=='updates.push.services.mozilla.com' or host.endswith('.push.services.mozilla.com'))
    if (parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password
            or parsed.fragment or not parsed.path or not accepted):
        raise ValueError('Fournisseur de notification non pris en charge.')
    return f'https://{parsed.netloc}'


def decoded_key(value):
    return base64.b64decode(value+'='*((-len(value))%4),altchars=b'-_',validate=True)


def current_sequence(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='changes'").fetchone():return 0
    return db.execute('SELECT COALESCE(MAX(sequence),0) FROM changes').fetchone()[0]


def configuration(path, device_id):
    with database(path) as db:
        config={r[0]:r[1] for r in db.execute('SELECT name,value FROM push_config')}
        active=db.execute('SELECT 1 FROM push_subscriptions WHERE device_id=? AND active=1',(device_id,)).fetchone()
    return {'enabled':bool(config.get('public_key') and config.get('subject') and config.get('sender_ready')!='0'),
            'public_key':config.get('public_key'),'subscribed':bool(active)}


def subscribe(path, device_id, subscription):
    endpoint=subscription['endpoint'];safe_endpoint(endpoint)
    raw=decoded_key(subscription['keys']['p256dh'])
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw)
    if len(decoded_key(subscription['keys']['auth']))!=16:raise ValueError('Clé d’abonnement invalide.')
    identity=sha256(endpoint.encode()).hexdigest()
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT device_id,start_sequence,active FROM push_subscriptions WHERE id=?',(identity,)).fetchone()
        fresh=not old or old['device_id']!=device_id or not old['active']
        sequence=current_sequence(db) if fresh else old['start_sequence']
        # Un nouvel appareil dans le même navigateur ne reprend pas les messages de l'ancien.
        if fresh:
            db.execute("UPDATE push_outbox SET status='dead' WHERE subscription_id=? AND status!='sent'",(identity,))
        db.execute('''INSERT INTO push_subscriptions VALUES (?,?,?,?,1)
            ON CONFLICT(id) DO UPDATE SET device_id=excluded.device_id,payload=excluded.payload,
            start_sequence=excluded.start_sequence,active=1''',
            (identity,device_id,canonical(subscription),sequence))


def unsubscribe(path, device_id):
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE push_subscriptions SET active=0 WHERE device_id=?',(device_id,))
        db.execute("UPDATE push_outbox SET status='dead' WHERE subscription_id IN (SELECT id FROM push_subscriptions WHERE device_id=?) AND status!='sent'",(device_id,))


def enqueue(path, *, now=None):
    now=int(time.time() if now is None else now)
    count=0
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='changes'").fetchone():return 0
        rows=db.execute('''SELECT c.sequence,c.kind,c.payload,c.scope,c.observed_at,s.id subscription_id
            FROM changes c JOIN devices d ON d.account_id=json_extract(c.scope,'$[0]')
            JOIN push_subscriptions s ON s.device_id=d.id
            WHERE s.active=1 AND d.revoked=0 AND d.expires_at>? AND c.sequence>s.start_sequence
            AND c.kind IN ('assessment_discovered','grade_published','grade_changed')
            AND NOT EXISTS (SELECT 1 FROM push_outbox o WHERE o.event_sequence=c.sequence AND o.subscription_id=s.id)''',(now,)).fetchall()
        for row in rows:
            change=json.loads(row['payload']);current=change.get('current');previous=change.get('previous')
            if not current or current['grade']['status']!='published':continue
            correction=row['kind']=='grade_changed'
            if correction and (not previous or previous['grade']['status']!='published'):continue
            # Une disparition simultanée peut être un renommage : ne pas notifier une fausse nouveauté.
            if row['kind']=='assessment_discovered' and db.execute(
                "SELECT 1 FROM changes WHERE scope=? AND observed_at=? AND kind='assessment_missing_unconfirmed'",
                (row['scope'],row['observed_at'])).fetchone():continue
            tag=sha256(f"{row['subscription_id']}:{row['sequence']}".encode()).hexdigest()[:32]
            message={'title':current['course'][:120],
                     'body':'Une note a été corrigée.' if correction else 'Une nouvelle note est disponible.',
                     'tag':'grade-'+tag,'event_id':tag}
            count+=db.execute('''INSERT OR IGNORE INTO push_outbox
                (subscription_id,event_sequence,payload,created_at,status,next_attempt) VALUES (?,?,?,?,'pending',?)''',
                (row['subscription_id'],row['sequence'],canonical(message),now,now)).rowcount
    return count


def claim(path, now):
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        # Une révocation/expiration ne doit pas être contournée par une ligne déjà en attente.
        db.execute("""UPDATE push_outbox SET status='dead' WHERE status IN ('pending','sending') AND
            (created_at<? OR attempts>=5 OR subscription_id IN (
                SELECT s.id FROM push_subscriptions s JOIN devices d ON d.id=s.device_id
                WHERE s.active=0 OR d.revoked=1 OR d.expires_at<=?))""",(now-86400,now))
        row=db.execute('''SELECT o.*,s.payload subscription FROM push_outbox o
            JOIN push_subscriptions s ON s.id=o.subscription_id JOIN devices d ON d.id=s.device_id
            WHERE s.active=1 AND d.revoked=0 AND d.expires_at>? AND o.next_attempt<=?
            AND (o.status='pending' OR (o.status='sending' AND o.claimed_at<?)) ORDER BY o.id LIMIT 1''',
            (now,now,now-300)).fetchone()
        if row is None:return None
        db.execute("UPDATE push_outbox SET status='sending',attempts=attempts+1,claimed_at=? WHERE id=?",(now,row['id']))
        result=dict(row);result['attempts']+=1;return result


def finish(path, row, status, now, retry_after=None):
    with database(path) as db:
        if status in (404,410):
            db.execute('UPDATE push_subscriptions SET active=0 WHERE id=?',(row['subscription_id'],))
            db.execute("UPDATE push_outbox SET status='dead' WHERE subscription_id=? AND status!='sent'",(row['subscription_id'],))
        else:
            state='sent' if status in (200,201,202) else 'pending' if (status is None or status==429 or status>=500) and row['attempts']<5 else 'dead'
            delay=min(3600,600*2**(row['attempts']-1))
            if retry_after is not None:delay=max(delay,min(86400,max(0,retry_after)))
            db.execute('UPDATE push_outbox SET status=?,next_attempt=?,claimed_at=NULL WHERE id=?',
                       (state,now+delay,row['id']))
