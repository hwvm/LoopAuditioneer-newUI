import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from audio_engine import analyze, demo_wave, preview_wave, read_wave, riff
from server import Workspace
import test_audio


class PreviewTests(unittest.TestCase):
    def test_sounding_pitch_is_estimated_independently_of_assigned_key(self):
        wave = read_wave(demo_wave(48))
        wave.note = 60
        result = analyze(wave)
        self.assertEqual(result['note'], 60)
        self.assertEqual(result['estimatedNote'], 48)
        self.assertGreater(result['pitchConfidence'], 80)

    def test_float_headroom_preserves_shape_and_crop_gain(self):
        samples = np.array([0, .5, 1, 2, 6, -6, -2, -.5] * 10, dtype='<f4')
        raw = riff([(b'fmt ', struct.pack('<HHIIHH', 3, 1, 48000, 192000, 4, 32)), (b'data', samples.tobytes())])
        wave = read_wave(raw)
        preview = read_wave(preview_wave(wave))
        self.assertLess(np.max(np.abs(preview.samples)), 1)
        np.testing.assert_allclose(preview.samples[:, 0], samples * .98 / 6, atol=1e-4)
        self.assertEqual(read_wave(preview_wave(wave, 16, 48)).pcm, preview.pcm[32:96])
        self.assertEqual(wave.pcm, samples.tobytes())

    def test_transient_does_not_hide_whole_sustain(self):
        wave = read_wave(demo_wave(48))
        wave.samples[100:200] = 6
        result = analyze(wave)
        self.assertIsNotNone(result['markers']['loopStart'])
        self.assertGreater(result['markers']['release'], wave.rate)


class PlayerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self.temp.name)
        self.raw = demo_wave(48)
        self.record = self.workspace.import_wave(self.raw, 'pipe.wav')
        self.record.update(analyze(self.workspace.wave(self.record['id']), '048.wav'))
        self.workspace.save()

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_survives_project_switch_without_approving_originals(self):
        before = (Path(self.temp.name) / 'session.json').read_bytes()
        stop = self.workspace.player.publish(self.workspace, 'Prestant 4', -12)
        sample = stop['samples'][0]
        self.assertEqual(sample['key'], 36)
        self.assertFalse(sample['reviewed'])
        self.assertEqual((Path(self.temp.name) / 'session.json').read_bytes(), before)
        self.assertEqual(self.workspace.wave(self.record['id']).pcm, read_wave(self.raw).pcm)
        expected = self.workspace.player.audio(stop['id'], sample['id'])
        self.workspace.projects.new('Other register')
        reopened = Workspace(self.temp.name)
        self.assertEqual(reopened.player.audio(stop['id'], sample['id']), expected)
        reopened.projects.open(reopened.projects.list()[0]['id'])
        restored = next(iter(reopened.records.values()))
        self.assertEqual(restored['estimatedNote'], self.record['estimatedNote'])
        self.assertEqual(restored['pitchConfidence'], self.record['pitchConfidence'])

    def test_duplicate_keys_rejected_before_publishing(self):
        duplicate = self.workspace.import_wave(self.raw, 'second.wav')
        duplicate.update(analyze(self.workspace.wave(duplicate['id']), '048.wav'))
        with self.assertRaisesRegex(ValueError, 'Duplicate keyboard key'):
            self.workspace.player.publish(self.workspace)
        self.assertEqual(self.workspace.player.catalog()['stops'], [])

    def test_bad_markers_and_unassigned_notes_are_reported(self):
        self.workspace.import_wave(test_audio.fixture(), 'unassigned.wav')
        stop = self.workspace.player.publish(self.workspace)
        self.assertEqual(len(stop['samples']), 1)
        self.assertEqual(stop['skipped'], ['unassigned.wav'])

    def test_failed_publish_retains_previous_library(self):
        self.workspace.player.publish(self.workspace, 'First')
        before = self.workspace.player.catalog()
        with patch('player_store.preview_wave', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.workspace.player.publish(self.workspace, 'Second')
        self.assertEqual(self.workspace.player.catalog(), before)
        self.assertEqual(len(list(self.workspace.player.folder.iterdir())), 2)

    def test_invalid_mapping_and_asset_traversal_rejected(self):
        for shift in (True, 37, '12', .5):
            with self.assertRaises(ValueError): self.workspace.player.publish(self.workspace, key_shift=shift)
        for stop, sample in [('../session', 'abc'), ('a' * 24, '..'), ('a' * 24, 'b' * 24)]:
            with self.assertRaises(ValueError): self.workspace.player.audio(stop, sample)


class PlayerServerTests(unittest.TestCase):
    setUp = test_audio.ServerTests.setUp
    tearDown = test_audio.ServerTests.tearDown
    call = test_audio.ServerTests.call

    def test_player_routes_publish_and_audio(self):
        record = json.loads(self.call('/api/import?name=048.wav', demo_wave(48)))
        self.call('/api/analyze', {'id': record['id']})
        stop = json.loads(self.call('/api/player/publish', {'name': 'Bourdon', 'keyShift': 12}))
        self.assertEqual(stop['samples'][0]['key'], 60)
        self.assertEqual(len(json.loads(self.call('/api/player'))['stops']), 1)
        self.assertIn(b'Start audio', self.call('/player'))
        self.assertEqual(read_wave(self.call(f"/api/player/audio/{stop['id']}/{record['id']}")).bits, 16)


if __name__ == '__main__':
    unittest.main()
