"""Run with python -m unittest -v. No test framework dependency."""
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import numpy as np

from audio_engine import (analyze, demo_wave, export_zip, filename_note, hauptwerk_filename, loop_candidates, peaks,
                          read_wave, riff, seam_quality, validate_markers, write_wave)
from server import Workspace, handler_for


def fixture(bits=16, channels=2, floating=False):
    n, rate = 1600, 8000
    v = np.linspace(-.8, .8, n * channels)
    if floating:
        pcm = v.astype('<f4').tobytes()
    elif bits == 8:
        pcm = ((v + 1) * 128).astype('u1').tobytes()
    elif bits == 24:
        values = (v * 8388607).astype(np.int32)
        pcm = np.stack([values & 255, (values >> 8) & 255, (values >> 16) & 255], axis=1).astype('u1').tobytes()
    else:
        pcm = (v * (2 ** (bits - 1) - 1)).astype('<i' + str(bits // 8)).tobytes()
    block = channels * bits // 8
    return riff([(b'fmt ', struct.pack('<HHIIHH', 3 if floating else 1, channels, rate, rate * block, block, bits)),
                 (b'JUNK', b'odd'), (b'LIST', b'INFOtest'), (b'data', pcm)])


def state(name='060-Test.wav'):
    return {'name': name, 'markers': {'attack': 50, 'loopStart': 200, 'loopEnd': 800, 'release': 1000, 'end': 1500}, 'note': 60, 'reviewed': True}


class WaveTests(unittest.TestCase):
    def test_bit_true_roundtrip_all_formats(self):
        for bits, floating in [(8,False),(16,False),(24,False),(32,False),(32,True)]:
            for channels in (1,2):
                with self.subTest(bits=bits, channels=channels, floating=floating):
                    source = read_wave(fixture(bits, channels, floating))
                    result = read_wave(write_wave(source, state()))
                    self.assertEqual(result.pcm, source.pcm)
                    self.assertEqual(result.loop, (200,800))
                    self.assertEqual(result.cue, 1000)
                    self.assertEqual(result.note, 60)
                    self.assertEqual(result.bits, bits)
                    self.assertIn((b'JUNK', b'odd'), result.chunks)
                    self.assertIn((b'LIST', b'INFOtest'), result.chunks)

    def test_trim_adjusts_every_offset(self):
        wave = read_wave(fixture(24))
        result = read_wave(write_wave(wave, state(), trim=True))
        self.assertEqual(result.frames, 1450)
        self.assertEqual(result.pcm, wave.pcm[50 * wave.block:1500 * wave.block])
        self.assertEqual(result.loop, (150,750))
        self.assertEqual(result.cue, 950)
        self.assertNotIn(b'JUNK', [tag for tag, _ in result.chunks])

    def test_split_keeps_looped_attack_and_exact_release(self):
        wave = read_wave(fixture())
        attack = read_wave(write_wave(wave, state(), part='attack'))
        release = read_wave(write_wave(wave, state(), part='release'))
        self.assertEqual(attack.frames, 750)
        self.assertEqual(attack.loop, (150,750))
        self.assertIsNone(attack.cue)
        self.assertEqual(release.pcm, wave.pcm[1000 * wave.block:1500 * wave.block])
        self.assertIsNone(release.loop)
        self.assertIsNone(release.cue)

    def test_loop_end_is_inclusive_in_riff(self):
        result = read_wave(write_wave(read_wave(fixture()), state()))
        sampler = next(data for tag, data in result.chunks if tag == b'smpl')
        self.assertEqual(struct.unpack_from('<I', sampler, 48)[0], 799)

    def test_rejects_invalid_markers(self):
        wave = read_wave(fixture())
        for key, value in [('attack',-1), ('loopStart',900), ('loopEnd',1200), ('release',1550), ('end',1601), ('loopStart',None), ('end',True), ('end',1500.1)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_markers(wave, {**state()['markers'], key:value})

    def test_rejects_corrupt_wavs(self):
        raw = fixture()
        for data in [b'', b'not a wave', raw[:-1], raw[:20]]:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                read_wave(data)

    def test_rejects_nan_float(self):
        raw = riff([(b'fmt ',struct.pack('<HHIIHH',3,1,8000,32000,4,32)), (b'data', np.full(20,np.nan,dtype='<f4').tobytes())])
        with self.assertRaises(ValueError): read_wave(raw)

    def test_extensible_pcm(self):
        wave = read_wave(fixture())
        fmt = struct.pack('<HHIIHHHHI',65534,2,8000,32000,4,16,22,16,3) + bytes.fromhex('0100000000001000800000aa00389b71')
        result = read_wave(riff([(b'fmt ',fmt), (b'data',wave.pcm)]))
        self.assertEqual(result.pcm, wave.pcm)

    def test_existing_markers_are_retained(self):
        wave = read_wave(write_wave(read_wave(fixture()), state()))
        result = analyze(wave)
        self.assertEqual(result['markers']['loopStart'], 200)
        self.assertEqual(result['markers']['loopEnd'], 800)
        self.assertEqual(result['markers']['release'], 1000)
        self.assertFalse(result['reviewed'])

    def test_pitch_from_filename(self):
        for name, note in [('060-Principal.wav',60),('Principal_C4.wav',60),('C#3.wav',49),('060.wav',60),('Rank_Bb2.wav',46),('unknown.wav',None),('999-Test.wav',None)]:
            with self.subTest(name=name): self.assertEqual(filename_note(name), note)

    def test_detects_synthetic_sustain(self):
        wave = read_wave(demo_wave(48))
        result = analyze(wave,'048-Principal.wav')
        validate_markers(wave,result['markers'])
        self.assertGreater(result['quality'],85)
        self.assertAlmostEqual(result['markers']['attack'] / wave.rate,.08,delta=.03)
        self.assertAlmostEqual(result['markers']['release'] / wave.rate,4.15,delta=.15)
        self.assertFalse(result['reviewed'])

    def test_loop_alternatives_are_distinct_valid_and_leave_markers_unchanged(self):
        wave = read_wave(demo_wave(48))
        wave.samples[:, 1] *= -1
        markers = analyze(wave)['markers']
        before = dict(markers)
        choices = loop_candidates(wave, markers, .7)
        self.assertEqual(len(choices), 5)
        self.assertEqual(markers, before)
        self.assertEqual(len({(c['loopStart'], c['loopEnd']) for c in choices}), len(choices))
        for c in choices:
            candidate = {**markers, 'loopStart': c['loopStart'], 'loopEnd': c['loopEnd']}
            validate_markers(wave, candidate)
            self.assertGreaterEqual(c['loopEnd'] - c['loopStart'], round(.7 * wave.rate))
            self.assertEqual(c['quality'], seam_quality(wave, candidate))
            self.assertGreater(abs(c['loopStart'] - markers['loopStart']) + abs(c['loopEnd'] - markers['loopEnd']), .08 * wave.rate)

    def test_alternative_loop_limits_and_silence(self):
        wave = read_wave(demo_wave(48))
        markers = analyze(wave)['markers']
        self.assertEqual(loop_candidates(wave, markers, 10), [])
        wave.samples[:] = 0
        self.assertEqual(loop_candidates(wave, markers), [])
        for value in [float('nan'), True, 0, 11, '1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                loop_candidates(wave, markers, value)

    def test_silence_never_has_a_loop(self):
        raw = riff([(b'fmt ',struct.pack('<HHIIHH',1,1,8000,16000,2,16)),(b'data',bytes(32000))])
        result = analyze(read_wave(raw))
        self.assertIsNone(result['markers']['loopStart'])
        self.assertTrue(result['warnings'])

    def test_short_sample_does_not_crash(self):
        raw = riff([(b'fmt ',struct.pack('<HHIIHH',1,1,8000,16000,2,16)),(b'data',struct.pack('<16h', *([2000]*16)))])
        wave = read_wave(raw)
        result = analyze(wave)
        validate_markers(wave,result['markers'],require_loop=False)

    def test_stereo_phase_cancellation_does_not_hide_signal(self):
        wave = read_wave(demo_wave(48))
        wave.samples[:,1] = -wave.samples[:,0]
        result = analyze(wave,'048.wav')
        self.assertIsNotNone(result['markers']['loopStart'])

    def test_waveform_contains_both_channels(self):
        wave = read_wave(fixture())
        result = peaks(wave,100)
        self.assertEqual(len(result),2)
        self.assertEqual(len(result[0]),100)
        self.assertTrue(all(low <= high for channel in result for low,high in channel))

    def test_seam_quality_changes_after_edit(self):
        wave = read_wave(demo_wave(48))
        result = analyze(wave,'048.wav')
        good = seam_quality(wave,result['markers'])
        result['markers']['loopEnd'] += 60
        self.assertLess(seam_quality(wave,result['markers']), good)

    def test_exports_hauptwerk_names_sfz_paths_and_source_report(self):
        wave = read_wave(fixture())
        first, second = {**state('Same.wav'), 'note': 36}, {**state('same.wav'), 'note': 37}
        raw = export_zip([(first,wave),(second,wave)],'sfz',True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIn('samples/036-C.wav',archive.namelist())
            self.assertIn('samples/037-C#.wav',archive.namelist())
            text = archive.read('instrument.sfz').decode()
            self.assertIn('loop_end=749',text)
            self.assertIn('sample=samples/037-C#.wav key=37 pitch_keycenter=37',text)
            report = json.loads(archive.read('preparation.json'))
            self.assertEqual(len(report['samples']),2)
            self.assertEqual(report['samples'][0]['name'], 'Same.wav')
            self.assertEqual(report['samples'][1]['exportedFiles'], ['samples/037-C#.wav'])
            self.assertEqual(first['name'], 'Same.wav')
            self.assertEqual(read_wave(archive.read('samples/037-C#.wav')).note, 37)

    def test_hauptwerk_pitch_classes_and_entire_midi_range(self):
        names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        for note in range(128):
            self.assertEqual(hauptwerk_filename(note), f'{note:03d}-{names[note % 12]}.wav')
            self.assertEqual(filename_note(hauptwerk_filename(note)), note)
        for note in [None, True, -1, 128, 60.0, '60']:
            with self.subTest(note=note), self.assertRaises(ValueError):
                export_zip([({**state(), 'note': note}, read_wave(fixture()))])

    def test_duplicate_keys_are_rejected_in_every_export_mode(self):
        wave = read_wave(fixture())
        for mode in ['wav', 'split', 'sfz']:
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'Duplicate MIDI key 60.*first.wav.*second.wav.*060-C.wav'):
                export_zip([(state('first.wav'), wave), (state('second.wav'), wave)], mode)

    def test_split_exports_matching_hauptwerk_names_and_exact_audio(self):
        wave = read_wave(fixture(24))
        record = {**state('recording-region-01.wav'), 'note': 46,
                  'source': {'name': 'Full-register.wav', 'start': 12000, 'end': 13600}}
        with zipfile.ZipFile(io.BytesIO(export_zip([(record, wave)], 'split'))) as archive:
            attack, release = 'attack/046-A#.wav', 'release/046-A#.wav'
            self.assertEqual(read_wave(archive.read(attack)).pcm, wave.pcm[50 * wave.block:800 * wave.block])
            self.assertEqual(read_wave(archive.read(release)).pcm, wave.pcm[1000 * wave.block:1500 * wave.block])
            item = json.loads(archive.read('preparation.json'))['samples'][0]
            self.assertEqual(item['exportedFiles'], [attack, release])
            self.assertEqual(item['source'], record['source'])

    def test_unreviewed_and_empty_exports_are_blocked(self):
        with self.assertRaises(ValueError): export_zip([])
        with self.assertRaises(ValueError): export_zip([({**state(),'reviewed':False},read_wave(fixture()))])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1',0), handler_for(self.workspace,0))
        self.port = self.server.server_port
        self.server.RequestHandlerClass = handler_for(self.workspace,self.port)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def call(self,path,body=None,headers=None):
        raw = body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',data=raw,headers={'X-Loop-Workspace':'1',**(headers or {})})
        with urllib.request.urlopen(req) as response:
            return response.read()

    def test_full_import_edit_export_and_persistence(self):
        original = fixture(24)
        sample = json.loads(self.call('/api/import?name=060-Test.wav',original))
        identity = sample['id']
        self.call('/api/update',{**state(),'id':identity})
        exported = self.call('/api/export',{'ids':[identity]})
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            self.assertEqual(read_wave(archive.read('samples/060-C.wav')).pcm,read_wave(original).pcm)
        self.assertTrue(Workspace(self.temp.name).records[identity]['reviewed'])
        self.assertEqual((Path(self.temp.name)/(identity+'.wav')).read_bytes(),original)
        updated = json.loads(self.call('/api/update',{'id':identity,'markers':{**state()['markers'],'attack':40}}))
        self.assertFalse(updated['reviewed'])
        self.assertTrue(updated['manual'])

    def test_testing_approval_still_validates_and_can_be_reviewed_or_edited(self):
        sample = json.loads(self.call('/api/import?name=Unknown.wav', fixture()))
        identity = sample['id']
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/update', {'id': identity, 'reviewed': True, 'testApproval': True})
        self.assertFalse(self.workspace.records[identity]['reviewed'])
        self.call('/api/update', {**state(), 'id': identity, 'note': None, 'reviewed': False})
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/update', {'id': identity, 'reviewed': True, 'testApproval': True})
        approved = json.loads(self.call('/api/update', {**state(), 'id': identity, 'testApproval': True}))
        self.assertTrue(approved['testApproved'])
        self.assertTrue(Workspace(self.temp.name).records[identity]['testApproved'])
        reviewed = json.loads(self.call('/api/update', {'id': identity, 'reviewed': True}))
        self.assertFalse(reviewed['testApproved'])
        edited = json.loads(self.call('/api/update', {'id': identity, 'note': 61}))
        self.assertFalse(edited['reviewed'])
        self.assertFalse(edited['testApproved'])

    def test_loop_search_is_read_only_and_try_keeps_other_markers(self):
        raw = demo_wave(48)
        sample = json.loads(self.call('/api/import?name=048-Pipe.wav', raw))
        identity = sample['id']
        sample = json.loads(self.call('/api/analyze', {'id': identity}))
        self.call('/api/update', {'id': identity, 'reviewed': True, 'testApproval': True})
        before = (Path(self.temp.name) / 'session.json').read_bytes()
        choices = json.loads(self.call('/api/loop-candidates', {'id': identity}))['candidates']
        self.assertEqual((Path(self.temp.name) / 'session.json').read_bytes(), before)
        choice = choices[0]
        markers = {**sample['markers'], 'loopStart': choice['loopStart'], 'loopEnd': choice['loopEnd']}
        changed = json.loads(self.call('/api/update', {'id': identity, 'markers': markers}))
        self.assertFalse(changed['reviewed'])
        self.assertFalse(changed['testApproved'])
        self.assertEqual(changed['note'], sample['note'])
        for key in ['attack', 'release', 'end']:
            self.assertEqual(changed['markers'][key], sample['markers'][key])
        self.assertEqual((Path(self.temp.name) / (identity + '.wav')).read_bytes(), raw)

    def test_invalid_edit_does_not_change_saved_state(self):
        sample = json.loads(self.call('/api/import?name=060-Test.wav',fixture()))
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/update',{'id':sample['id'],'markers':{**state()['markers'],'end':9999}})
        self.assertEqual(self.workspace.records[sample['id']]['markers'],sample['markers'])

    def test_cross_origin_mutations_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/import?name=060-Test.wav',fixture(),{'Origin':'https://example.com'})
        self.assertFalse(self.workspace.records)

    def test_unknown_ids_cannot_access_files(self):
        with self.assertRaises(urllib.error.HTTPError): self.call('/api/audio/..')
        with self.assertRaises(urllib.error.HTTPError): self.call('/api/remove',{'ids':['../elsewhere']})


if __name__ == '__main__':
    unittest.main()
