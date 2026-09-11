import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import urllib.error

import numpy as np

from audio_engine import read_wave, riff
from note_splitter import detect_regions, estimate_note, slice_wave, validate_regions
import test_audio
from test_audio import fixture, state


def recording(notes=(48, 52, 55), gap=.4, anti_phase=False, noise=0):
    rate = 16000
    parts = [np.zeros((round(rate * .25), 2))]
    rng = np.random.default_rng(10)
    for note in notes:
        t = np.arange(round(rate * 1.7)) / rate
        envelope = np.minimum(t / .04, 1) * np.exp(-np.maximum(t - 1.1, 0) * 12)
        tone = .3 * envelope * (np.sin(2 * np.pi * 440 * 2 ** ((note - 69) / 12) * t) + .15 * np.sin(4 * np.pi * 440 * 2 ** ((note - 69) / 12) * t))
        parts.extend([np.stack([tone, -tone if anti_phase else tone * .9], axis=1), np.zeros((round(rate * gap), 2))])
    samples = np.concatenate(parts)
    samples += rng.normal(0, noise, samples.shape)
    return riff([(b'fmt ', struct.pack('<HHIIHH', 1, 2, rate, rate * 4, 4, 16)),
                 (b'data', (samples * 32767).astype('<i2').tobytes())])


class SplitterTests(unittest.TestCase):
    def test_three_notes_with_pauses_and_pitch(self):
        wave = read_wave(recording())
        regions = detect_regions(wave)['regions']
        self.assertEqual([r['note'] for r in regions], [48, 52, 55])
        validate_regions(wave, regions)
        for index, r in enumerate(regions):
            onset = .25 + index * 2.1
            self.assertLess(r['start'] / wave.rate, onset + .015)
            self.assertGreater(r['end'] / wave.rate, onset + 1.5)

    def test_opposite_phase_and_noise(self):
        regions = detect_regions(read_wave(recording(anti_phase=True, noise=.0002)))['regions']
        self.assertEqual([r['note'] for r in regions], [48, 52, 55])

    def test_repeated_pitch_still_splits_at_pauses(self):
        regions = detect_regions(read_wave(recording((60, 60, 60))))['regions']
        self.assertEqual([r['note'] for r in regions], [60, 60, 60])

    def test_microphone_noise_calibration_and_manual_override(self):
        # Reproduce a register take whose pauses are louder than the old -48 gate.
        wave = read_wave(recording(noise=.012, anti_phase=True))
        automatic = detect_regions(wave)
        self.assertEqual([r['note'] for r in automatic['regions']], [48, 52, 55])
        self.assertAlmostEqual(automatic['calibration']['noiseDb'], -38.2, delta=1)
        self.assertAlmostEqual(automatic['calibration']['thresholdDb'], -30.2, delta=1)
        self.assertAlmostEqual(automatic['calibration']['quietDb'], -32.2, delta=1)
        manual = detect_regions(wave, auto_threshold=False)
        self.assertEqual(len(manual['regions']), 1)
        self.assertEqual(manual['calibration']['thresholdDb'], -48)
        self.assertTrue(any('above the quiet-gap level' in w for w in manual['warnings']))
        self.assertEqual(len(detect_regions(wave, threshold=-26, auto_threshold=False)['regions']), 3)

    def test_quieter_note_and_tails_survive_noise_calibration(self):
        wave = read_wave(recording())
        wave.samples[round(2.35 * wave.rate):round(4.05 * wave.rate)] *= .35
        wave.samples += np.random.default_rng(4).normal(0, .012, wave.samples.shape)
        result = detect_regions(wave)
        self.assertEqual(len(result['regions']), 3)
        validate_regions(wave, result['regions'])
        for index, region in enumerate(result['regions']):
            onset = .25 + index * 2.1
            self.assertLess(region['start'] / wave.rate, onset + .015)
            self.assertGreater(region['end'] / wave.rate, onset + 1.35)

    def test_weak_pipe_triggers_without_merging_neighboring_notes(self):
        wave = read_wave(recording())
        # A quiet pipe is only about 9 dB above the room; the former +12 dB
        # trigger missed it. The pause gate must still stay above the noise.
        wave.samples[round(2.35 * wave.rate):round(4.05 * wave.rate)] *= .15
        wave.samples += np.random.default_rng(12).normal(0, .012, wave.samples.shape)
        regions = detect_regions(wave)['regions']
        self.assertEqual(len(regions), 3)
        self.assertEqual(regions[1]['note'], 52)
        validate_regions(wave, regions)

    def test_high_four_foot_pipe_does_not_alias_to_lower_octave(self):
        rate = 48000
        t = np.arange(rate) / rate
        for note in (89, 91, 100, 101, 103):
            with self.subTest(note=note):
                tone = .25 * np.sin(2 * np.pi * 440 * 2 ** ((note - 69) / 12) * t)
                raw = riff([(b'fmt ', struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)),
                            (b'data', (tone * 32767).astype('<i2').tobytes())])
                wave = read_wave(raw)
                self.assertEqual(estimate_note(wave, 0, wave.frames)[0], note)

    def test_background_without_notes_is_not_one_long_note(self):
        wave = read_wave(recording((), noise=.012))
        result = detect_regions(wave)
        self.assertEqual(result['regions'], [])
        self.assertTrue(any('Little level contrast' in w for w in result['warnings']))

    def test_no_silent_intro_needed(self):
        raw = read_wave(recording(noise=.012))
        wave = read_wave(slice_wave(raw, {'start': round(.4 * raw.rate), 'end': raw.frames, 'note': None}))
        result = detect_regions(wave)
        self.assertEqual([r['note'] for r in result['regions']], [48, 52, 55])
        self.assertEqual(result['regions'][0]['start'], 0)

    def test_silence_produces_no_regions(self):
        wave = read_wave(recording(()))
        self.assertEqual(detect_regions(wave)['regions'], [])
        self.assertEqual(estimate_note(wave, 0, wave.frames), (None, None))

    def test_too_short_transient_is_ignored(self):
        wave = read_wave(recording(()))
        wave.samples[100:120] = .8
        self.assertEqual(detect_regions(wave)['regions'], [])

    def test_padding_never_overlaps_neighbors(self):
        wave = read_wave(recording(gap=.05))
        regions = detect_regions(wave, gap_ms=30, tail_ms=5000)['regions']
        self.assertEqual(len(regions), 3)
        validate_regions(wave, regions)
        for a, b in zip(regions, regions[1:]):
            self.assertLessEqual(a['end'], b['start'])

    def test_gap_setting_merges_without_artificial_pitch_cuts(self):
        result = detect_regions(read_wave(recording()), gap_ms=1500)
        self.assertEqual(len(result['regions']), 1)
        self.assertTrue(any('Only one region' in message for message in result['warnings']))

    def test_octaves_and_low_notes(self):
        for note in (24, 36, 60, 84):
            with self.subTest(note=note):
                wave = read_wave(recording((note,)))
                self.assertEqual(estimate_note(wave, round(wave.rate * .4), round(wave.rate * 1.2))[0], note)

    def test_invalid_settings_rejected(self):
        wave = read_wave(recording())
        for kwargs in [{'threshold': float('nan')}, {'gap_ms': -1}, {'minimum_ms': 0}, {'tail_ms': 9000}, {'threshold': True}, {'auto_threshold': 'false'}, {'auto_threshold': 1}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                detect_regions(wave, **kwargs)

    def test_exact_slices_all_pcm_and_float_formats(self):
        region = {'start': 37, 'end': 1001, 'note': 62}
        for bits, floating in [(8, False), (16, False), (24, False), (32, False), (32, True)]:
            with self.subTest(bits=bits, floating=floating):
                wave = read_wave(fixture(bits, floating=floating))
                child = read_wave(slice_wave(wave, region))
                self.assertEqual(child.pcm, wave.pcm[37 * wave.block:1001 * wave.block])
                self.assertEqual(child.note, 62)
                self.assertIsNone(child.loop)
                self.assertIsNone(child.cue)
                self.assertEqual(child.bits, bits)
                self.assertEqual(child.rate, wave.rate)

    def test_old_markers_do_not_leak_into_children(self):
        from audio_engine import write_wave
        wave = read_wave(write_wave(read_wave(fixture()), state()))
        child = read_wave(slice_wave(wave, {'start': 0, 'end': 800, 'note': None}))
        self.assertIsNone(child.note)
        self.assertIsNone(child.loop)
        self.assertIsNone(child.cue)

    def test_out_of_order_overlapping_and_invalid_regions_rejected(self):
        wave = read_wave(fixture())
        good = {'start': 0, 'end': 800, 'note': 60}
        for regions in [[], [good, {**good, 'start': 700}], [{**good, 'start': -1}], [{**good, 'end': 1601}], [{**good, 'note': 128}], [{**good, 'start': True}], [{**good, 'end': .5}], [{**good, 'end': 4}]]:
            with self.subTest(regions=regions), self.assertRaises(ValueError): validate_regions(wave, regions)


class SplitServerTests(unittest.TestCase):
    setUp = test_audio.ServerTests.setUp
    tearDown = test_audio.ServerTests.tearDown
    call = test_audio.ServerTests.call

    def test_preview_routes_automatic_and_manual_calibration(self):
        parent = json.loads(self.call('/api/import?name=Noisy-register.wav', recording(noise=.012)))
        auto = json.loads(self.call('/api/split-preview', {'id': parent['id']}))
        manual = json.loads(self.call('/api/split-preview', {'id': parent['id'], 'autoThreshold': False}))
        self.assertEqual(len(auto['regions']), 3)
        self.assertEqual(len(manual['regions']), 1)
        self.assertEqual(len(self.workspace.records), 1)
        self.assertEqual(self.workspace.project['drafts'], {})

    def test_preview_does_not_create_files_and_split_preserves_source(self):
        raw = recording()
        parent = json.loads(self.call('/api/import?name=Whole-rank.wav', raw))
        preview = json.loads(self.call('/api/split-preview', {'id': parent['id']}))
        self.assertEqual(len(self.workspace.records), 1)
        result = json.loads(self.call('/api/split-create', {'id': parent['id'], 'regions': preview['regions']}))
        self.assertEqual(len(result['created']), 3)
        self.assertEqual(len(result['samples']), 4)
        self.assertEqual((Path(self.temp.name) / (parent['id'] + '.wav')).read_bytes(), raw)
        wave = read_wave(raw)
        for identity, region in zip(result['created'], preview['regions']):
            child = self.workspace.records[identity]
            self.assertFalse(child['reviewed'])
            self.assertFalse(child['analyzed'])
            self.assertEqual(child['note'], region['note'])
            self.assertEqual(child['source']['id'], parent['id'])
            self.assertEqual(self.workspace.wave(identity).pcm, wave.pcm[region['start'] * wave.block:region['end'] * wave.block])
            detected = json.loads(self.call('/api/analyze', {'id': identity}))
            self.assertEqual(detected['note'], region['note'])

    def test_invalid_split_does_not_create_partial_batch(self):
        parent = json.loads(self.call('/api/import?name=Whole-rank.wav', recording()))
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/split-create', {'id': parent['id'], 'regions': [{'start': 0, 'end': 1000, 'note': 60}, {'start': 500, 'end': 1500, 'note': 61}]})
        self.assertEqual(list(self.workspace.records), [parent['id']])
        self.assertEqual(len(list(Path(self.temp.name).glob('*.wav'))), 1)

    def test_unassigned_region_number_is_not_mistaken_for_pitch(self):
        parent = self.workspace.import_wave(recording((60,)), '060-recording.wav')
        regions = detect_regions(self.workspace.wave(parent['id']))['regions']
        regions[0]['note'] = None
        identity = self.workspace.split_recording(parent['id'], regions)[0]
        detected = json.loads(self.call('/api/analyze', {'id': identity}))
        self.assertIsNone(detected['note'])

    def test_write_failure_rolls_back_children(self):
        parent = self.workspace.import_wave(recording(), 'Whole-rank.wav')
        regions = detect_regions(self.workspace.wave(parent['id']))['regions']
        original = self.workspace.import_wave
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('Simulated write failure')
            return original(*args, **kwargs)
        with patch.object(self.workspace, 'import_wave', side_effect=fail_second):
            with self.assertRaises(OSError): self.workspace.split_recording(parent['id'], regions)
        self.assertEqual(list(self.workspace.records), [parent['id']])
        self.assertEqual(len(list(Path(self.temp.name).glob('*.wav'))), 1)
        from server import Workspace
        self.assertEqual(list(Workspace(self.temp.name).records), [parent['id']])

    def test_budget_failure_has_no_side_effects(self):
        parent = self.workspace.import_wave(recording(), 'Whole-rank.wav')
        regions = detect_regions(self.workspace.wave(parent['id']))['regions']
        with patch('server.MAX_BATCH', parent['bytes'] + 10):
            with self.assertRaises(ValueError): self.workspace.split_recording(parent['id'], regions)
        self.assertEqual(len(self.workspace.records), 1)

    def test_region_preview_audio_is_cropped(self):
        parent = self.workspace.import_wave(recording(), 'Whole-rank.wav')
        raw = self.call(f'/api/audio/{parent["id"]}?start=200&end=1000')
        self.assertEqual(read_wave(raw).frames, 800)
        with self.assertRaises(urllib.error.HTTPError):
            self.call(f'/api/audio/{parent["id"]}?start=-1&end=1000')


if __name__ == '__main__':
    unittest.main()
