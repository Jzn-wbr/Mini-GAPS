from copy import deepcopy
import tempfile
from pathlib import Path
import unittest

from gaps_scraper.notes.programme import parse_programme, parse_unit_alias, enrich_notes
from gaps_scraper.notes.store import synchronize
from gaps_scraper.common.parsing import ParseError
from test_notes import document, course


def module(identity='11',code='MOD-A',name='Module fictif'):
    return f'<tr class="program_module_row"><td><a href="../fiches/mv/mv.php?id={identity}&plan=101">{name} ({code}) 2025</a></td><td></td><td>Coef.</td><td>S1</td></tr>'


def unit(code='BR-A',identity='21',weight='2'):
    return f'<tr class="program_unit_row"><td><a href="/consultation/fiches/uv/uv.php?id={identity}&plan=101">Branche fictive ({code}) 2025</a></td><td></td><td>{weight}</td><td>4</td></tr>'


def programme(rows=None):
    return '<table id="teachingplanbox">'+(rows or module()+unit()+unit('BR-B','22','1'))+'</table>'


def alias(code='BR-A-TIN',module_id='11'):
    return f'<span class="sheet-subtitle">{code} 2025 PT</span><span class="sheet-sct-tooltip"><a href="/consultation/fiches/mv/mv.php?id={module_id}&plan=101">Module fictif</a></span>'


class Client:
    def __init__(self,source=None,fiche=None):self.source=source or programme();self.fiche=fiche or alias();self.calls=[]
    def fetch(self,url):
        self.calls.append(url)
        return self.source if url=='/consultation/programmes/detail.php' else self.fiche


def notes(a='BR-A',b='BR-B'):
    result=document(course(name=a)+course(name=b))
    result['courses'][0]['mean']='5';result['courses'][1]['mean']='2'
    return result


class ProgrammeTests(unittest.TestCase):
    def test_merged_course_columns_keep_coefficient_in_logical_third_column(self):
        merged=unit().replace('<td><a','<td colspan="2"><a').replace('</td><td></td><td>2','</td><td>2')
        self.assertEqual(parse_programme(programme(module()+merged))['modules'][0]['units'][0]['coefficient'],'2')

    def test_rowspan_alternative_marker_and_iduv_link(self):
        first=unit().replace('<td></td>','<td rowspan="2">ou</td>')
        second=unit('BR-B','22','3').replace('<td></td>','').replace('id=22','iduv=22')
        units=parse_programme(programme(module()+first+second))['modules'][0]['units']
        self.assertEqual([u['coefficient'] for u in units],['2','3'])
        self.assertEqual(units[1]['id'],'22')

    def test_extracts_modules_and_source_coefficients(self):
        result=parse_programme(programme())
        self.assertEqual(result['plan'],'101')
        self.assertEqual(result['modules'][0]['name'],'Module fictif')
        self.assertEqual([u['coefficient'] for u in result['modules'][0]['units']],['2','1'])

    def test_weighted_provisional_mean_uses_available_course_means(self):
        result=enrich_notes(Client(),notes())
        self.assertEqual(result['modules'][0]['mean'],'4')
        self.assertTrue(result['modules'][0]['provisional'])
        self.assertEqual(result['unmapped_courses'],[])
        partial=notes();partial['courses'][1]['mean']=None
        self.assertEqual(enrich_notes(Client(),partial)['modules'][0]['mean'],'5')

    def test_changed_source_grouping_and_weights_are_not_hardcoded(self):
        source=programme(module('12','NEW','Autre module')+unit(weight='1')+unit('BR-B','22','2'))
        result=enrich_notes(Client(source),notes())
        self.assertEqual(result['modules'][0]['name'],'Autre module')
        self.assertEqual(result['modules'][0]['mean'],'3')

    def test_alias_must_be_confirmed_by_unit_sheet(self):
        client=Client();result=enrich_notes(client,notes(a='BR-A-TIN'))
        self.assertEqual(result['unmapped_courses'],[])
        self.assertEqual(result['modules'][0]['units'][0]['aliases'],['BR-A-TIN'])
        self.assertEqual(len(client.calls),2)
        result=enrich_notes(Client(fiche=alias('BR-A-OTHER')),notes(a='BR-A-TIN'))
        self.assertEqual(result['unmapped_courses'],['BR-A-TIN'])
        with self.assertRaises(ParseError):enrich_notes(Client(fiche=alias(module_id='99')),notes(a='BR-A-TIN'))

    def test_ambiguous_mapping_and_duplicate_modules_fail(self):
        with self.assertRaises(ParseError):
            enrich_notes(Client(programme(module()+unit()+module('12','MOD-B')+unit(identity='23'))),notes())
        with self.assertRaises(ParseError):parse_programme(programme(module()+unit()+module()+unit()))

    def test_optional_alternative_and_choices_are_explicit(self):
        choices='<tr class="program_unit_row"><td colspan="2">Unités à choix</td><td>--</td></tr>'
        units=parse_programme(programme(module()+unit(weight='fac.')+unit('BR-B','22','(3)')+choices))['modules'][0]['units']
        self.assertIsNone(units[0]['coefficient']);self.assertTrue(units[0]['optional'])
        self.assertEqual(units[1]['coefficient'],'3');self.assertTrue(units[1]['alternative'])
        self.assertIsNone(enrich_notes(Client(programme(module()+unit(weight='fac.')+unit('BR-B','22'))),notes())['modules'][0]['mean'])
        self.assertTrue(parse_programme(programme(module()+choices))['modules'][0]['elective'])

    def test_unknown_structure_mixed_plans_and_bad_weights_fail(self):
        for source in ('<table></table>',programme().replace('Coef.','Nouveau'),
                       programme().replace('plan=101','plan=102',1),programme(module()+unit(weight='0')),
                       programme(module()+unit(weight='inconnu'))):
            with self.subTest(source=source),self.assertRaises(ParseError):parse_programme(source)

    def test_module_metadata_does_not_create_grade_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'notes.sqlite3';original=notes();synchronize(path,original)
            result=synchronize(path,enrich_notes(Client(),deepcopy(original)))
            self.assertTrue(result['snapshot_changed']);self.assertEqual(result['changes_recorded'],0)
            self.assertFalse(synchronize(path,enrich_notes(Client(),deepcopy(original)))['snapshot_changed'])
