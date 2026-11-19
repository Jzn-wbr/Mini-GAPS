"""Envoi Web Push borné, sans redirection ni journal contenant un abonnement."""

import base64
import json
import os
from pathlib import Path
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import webpush, WebPushException
import requests

from gaps_scraper.common.storage import canonical
from gaps_scraper.notifications.store import claim, finish, safe_endpoint
from gaps_scraper.common.storage import database


def valid_subject(value):
    parsed=urlsplit(value)
    if parsed.scheme=='mailto' and '@' in parsed.path:return True
    return parsed.scheme=='https' and bool(parsed.hostname) and not parsed.username and not parsed.password


def public_value(vapid):
    return base64.urlsafe_b64encode(vapid.public_key.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')


def initialize_keys(path, key_file, subject):
    if not valid_subject(subject):raise ValueError('Contact VAPID HTTPS ou mailto requis.')
    key_file=Path(key_file);key_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with database(path) as db:
        existing={r[0]:r[1] for r in db.execute('SELECT name,value FROM push_config')}
    if existing.get('public_key') and not key_file.exists():
        raise ValueError('Restaurer la clé VAPID existante avant de continuer.')
    if key_file.exists():
        if key_file.stat().st_mode & 0o077:raise ValueError('Clé VAPID non privée.')
        vapid=Vapid.from_file(str(key_file))
    else:
        vapid=Vapid();vapid.generate_keys()
        fd=os.open(key_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as output:output.write(vapid.private_pem())
    public=public_value(vapid)
    with database(path) as db:
        old=db.execute("SELECT value FROM push_config WHERE name='public_key'").fetchone()
        if old and old[0]!=public:raise ValueError('La clé ne correspond pas à la configuration existante.')
        db.execute("INSERT OR REPLACE INTO push_config VALUES ('public_key',?)",(public,))
        db.execute("INSERT OR REPLACE INTO push_config VALUES ('subject',?)",(subject,))
        db.execute("INSERT OR REPLACE INTO push_config VALUES ('sender_ready','1')")
        if existing.get('subject')!=subject:db.execute('DELETE FROM push_vapid_headers')


class NoRedirectSession(requests.Session):
    def request(self,method,url,**kwargs):
        safe_endpoint(url)
        kwargs['allow_redirects']=False
        kwargs['timeout']=10
        return super().request(method,url,**kwargs)


def vapid_headers(path,key_file,subject,audience,now):
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        cached=db.execute('SELECT * FROM push_vapid_headers WHERE audience=?',(audience,)).fetchone()
        if cached and now-cached['created_at']<3600 and cached['expires_at']>now+60:
            return json.loads(cached['headers'])
        expiry=now+12*3600
        headers=Vapid.from_file(str(key_file)).sign({'aud':audience,'exp':expiry,'sub':subject})
        db.execute('INSERT OR REPLACE INTO push_vapid_headers VALUES (?,?,?,?)',(audience,canonical(headers),now,expiry))
        return headers


def deliver(path,key_file,*,now=None,send=webpush,limit=20):
    now=int(time.time() if now is None else now)
    with database(path) as db:config={r[0]:r[1] for r in db.execute('SELECT name,value FROM push_config')}
    ready=False
    if config.get('subject') and config.get('public_key') and Path(key_file).is_file():
        try:
            ready=(not Path(key_file).stat().st_mode & 0o077 and valid_subject(config['subject'])
                   and public_value(Vapid.from_file(str(key_file)))==config['public_key'])
        except (ValueError,OSError):pass
    if config.get('public_key') and config.get('sender_ready')!=str(int(ready)):
        with database(path) as db:db.execute("INSERT OR REPLACE INTO push_config VALUES ('sender_ready',?)",(str(int(ready)),))
    if not ready:
        return {'configured':False,'processed':0}
    processed=accepted=0
    responses={}
    with NoRedirectSession() as session:
        for _ in range(limit):
            row=claim(path,now)
            if row is None:break
            status=None
            retry_after=None
            try:
                subscription=json.loads(row['subscription']);audience=safe_endpoint(subscription['endpoint'])
                headers=vapid_headers(path,key_file,config['subject'],audience,now)
                response=send(subscription_info=subscription,data=row['payload'],headers=headers,
                              timeout=10,ttl=86400,requests_session=session)
                status=response.status_code
                retry_after=getattr(response,'headers',{}).get('Retry-After')
            except WebPushException as exc:
                status=exc.response.status_code if exc.response is not None else None
                if exc.response is not None:retry_after=exc.response.headers.get('Retry-After')
            except requests.RequestException:
                status=None
            except (ValueError,OSError):
                status=400
            if retry_after is not None:
                try:
                    retry_after=int(retry_after) if str(retry_after).isdigit() else int(parsedate_to_datetime(retry_after).timestamp()-now)
                except (ValueError,TypeError,OverflowError):retry_after=None
            finish(path,row,status,now,retry_after);processed+=1
            label=str(status) if status is not None else 'network'
            responses[label]=responses.get(label,0)+1
            if status in (200,201,202):accepted+=1
    return {'configured':True,'processed':processed,'accepted_by_provider':accepted,'responses':responses}
