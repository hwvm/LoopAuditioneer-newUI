"""Versioned, portable register projects. Archives contain audio and edit decisions."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import zipfile
from types import SimpleNamespace
from player_store import fingerprint

from audio_engine import peaks, read_wave, validate_markers

FORMAT = 'LoopAuditioneer project'
VERSION = 1
MAX_AUDIO = 1024 * 1024 * 1024
MAX_MANIFEST = 16 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def default_project():
    return {'id': None, 'name': 'Untitled register', 'notes': '', 'savedAt': None,
            'drafts': {}, 'settings': {'minimumLoop': .4, 'threshold': -55},
            'ui': {}, 'export': {'mode': 'wav', 'trim': False}, 'player': {'name': '', 'keyShift': 0}}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{24}', value):
        raise ValueError('Invalid project or sample identifier.')
    return value


def clean_project(value, records):
    if not isinstance(value, dict):
        raise ValueError('Invalid project details.')
    result = default_project()
    for key, limit in [('name', 120), ('notes', 8000)]:
        text = value.get(key, result[key])
        if not isinstance(text, str) or len(text) > limit:
            raise ValueError(f'Project {key} is too long or invalid.')
        result[key] = text.strip() if key == 'name' else text
    if not result['name']:
        raise ValueError('Give this register a project name.')
    result['id'] = identifier(value['id']) if value.get('id') is not None else None
    result['savedAt'] = value.get('savedAt') if isinstance(value.get('savedAt'), str) else None
    settings = value.get('settings', result['settings'])
    if not isinstance(settings, dict):
        raise ValueError('Invalid detection settings.')
    for key, low, high in [('minimumLoop', .1, 10), ('threshold', -90, -20)]:
        v = settings.get(key, result['settings'][key])
        if type(v) not in (int, float) or not low <= v <= high:
            raise ValueError('Invalid detection settings.')
        result['settings'][key] = v
    export = value.get('export', result['export'])
    if not isinstance(export, dict) or export.get('mode') not in ('wav', 'split', 'sfz') or type(export.get('trim')) is not bool:
        raise ValueError('Invalid project export settings.')
    result['export'] = {'mode': export['mode'], 'trim': export['trim']}
    player = value.get('player', result['player'])
    if (not isinstance(player, dict) or not isinstance(player.get('name', ''), str) or len(player.get('name', '')) > 120
            or type(player.get('keyShift', 0)) is not int or not -36 <= player.get('keyShift', 0) <= 36):
        raise ValueError('Invalid project player settings.')
    scope = player.get('scope', 'all')
    chosen = player.get('sampleIds', [])
    if scope not in ('all', 'approved', 'selected') or not isinstance(chosen, list) or len(chosen) > 500 or any(not isinstance(i, str) for i in chosen):
        raise ValueError('Invalid project player selection.')
    result['player'] = {'name': player.get('name', '').strip(), 'keyShift': player.get('keyShift', 0),
                        'scope': scope, 'sampleIds': [i for i in dict.fromkeys(chosen) if i in records]}
    ui = value.get('ui', {})
    if not isinstance(ui, dict):
        raise ValueError('Invalid workspace view.')
    selected = ui.get('selected', [])
    if not isinstance(selected, list) or len(selected) > 500 or any(not isinstance(i, str) for i in selected):
        raise ValueError('Invalid sample selection.')
    result['ui'] = {'activeId': ui.get('activeId') if isinstance(ui.get('activeId'), str) and ui.get('activeId') in records else None,
                    'selected': [i for i in selected if i in records],
                    'stage': ui.get('stage') if ui.get('stage') in ('import', 'split', 'cuts', 'loops', 'export') else None,
                    'sourceId': ui.get('sourceId') if isinstance(ui.get('sourceId'), str) and ui['sourceId'] in records else None,
                    'filter': ui.get('filter') if ui.get('filter') in ('all', 'review', 'approved') else 'all',
                    'search': ui.get('search', '')[:200] if isinstance(ui.get('search', ''), str) else '',
                    'mode': ui.get('mode') if ui.get('mode') in ('full', 'loop', 'release') else 'full',
                    'volume': ui.get('volume') if type(ui.get('volume')) in (int, float) and 0 <= ui['volume'] <= 100 else 65}
    drafts = value.get('drafts', {})
    if not isinstance(drafts, dict) or len(drafts) > 500:
        raise ValueError('Invalid saved split drafts.')
    for identity, draft in drafts.items():
        if identity not in records:
            continue
        if not isinstance(draft, dict) or not isinstance(draft.get('regions'), list) or len(draft['regions']) > 500:
            raise ValueError('Invalid saved split regions.')
        regions = []
        for region in draft['regions']:
            if not isinstance(region, dict):
                raise ValueError('Invalid saved split region.')
            # Drafts may be unfinished or overlap. Creation still uses strict validation.
            clean = {}
            for key in ('start', 'end', 'note'):
                item = region.get(key)
                if item is not None and (type(item) is not int or abs(item) > 10**12):
                    raise ValueError('Invalid value in a split draft.')
                clean[key] = item
            confidence = region.get('confidence')
            clean['confidence'] = confidence if type(confidence) in (int, float) and 0 <= confidence <= 100 else None
            clean['keep'] = bool(region.get('keep', True))
            regions.append(clean)
        parameters = {}
        raw_settings = draft.get('settings', {})
        if not isinstance(raw_settings, dict):
            raise ValueError('Invalid split settings.')
        automatic = raw_settings.get('autoThreshold', True)
        if type(automatic) is not bool:
            raise ValueError('Invalid automatic split threshold.')
        parameters['autoThreshold'] = automatic
        for key, default, low, high in [('threshold', -48, -90, -12), ('gapMs', 250, 30, 5000), ('minimumMs', 250, 50, 10000), ('tailMs', 150, 0, 5000), ('first', 36, 0, 127), ('step', 1, -24, 24)]:
            v = raw_settings.get(key, default)
            parameters[key] = v if type(v) in (int, float) and low <= v <= high else default
        warnings = draft.get('warnings', [])
        if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
            raise ValueError('Invalid split warnings.')
        cursor = draft.get('cursor')
        result['drafts'][identity] = {'regions': regions, 'settings': parameters, 'warnings': [w[:1000] for w in warnings[:20]],
                                      'index': draft.get('index') if type(draft.get('index')) is int and 0 <= draft['index'] < len(regions) else 0,
                                      'cursor': cursor if type(cursor) is int and 0 <= cursor <= records[identity]['frames'] else None,
                                      'analyzeAfter': bool(draft.get('analyzeAfter', True))}
    return result


def write_archive(workspace, destination, project):
    records, written = [], set()
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for record in workspace.records.values():
            raw = (workspace.folder / (record['id'] + '.wav')).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            path = f'audio/{digest}.wav'
            if path not in written:
                archive.writestr(path, raw)
                written.add(path)
            saved = {k: v for k, v in record.items() if k != 'peaks'}
            saved['asset'] = path
            records.append(saved)
        manifest = {'format': FORMAT, 'version': VERSION, 'project': project, 'samples': records}
        encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_MANIFEST:
            raise ValueError('Project metadata exceeds the 16 MB limit.')
        archive.writestr('project.json', encoded)


def read_archive(source):
    """Validate the complete archive before touching the active workspace."""
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if len(members) > 501 or len(set(names)) != len(names) or sum(m.file_size for m in members) > MAX_AUDIO + MAX_MANIFEST:
                raise ValueError('Project archive is too large or contains duplicate entries.')
            if 'project.json' not in names or archive.getinfo('project.json').file_size > MAX_MANIFEST:
                raise ValueError('Project manifest is missing or too large.')
            manifest = json.loads(archive.read('project.json'))
            if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or manifest.get('version') != VERSION:
                raise ValueError('Unsupported project format or version.')
            originals = manifest.get('samples')
            if not isinstance(originals, list) or len(originals) > 500:
                raise ValueError('Invalid project sample list.')
            records, files, total = {}, {}, 0
            expected = {'project.json'}
            for item in originals:
                if not isinstance(item, dict):
                    raise ValueError('Invalid project sample.')
                identity = identifier(item.get('id'))
                if identity in records:
                    raise ValueError('Project contains duplicate sample identifiers.')
                asset = item.get('asset', '')
                if not isinstance(asset, str) or not re.fullmatch(r'audio/[a-f0-9]{64}\.wav', asset) or asset not in names:
                    raise ValueError('Project audio is missing or has an invalid path.')
                expected.add(asset)
                total += archive.getinfo(asset).file_size
                if total > MAX_AUDIO:
                    raise ValueError('Project exceeds the 1 GB workspace budget.')
                raw = archive.read(asset)
                if hashlib.sha256(raw).hexdigest() != Path(asset).stem:
                    raise ValueError('Project audio failed its integrity check.')
                wave = read_wave(raw)
                markers = item.get('markers')
                validate_markers(wave, markers, require_loop=bool(item.get('reviewed')))
                note = item.get('note')
                if note is not None and (type(note) is not int or not 0 <= note <= 127):
                    raise ValueError('Project contains an invalid MIDI note.')
                if item.get('reviewed') and note is None:
                    raise ValueError('An approved project sample is missing its root note.')
                name = item.get('name')
                if not isinstance(name, str) or not name.lower().endswith('.wav') or '/' in name or '\\' in name or len(name) > 180:
                    raise ValueError('Project contains an invalid sample name.')
                warnings = item.get('warnings', [])
                if not isinstance(warnings, list) or len(warnings) > 30 or any(not isinstance(w, str) or len(w) > 2000 for w in warnings):
                    raise ValueError('Invalid sample review notes.')
                record = {'id': identity, 'name': name, 'rate': wave.rate, 'channels': wave.channels, 'bits': wave.bits,
                          'audioHash': Path(asset).stem,
                          'kind': 'recording' if item.get('kind') == 'recording' else 'sample',
                          'frames': wave.frames, 'duration': wave.frames / wave.rate, 'bytes': len(raw), 'peaks': peaks(wave),
                          'markers': markers, 'note': note, 'reviewed': bool(item.get('reviewed')), 'analyzed': bool(item.get('analyzed')),
                          'testApproved': bool(item.get('reviewed') and item.get('testApproved')),
                          'manual': bool(item.get('manual')), 'demo': bool(item.get('demo')), 'warnings': warnings,
                          'quality': item.get('quality') if type(item.get('quality')) in (int, float) and 0 <= item['quality'] <= 100 else None}
                estimate, confidence = item.get('estimatedNote'), item.get('pitchConfidence')
                record['estimatedNote'] = estimate if type(estimate) is int and 0 <= estimate <= 127 else None
                record['pitchConfidence'] = confidence if type(confidence) in (int, float) and 0 <= confidence <= 100 else None
                provenance = item.get('source')
                if provenance is not None:
                    if not isinstance(provenance, dict) or not isinstance(provenance.get('name'), str) or len(provenance['name']) > 180:
                        raise ValueError('Invalid source recording reference.')
                    identifier(provenance.get('id'))
                    a, b = provenance.get('start'), provenance.get('end')
                    if type(a) is not int or type(b) is not int or not 0 <= a < b or b - a != wave.frames:
                        raise ValueError('Invalid source recording range.')
                    record['source'] = {k: provenance[k] for k in ('id', 'name', 'start', 'end')}
                records[identity] = record
                files[identity] = raw
            if set(names) != expected:
                raise ValueError('Project contains unexpected files.')
            project = clean_project(manifest.get('project'), records)
            return project, records, files
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as error:
        raise ValueError('The project file is damaged or unsupported.') from error


class ProjectStore:
    def __init__(self, workspace):
        self.workspace = workspace
        self.folder = workspace.folder / 'projects'
        self.folder.mkdir(exist_ok=True)

    def list(self):
        projects = []
        for path in self.folder.glob('*.json'):
            try:
                identifier(path.stem)
                summary = json.loads(path.read_text('utf-8'))
                if (self.folder / (path.stem + '.loopproject')).exists():
                    if 'notes' not in summary or 'playerFingerprint' not in summary:
                        with zipfile.ZipFile(self.folder / (path.stem + '.loopproject')) as archive:
                            manifest = json.loads(archive.read('project.json'))
                        records = {r['id']: {**r, 'audioHash': Path(r['asset']).stem} for r in manifest['samples']}
                        project = manifest['project']
                        sources = ({r['id'] for r in records.values() if r.get('kind') == 'recording'}
                                   | {r.get('source', {}).get('id') for r in records.values()} | set(project.get('drafts', {}))) - {None}
                        summary.update(sources=len(sources), notes=len(records) - len(sources),
                                       playerFingerprint=fingerprint(SimpleNamespace(records=records, project=project, source_ids=lambda:sources)))
                    projects.append(summary)
            except (ValueError, OSError, KeyError, zipfile.BadZipFile):
                continue
        return sorted(projects, key=lambda p: p.get('savedAt', ''), reverse=True)

    def save(self, name=None, make_copy=False):
        workspace = self.workspace
        candidate = copy.deepcopy(workspace.project)
        if name is not None:
            candidate['name'] = name
        if make_copy or candidate['id'] is None:
            candidate['id'] = secrets.token_hex(12)
        candidate['savedAt'] = now()
        candidate = clean_project(candidate, workspace.records)
        identity = candidate['id']
        target = self.folder / (identity + '.loopproject')
        temporary = self.folder / (identity + '.tmp')
        try:
            write_archive(workspace, temporary, candidate)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        summary = {'id': identity, 'name': candidate['name'], 'savedAt': candidate['savedAt'],
                   'samples': len(workspace.records), 'sources': len(workspace.source_ids()),
                   'notes': len(workspace.records) - len(workspace.source_ids()),
                   'approved': sum(bool(r['reviewed']) for r in workspace.records.values() if r['id'] not in workspace.source_ids()),
                   'playerFingerprint': fingerprint(workspace)}
        index = self.folder / (identity + '.json')
        temp_index = index.with_suffix('.json.tmp')
        temp_index.write_text(json.dumps(summary), 'utf-8')
        temp_index.replace(index)
        workspace.project = candidate
        workspace.save()
        if make_copy:
            workspace.token = secrets.token_hex(16)
        return target

    def preserve_current(self):
        if self.workspace.records or self.workspace.project['id'] is not None or self.workspace.project['name'] != 'Untitled register':
            self.save()

    def player_view(self, identity):
        """Read a saved register for audition without replacing the active editor."""
        identifier(identity)
        path = self.folder / (identity + '.loopproject')
        if not path.exists():
            raise ValueError('Saved project no longer exists.')
        project, records, files = read_archive(path)
        project['id'] = identity
        sources = ({r['id'] for r in records.values() if r.get('kind') == 'recording'}
                   | {r.get('source', {}).get('id') for r in records.values()} | set(project['drafts'])) - {None}
        return SimpleNamespace(project=project, records=records, source_ids=lambda: sources,
                               wave=lambda key: read_wave(files[key]))

    def activate(self, project, records, files, imported=False):
        workspace = self.workspace
        self.preserve_current()
        # Give restored working files fresh IDs. Never overwrite an earlier recording.
        mapping = {old: secrets.token_hex(12) for old in records}
        restored = {}
        for old, record in records.items():
            record['id'] = mapping[old]
            if record.get('source') and record['source']['id'] in mapping:
                record['source']['id'] = mapping[record['source']['id']]
            restored[record['id']] = record
        project['drafts'] = {mapping[key]: value for key, value in project['drafts'].items() if key in mapping}
        project['ui']['activeId'] = mapping.get(project['ui'].get('activeId'))
        project['ui']['sourceId'] = mapping.get(project['ui'].get('sourceId'))
        project['ui']['selected'] = [mapping[key] for key in project['ui'].get('selected', []) if key in mapping]
        project['player']['sampleIds'] = [mapping[key] for key in project['player'].get('sampleIds', []) if key in mapping]
        if imported:
            project['id'], project['savedAt'] = None, None
        before_records, before_project = workspace.records, workspace.project
        paths = []
        try:
            for old, raw in files.items():
                path = workspace.folder / (mapping[old] + '.wav')
                paths.append(path)
                path.write_bytes(raw)
            workspace.records, workspace.project = restored, project
            workspace.save()
        except Exception:
            workspace.records, workspace.project = before_records, before_project
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        workspace.token = secrets.token_hex(16)

    def open(self, identity):
        identifier(identity)
        path = self.folder / (identity + '.loopproject')
        if not path.exists():
            raise ValueError('Saved project no longer exists.')
        if self.workspace.project['id'] == identity:
            self.save()
            return
        project, records, files = read_archive(path)
        project['id'] = identity
        self.activate(project, records, files)

    def new(self, name):
        candidate = clean_project({**default_project(), 'name': name}, {})
        self.preserve_current()
        before_records, before_project = self.workspace.records, self.workspace.project
        self.workspace.records, self.workspace.project = {}, candidate
        try:
            self.workspace.save()
        except Exception:
            self.workspace.records, self.workspace.project = before_records, before_project
            raise
        self.workspace.token = secrets.token_hex(16)

    def import_file(self, raw):
        project, records, files = read_archive(io.BytesIO(raw))
        self.activate(project, records, files, imported=True)
