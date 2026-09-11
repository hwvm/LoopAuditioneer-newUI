import json
import unittest
from unittest.mock import patch
import urllib.error

import test_player
import test_audio
from player_store import fingerprint
from audio_engine import demo_wave, read_wave


class LinkedStopTests(unittest.TestCase):
    setUp = test_player.PlayerStoreTests.setUp
    tearDown = test_player.PlayerStoreTests.tearDown

    def saved(self):
        self.workspace.project['name'] = 'Bourdon 8'
        self.workspace.projects.save()
        return self.workspace.player.publish(self.workspace, linked=True)

    def test_update_keeps_stop_identity_and_old_audio_revision_readable(self):
        first = self.saved()
        old = self.workspace.player.audio(first['id'], self.record['id'], first['revision'])
        self.record['markers']['attack'] += 100
        second = self.workspace.player.publish(self.workspace, linked=True)
        self.assertEqual(first['id'], second['id'])
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(len(self.workspace.player.catalog()['stops']), 1)
        self.assertEqual(self.workspace.player.audio(first['id'], self.record['id'], first['revision']), old)
        new = self.workspace.player.audio(second['id'], self.record['id'], second['revision'])
        self.assertEqual(read_wave(old).frames - read_wave(new).frames, 100)

    def test_only_musical_edits_change_sync_status_and_reopen_keeps_fingerprint(self):
        self.saved()
        expected = fingerprint(self.workspace)
        self.workspace.project['ui']['stage'] = 'export'
        self.workspace.projects.save()
        self.assertEqual(self.workspace.player.overview(self.workspace)['stops'][0]['syncState'], 'current')
        project_id = self.workspace.project['id']
        self.workspace.projects.new('Another')
        self.assertEqual(self.workspace.player.overview(self.workspace)['stops'][0]['syncState'], 'current')
        self.workspace.projects.open(project_id)
        self.assertEqual(fingerprint(self.workspace), expected)
        record = next(iter(self.workspace.records.values()))
        record['markers']['loopStart'] += 1
        self.assertEqual(self.workspace.player.overview(self.workspace)['stops'][0]['syncState'], 'changed')

    def test_failed_update_preserves_catalog_and_previous_audio(self):
        first = self.saved()
        self.record['markers']['attack'] += 1
        before = self.workspace.player.catalog()
        files = set(self.workspace.player.folder.iterdir())
        with patch('player_store.preview_wave', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.workspace.player.publish(self.workspace, linked=True)
        self.assertEqual(self.workspace.player.catalog(), before)
        self.assertEqual(set(self.workspace.player.folder.iterdir()), files)
        self.assertTrue(self.workspace.player.audio(first['id'], self.record['id'], first['revision']))

    def test_saved_project_can_be_published_without_switching_active_editor(self):
        stop = self.saved()
        self.workspace.projects.new('Working register')
        before = self.workspace.payload()
        view = self.workspace.projects.player_view(stop['projectId'])
        updated = self.workspace.player.publish(view, linked=True)
        self.assertEqual(self.workspace.payload(), before)
        self.assertEqual(updated['id'], stop['id'])
        self.assertEqual(updated['sourceFingerprint'], stop['sourceFingerprint'])

    def test_linking_legacy_and_rejecting_another_projects_stop(self):
        legacy = self.workspace.player.publish(self.workspace, 'Old stop')
        self.workspace.projects.save()
        linked = self.workspace.player.publish(self.workspace, linked=True, stop_id=legacy['id'])
        self.assertEqual(linked['id'], legacy['id'])
        self.workspace.projects.save(make_copy=True)
        with self.assertRaisesRegex(ValueError, 'different project'):
            self.workspace.player.publish(self.workspace, linked=True, stop_id=legacy['id'])

    def test_empty_selection_is_not_all_notes_and_revision_cannot_cross_stops(self):
        first = self.saved()
        with self.assertRaisesRegex(ValueError, 'No playable samples'):
            self.workspace.player.publish(self.workspace, ids=[])
        self.workspace.projects.save(make_copy=True)
        second = self.workspace.player.publish(self.workspace, 'Another', 12, linked=True)
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            self.workspace.player.audio(first['id'], self.record['id'], second['revision'])
        with self.assertRaises(ValueError):
            self.workspace.player.audio(first['id'], self.record['id'], '../session')

    def test_project_player_mapping_survives_archive(self):
        self.workspace.project['player'] = {'name':'Bourdon 16', 'keyShift':12}
        self.workspace.projects.save()
        view = self.workspace.projects.player_view(self.workspace.project['id'])
        self.assertEqual(view.project['player'], self.workspace.project['player'])

    def test_unchanged_updates_reuse_audio_but_mapping_changes_replace_same_stop(self):
        first = self.saved()
        with patch('player_store.preview_wave', side_effect=AssertionError('unnecessary render')):
            self.assertEqual(self.workspace.player.publish(self.workspace, linked=True), first)
        changed = self.workspace.player.publish(self.workspace, key_shift=12, linked=True)
        self.assertEqual(first['id'], changed['id'])
        self.assertEqual(changed['samples'][0]['key'], first['samples'][0]['key'] + 12)

    def test_checked_take_selection_survives_fresh_sample_ids(self):
        self.workspace.project['player'].update(scope='selected', sampleIds=[self.record['id']])
        self.workspace.projects.save()
        expected = fingerprint(self.workspace)
        identity = self.workspace.project['id']
        self.workspace.projects.new('Other')
        self.workspace.projects.open(identity)
        restored = next(iter(self.workspace.records))
        self.assertNotEqual(restored, self.record['id'])
        self.assertEqual(self.workspace.project['player']['sampleIds'], [restored])
        self.assertEqual(fingerprint(self.workspace), expected)


class LinkedStopServerTests(unittest.TestCase):
    setUp = test_audio.ServerTests.setUp
    tearDown = test_audio.ServerTests.tearDown
    call = test_audio.ServerTests.call

    def test_sync_saves_current_project_and_reuses_stop_with_latest_edits(self):
        record = json.loads(self.call('/api/import?name=048.wav', demo_wave(48)))
        record = json.loads(self.call('/api/analyze', {'id':record['id']}))
        first = json.loads(self.call('/api/player/sync', {'current':True}))
        self.assertIsNotNone(first['project']['id'])
        markers = dict(record['markers']); markers['attack'] += 10
        self.call('/api/update', {'id':record['id'], 'markers':markers})
        changed = json.loads(self.call('/api/player'))['stops'][0]
        self.assertEqual(changed['syncState'], 'changed')
        second = json.loads(self.call('/api/player/sync', {'projectId':first['project']['id']}))
        self.assertEqual(first['stop']['id'], second['stop']['id'])
        self.assertEqual(len(json.loads(self.call('/api/player'))['stops']), 1)
        url = f"/api/player/audio/{first['stop']['id']}/{record['id']}?revision={first['stop']['revision']}"
        self.assertTrue(self.call(url))
        self.call('/api/project-new', {'name':'Other'})
        before = json.loads(self.call('/api/samples'))
        self.call('/api/player/sync', {'projectId':first['project']['id']})
        self.assertEqual(json.loads(self.call('/api/samples')), before)

    def test_checked_takes_allow_duplicate_recordings_without_losing_either_take(self):
        records = []
        for name in ('048-take1.wav', '048-take2.wav'):
            record = json.loads(self.call('/api/import?name=' + name, demo_wave(48)))
            records.append(json.loads(self.call('/api/analyze', {'id':record['id']})))
        self.call('/api/project-details', {'player':{'scope':'selected', 'sampleIds':[records[1]['id']], 'keyShift':0, 'name':''}})
        result = json.loads(self.call('/api/player/sync', {'current':True}))
        self.assertEqual([s['id'] for s in result['stop']['samples']], [records[1]['id']])
        self.assertEqual(len(json.loads(self.call('/api/samples'))['samples']), 2)


if __name__ == '__main__':
    unittest.main()
