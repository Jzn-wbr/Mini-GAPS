from contextlib import closing
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gaps_scraper.portal.client import CaptureError
from gaps_scraper.common.parsing import ParseError
from gaps_scraper.schedule.client import export_selection
from gaps_scraper.schedule.parser import parse_schedule
from gaps_scraper.schedule.store import synchronize_schedule


SELECTION = {"account_id":"synthetic-account", "academic_year":{"value":"2024","label":"2024-2025"},
             "semester":{"value":"S1","label":"Semestre 1"}, "source_period":"2"}


def calendar(event):
    return 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Synthetic Tests//FR\r\n'+event+'END:VCALENDAR\r\n'


def event(extra='', start='DTSTART;TZID=Europe/Zurich:20241021T081500',
          end='DTEND;TZID=Europe/Zurich:20241021T094500', uid='synthetic-course'):
    return ('BEGIN:VEVENT\r\nUID:'+uid+'\r\n'+start+'\r\n'+end+
            '\r\nSUMMARY:Cours fictif\r\nLOCATION:Salle fictive\r\n'+extra+'END:VEVENT\r\n')


def document(extra=''):
    return parse_schedule(calendar(event(extra)),SELECTION)


PAGE = '''<span>Semestre 1 - 2024/2025</span>
<a href="?annee=2024&amp;trimestre=2&amp;type=2&amp;id=42&amp;icalendarversion=2&amp;individual=1">iCalendar</a>'''


class ScheduleClientTests(unittest.TestCase):
    def test_period_code_is_read_from_export_not_inferred_from_notes(self):
        url,selection=export_selection(PAGE,'42')
        self.assertEqual(selection['source_period'],'2')
        self.assertEqual(selection['semester']['value'],'S1')
        self.assertIn('trimestre=2',url)

    def test_wrong_account_wrong_schedule_type_and_foreign_host_refused(self):
        for page in (PAGE.replace('id=42','id=43'),PAGE.replace('type=2','type=1'),
                     PAGE.replace('href="?', 'href="https://other.example/consultation/horaires/?')):
            with self.subTest(page=page),self.assertRaises(CaptureError):export_selection(page,'42')

    def test_missing_ambiguous_or_mismatched_selection_refused(self):
        for page in ('',PAGE+PAGE,PAGE.replace('Semestre 1 - 2024/2025','Semestre 1 - 2025/2026')):
            with self.subTest(page=page),self.assertRaises(CaptureError):export_selection(page,'42')

    def test_default_export_follows_s2_and_new_academic_year(self):
        for year, semester, period in [('2026', '2', '3'), ('2027', '1', '2')]:
            with self.subTest(year=year, semester=semester):
                page = (f'<span>Semestre {semester} - {year}/{int(year)+1}</span>'
                        f'<a href="?annee={year}&amp;trimestre={period}&amp;type=2&amp;id=42'
                        '&amp;icalendarversion=2&amp;individual=1">iCalendar</a>')
                url, selection = export_selection(page, '42')
                self.assertEqual(selection['academic_year']['value'], year)
                self.assertEqual(selection['semester']['value'], 'S'+semester)
                self.assertEqual(selection['source_period'], period)
                self.assertIn(f'annee={year}&trimestre={period}', url)


class ScheduleParserTests(unittest.TestCase):
    def test_weekly_recurrence_keeps_local_time_across_daylight_saving(self):
        doc=document('RRULE:FREQ=WEEKLY;COUNT=3\r\n')
        starts=[x['start'] for x in doc['occurrences']]
        self.assertEqual(starts,['2024-10-21T08:15:00+02:00','2024-10-28T08:15:00+01:00','2024-11-04T08:15:00+01:00'])
        self.assertEqual([x['end'][11:16] for x in doc['occurrences']],['09:45']*3)

    def test_excluded_date_is_removed_and_until_is_inclusive(self):
        doc=document('RRULE:FREQ=WEEKLY;UNTIL=20241104T071500Z;\r\n'
                     'EXDATE;TZID=Europe/Zurich:20241028T081500\r\n')
        self.assertEqual([x['start'][:10] for x in doc['occurrences']],['2024-10-21','2024-11-04'])

    def test_multiple_exdate_properties_and_utc_forms(self):
        doc=document('RRULE:FREQ=WEEKLY;COUNT=3\r\n'
                     'EXDATE:20241028T071500Z\r\nEXDATE:20241104T071500Z\r\n')
        self.assertEqual(len(doc['occurrences']),1)

    def test_all_day_has_exclusive_date_end(self):
        doc=parse_schedule(calendar(event(start='DTSTART;VALUE=DATE:20241021',
                                          end='DTEND;VALUE=DATE:20241023')),SELECTION)
        item=doc['occurrences'][0]
        self.assertTrue(item['all_day']);self.assertEqual(item['start'],'2024-10-21')
        self.assertEqual(item['end'],'2024-10-23')

    def test_cancelled_series_remains_known_without_active_occurrences(self):
        doc=document('STATUS:CANCELLED\r\nRRULE:FREQ=WEEKLY;COUNT=2\r\n')
        self.assertEqual(doc['occurrences'],[]);self.assertEqual(doc['series'][0]['status'],'CANCELLED')

    def test_folding_and_escaped_text_are_decoded(self):
        source=calendar(event().replace('SUMMARY:Cours fictif','SUMMARY:Cours\\, fictif long\r\n  prolongé'))
        doc=parse_schedule(source,SELECTION)
        self.assertEqual(doc['series'][0]['summary'],'Cours, fictif long prolongé')

    def test_unbounded_unsupported_or_excessive_recurrence_refused(self):
        for extra in ('RRULE:FREQ=WEEKLY\r\n','RRULE:FREQ=MINUTELY;COUNT=3\r\n',
                      'RRULE:FREQ=WEEKLY;COUNT=10000\r\n','RRULE:FREQ=WEEKLY;COUNT=3;INTERVAL=0\r\n',
                      'RRULE:FREQ=WEEKLY;UNTIL=20300101T000000Z\r\n',
                      'RECURRENCE-ID;TZID=Europe/Zurich:20241021T081500\r\n'):
            with self.subTest(extra=extra),self.assertRaises(ParseError):document(extra)

    def test_html_truncation_duplicate_uid_and_floating_times_refused(self):
        for source in ('<html>Login</html>',calendar(event())[:-20],calendar(event()+event()),
                       calendar(event(start='DTSTART:20241021T081500'))):
            with self.subTest(source=source),self.assertRaises(ParseError):parse_schedule(source,SELECTION)

    def test_source_order_and_cosmetic_timestamps_do_not_change_snapshot(self):
        first=event(uid='synthetic-A');second=event(uid='synthetic-B')
        self.assertEqual(parse_schedule(calendar(first+second),SELECTION),
                         parse_schedule(calendar(second+first),SELECTION))
        self.assertEqual(document(),document('DTSTAMP:20241001T120000Z\r\n'))

    def test_location_changes_keep_occurrence_identity(self):
        first=document();second=parse_schedule(calendar(event().replace('Salle fictive','Autre salle')),SELECTION)
        self.assertEqual(first['occurrences'][0]['id'],second['occurrences'][0]['id'])
        self.assertNotEqual(first['occurrences'][0]['location'],second['occurrences'][0]['location'])

    def test_structurally_valid_empty_calendar_distinct_from_failure(self):
        self.assertEqual(parse_schedule(calendar(''),SELECTION)['occurrences'],[])


class ScheduleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.sqlite3'

    def test_baseline_and_identical_sync(self):
        self.assertTrue(synchronize_schedule(self.path,document())['baseline_created'])
        self.assertFalse(synchronize_schedule(self.path,document())['snapshot_changed'])

    def test_location_update_is_persisted_and_missing_occurrence_retained(self):
        doc=document('RRULE:FREQ=WEEKLY;COUNT=2\r\n');synchronize_schedule(self.path,doc)
        modified=deepcopy(doc);missing=modified['occurrences'].pop();modified['occurrences'][0]['location']='Autre salle'
        self.assertTrue(synchronize_schedule(self.path,modified)['snapshot_changed'])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT present FROM schedule_occurrences WHERE id=?',(missing['id'],)).fetchone()[0],0)

    def test_failed_sql_write_preserves_last_valid_schedule(self):
        synchronize_schedule(self.path,document())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_write BEFORE UPDATE ON schedule_occurrences BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):synchronize_schedule(self.path,document('RRULE:FREQ=WEEKLY;COUNT=2\r\n'))
        self.assertFalse(synchronize_schedule(self.path,document())['snapshot_changed'])


if __name__ == '__main__':unittest.main()
