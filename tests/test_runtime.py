import io
import logging
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from gaps_scraper.common.runtime import app_origin, configure_logging, development
from gaps_scraper.jobs.sync import report_results


class RuntimeTests(unittest.TestCase):
    def test_modes_choose_origin_and_logging_together(self):
        for dev, origin, level in [
            ('true', 'http://127.0.0.1:8000', logging.INFO),
            ('false', 'https://school.example', logging.WARNING),
        ]:
            env = {'DEV': dev}
            if dev == 'false':
                env['APP_ORIGIN'] = origin
            with self.subTest(dev=dev), patch.dict('os.environ', env, clear=True):
                self.assertEqual(app_origin(), origin)
                self.assertEqual(configure_logging().level, level)

    def test_default_is_production_and_invalid_mode_fails(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(development())
            with self.assertRaisesRegex(ValueError, 'APP_ORIGIN'):
                app_origin()
        with patch.dict('os.environ', {'DEV': 'treu'}, clear=True):
            with self.assertRaises(ValueError):
                development()

    def test_empty_origin_is_local_only_in_development(self):
        for dev in ('true', 'false'):
            with self.subTest(dev=dev), patch.dict('os.environ', {'DEV': dev, 'APP_ORIGIN': ''}, clear=True):
                if dev == 'true':
                    self.assertEqual(app_origin(), 'http://127.0.0.1:8000')
                else:
                    with self.assertRaises(ValueError):
                        app_origin()

    def test_production_rejects_http_override(self):
        with patch.dict('os.environ', {'DEV': 'false', 'APP_ORIGIN': 'http://127.0.0.1:8000'}, clear=True):
            with self.assertRaises(ValueError):
                app_origin()
        with patch.dict('os.environ', {'DEV': 'false', 'APP_ORIGIN': 'https://school.example'}, clear=True):
            self.assertEqual(app_origin(), 'https://school.example')

    def test_production_cron_is_quiet_on_success_but_reports_failure(self):
        logger = logging.Logger('test-runtime', level=logging.WARNING)
        stream = io.StringIO()
        logger.addHandler(logging.StreamHandler(stream))
        report_results({'notes': {'ok': True}}, scheduled_run=True, logger=logger)
        self.assertEqual(stream.getvalue(), '')
        report_results({'notes': {'ok': False, 'error': 'network'}}, scheduled_run=True, logger=logger)
        self.assertIn('network', stream.getvalue())
        with redirect_stdout(io.StringIO()) as manual:
            report_results({'notes': {'ok': True}}, scheduled_run=False, logger=logger)
        self.assertIn('"ok": true', manual.getvalue())


if __name__ == '__main__':
    unittest.main()
