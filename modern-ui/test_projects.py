import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from project_store import read_archive
from server import Workspace
import test_audio
from test_audio import fixture, state


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self.temp.name)
        self.raw = fixture(24)
        self.sample = self.workspace.import_wave(self.raw, '060-Principal.wav')

    def tearDown(self):
        self.temp.cleanup()

    def add_edits(self):
        self.sample.update(state(self.sample['name']))
        self.sample['manual'] = True
        self.workspace.project.update(name='Principal 8′', notes='Great · front microphones',
            settings={'minimumLoop': .7, 'threshold': -62},
            export={'mode': 'split', 'trim': True},
            ui={'activeId': self.sample['id'], 'selected': [self.sample['id']], 'filter': 'approved', 'search': 'Principal', 'mode': 'loop', 'volume': 42},
            drafts={self.sample['id']: {'regions': [{'start': 0, 'end': 800, 'note': 60, 'keep': True}, {'start': 900, 'end': 1600, 'note': 61, 'keep': False}], 'settings': {'first': 60, 'step': 1}, 'index': 1, 'cursor': 1200, 'warnings': ['Check the tail.']}})
        self.workspace.save()

    def test_save_reopen_restores_edits_and_source_pcm(self):
        self.add_edits()
        path = self.workspace.projects.save()
        identity = self.workspace.project['id']
        self.workspace.projects.new('Flute 4′')
        self.assertEqual(self.workspace.records, {})
        self.workspace.projects.open(identity)
        project = self.workspace.project
        sample = next(iter(self.workspace.records.values()))
        self.assertEqual(project['name'], 'Principal 8′')
        self.assertEqual(project['notes'], 'Great · front microphones')
        self.assertEqual(project['settings']['threshold'], -62)
        self.assertEqual(project['export'], {'mode': 'split', 'trim': True})
        self.assertEqual(project['ui']['activeId'], sample['id'])
        self.assertEqual(project['ui']['selected'], [sample['id']])
        self.assertFalse(project['drafts'][sample['id']]['regions'][1]['keep'])
        self.assertEqual(project['drafts'][sample['id']]['cursor'], 1200)
        self.assertEqual(sample['markers'], state()['markers'])
        self.assertTrue(sample['reviewed'])
        self.assertTrue(sample['manual'])
        self.assertEqual((self.workspace.folder / (sample['id'] + '.wav')).read_bytes(), self.raw)
        self.assertTrue(path.exists())

    def test_portable_project_opens_in_an_empty_workspace(self):
        self.add_edits()
        path = self.workspace.projects.save()
        with tempfile.TemporaryDirectory() as folder:
            other = Workspace(folder)
            other.projects.import_file(path.read_bytes())
            sample = next(iter(other.records.values()))
            self.assertEqual(other.project['name'], self.workspace.project['name'])
            self.assertIsNone(other.project['id'])
            self.assertEqual(other.wave(sample['id']).pcm, self.workspace.wave(self.sample['id']).pcm)
            self.assertEqual(other.project['drafts'][sample['id']]['index'], 1)

    def test_test_approval_survives_portable_project(self):
        self.add_edits()
        self.sample['testApproved'] = True
        archive = self.workspace.projects.save()
        _, records, _ = read_archive(archive)
        self.assertTrue(records[self.sample['id']]['testApproved'])

    def test_new_project_preserves_unsaved_named_and_unnamed_work(self):
        self.workspace.projects.new('Flute')
        saved = self.workspace.projects.list()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['name'], 'Untitled register')
        self.workspace.projects.open(saved[0]['id'])
        self.assertEqual(len(self.workspace.records), 1)

    def test_copy_is_independent_and_changes_project_token(self):
        self.add_edits()
        original = self.workspace.projects.save()
        old_id, old_token = self.workspace.project['id'], self.workspace.token
        self.workspace.projects.save('Principal alternative', make_copy=True)
        self.assertNotEqual(self.workspace.project['id'], old_id)
        self.assertNotEqual(self.workspace.token, old_token)
        self.workspace.records[self.sample['id']]['markers']['attack'] = 10
        self.workspace.projects.save()
        project, records, _ = read_archive(original)
        self.assertEqual(project['name'], 'Principal 8′')
        self.assertEqual(records[self.sample['id']]['markers']['attack'], 50)

    def test_restart_restores_autosaved_project_and_unfinished_draft(self):
        self.add_edits()
        self.workspace.project['drafts'][self.sample['id']]['regions'][0]['start'] = None
        self.workspace.save()
        restarted = Workspace(self.temp.name)
        self.assertEqual(restarted.project['name'], 'Principal 8′')
        self.assertIsNone(restarted.project['drafts'][self.sample['id']]['regions'][0]['start'])

    def test_split_calibration_mode_round_trips_and_old_draft_survives(self):
        self.add_edits()
        identity = self.sample['id']
        before = copy.deepcopy(self.workspace.project['drafts'][identity]['regions'])
        restored = Workspace(self.temp.name)
        self.assertTrue(restored.project['drafts'][identity]['settings']['autoThreshold'])
        self.assertEqual(restored.project['drafts'][identity]['regions'], before)
        restored.project['drafts'][identity]['settings'].update(autoThreshold=False, threshold=-27)
        archive = restored.projects.save()
        project, _, _ = read_archive(archive)
        self.assertFalse(project['drafts'][identity]['settings']['autoThreshold'])
        self.assertEqual(project['drafts'][identity]['settings']['threshold'], -27)
        self.assertEqual(project['drafts'][identity]['regions'], before)

    def test_legacy_session_is_loaded_and_migrated(self):
        old = [self.sample]
        (self.workspace.folder / 'session.json').write_text(json.dumps(old), 'utf-8')
        restored = Workspace(self.temp.name)
        self.assertEqual(len(restored.records), 1)
        self.assertEqual(restored.project['name'], 'Untitled register')
        restored.save()
        self.assertEqual(json.loads((self.workspace.folder / 'session.json').read_text('utf-8'))['version'], 2)

    def test_source_references_and_drafts_follow_restored_ids(self):
        child = self.workspace.split_recording(self.sample['id'], [{'start': 0, 'end': 800, 'note': 60}])[0]
        path = self.workspace.projects.save('Principal')
        with tempfile.TemporaryDirectory() as folder:
            other = Workspace(folder)
            other.projects.import_file(path.read_bytes())
            child = next(r for r in other.records.values() if r.get('source'))
            self.assertIn(child['source']['id'], other.records)
            self.assertEqual(child['source']['start'], 0)
            self.assertEqual(child['source']['end'], 800)

    def test_archive_write_failure_keeps_previous_save(self):
        target = self.workspace.projects.save('Principal')
        previous = target.read_bytes()
        with patch('project_store.write_archive', side_effect=OSError('No disk space')):
            with self.assertRaises(OSError): self.workspace.projects.save('New title')
        self.assertEqual(target.read_bytes(), previous)
        self.assertFalse(target.with_suffix('.tmp').exists())

    def test_failed_restore_keeps_current_session(self):
        target = self.workspace.projects.save('Principal')
        self.workspace.projects.new('Flute')
        before = copy.deepcopy(self.workspace.project)
        with patch.object(Path, 'write_bytes', side_effect=OSError('No disk space')):
            with self.assertRaises(OSError): self.workspace.projects.import_file(target.read_bytes())
        self.assertEqual(self.workspace.project['name'], before['name'])
        self.assertEqual(self.workspace.records, {})

    def corrupt(self, transform):
        target = self.workspace.projects.save('Principal')
        with zipfile.ZipFile(target) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        transform(entries)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, content in entries.items(): archive.writestr(name, content)
        return buffer.getvalue()

    def test_corrupt_audio_rejected_before_changing_active_project(self):
        def alter(entries):
            name = next(k for k in entries if k.endswith('.wav'))
            entries[name] = entries[name][:-1] + bytes([entries[name][-1] ^ 1])
        raw = self.corrupt(alter)
        old_ids = list(self.workspace.records)
        with self.assertRaises(ValueError): self.workspace.projects.import_file(raw)
        self.assertEqual(list(self.workspace.records), old_ids)

    def test_missing_audio_and_traversal_files_are_rejected(self):
        for transform in [lambda e: e.pop(next(k for k in e if k.endswith('.wav'))), lambda e: e.update({'../../escape.wav': b'bad'})]:
            with self.subTest(transform=transform):
                raw = self.corrupt(transform)
                with self.assertRaises(ValueError): self.workspace.projects.import_file(raw)

    def test_unknown_version_is_rejected(self):
        def alter(entries):
            manifest = json.loads(entries['project.json']); manifest['version'] = 900
            entries['project.json'] = json.dumps(manifest)
        raw = self.corrupt(alter)
        with self.assertRaises(ValueError): self.workspace.projects.import_file(raw)

    def test_invalid_project_id_cannot_escape_library(self):
        with self.assertRaises(ValueError): self.workspace.projects.open('../../elsewhere')


class ProjectApiTests(unittest.TestCase):
    setUp = test_audio.ServerTests.setUp
    tearDown = test_audio.ServerTests.tearDown
    call = test_audio.ServerTests.call

    def test_save_download_new_and_open(self):
        sample = json.loads(self.call('/api/import?name=060-Test.wav', fixture()))
        self.call('/api/project-details', {'name': 'Principal 8', 'notes': 'Great'})
        draft = {'regions': [{'start': 0, 'end': 1000, 'note': 60, 'keep': True}], 'settings': {}}
        self.call('/api/project-draft', {'id': sample['id'], 'draft': draft})
        saved = json.loads(self.call('/api/project-save', {}))
        downloaded = self.call('/api/project-download', {})
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(downloaded)))
        self.call('/api/project-new', {'name': 'Flute 4'})
        opened = json.loads(self.call('/api/project-open', {'id': saved['project']['id']}))
        self.assertEqual(opened['project']['name'], 'Principal 8')
        self.assertEqual(len(opened['project']['drafts']), 1)
        self.assertEqual(len(opened['samples']), 1)

    def test_stale_tab_cannot_edit_after_project_switch(self):
        token = json.loads(self.call('/api/samples'))['token']
        self.call('/api/project-new', {'name': 'New register'}, {'X-Loop-Project': token})
        with self.assertRaises(urllib.error.HTTPError):
            self.call('/api/import?name=060-Test.wav', fixture(), {'X-Loop-Project': token})
        self.assertEqual(len(self.workspace.records), 0)

    def test_cannot_remove_source_while_notes_remain(self):
        source = self.workspace.import_wave(fixture(), 'Register.wav')
        self.workspace.split_recording(source['id'], [{'start': 0, 'end': 800, 'note': 60}])
        with self.assertRaises(urllib.error.HTTPError): self.call('/api/remove', {'ids': [source['id']]})
        self.assertEqual(len(self.workspace.records), 2)


if __name__ == '__main__':
    unittest.main()
