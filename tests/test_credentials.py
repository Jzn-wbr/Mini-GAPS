import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gaps_scraper.common.credentials import credentials,CredentialsError


def quoted(value):
    return "'"+value.replace('\\','\\\\').replace("'","\\'")+"'"


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'.env'
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)

    def write(self,contents):
        self.path.write_text(contents);self.path.chmod(0o600)

    def test_special_characters_preserved_without_shell_or_variable_interpretation(self):
        value='synthetic $HOME ${HOME} # `command` $(command) \\ \\" é apostrophe\''
        self.write("APP_ORIGIN=https://school.example\nGAPS_USERNAME='test-account'\nGAPS_PASSWORD="+quoted(value)+'\n')
        self.assertEqual(credentials(self.path),('test-account',value))

    def test_environment_pair_takes_priority(self):
        self.write('invalid')
        with patch.dict(os.environ,{'GAPS_USERNAME':'env-account','GAPS_PASSWORD':'synthetic'}):
            self.assertEqual(credentials(self.path),('env-account','synthetic'))

    def test_partial_missing_and_invalid_configuration_never_echo_values(self):
        for raw in ('sensitive-invalid-line',"GAPS_USERNAME='sensitive'\nGAPS_PASSWORD=''", "GAPS_USERNAME='sensitive'\nGAPS_PASSWORD='unterminated"):
            self.write(raw)
            with self.assertRaises(CredentialsError) as caught:
                credentials(self.path)
            self.assertNotIn('sensitive',str(caught.exception))
        with patch.dict(os.environ,{'GAPS_USERNAME':'sensitive'}):
            with self.assertRaises(CredentialsError):credentials(self.path)
        self.path.unlink()
        with self.assertRaises(CredentialsError):credentials(self.path)

    def test_readable_by_other_users_is_rejected(self):
        self.write("GAPS_USERNAME='test'\nGAPS_PASSWORD='synthetic'")
        self.path.chmod(0o644)
        with self.assertRaises(CredentialsError):credentials(self.path)

    def test_secret_mount_path_is_read_without_loading_other_values_into_environment(self):
        self.write("GAPS_USERNAME='test'\nGAPS_PASSWORD='synthetic'\nAPP_ORIGIN=https://school.example")
        with patch.dict(os.environ,{'GAPS_CREDENTIALS_FILE':str(self.path)}):
            self.assertEqual(credentials(),('test','synthetic'))
            self.assertNotIn('APP_ORIGIN',os.environ)
            self.assertNotIn('GAPS_PASSWORD',os.environ)
