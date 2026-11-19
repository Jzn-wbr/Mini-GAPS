from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gaps_scraper.absences.client import fetch_absences
from gaps_scraper.absences.parser import parse_absences
from gaps_scraper.absences.store import synchronize_absences
from gaps_scraper.portal.client import CaptureError
from gaps_scraper.common.parsing import ParseError


HEADER = '''<tr><td>Etudiant-e</td><td>Orientation</td><td>Unites</td>
<td>E</td><td>T1</td><td>T2</td><td>T3</td><td>T4</td><td>Total</td><td>Relatif</td><td>Absolu</td></tr>
<tr><td>Périodes</td><td>Taux</td><td>Taux hors justifiées</td>
<td>Périodes</td><td>Taux</td><td>Taux hors justifiées</td></tr>'''


def grid(name='Cours fictif'):
    # Les fermetures td manquantes et le dernier tr reproduisent la forme HTML,
    # les noms et nombres sont entièrement inventés.
    return '''<div id="studentAbsGrid"><table class="absenceArray">''' + HEADER + f'''
<tr class="a_r_0"><td class="s_cell">Élève fictif</td><td class="l_cell s_cell">FICT</td>
<td class="l_cell">{name}<td class="b_cell"></td><td class="b_cell">2 [1]</td>
<td class="b_cell">0</td><td class="b_cell"> </td><td class="b_cell"> </td>
<td class="b_cell">2 [1]</td><td class="b_cell">20</td><td class="tot_cell">10%</td>
<td class="tot_cell">5%</td><td class="b_cell">100</td><td class="tot_cell">2%</td>
<td class="tot_cell">1%</td></tr><tr class="a_r_0"></table></div>'''


def document():
    return parse_absences(grid(), year=2025, account_id='synthetic-account')


DETAILS = '''<div id="studentDataDiv">
<select id="studentAbsGrid_yearSelectorId"><option value="2025">2025-2026</option>
<option selected value="2026">2026-2027</option></select>
<select id="studentAbsGrid_rateSelectorId"><option value="0">Toutes les absences</option>
<option selected value="10">&gt;= 10%</option></select></div>'''


def envelope(contents):
    return '+:' + json.dumps('@££@' + contents + '@££@')


class FakeClient:
    def __init__(self, final=None, details=DETAILS):
        self.calls = []
        self.responses = iter([
            '<form method="post"><input name="login"><input name="password"></form>', '',
            'const DEFAULT_STUDENT_ID = 42;<input id="upid" value="fake-page">',
            envelope(details), envelope(final if final is not None else grid())])

    def fetch(self, *args):
        self.calls.append(args)
        return next(self.responses)


class AbsenceParserTests(unittest.TestCase):
    def test_optional_cell_closing_and_private_labels_omitted(self):
        doc = document()
        self.assertEqual(len(doc['courses']), 1)
        self.assertNotIn('Élève fictif', json.dumps(doc, ensure_ascii=False))
        self.assertNotIn('FICT', json.dumps(doc))

    def test_blank_zero_and_justifications_distinct(self):
        course = document()['courses'][0]
        self.assertIsNone(course['periods']['E'])
        self.assertEqual(course['periods']['T2'], {'total': 0, 'justified': 0, 'unjustified': 0})
        self.assertEqual(course['periods']['T1'], {'total': 2, 'justified': 1, 'unjustified': 1})
        self.assertEqual(course['relative']['unjustified_rate_percent'], '5')
        self.assertEqual(course['absolute']['periods'], 100)

    def test_bad_values_columns_and_empty_table_refused(self):
        for source in (grid().replace('2 [1]', '2 [3]'), grid().replace('20</td>', 'inconnu</td>'),
                       grid().replace('10%', 'inconnu'), grid().replace('Unites', 'Colonne nouvelle'),
                       '<div id="studentAbsGrid"><table class="absenceArray">'+HEADER+'</table></div>'):
            with self.subTest(source=source), self.assertRaises(ParseError):
                parse_absences(source, year=2025, account_id='synthetic')

    def test_duplicate_course_refused_without_overwriting(self):
        source=grid()
        row=source[source.index('<tr class="a_r_0">'):source.index('</tr><tr class="a_r_0">')+5]
        with self.assertRaises(ParseError):
            parse_absences(source.replace('</table>',row+'</table>'),year=2025,account_id='synthetic')

    def test_account_and_year_separate_course_identity(self):
        first = document()['courses'][0]['id']
        for account,year in [('another-account',2025),('synthetic-account',2026)]:
            self.assertNotEqual(first,parse_absences(grid(),year=year,account_id=account)['courses'][0]['id'])

    def test_justification_legend_after_empty_tr_is_not_a_course(self):
        footer = ('<tr><td></td><td colspan="14">'
                  'Les valeurs indiquent toutes les absences, justifiées ou non. '
                  'Le nombre de périodes justifiées apparaît entre [].</td></tr>')
        doc = parse_absences(grid().replace('</table>', footer+'</table>'), year=2025, account_id='synthetic')
        self.assertEqual(len(doc['courses']),1)
        with self.assertRaises(ParseError):
            parse_absences(grid().replace('</table>', '<tr><td>Structure inconnue</td></tr></table>'),
                           year=2025,account_id='synthetic')


class AbsenceClientTests(unittest.TestCase):
    def test_default_ten_percent_filter_replaced_with_all(self):
        client=FakeClient()
        _,metadata=fetch_absences(client,'test-user','test-password')
        args=json.loads(client.calls[-1][1]['rsargs'])
        self.assertEqual(args,['studentAbsGrid_rateSelectorId','studentAbsGrid','fake-page',None,None,'2026','0',42,None])
        self.assertEqual(metadata['year'],'2026')

    def test_historical_year_is_sent_without_changing_student(self):
        client=FakeClient()
        fetch_absences(client,'test-user','test-password',year=2025)
        self.assertEqual(json.loads(client.calls[-1][1]['rsargs'])[5:8],['2025','0',42])

    def test_default_selection_follows_new_academic_year(self):
        details = DETAILS.replace('value="2026">2026-2027', 'value="2027">2027-2028')
        client = FakeClient(details=details)
        _, metadata = fetch_absences(client, 'test-user', 'test-password')
        self.assertEqual(metadata['year'], '2027')
        self.assertEqual(json.loads(client.calls[-1][1]['rsargs'])[5:7], ['2027', '0'])

    def test_unavailable_year_or_missing_all_option_stops_before_grid_fetch(self):
        for year,details in [(2024,DETAILS),(2026,DETAILS.replace('Toutes les absences','Option inconnue'))]:
            client=FakeClient(details=details)
            with self.assertRaises(CaptureError):fetch_absences(client,'test-user','test-password',year=year)
            self.assertEqual(len(client.calls),4)

    def test_error_or_unrelated_ajax_response_is_not_empty_absence_list(self):
        client=FakeClient()
        client.responses=iter(['<form method="post"><input name="login"><input name="password"></form>',
                               '', 'const DEFAULT_STUDENT_ID = 42;<input id="upid" value="fake-page">',
                               envelope(DETAILS),'-:session expired'])
        with self.assertRaises(CaptureError):fetch_absences(client,'test-user','test-password')


class AbsenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.sqlite3'

    def test_baseline_then_identical_read_is_idempotent(self):
        self.assertTrue(synchronize_absences(self.path,document())['baseline_created'])
        self.assertFalse(synchronize_absences(self.path,document())['snapshot_changed'])

    def test_transaction_failure_preserves_last_valid_snapshot(self):
        synchronize_absences(self.path,document())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON absence_rows BEGIN SELECT RAISE(ABORT,'test'); END")
        changed=document();changed['courses'][0]['relative']['rate_percent']='15'
        with self.assertRaises(sqlite3.IntegrityError):synchronize_absences(self.path,changed)
        self.assertFalse(synchronize_absences(self.path,document())['snapshot_changed'])

    def test_missing_course_is_retained_as_unconfirmed(self):
        first=document();second_course=deepcopy(first['courses'][0]);second_course['id']='second-id'
        second_course['name']='Autre cours fictif';first['courses'].append(second_course)
        synchronize_absences(self.path,first);synchronize_absences(self.path,document())
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT present FROM absence_rows WHERE id=?',('second-id',)).fetchone()[0],0)


if __name__ == '__main__':unittest.main()
