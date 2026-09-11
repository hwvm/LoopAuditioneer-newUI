"""Independent local stop snapshots for the browser instrument.

Publishing never changes a project's PCM, approval state, or active register.
"""
import json
import hashlib
from datetime import datetime, timezone
import re
import secrets
import shutil
from pathlib import Path

import numpy as np

from audio_engine import preview_wave, validate_markers


def fingerprint(workspace):
    """Musical edits only; stable when archives restore files under fresh IDs."""
    sources = workspace.source_ids()
    config = workspace.project.get('player', {})
    scope = config.get('scope', 'all')
    rows = []
    for record in workspace.records.values():
        if record['id'] in sources:
            continue
        if scope == 'selected' and record['id'] not in config.get('sampleIds', []):
            continue
        if scope == 'approved' and not record.get('reviewed'):
            continue
        rows.append({key: record.get(key) for key in ('name', 'audioHash', 'frames', 'rate',
                     'markers', 'note', 'reviewed', 'testApproved')})
        for key in ('reviewed', 'testApproved'):
            rows[-1][key] = bool(record.get(key))
    encoded = sorted(json.dumps(row, sort_keys=True) for row in rows)
    return hashlib.sha256(json.dumps([scope, config.get('keyShift', 0), encoded]).encode()).hexdigest()


class PlayerStore:
    def __init__(self, folder):
        self.folder = Path(folder) / 'player'
        self.folder.mkdir(parents=True, exist_ok=True)

    def catalog(self):
        path = self.folder / 'library.json'
        return json.loads(path.read_text('utf-8')) if path.exists() else {'version': 1, 'stops': []}

    def audio(self, stop_id, sample_id, revision=None):
        if not all(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{24}', value) for value in (stop_id, sample_id)):
            raise ValueError('Invalid player sample.')
        # Only manifest-referenced assets can be served.
        stop = next((s for s in self.catalog()['stops'] if s['id'] == stop_id), None)
        if revision is not None:
            if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{24}', revision):
                raise ValueError('Invalid player revision.')
            manifest = self.folder / revision / 'snapshot.json'
            if not manifest.exists():
                raise ValueError('Player revision no longer exists. Refresh the instrument.')
            stop = json.loads(manifest.read_text('utf-8'))
            if stop['id'] != stop_id or stop.get('revision') != revision:
                raise ValueError('Player revision does not belong to this stop.')
        if stop is None or not any(s['id'] == sample_id for s in stop['samples']):
            raise ValueError('Player sample no longer exists. Reload the player.')
        return (self.folder / (stop.get('revision') or stop_id) / (sample_id + '.wav')).read_bytes()

    def overview(self, workspace):
        catalog = self.catalog()
        projects = {p['id']: p for p in workspace.projects.list()}
        for stop in catalog['stops']:
            identity = stop.get('projectId')
            summary = projects.get(identity)
            current = identity is not None and identity == workspace.project['id']
            latest = fingerprint(workspace) if current else (summary or {}).get('playerFingerprint')
            stop['projectAvailable'] = current or summary is not None
            stop['syncState'] = ('unlinked' if not identity else 'missing' if not stop['projectAvailable']
                                 else 'unknown' if not latest or not stop.get('sourceFingerprint') else 'current' if latest == stop.get('sourceFingerprint') else 'changed')
        return {**catalog, 'projects': list(projects.values()),
                'currentProject': {'id': workspace.project['id'], 'name': workspace.project['name']}}

    def publish(self, workspace, name=None, key_shift=0, ids=None, linked=False, stop_id=None):
        name = workspace.project['name'] if name is None else name
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('Give this stop a name (1–120 characters).')
        if type(key_shift) is not int or not -36 <= key_shift <= 36:
            raise ValueError('Keyboard offset must be an integer from −36 to 36.')
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(i, str) or i not in workspace.records for i in ids)):
            raise ValueError('A selected sample no longer exists.')
        records = [workspace.records[i] for i in dict.fromkeys(ids)] if ids is not None else list(workspace.records.values())
        parents = workspace.source_ids()
        candidates, skipped, keys = [], [], set()
        for record in records:
            if record['id'] in parents:
                continue
            note = record.get('note')
            if type(note) is not int or not 0 <= note + key_shift <= 127:
                skipped.append(record['name'])
                continue
            wave = workspace.wave(record['id'])
            try:
                validate_markers(wave, record['markers'])
            except ValueError:
                skipped.append(record['name'])
                continue
            key = note + key_shift
            if key in keys:
                raise ValueError(f'Duplicate keyboard key {key}. In Export & play choose Checked notes, then check one take per key in Loops & trim before updating.')
            keys.add(key)
            candidates.append(record)
        if not candidates:
            raise ValueError('Detect loops and assign MIDI keys first. No playable samples were found.')
        catalog = self.catalog()
        previous = None
        if linked:
            if not workspace.project.get('id'):
                raise ValueError('Save the register project before sending it to the player.')
            previous = next((s for s in catalog['stops'] if s['id'] == stop_id), None) if stop_id else next(
                (s for s in catalog['stops'] if s.get('projectId') == workspace.project['id']), None)
            if stop_id and previous is None:
                raise ValueError('The stop to update no longer exists.')
            if previous and previous.get('projectId') not in (None, workspace.project['id']):
                raise ValueError('This stop belongs to a different project.')
            if (previous and previous.get('sourceFingerprint') == fingerprint(workspace)
                    and previous['name'] == name.strip() and previous['keyShift'] == key_shift
                    and sorted((s['key'], s['name']) for s in previous['samples'])
                    == sorted((r['note'] + key_shift, r['name']) for r in candidates)):
                return previous
        elif stop_id:
            raise ValueError('Only linked project stops can be updated.')
        if previous is None and len(catalog['stops']) >= 16:
            raise ValueError('The mini player holds at most 16 stop snapshots.')
        identity = previous['id'] if previous else secrets.token_hex(12)
        revision = secrets.token_hex(12) if linked else identity
        destination = self.folder / revision
        destination.mkdir()
        stop = {'id': identity, 'name': name.strip(), 'keyShift': key_shift,
                'projectName': workspace.project['name'], 'samples': [], 'skipped': skipped}
        if linked:
            stop.update(projectId=workspace.project['id'], revision=revision,
                        sourceFingerprint=fingerprint(workspace), updatedAt=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        temporary = self.folder / 'library.tmp'
        try:
            for record in candidates:
                wave = workspace.wave(record['id'])
                m = record['markers']
                # Trim lead-in for responsive key-down. Marker seconds use the
                # original sample rate, even when decodeAudioData resamples.
                raw = preview_wave(wave, m['attack'], m['end'])
                (destination / (record['id'] + '.wav')).write_bytes(raw)
                level = float(np.sqrt(np.mean(wave.samples[m['loopStart']:m['loopEnd']].astype(np.float64) ** 2)))
                proxy_gain = min(1., .98 / max(float(np.max(np.abs(wave.samples))), 1e-12))
                stop['samples'].append({'id': record['id'], 'name': record['name'], 'key': record['note'] + key_shift,
                    'loopStart': (m['loopStart'] - m['attack']) / wave.rate,
                    'loopEnd': (m['loopEnd'] - m['attack']) / wave.rate,
                    'gain': min(4., .12 / max(level * proxy_gain, .001)),
                    'reviewed': bool(record['reviewed']), 'testApproved': bool(record.get('testApproved')),
                    'quality': record.get('quality'), 'source': record.get('source')})
            stop['samples'].sort(key=lambda s: s['key'])
            if linked:
                (destination / 'snapshot.json').write_text(json.dumps(stop, ensure_ascii=False, allow_nan=False), 'utf-8')
            if previous:
                catalog['stops'][catalog['stops'].index(previous)] = stop
            else:
                catalog['stops'].append(stop)
            temporary.write_text(json.dumps(catalog, ensure_ascii=False, allow_nan=False), 'utf-8')
            temporary.replace(self.folder / 'library.json')
        except Exception:
            # This newly generated directory is always inside the player folder.
            if destination.resolve().parent != self.folder.resolve():
                raise RuntimeError('Player rollback path escaped its workspace.')
            shutil.rmtree(destination)
            temporary.unlink(missing_ok=True)
            raise
        return stop
