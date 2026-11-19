"""Association ponctuelle et sessions opaques par appareil, révocables en SQLite."""

from hashlib import sha256
from datetime import datetime,timezone
import json
import secrets
import time
import uuid

from gaps_scraper.common.storage import database, prepare_directory

SESSION_YEARS = 5
PAIR_SECONDS = 10 * 60


def session_expiry(created_at):
    created=datetime.fromtimestamp(created_at,timezone.utc)
    try:
        expires=created.replace(year=created.year+SESSION_YEARS)
    except ValueError:
        expires=created.replace(year=created.year+SESSION_YEARS,day=28)
    return int(expires.timestamp())


class AuthError(ValueError):
    pass


class PairRateLimit(AuthError):
    pass


def digest(value):
    return sha256(value.encode()).hexdigest()


def initialize_auth(path,*,now=None):
    prepare_directory(path)
    with database(path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS pairing_codes (
                digest TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS device_sessions (
                digest TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id));
            CREATE TABLE IF NOT EXISTS pair_attempts (
                id INTEGER PRIMARY KEY CHECK(id=1), window_start INTEGER NOT NULL, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS auth_policy (name TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        db.execute('BEGIN IMMEDIATE')
        policy=db.execute("SELECT value FROM auth_policy WHERE name='session_years'").fetchone()
        if not policy or policy[0]!=str(SESSION_YEARS):
            stamp=int(time.time() if now is None else now)
            for row in db.execute('SELECT id,created_at FROM devices WHERE revoked=0 AND expires_at>?',(stamp,)).fetchall():
                db.execute('UPDATE devices SET expires_at=? WHERE id=?',(session_expiry(row['created_at']),row['id']))
            db.execute("INSERT OR REPLACE INTO auth_policy VALUES ('session_years',?)",(str(SESSION_YEARS),))


def known_accounts(path):
    accounts = set()
    with database(path) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("snapshots", "absence_snapshots", "schedule_snapshots"):
            if table in tables:
                for row in db.execute(f"SELECT payload FROM {table}"):
                    accounts.add(json.loads(row[0])["account_id"])
    return accounts


def issue_pairing(path, account_id=None, *, now=None):
    initialize_auth(path)
    accounts = known_accounts(path)
    if account_id is None:
        if len(accounts) != 1:
            raise AuthError("Réaliser une synchronisation ou préciser le compte localement.")
        account_id = next(iter(accounts))
    if account_id not in accounts:
        raise AuthError("Compte absent des données locales.")
    now = int(time.time() if now is None else now)
    code = secrets.token_urlsafe(24)
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM pairing_codes WHERE account_id=? OR expires_at<=?",(account_id,now))
        db.execute("INSERT INTO pairing_codes VALUES (?,?,?)",(digest(code),account_id,now+PAIR_SECONDS))
    return code


def exchange_code(path, code, name, *, now=None):
    now = int(time.time() if now is None else now)
    result = None
    limited = False
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        attempt = db.execute("SELECT window_start,count FROM pair_attempts WHERE id=1").fetchone()
        if attempt is None or now-attempt[0] >= 600:
            db.execute("INSERT OR REPLACE INTO pair_attempts VALUES (1,?,1)",(now,))
        elif attempt[1] >= 20:
            limited = True
        else:
            db.execute("UPDATE pair_attempts SET count=count+1 WHERE id=1")
        if not limited:
            row = db.execute("SELECT account_id FROM pairing_codes WHERE digest=? AND expires_at>?",
                             (digest(code),now)).fetchone()
            if row:
                db.execute("DELETE FROM pairing_codes WHERE digest=?",(digest(code),))
                identity, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
                expiry = session_expiry(now)
                db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,0)",
                           (identity,row[0],name,now,expiry,now))
                db.execute("INSERT INTO device_sessions VALUES (?,?)",(digest(token),identity))
                result = (token, {"id":identity,"account_id":row[0],"name":name,"expires_at":expiry})
    # Lever après le commit : les essais invalides restent comptés après redémarrage.
    if limited:
        raise PairRateLimit("Trop de tentatives d'association.")
    if result is None:
        raise AuthError("Code invalide, expiré ou déjà utilisé.")
    return result


def authenticate_device(path, token, *, now=None):
    if not token or len(token) > 128:
        raise AuthError("Appareil non associé.")
    now = int(time.time() if now is None else now)
    with database(path) as db:
        row = db.execute('''SELECT d.* FROM device_sessions s JOIN devices d ON d.id=s.device_id
            WHERE s.digest=? AND d.revoked=0 AND d.expires_at>?''',(digest(token),now)).fetchone()
        if row is None:
            raise AuthError("Session invalide, expirée ou révoquée.")
        # Éviter une écriture pour chaque requête de lecture.
        if now-row["last_seen"] >= 300:
            db.execute("UPDATE devices SET last_seen=? WHERE id=? AND last_seen<=?",
                       (now,row["id"],now-300))
        return dict(row)


def revoke_device(path, identity):
    with database(path) as db:
        db.execute("BEGIN IMMEDIATE")
        found = db.execute("UPDATE devices SET revoked=1 WHERE id=?",(identity,)).rowcount
        db.execute("DELETE FROM device_sessions WHERE device_id=?",(identity,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='push_subscriptions'").fetchone():
            db.execute("UPDATE push_subscriptions SET active=0 WHERE device_id=?",(identity,))
            db.execute("UPDATE push_outbox SET status='dead' WHERE status!='sent' AND subscription_id IN (SELECT id FROM push_subscriptions WHERE device_id=?)",(identity,))
    return bool(found)


def list_devices(path):
    with database(path) as db:
        return [dict(r) for r in db.execute(
            "SELECT id,name,created_at,expires_at,last_seen,revoked FROM devices ORDER BY created_at")]
