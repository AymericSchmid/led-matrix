import itertools
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from configuration import configured_value, dotenv_value, load_sinric_settings
import spotify_client

VALUES = dict(SINRIC_APP_KEY='12345678-1234-1234-1234-123456789abc', SINRIC_APP_SECRET='s' * 32,
              SINRIC_DEVICE_ID='a' * 24, SINRIC_SCENE_INSTANCE_ID='scene')


class ConfigurationTests(unittest.TestCase):
    def test_resolution_and_safe_parsing(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / '.env'
            path.write_text("EXAMPLE='from file'\nLITERAL=$(never_execute)\n", encoding='utf-8')
            self.assertEqual(dotenv_value('EXAMPLE', path), 'from file')
            self.assertEqual(dotenv_value('LITERAL', path), '$(never_execute)')
            for value, expected in [('from environment', 'from environment'), ('', 'from file'), ('  ', 'from file')]:
                with patch.dict(os.environ, EXAMPLE=value):
                    self.assertEqual(configured_value('EXAMPLE', path), expected)
            self.assertEqual(dotenv_value('EXAMPLE', path.parent), '')
            self.assertEqual(dotenv_value('EXAMPLE', path.parent / 'missing'), '')

    def test_sinric_configuration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'missing'
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(load_sinric_settings(path))
            for count in range(1, 4):
                for names in itertools.combinations(VALUES, count):
                    with patch.dict(os.environ, {k: VALUES[k] for k in names}, clear=True):
                        with self.assertRaises(ValueError) as raised:
                            load_sinric_settings(path)
                        for value in VALUES.values():
                            self.assertNotIn(value, str(raised.exception))
            with patch.dict(os.environ, VALUES, clear=True):
                settings = load_sinric_settings(path)
                self.assertEqual((settings.app_key, settings.app_secret, settings.device_id, settings.scene_instance_id), tuple(VALUES.values()))
                self.assertNotIn(VALUES['SINRIC_APP_KEY'], repr(settings))
                self.assertNotIn(VALUES['SINRIC_APP_SECRET'], repr(settings))
            for key in VALUES:
                invalid = {**VALUES, key: 'bad' if key != 'SINRIC_SCENE_INSTANCE_ID' else ' '}
                with patch.dict(os.environ, invalid, clear=True), self.assertRaises(ValueError):
                    load_sinric_settings(path)

    def test_spotify_wrapper_patch_points(self):
        with patch.dict(os.environ, {}, clear=True), patch('spotify_client.dotenv_value', return_value='a' * 32):
            self.assertEqual(spotify_client.configured_env_client_id(), 'a' * 32)
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('SPOTIFY_CLIENT_ID=' + 'b' * 32, encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True), patch('spotify_client.ENV_FILE', path):
                self.assertEqual(spotify_client.configured_env_client_id(), 'b' * 32)
