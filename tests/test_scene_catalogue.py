import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scene_catalogue import SCENE_CATALOGUE, SCENE_IDS, SCENE_OPTIONS, scene_metadata
from control_service import ControlService, SettingsStore


class CatalogueTests(unittest.TestCase):
    def test_metadata_and_service(self):
        self.assertEqual(SCENE_IDS, ('clock', 'circle', 'spotify', 'weather', 'moon', 'eyes', 'walker', 'kintsugi', 'shadow'))
        self.assertEqual([s.voice_label_fr for s in SCENE_CATALOGUE],
                         ['Horloge', 'Cercle', 'Spotify', 'Météo', 'Lune', 'Yeux', 'Marcheur', 'Kintsugi', 'Ombre'])
        self.assertEqual([s.label for s in SCENE_CATALOGUE], ['Clock', 'Circle', 'Spotify', 'Weather glass', 'Moon', 'Eyes', 'Night walker', 'Kintsugi', 'Impossible shadow'])
        self.assertEqual([s.icon for s in SCENE_CATALOGUE], ['◷', '◯', '♫', '☀', '☾', '◉', '♟', '◇', '◌'])
        for name in ('circle', 'moon', 'walker'):
            self.assertEqual(SCENE_OPTIONS[name], frozenset())
        with TemporaryDirectory() as directory, patch('control_service.configured_env_client_id', return_value=''):
            service = ControlService(store=SettingsStore(Path(directory) / 'state.db'))
            self.addCleanup(service.stop)
            self.assertEqual(service.state()['scene_metadata'], scene_metadata())
            self.assertEqual(tuple(service.state()['scenes']), SCENE_IDS)
            for item in scene_metadata():
                self.assertEqual(item['options'], sorted(SCENE_OPTIONS[item['id']]))

    def test_cli_choices_match_catalogue(self):
        result = subprocess.run([sys.executable, 'main.py', '--help'], capture_output=True, text=True, check=True)
        self.assertIn('--scene {' + ','.join(SCENE_IDS) + '}', result.stdout)
