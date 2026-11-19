from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gaps_scraper.common.parsing import ParseError
from gaps_scraper.notes.parser import parse_notes
from gaps_scraper.notes.store import synchronize


PAGE = '''<select id="selyear"><option selected value="2024">2024-2025</option></select>
<select id="selta"><option selected value="0">S1</option></select>'''


def course(name="MAT-FICTIVE", description="Épreuve fictive", day="12.10.2024", grade="5.0"):
    return f'''<table class="displayArray"><tbody>
    <tr><td class="bigheader" colspan="6">{name} - moyenne hors examen : 5.00</td></tr>
    <tr><td rowspan="2">Cours<br/>moyenne : 5.0<br/>poids : 80</td>
    <td>date</td><td>descriptif</td><td>moyenne</td><td>coef.</td><td>note</td></tr>
    <tr><td>{day}</td><td><div>{description}</div></td><td>4.2</td><td>1/2 (50%)</td>
    <td>{grade}</td></tr></tbody></table>'''


def document(contents=None):
    result = parse_notes(PAGE, '<div id="result">' + (contents if contents is not None else course()) + '</div>')
    result["account_id"] = "synthetic-account"
    return result


class ParserTests(unittest.TestCase):
    def test_decimal_and_source_means_not_recalculated(self):
        result = document()
        self.assertEqual(result["courses"][0]["mean"], "5")
        item = result["assessments"][0]
        self.assertEqual(item["weight"], {"numerator": 1, "denominator": 2, "percent": "50"})
        self.assertEqual(item["date"], "2024-10-12")

    def test_same_description_on_distinct_dates_does_not_collide(self):
        one = course().replace('rowspan="2"', 'rowspan="3"')
        row = '<tr><td>13.10.2024</td><td>Épreuve fictive</td><td>4.2</td><td>1/2 (50%)</td><td>4.0</td></tr>'
        result = document(one.replace('</tbody>', row + '</tbody>'))
        self.assertEqual(len({a["id"] for a in result["assessments"]}), 2)

    def test_integer_coefficient_and_rounded_fraction_percentage(self):
        for raw, expected in [('1 (100%)', {'numerator': 1, 'denominator': 1, 'percent': '100'}),
                              ('2 (100 %)', {'numerator': 2, 'denominator': 1, 'percent': '100'}),
                              ('1/3 (33.3%)', {'numerator': 1, 'denominator': 3, 'percent': '33.3'}),
                              ('3/2 (150%)', {'numerator': 3, 'denominator': 2, 'percent': '150'})]:
            with self.subTest(raw=raw):
                result = document(course().replace('1/2 (50%)', raw))
                self.assertEqual(result['assessments'][0]['weight'], expected)

    def test_ambiguous_duplicates_rejected(self):
        html = course()
        row = html[html.rindex('<tr>'):html.index('</tbody>')]
        with self.assertRaises(ParseError):
            document(html.replace('</tbody>', row + '</tbody>'))

    def test_unknown_structure_and_bad_date_do_not_become_empty(self):
        for contents in (course().replace('coef.', 'nouvelle colonne'),
                         course(day="31.02.2024"), course().replace('1/2 (50%)', 'inconnu'),
                         course().replace('1/2 (50%)', '1/0 (50%)')):
            with self.subTest(contents=contents), self.assertRaises(ParseError):
                document(contents)

    def test_grade_markers(self):
        for text, expected in [('-', (None, 'unpublished', False, False)),
                               ('[4.5]', ('4.5', 'published', True, False)),
                               ('a 1.0', ('1', 'published', False, True))]:
            with self.subTest(text=text):
                grade = document(course(grade=text))["assessments"][0]["grade"]
                self.assertEqual(tuple(grade[k] for k in ('value','status','ignored','absent')), expected)

    def test_order_does_not_change_structured_result(self):
        a, b = course(name="FICTIVE-A"), course(name="FICTIVE-B")
        self.assertEqual(document(a+b), document(b+a))

    def test_empty_requires_explicit_server_message(self):
        self.assertEqual(document('Aucun contrôle continu trouvé')["assessments"], [])
        with self.assertRaises(ParseError):
            document('')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'state.sqlite3'

    def kinds(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            return [r[0] for r in db.execute('SELECT kind FROM changes ORDER BY sequence')]

    def test_baseline_and_restart_are_idempotent(self):
        first = synchronize(self.db, document())
        second = synchronize(self.db, document())
        self.assertTrue(first["baseline_created"])
        self.assertFalse(second["snapshot_changed"])
        self.assertEqual(self.kinds(), [])

    def test_publication_and_correction_keep_identity(self):
        synchronize(self.db, document(course(grade='-')))
        synchronize(self.db, document(course(grade='5.0')))
        synchronize(self.db, document(course(grade='5.5')))
        synchronize(self.db, document(course(grade='5.5')))
        self.assertEqual(self.kinds(), ['grade_published', 'grade_changed'])

    def test_mean_change_is_not_new_grade(self):
        synchronize(self.db, document())
        modified = document()
        modified['assessments'][0]['class_mean'] = '4.4'
        synchronize(self.db, modified)
        self.assertEqual(self.kinds(), ['assessment_metadata_changed'])

    def test_disappearance_not_confirmed_deletion_and_return_not_new(self):
        synchronize(self.db, document())
        empty = document('Aucun contrôle continu trouvé')
        synchronize(self.db, empty)
        synchronize(self.db, empty)
        synchronize(self.db, document())
        self.assertEqual(self.kinds(), ['assessment_missing_unconfirmed', 'assessment_reappeared'])

    def test_distinct_accounts_and_semesters_start_separate_baselines(self):
        synchronize(self.db, document())
        another = document()
        another['account_id'] = 'another-synthetic-account'
        self.assertTrue(synchronize(self.db, another)['baseline_created'])
        another['semester'] = {'value': '1', 'label': 'S2'}
        self.assertTrue(synchronize(self.db, another)['baseline_created'])
        self.assertEqual(self.kinds(), [])

    def test_transaction_rolls_back_when_writing_change_fails(self):
        synchronize(self.db, document())
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TRIGGER reject_change BEFORE INSERT ON changes BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            synchronize(self.db, document(course(grade='5.5')))
        self.assertEqual(self.kinds(), [])
        self.assertFalse(synchronize(self.db, document())["snapshot_changed"])


if __name__ == '__main__':
    unittest.main()
