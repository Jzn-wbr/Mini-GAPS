from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from gaps_scraper.portal.client import authenticate
from gaps_scraper.common.parsing import ParseError
from gaps_scraper.common.state import expected_success, freshness, scheduled
from gaps_scraper.common.storage import database
from gaps_scraper.jobs.sync import sync_all


class CachedClient:
    def __init__(self):
        self.calls=[]
        self.values=iter(['<form method="post"><input name="login"><input name="password"></form>',
                          '', 'const DEFAULT_STUDENT_ID = 42;'])
    def fetch(self,*args):
        self.calls.append(args);return next(self.values)


class SyncTests(unittest.TestCase):
    def test_authentication_shared_between_components_without_persisting_cookie(self):
        client=CachedClient()
        first=authenticate(client,"test","synthetic")
        self.assertEqual(authenticate(client,"test","synthetic"),first)
        self.assertEqual(len(client.calls),3)

    def test_swiss_schedule_and_midnight_pause(self):
        local=ZoneInfo("Europe/Zurich")
        for text,expected in [('2026-11-18T06:00',True),('2026-11-18T23:50',True),
                              ('2026-11-18T06:01',False),('2026-11-18T05:50',False),
                              ('2026-11-18T00:00',False)]:
            with self.subTest(text=text):
                now=datetime.fromisoformat(text).replace(tzinfo=local)
                self.assertEqual(scheduled(now.astimezone(timezone.utc)),expected)

    def test_overnight_freshness_does_not_show_false_staleness(self):
        local=ZoneInfo("Europe/Zurich")
        last=datetime(2026,11,17,23,50,tzinfo=local).isoformat()
        night=datetime(2026,11,18,3,0,tzinfo=local)
        self.assertEqual(freshness(last,None,night),"fresh")
        self.assertEqual(freshness(last,"network",night),"stale")
        self.assertEqual(freshness(last,None,datetime(2026,11,18,6,5,tzinfo=local)),"stale")

    def test_failure_of_one_category_does_not_block_other_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"state.sqlite3"
            doc={"academic_year":{"value":"2026"},"semester":{"value":"0"},"source_period":"2"}
            with patch("gaps_scraper.jobs.sync.authenticate",return_value=("", "42")), \
                 patch("gaps_scraper.jobs.sync.fetch_notes",side_effect=ParseError("sensitive raw body")), \
                 patch("gaps_scraper.jobs.sync.fetch_absences",return_value=("",{})), \
                 patch("gaps_scraper.jobs.sync.parse_absences",return_value=doc), \
                 patch("gaps_scraper.jobs.sync.synchronize_absences",return_value={"courses":1}), \
                 patch("gaps_scraper.jobs.sync.fetch_schedule",return_value=("",{})), \
                 patch("gaps_scraper.jobs.sync.parse_schedule",return_value=doc), \
                 patch("gaps_scraper.jobs.sync.synchronize_schedule",return_value={"occurrences":1}):
                result=sync_all(object(),"test","synthetic",path)
            self.assertFalse(result["notes"]["ok"])
            self.assertTrue(result["absences"]["ok"])
            self.assertTrue(result["horaire"]["ok"])
            self.assertNotIn("sensitive",str(result))
            with database(path) as db:
                self.assertEqual(db.execute("SELECT error_code FROM dataset_state WHERE component='notes'").fetchone()[0],"parsing")


if __name__ == "__main__":unittest.main()
