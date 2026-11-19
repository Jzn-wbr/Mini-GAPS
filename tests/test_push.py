import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import requests

from gaps_scraper.api.auth import initialize_auth,issue_pairing,exchange_code,revoke_device
from gaps_scraper.notes.store import synchronize
from gaps_scraper.notifications.sender import deliver,initialize_keys,vapid_headers
from gaps_scraper.notifications.store import initialize_push,subscribe,enqueue,claim,safe_endpoint,unsubscribe
from gaps_scraper.common.storage import database
from test_notes import document,course


def subscription(endpoint='https://web.push.apple.com/synthetic-subscription'):
    key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    encode=lambda value:base64.urlsafe_b64encode(value).decode().rstrip('=')
    return {'endpoint':endpoint,'expirationTime':None,'keys':{'p256dh':encode(key),'auth':encode(b'synthetic-key-16')}}


class PushTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.sqlite3';self.key=Path(self.temp.name)/'vapid.pem'
        self.now=int(time.time());synchronize(self.path,document());initialize_auth(self.path);initialize_push(self.path)
        initialize_keys(self.path,self.key,'https://school.example')
        code=issue_pairing(self.path,now=self.now)
        _,self.device=exchange_code(self.path,code,'Push test',now=self.now)
        self.sub=subscription();subscribe(self.path,self.device['id'],self.sub)

    def corrected(self,grade='5.5'):
        synchronize(self.path,document(course(grade=grade)))

    def test_no_old_notes_and_persistent_unique_event_device_queue(self):
        self.assertEqual(enqueue(self.path,now=self.now),0)
        self.corrected();self.assertEqual(enqueue(self.path,now=self.now),1)
        initialize_push(self.path);self.assertEqual(enqueue(self.path,now=self.now),0)
        with database(self.path) as db:
            message=json.loads(db.execute('SELECT payload FROM push_outbox').fetchone()[0])
        self.assertEqual(message['title'],'MAT-FICTIVE')
        self.assertIn('corrigée',message['body'])
        self.assertNotIn('5.5',json.dumps(message))

    def test_publication_and_metadata_change_are_distinct(self):
        # Une nouvelle épreuve sans note ne déclenche rien.
        synchronize(self.path,document(course(description='Nouvelle épreuve fictive',grade='-')))
        self.assertEqual(enqueue(self.path,now=self.now),0)
        synchronize(self.path,document(course(description='Nouvelle épreuve fictive',grade='4.5')))
        self.assertEqual(enqueue(self.path,now=self.now),1)
        modified=document(course(description='Nouvelle épreuve fictive',grade='4.5'))
        modified['assessments'][0]['class_mean']='4.1';synchronize(self.path,modified)
        self.assertEqual(enqueue(self.path,now=self.now),0)

    def test_renamed_assessment_with_simultaneous_missing_record_is_not_false_new_push(self):
        synchronize(self.path,document(course(description='Intitulé corrigé')))
        self.assertEqual(enqueue(self.path,now=self.now),0)

    def test_subscription_refresh_keeps_sequence_and_other_account_is_isolated(self):
        self.corrected();subscribe(self.path,self.device['id'],self.sub)
        self.assertEqual(enqueue(self.path,now=self.now),1)
        other=document();other['account_id']='other-account';synchronize(self.path,other)
        other=document(course(grade='4.0'));other['account_id']='other-account';synchronize(self.path,other)
        self.assertEqual(enqueue(self.path,now=self.now),0)

    def test_revocation_stops_queued_messages(self):
        self.corrected();enqueue(self.path,now=self.now);revoke_device(self.path,self.device['id'])
        self.assertIsNone(claim(self.path,self.now))

    def test_reenable_skips_events_while_disabled(self):
        unsubscribe(self.path,self.device['id']);self.corrected()
        subscribe(self.path,self.device['id'],self.sub)
        self.assertEqual(enqueue(self.path,now=self.now),0)

    def test_retry_is_delayed_and_success_survives_restart(self):
        self.corrected();enqueue(self.path,now=self.now)
        calls=[]
        def failing(**kwargs):calls.append(kwargs);return SimpleNamespace(status_code=503)
        first=deliver(self.path,self.key,now=self.now,send=failing)
        self.assertEqual(first['processed'],1)
        self.assertEqual(deliver(self.path,self.key,now=self.now,send=failing)['processed'],0)
        initialize_push(self.path)
        result=deliver(self.path,self.key,now=self.now+600,send=lambda **kw:SimpleNamespace(status_code=201))
        self.assertEqual(result['accepted_by_provider'],1)
        self.assertEqual(deliver(self.path,self.key,now=self.now+601,send=failing)['processed'],0)
        self.assertEqual(calls[0]['ttl'],86400);self.assertEqual(calls[0]['timeout'],10)

    def test_gone_subscription_disabled(self):
        self.corrected();enqueue(self.path,now=self.now)
        deliver(self.path,self.key,now=self.now,send=lambda **kw:SimpleNamespace(status_code=410))
        with database(self.path) as db:
            self.assertEqual(db.execute('SELECT active FROM push_subscriptions').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM push_outbox').fetchone()[0],'dead')

    def test_retry_after_is_respected(self):
        self.corrected();enqueue(self.path,now=self.now)
        deliver(self.path,self.key,now=self.now,send=lambda **kw:SimpleNamespace(status_code=429,headers={'Retry-After':'7200'}))
        with database(self.path) as db:
            self.assertEqual(db.execute('SELECT next_attempt FROM push_outbox').fetchone()[0],self.now+7200)

    def test_real_encryption_uses_no_redirects_and_explicit_timeout_without_network(self):
        self.corrected();enqueue(self.path,now=self.now)
        response=requests.Response();response.status_code=201;response._content=b''
        with patch('requests.sessions.Session.request',return_value=response) as sent:
            result=deliver(self.path,self.key,now=self.now)
        self.assertEqual(result['accepted_by_provider'],1)
        kwargs=sent.call_args.kwargs
        self.assertFalse(kwargs['allow_redirects']);self.assertEqual(kwargs['timeout'],10)
        self.assertNotIn(b'MAT-FICTIVE',kwargs['data'])
        self.assertIn('vapid',kwargs['headers']['Authorization'])

    def test_vapid_headers_reused_for_hour_and_key_init_is_idempotent(self):
        audience='https://web.push.apple.com'
        first=vapid_headers(self.path,self.key,'https://school.example',audience,self.now)
        private=self.key.read_bytes()
        initialize_keys(self.path,self.key,'https://school.example')
        second=vapid_headers(self.path,self.key,'https://school.example',audience,self.now+3599)
        self.assertEqual(first,second);self.assertEqual(self.key.read_bytes(),private)
        self.assertEqual(self.key.stat().st_mode & 0o777,0o600)
        self.assertNotEqual(first,vapid_headers(self.path,self.key,'https://school.example',audience,self.now+3600))

    def test_lost_key_is_not_silently_replaced(self):
        self.key.unlink()
        with self.assertRaises(ValueError):initialize_keys(self.path,self.key,'https://school.example')
        self.assertFalse(self.key.exists())

    def test_missing_sender_key_leaves_queue_pending_and_marks_configuration_unavailable(self):
        self.corrected();enqueue(self.path,now=self.now);self.key.unlink()
        self.assertFalse(deliver(self.path,self.key,now=self.now)['configured'])
        with database(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM push_outbox').fetchone()[0],'pending')
            self.assertEqual(db.execute("SELECT value FROM push_config WHERE name='sender_ready'").fetchone()[0],'0')

    def test_untrusted_hosts_credentials_ports_and_redirect_targets_refused(self):
        for endpoint in ('http://web.push.apple.com/x','https://127.0.0.1/x','https://web.push.apple.com.evil.test/x',
                         'https://web.push.apple.com:8080/x','https://user:pass@web.push.apple.com/x'):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):safe_endpoint(endpoint)


if __name__=='__main__':unittest.main()
