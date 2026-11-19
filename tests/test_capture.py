import http.cookiejar
import json
import tempfile
from pathlib import Path
import unittest

from gaps_scraper.portal.client import CaptureError, SameOriginRedirect, authenticated_student
from gaps_scraper.diagnostics.capture import capture, redact_known_secrets
from gaps_scraper.notes.client import decode_ajax_result, fetch_notes
from urllib.request import Request


FORM = '<form method="post"><input name="login"><input name="password"></form>'
NOTES = '''<select id="selyear"><option selected value="2026">2026-2027</option></select>
<select id="selta"><option selected value="0">S1</option></select>
<div id="result">Aucun contrôle continu trouvé</div>'''
RESULT = '<div id="result"><table class="displayArray"><tr><td>Fictif</td></tr></table></div>'


class FakeClient:
    def __init__(self, student, ajax=None):
        self.cookies = http.cookiejar.CookieJar()
        self.calls = []
        self.responses = iter([FORM, "", student, NOTES,
                               ajax if ajax is not None else '+:' + json.dumps(json.dumps({"parts": {"result": RESULT}}))])

    def fetch(self, *args):
        self.calls.append(args)
        return next(self.responses)


class CaptureTests(unittest.TestCase):
    def test_session_cookie_alone_is_not_authentication(self):
        for source in (FORM, "", "const DEFAULT_STUDENT_ID = 0;",
                       FORM + "const DEFAULT_STUDENT_ID = 42;"):
            with self.subTest(source=source), self.assertRaises(CaptureError):
                authenticated_student(source)

    def test_external_redirect_blocked_before_credentials_forwarded(self):
        handler = SameOriginRedirect()
        request = Request("https://gaps.heig-vd.ch/consultation/", data=b"secret")
        with self.assertRaises(CaptureError):
            handler.redirect_request(request, None, 307, "", {}, "https://other.example/")

    def test_known_cookie_removed_in_html_and_encoded_forms(self):
        self.assertEqual(redact_known_secrets("a&b a&amp;b a%26b", ["a&b"]),
                         "[SECRET_RETIRE] [SECRET_RETIRE] [SECRET_RETIRE]")

    def test_failed_authentication_leaves_previous_capture_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous.html"
            previous.write_text("previous valid data")
            with self.assertRaises(CaptureError):
                capture(FakeClient(FORM), "test-user", "test-pass", root)
            self.assertEqual(list(root.iterdir()), [previous])
            self.assertEqual(previous.read_text(), "previous valid data")

    def test_success_does_not_claim_grades_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = capture(FakeClient("const DEFAULT_STUDENT_ID = 42;"),
                                "test-user", "test-pass", Path(tmp))
            manifest = json.loads((directory / "manifest.json").read_text())
            self.assertFalse(manifest["grades_parsed"])
            self.assertFalse(manifest["default_term_verified"])
            self.assertTrue(manifest["notes_ajax_completed"])
            self.assertEqual(manifest["notes_observation"]["page_state"], "grade_tables_present_unparsed")
            self.assertEqual((directory / "notes.html").stat().st_mode & 0o777, 0o600)

    def test_ajax_error_does_not_replace_successful_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CaptureError):
                capture(FakeClient("const DEFAULT_STUDENT_ID = 42;", ajax='-:session expired'),
                        "test-user", "test-pass", Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_ajax_uses_selected_filters_and_current_student(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient("const DEFAULT_STUDENT_ID = 42;")
            capture(client, "test-user", "test-pass", Path(tmp))
            url, fields = client.calls[-1]
            self.assertTrue(url.endswith("?idst=42&year=2026&idas=0"))
            self.assertEqual(fields["rs"], "replaceHtmlPart")
            self.assertEqual(json.loads(fields["rsargs"]), ["result", None])

    def test_ajax_requires_successful_envelope_and_result(self):
        for response in ('', '-:error', '+:{}', '+:{"parts": {"result": null}}',
                         '+:' + json.dumps({"parts": {"result": FORM}})):
            with self.subTest(response=response), self.assertRaises(CaptureError):
                decode_ajax_result(response)

    def test_explicit_past_selection_verified_before_ajax(self):
        client = FakeClient('const DEFAULT_STUDENT_ID = 42;')
        selected = NOTES.replace('2026', '2025').replace('2027', '2026')
        client.responses = iter([FORM, '', 'const DEFAULT_STUDENT_ID = 42;', NOTES,
                                 selected, '+:' + json.dumps({'parts': {'result': RESULT}})])
        sources, observation = fetch_notes(client, 'test-user', 'test-pass', year=2025, semester='S1')
        self.assertEqual(observation['academic_year']['value'], '2025')
        self.assertIn('year=2025&idas=0', client.calls[-1][0])

    def test_server_ignoring_requested_selection_aborts_before_ajax(self):
        client = FakeClient('const DEFAULT_STUDENT_ID = 42;')
        client.responses = iter([FORM, '', 'const DEFAULT_STUDENT_ID = 42;', NOTES, NOTES])
        with self.assertRaises(CaptureError):
            fetch_notes(client, 'test-user', 'test-pass', year=2025, semester='S1')
        self.assertEqual(len(client.calls), 5)

    def test_incomplete_selection_rejected_before_network(self):
        client = FakeClient('unused')
        with self.assertRaises(CaptureError):
            fetch_notes(client, 'test-user', 'test-pass', year=2025)
        self.assertEqual(client.calls, [])

    def test_default_selection_follows_new_semester_and_academic_year(self):
        for year, semester, label in [('2026', '1', 'S2'), ('2027', '0', 'S1')]:
            with self.subTest(year=year, semester=semester):
                page = (f'<select id="selyear"><option selected value="{year}">{year}-{int(year)+1}</option></select>'
                        f'<select id="selta"><option selected value="{semester}">{label}</option></select>'
                        '<div id="result">Aucun contrôle continu trouvé</div>')
                client = FakeClient('unused')
                client.responses = iter([FORM, '', 'const DEFAULT_STUDENT_ID = 42;', page,
                                         '+:' + json.dumps({'parts': {'result': RESULT}})])
                _, observation = fetch_notes(client, 'test-user', 'test-pass')
                self.assertEqual(observation['academic_year']['value'], year)
                self.assertEqual(observation['semester']['value'], semester)
                self.assertIn(f'year={year}&idas={semester}', client.calls[-1][0])


if __name__ == "__main__":
    unittest.main()
