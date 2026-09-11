import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from audio_engine import analyze, demo_wave, read_wave, refine_loop, seam_quality, validate_markers
from project_store import read_archive
from server import Workspace
import test_audio


class NearbyLoopTests(unittest.TestCase):
    def test_search_improves_seam_and_stays_near_both_anchors(self):
        wave = read_wave(demo_wave(48))
        markers = analyze(wave)['markers']
        start, end = markers['loopStart'] + 7, markers['loopEnd'] - 60
        before = copy.deepcopy(markers), wave.pcm, wave.samples.copy()
        proposed = {**markers, 'loopStart': start, 'loopEnd': end}
        choices = refine_loop(wave, markers, start, end, 20, .4)
        self.assertTrue(choices)
        self.assertGreater(choices[0]['quality'], seam_quality(wave, proposed))
        for choice in choices:
            self.assertLessEqual(abs(choice['loopStart'] - start), wave.rate * .02)
            self.assertLessEqual(abs(choice['loopEnd'] - end), wave.rate * .02)
            validate_markers(wave, {**markers, **{k: choice[k] for k in ('loopStart', 'loopEnd')}})
        self.assertEqual(markers, before[0])
        self.assertEqual(wave.pcm, before[1])
        self.assertTrue((wave.samples == before[2]).all())

    def test_missing_loop_can_be_proposed_and_strict_inputs_rejected(self):
        wave = read_wave(demo_wave(48))
        markers = {**analyze(wave)['markers'], 'loopStart': None, 'loopEnd': None}
        start, end = round(wave.rate * .5), round(wave.rate * 1.1)
        self.assertTrue(refine_loop(wave, markers, start, end))
        for a, b, radius in [(True, end, 20), (-1,end,20), (end,start,20), (start,wave.frames+1,20), (start,end,0), (start,end,251), (start,end,float('nan'))]:
            with self.subTest(a=a,b=b,radius=radius), self.assertRaises(ValueError):
                refine_loop(wave, markers, a, b, radius)
        self.assertEqual(refine_loop(wave, markers, start, end, 1, 10), [])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def sample(self, name='048.wav', kind='sample'):
        record = self.workspace.import_wave(demo_wave(48), name, kind=kind)
        record.update(analyze(self.workspace.wave(record['id']), '048.wav'))
        return record

    def test_source_roles_survive_archive_and_legacy_sources_are_inferred(self):
        parent = self.sample('long.wav', 'recording')
        note = self.sample()
        self.workspace.project['ui'] = {'stage':'cuts', 'sourceId':parent['id'], 'activeId':note['id']}
        path = self.workspace.projects.save()
        project, records, _ = read_archive(path)
        self.assertEqual(records[parent['id']]['kind'], 'recording')
        self.assertEqual(project['ui']['stage'], 'cuts')
        self.workspace.projects.new('Other')
        self.workspace.projects.open(project['id'])
        self.assertIn(self.workspace.project['ui']['sourceId'], self.workspace.source_ids())
        # A legacy archive may not have an explicit kind flag.
        parent = next(r for r in self.workspace.records.values() if r['kind'] == 'recording')
        parent.pop('kind')
        self.workspace.project['drafts'][parent['id']] = {'regions':[]}
        self.assertIn(parent['id'], self.workspace.source_ids())

    def test_bulk_approval_skips_sources_invalid_loops_and_missing_keys(self):
        parent = self.sample('source.wav','recording')
        good, invalid, unassigned = self.sample(), self.sample(), self.sample()
        invalid['markers']['loopStart'] = invalid['markers']['loopEnd'] = None
        unassigned['note'] = None
        result = self.workspace.approve_batch([r['id'] for r in (parent,good,invalid,unassigned)])
        self.assertEqual(result['approved'], [good['id']])
        self.assertEqual(len(result['skipped']), 3)
        self.assertFalse(good['testApproved'])
        self.assertFalse(parent['reviewed'])
        self.assertTrue(Workspace(self.temp.name).records[good['id']]['reviewed'])

    def test_test_approval_can_be_promoted_without_downgrading_listening_review(self):
        record = self.sample()
        self.workspace.approve_batch([record['id']], True)
        self.assertTrue(record['testApproved'])
        self.workspace.approve_batch([record['id']], False)
        self.assertFalse(record['testApproved'])
        self.workspace.approve_batch([record['id']], True)
        self.assertFalse(record['testApproved'])

    def test_failed_bulk_save_rolls_back_approval(self):
        record = self.sample()
        with patch.object(self.workspace, 'save', side_effect=OSError('full disk')):
            with self.assertRaises(OSError): self.workspace.approve_batch([record['id']])
        self.assertFalse(self.workspace.records[record['id']]['reviewed'])


class WorkflowServerTests(unittest.TestCase):
    setUp = test_audio.ServerTests.setUp
    tearDown = test_audio.ServerTests.tearDown
    call = test_audio.ServerTests.call

    def test_mistaken_import_type_can_be_corrected_before_splitting(self):
        record = json.loads(self.call('/api/import?name=048.wav&kind=recording', demo_wave(48)))
        self.workspace.project['drafts'][record['id']] = {'regions':[]}
        result = json.loads(self.call('/api/sample-kind', {'id':record['id'], 'kind':'sample'}))
        self.assertEqual(result['samples'][0]['kind'], 'sample')
        self.assertNotIn(record['id'], result['project']['drafts'])
        self.assertNotIn(record['id'], self.workspace.source_ids())
        result = json.loads(self.call('/api/sample-kind', {'id':record['id'], 'kind':'recording'}))
        self.assertEqual(result['samples'][0]['kind'], 'recording')

    def test_recording_is_not_a_detection_approval_export_or_player_target(self):
        record = json.loads(self.call('/api/import?name=048.wav&kind=recording', demo_wave(48)))
        record.update(analyze(self.workspace.wave(record['id']), '048.wav'))
        self.workspace.records[record['id']] = record
        for path, body in [('/api/analyze', {'id':record['id']}), ('/api/update', {'id':record['id'],'reviewed':True}), ('/api/export', {'ids':[record['id']]}), ('/api/player/publish',{})]:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError): self.call(path, body)
        result = json.loads(self.call('/api/approve-batch', {'ids':[record['id']]}))
        self.assertEqual(len(result['skipped']), 1)

    def test_refine_is_read_only_then_apply_can_create_a_missing_loop(self):
        record = json.loads(self.call('/api/import?name=048.wav', demo_wave(48)))
        # Imported WAV has no loop; use the default attack/release bounds.
        before = (Path(self.temp.name) / 'session.json').read_bytes()
        result = json.loads(self.call('/api/loop-refine', {'id':record['id'], 'start':24000, 'end':48000, 'radiusMs':20}))
        self.assertTrue(result['candidates'])
        self.assertEqual((Path(self.temp.name) / 'session.json').read_bytes(), before)
        c = result['candidates'][0]
        updated = json.loads(self.call('/api/update', {'id':record['id'], 'note':48, 'markers':{**record['markers'], 'loopStart':c['loopStart'], 'loopEnd':c['loopEnd']}}))
        self.assertEqual(updated['markers']['loopStart'], c['loopStart'])
        self.assertTrue(updated['manual'])
        self.assertFalse(updated['reviewed'])


if __name__ == '__main__':
    unittest.main()
