from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from gaps_scraper.api.app import Settings, create_app
from gaps_scraper.api.auth import (
    AuthError, PairRateLimit, authenticate_device, digest,
    exchange_code, initialize_auth, issue_pairing, list_devices, revoke_device,
)
from gaps_scraper.notes.store import synchronize
from gaps_scraper.common.storage import canonical
from gaps_scraper.common.state import record_attempt
from gaps_scraper.common.storage import database


def notes(account="account-A", year="2026"):
    return {"schema_version":1,"account_id":account,"academic_year":{"value":year,"label":f"{year}-{int(year)+1}"},
            "semester":{"value":"0","label":"S1"},"timezone":"Europe/Zurich",
            "courses":[],"assessments":[],"source_state":"explicit_empty"}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"state.sqlite3"
        synchronize(self.path,notes())
        self.now=[datetime(2026,11,18,12,0,tzinfo=timezone.utc).timestamp()]
        self.settings=Settings(self.path,"https://school.test")
        self.client=TestClient(create_app(self.settings,clock=lambda:self.now[0]),base_url=self.settings.origin)
        self.addCleanup(self.client.close)
        self.origin={"Origin":self.settings.origin}

    def pair(self,account=None):
        code=issue_pairing(self.path,account,now=self.now[0])
        return self.client.post("/api/pair",json={"code":code,"device_name":"iPhone test"},headers=self.origin)

    def test_private_endpoints_require_session_and_public_docs_are_disabled(self):
        for route in ("notes","absences","horaire","session"):
            with self.subTest(route=route):
                response=self.client.get("/api/"+route)
                self.assertEqual(response.status_code,401)
                self.assertEqual(response.headers["cache-control"],"no-store")
        self.assertEqual(self.client.get("/docs").status_code,404)
        self.assertEqual(self.client.get("/openapi.json").status_code,404)
        self.assertEqual(self.client.get("/healthz").json(),{"status":"ok"})

    def test_public_shell_has_no_school_data_and_pwa_assets_are_available(self):
        index=self.client.get("/")
        self.assertEqual(index.status_code,200)
        self.assertIn("Associer cet appareil",index.text)
        self.assertNotIn("account-A",index.text)
        self.assertNotIn("'unsafe-inline'",index.headers["content-security-policy"])
        for path in ('/assets/app.js','/assets/app.css','/assets/icon-192.png','/manifest.webmanifest','/sw.js'):
            with self.subTest(path=path):self.assertEqual(self.client.get(path).status_code,200)
        worker=self.client.get('/sw.js')
        self.assertEqual(worker.headers['service-worker-allowed'],'/')
        self.assertIn("url.pathname.startsWith('/api/')",worker.text)

    def test_pairing_sets_private_cookie_without_returning_token(self):
        response=self.pair();self.assertEqual(response.status_code,200)
        cookie=response.headers["set-cookie"]
        self.assertIn("__Host-gaps_session=",cookie)
        for part in ("HttpOnly","Secure","SameSite=strict","Path=/"):
            self.assertIn(part,cookie)
        self.assertNotIn("Domain=",cookie)
        self.assertEqual(set(response.json()),{"id","name","expires_at"})
        token=self.client.cookies.get(self.settings.cookie_name)
        with database(self.path) as db:
            self.assertEqual(db.execute("SELECT digest FROM device_sessions").fetchone()[0],digest(token))
        self.assertEqual(self.client.get("/api/notes").status_code,200)
        self.assertNotIn("account_id",self.client.get("/api/notes").text)

    def test_single_use_and_expired_code_rejected(self):
        code=issue_pairing(self.path,now=self.now[0])
        body={"code":code,"device_name":"test"}
        self.assertEqual(self.client.post("/api/pair",json=body,headers=self.origin).status_code,200)
        self.assertEqual(self.client.post("/api/pair",json=body,headers=self.origin).status_code,401)
        code=issue_pairing(self.path,now=self.now[0]);self.now[0]+=601
        response=self.client.post("/api/pair",json={"code":code,"device_name":"test"},headers=self.origin)
        self.assertEqual(response.status_code,401)
        self.assertNotIn(code,response.text)

    def test_pairing_cannot_choose_or_override_account(self):
        synchronize(self.path,notes("account-B","2025"))
        response=self.pair("account-B");self.assertEqual(response.status_code,200)
        response=self.client.get("/api/notes?account_id=account-A")
        self.assertEqual(response.json()["data"]["academic_year"]["value"],"2025")
        self.assertNotIn("account-A",response.text)

    def test_one_account_cannot_read_other_accounts_only_dataset(self):
        synchronize(self.path,notes("account-B","2025"));self.pair("account-B")
        with database(self.path) as db:
            db.execute("DELETE FROM snapshots WHERE json_extract(payload,'$.account_id')=?",("account-B",))
        response=self.client.get("/api/notes")
        self.assertIsNone(response.json()["data"])
        self.assertEqual(response.json()["freshness"],"unavailable")

    def test_cookie_survives_api_restart_and_revocation_is_immediate(self):
        identity=self.pair().json()["id"]
        other=TestClient(create_app(self.settings,clock=lambda:self.now[0]),base_url=self.settings.origin)
        self.addCleanup(other.close);other.cookies.update(self.client.cookies)
        self.assertEqual(other.get("/api/session").status_code,200)
        self.assertTrue(revoke_device(self.path,identity))
        self.assertEqual(other.get("/api/notes").status_code,401)
        self.assertEqual(self.client.get("/api/session").status_code,401)
        self.assertNotIn("digest",json.dumps(list_devices(self.path)))

    def test_session_has_absolute_expiry(self):
        self.now[0]=self.pair().json()['expires_at']
        self.assertEqual(self.client.get("/api/session").status_code,401)

    def test_session_lasts_five_calendar_years_and_cookie_is_refreshed(self):
        result=self.pair()
        expiry=datetime.fromtimestamp(result.json()['expires_at'],timezone.utc)
        self.assertEqual(expiry,datetime(2031,11,18,12,0,tzinfo=timezone.utc))
        self.assertIn('set-cookie',self.client.get('/api/session').headers)

    def test_migration_extends_only_still_valid_unrevoked_sessions(self):
        from gaps_scraper.api.auth import session_expiry
        identity=self.pair().json()['id']
        expired=self.pair().json()['id']
        revoked=self.pair().json()['id']
        legacy=int(self.now[0])+90*86400
        past=int(self.now[0])-1
        with database(self.path) as db:
            db.execute('UPDATE devices SET expires_at=?',(legacy,))
            db.execute('UPDATE devices SET expires_at=? WHERE id=?',(past,expired))
            db.execute('UPDATE devices SET revoked=1 WHERE id=?',(revoked,))
            db.execute("DELETE FROM auth_policy WHERE name='session_years'")
        initialize_auth(self.path,now=self.now[0])
        with database(self.path) as db:
            values={row['id']:row['expires_at'] for row in db.execute('SELECT id,expires_at FROM devices')}
            self.assertEqual(values[identity],session_expiry(self.now[0]))
            self.assertEqual(values[expired],past)
            self.assertEqual(values[revoked],legacy)
        leap=datetime(2028,2,29,8,0,tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(session_expiry(leap),timezone.utc),datetime(2033,2,28,8,0,tzinfo=timezone.utc))

    def test_csrf_origin_required_and_cross_site_reads_rejected(self):
        code=issue_pairing(self.path,now=self.now[0]);body={"code":code,"device_name":"test"}
        for headers in ({},{"Origin":"https://evil.test"}):
            self.assertEqual(self.client.post("/api/pair",json=body,headers=headers).status_code,403)
        self.pair()
        self.assertEqual(self.client.get("/api/notes",headers={"Sec-Fetch-Site":"cross-site"}).status_code,403)
        self.assertEqual(self.client.post("/api/logout").status_code,403)
        self.assertEqual(self.client.post("/api/logout",headers=self.origin).status_code,204)
        self.assertEqual(self.client.get("/api/session").status_code,401)

    def test_invalid_input_does_not_echo_pairing_code_and_request_size_bounded(self):
        secret="x"*32
        response=self.client.post("/api/pair",json={"code":secret,"device_name":"test","unexpected":True},headers=self.origin)
        self.assertEqual(response.status_code,422);self.assertNotIn(secret,response.text)
        response=self.client.post("/api/pair",json={"code":"x"*3000,"device_name":"test"},headers=self.origin)
        self.assertEqual(response.status_code,413)
        self.assertEqual(self.client.post("/api/pair",content="code=test",headers=self.origin).status_code,415)

    def test_rate_limit_persists_across_api_instances(self):
        for _ in range(20):
            with self.assertRaises(AuthError):exchange_code(self.path,"invalid"*4,"test",now=self.now[0])
        initialize_auth(self.path)
        with self.assertRaises(PairRateLimit):exchange_code(self.path,"invalid"*4,"test",now=self.now[0])
        self.now[0]+=601
        self.assertEqual(self.pair().status_code,200)

    def test_failed_sync_keeps_previous_data_and_is_reported_stale(self):
        self.pair()
        record_attempt(self.path,"account-A","notes",error="parsing")
        response=self.client.get("/api/notes")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["freshness"],"stale")
        self.assertEqual(response.json()["error"],"parsing")
        self.assertIsNotNone(response.json()["data"])

    def test_current_scope_pointer_wins_over_historical_manual_checks(self):
        old=notes(year="2025");synchronize(self.path,old)
        scope=canonical(["account-A","2025","0"])
        record_attempt(self.path,"account-A","notes",scope=scope)
        self.pair()
        self.assertEqual(self.client.get("/api/notes").json()["data"]["academic_year"]["value"],"2025")

    def test_historical_notes_and_periods_are_private_and_do_not_change_current_scope(self):
        old=notes(year='2025');synchronize(self.path,old)
        other=notes(account='other-account',year='2024');synchronize(self.path,other)
        self.assertEqual(self.client.get('/api/notes/periods').status_code,401)
        self.pair(account='account-A')
        periods=self.client.get('/api/notes/periods').json()
        self.assertEqual([p['academic_year']['value'] for p in periods],['2026','2025'])
        response=self.client.get('/api/notes?year=2025&semester=0')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['data']['academic_year']['value'],'2025')
        self.assertIsNone(self.client.get('/api/notes?year=2024&semester=0').json()['data'])
        self.assertEqual(self.client.get('/api/notes').json()['data']['academic_year']['value'],'2026')

    def test_notes_selection_requires_valid_paired_year_and_semester(self):
        self.pair()
        for query in ('year=2025','semester=0','year=1999&semester=0','year=2025&semester=S3'):
            self.assertEqual(self.client.get('/api/notes?'+query).status_code,422)

    def test_invalid_saved_data_returns_generic_error(self):
        self.pair()
        with database(self.path) as db:
            db.execute("UPDATE snapshots SET payload=?",(json.dumps({"account_id":"account-A","academic_year":{"value":"2026"},"private":"do-not-echo"}),))
        response=self.client.get("/api/notes")
        self.assertEqual(response.status_code,503)
        self.assertNotIn("do-not-echo",response.text)

    def test_api_read_never_calls_scraper(self):
        self.pair()
        with patch("gaps_scraper.portal.client.Client.fetch",side_effect=AssertionError("network forbidden")):
            self.assertEqual(self.client.get("/api/notes").status_code,200)

    def test_push_enrollment_is_private_and_bound_to_current_device(self):
        from gaps_scraper.notifications.sender import initialize_keys
        from test_push import subscription
        self.assertEqual(self.client.get('/api/push/config').status_code,401)
        self.pair()
        self.assertFalse(self.client.get('/api/push/config').json()['enabled'])
        initialize_keys(self.path,Path(self.temp.name)/'vapid.pem','https://school.example')
        response=self.client.post('/api/push/subscribe',json=subscription(),headers=self.origin)
        self.assertEqual(response.status_code,204)
        self.assertTrue(self.client.get('/api/push/config').json()['subscribed'])
        self.assertNotIn('endpoint',self.client.get('/api/push/config').text)
        self.pair()
        self.assertFalse(self.client.get('/api/push/config').json()['subscribed'])

    def test_push_subscription_cannot_target_arbitrary_server(self):
        from gaps_scraper.notifications.sender import initialize_keys
        from test_push import subscription
        self.pair();initialize_keys(self.path,Path(self.temp.name)/'vapid.pem','https://school.example')
        response=self.client.post('/api/push/subscribe',json=subscription('https://127.0.0.1/private'),headers=self.origin)
        self.assertEqual(response.status_code,422)
        self.assertNotIn('127.0.0.1',response.text)

    def test_insecure_nonlocal_origin_refused(self):
        with self.assertRaises(ValueError):Settings(self.path,"http://school.test")
        with self.assertRaises(ValueError):Settings(self.path,"https://school.test/path")

    def test_code_consumption_is_atomic_across_threads(self):
        code=issue_pairing(self.path,now=self.now[0])
        def exchange(_):
            try:
                exchange_code(self.path,code,"concurrent test",now=self.now[0]);return True
            except AuthError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(exchange,range(2))),1)


if __name__ == "__main__":unittest.main()
