import unittest

from gaps_scraper.notes.structure import inspect_notes, StructureError


# Exemple entièrement synthétique, sans copie des pages privées.
SELECTORS = '''
<select id="selyear"><option value="2023">2023–2024</option>
<option value="2024" selected>2024–2025</option></select>
<select id="selta"><option value="-1">Tous</option>
<option value="0" selected>S1</option><option value="1">S2</option></select>
'''


class InspectTests(unittest.TestCase):
    def test_actual_selection_not_inferred_from_date(self):
        result = inspect_notes(SELECTORS + '<div id="result"><br/>Aucun contr&ocirc;le continu trouv&eacute;</div>')
        self.assertEqual(result["academic_year"]["value"], "2024")
        self.assertEqual(result["semester"]["label"], "S1")
        self.assertEqual(result["page_state"], "initial_empty_unverified")
        self.assertFalse(result["synchronization_validated"])

    def test_empty_only_confirmed_after_ajax(self):
        result = inspect_notes(SELECTORS + '<div id="result">Aucun contrôle continu trouvé</div>',
                               ajax_completed=True)
        self.assertEqual(result["page_state"], "explicit_empty")

    def test_legend_or_script_does_not_prove_empty_result(self):
        for contents in ("", "Chargement…", "Session expirée"):
            with self.subTest(contents=contents), self.assertRaises(StructureError):
                inspect_notes(SELECTORS + '<script>"Aucun contrôle continu trouvé"</script>'
                              + '<div id="result">' + contents + '</div>')

    def test_missing_and_duplicate_results_rejected(self):
        for contents in ("", '<div id="result">Aucun contrôle continu trouvé</div>' * 2):
            with self.subTest(contents=contents), self.assertRaises(StructureError):
                inspect_notes(SELECTORS + contents)

    def test_tables_are_not_misreported_as_parsed_grades(self):
        result = inspect_notes(SELECTORS + '<div id="result"><table class="displayArray">'
                               '<tr><td>Épreuve fictive</td></tr></table></div>')
        self.assertEqual(result["page_state"], "grade_tables_present_unparsed")
        self.assertFalse(result["grades_parsed"])

    def test_unknown_semester_or_ambiguous_selection_rejected(self):
        for source in (SELECTORS.replace('value="0"', 'value="9"'),
                       SELECTORS.replace('value="-1"', 'selected value="-1"')):
            with self.subTest(source=source), self.assertRaises(StructureError):
                inspect_notes(source + '<div id="result">Aucun contrôle continu trouvé</div>')


if __name__ == "__main__":
    unittest.main()
