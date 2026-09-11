"""Run: python server.py [--port 8765] [--no-browser]. Local files stay local."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import urllib.parse
import webbrowser

from audio_engine import analyze, demo_wave, export_zip, loop_candidates, refine_loop, peaks, preview_wave, read_wave, seam_quality, validate_markers
from note_splitter import detect_regions, slice_wave, validate_regions
from project_store import ProjectStore, clean_project, default_project
from player_store import PlayerStore

ROOT = Path(__file__).resolve().parent
MAX_FILE = 300 * 1024 * 1024
MAX_BATCH = 1024 * 1024 * 1024


class Workspace:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.records = {}
        self.project = default_project()
        self.token = secrets.token_hex(16)
        index = self.folder / 'session.json'
        if index.exists():
            data = json.loads(index.read_text('utf-8'))
            records = data if isinstance(data, list) else data['samples']
            self.records = {r['id']: r for r in records if (self.folder / (r['id'] + '.wav')).exists()}
            for record in self.records.values():
                if not record.get('audioHash'):
                    record['audioHash'] = hashlib.sha256((self.folder / (record['id'] + '.wav')).read_bytes()).hexdigest()
            if isinstance(data, dict):
                self.project = clean_project(data.get('project', default_project()), self.records)
        self.projects = ProjectStore(self)
        self.player = PlayerStore(self.folder)

    def save(self):
        target = self.folder / 'session.json'
        temporary = self.folder / 'session.tmp'
        self.project = clean_project(self.project, self.records)
        temporary.write_text(json.dumps({'version': 2, 'project': self.project, 'samples': list(self.records.values())}, allow_nan=False), 'utf-8')
        temporary.replace(target)

    def payload(self):
        return {'samples': list(self.records.values()), 'project': self.project, 'token': self.token}

    def source_ids(self):
        return ({r['id'] for r in self.records.values() if r.get('kind') == 'recording'}
                | {r.get('source', {}).get('id') for r in self.records.values()}
                | set(self.project.get('drafts', {}))) - {None}

    def approve_batch(self, ids, testing=False):
        if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(i, str) or i not in self.records for i in ids):
            raise ValueError('Select existing note samples to approve.')
        if type(testing) is not bool:
            raise ValueError('Testing approval must be true or false.')
        approved, skipped, before = [], [], {}
        sources = self.source_ids()
        for identity in dict.fromkeys(ids):
            record = self.records[identity]
            try:
                if identity in sources:
                    raise ValueError('Full recording: split it into note samples first.')
                validate_markers(self.wave(identity), record['markers'])
                if type(record['note']) is not int or not 0 <= record['note'] <= 127:
                    raise ValueError('Assign a MIDI keyboard key first.')
            except ValueError as error:
                skipped.append({'id': identity, 'name': record['name'], 'reason': str(error)})
                continue
            if record['reviewed'] and (not record.get('testApproved') or testing):
                continue
            before[identity] = dict(record)
            record.update(reviewed=True, testApproved=testing)
            approved.append(identity)
        try:
            self.save()
        except Exception:
            self.records.update(before)
            raise
        return {'samples': list(self.records.values()), 'approved': approved, 'skipped': skipped}

    def wave(self, identity):
        if identity not in self.records:
            raise ValueError('Sample no longer exists. Refresh the workspace.')
        return read_wave((self.folder / (identity + '.wav')).read_bytes())

    def import_wave(self, raw, name, demo=False, persist=True, kind='sample'):
        if kind not in ('sample', 'recording'):
            raise ValueError('Choose individual notes or a full recording.')
        if len(self.records) >= 500:
            raise ValueError('This batch is full (500 samples). Remove samples to start another batch.')
        if sum(r['bytes'] for r in self.records.values()) + len(raw) > MAX_BATCH:
            raise ValueError('This local batch is full (1 GB). Export and remove samples first.')
        wave = read_wave(raw)
        identity = secrets.token_hex(12)
        name = name.replace('\\', '/').split('/')[-1][:180]
        if not name.lower().endswith('.wav'):
            raise ValueError('Please import a .wav file.')
        # Keep the imported original immutable. All edits live in session.json.
        record = {'id': identity, 'name': name, 'rate': wave.rate, 'bits': wave.bits,
                  'audioHash': hashlib.sha256(raw).hexdigest(),
                  'channels': wave.channels, 'frames': wave.frames, 'duration': wave.frames / wave.rate,
                  'bytes': len(raw), 'demo': demo, 'kind': kind, 'peaks': peaks(wave), 'analyzed': False,
                  'reviewed': False, 'quality': None, 'warnings': [], 'note': wave.note,
                  'markers': {'attack': 0, 'loopStart': None, 'loopEnd': None, 'release': max(0, wave.frames - 1), 'end': wave.frames}}
        destination = self.folder / (identity + '.wav')
        try:
            destination.write_bytes(raw)
        except OSError:
            destination.unlink(missing_ok=True)
            raise
        self.records[identity] = record
        if persist:
            self.save()
        return record

    def split_recording(self, identity, regions):
        wave = self.wave(identity)
        validate_regions(wave, regions)
        if len(self.records) + len(regions) > 500:
            raise ValueError('The resulting batch exceeds 500 samples. Exclude some regions or remove existing samples first.')
        estimated = sum((r['end'] - r['start']) * wave.block + 256 for r in regions)
        if sum(r['bytes'] for r in self.records.values()) + estimated > MAX_BATCH:
            raise ValueError('Not enough space in the 1 GB workspace budget for these copies. Export and remove other samples first.')
        parent = self.records[identity]
        base = parent['name'].rsplit('.', 1)[0][:100]
        before = set(self.records)
        created = []
        try:
            for index, region in enumerate(regions):
                prefix = f'{region["note"]:03d}' if region.get('note') is not None else 'Unassigned'
                child = self.import_wave(slice_wave(wave, region), f'{prefix}-{base}-{index + 1:03d}.wav', parent.get('demo', False), persist=False)
                child['source'] = {'id': identity, 'name': parent['name'], 'start': region['start'], 'end': region['end']}
                created.append(child['id'])
            self.save()
        except Exception:
            # Roll back every child, leaving source files and session index unchanged.
            for child_id in set(self.records) - before:
                del self.records[child_id]
                (self.folder / (child_id + '.wav')).unlink(missing_ok=True)
            raise
        return created


def handler_for(workspace, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, data, mime='application/json', extra=None):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def local_request(self, mutate=False):
            if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                raise ValueError('Open the app using its localhost address.')
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
                raise ValueError('Cross-origin requests are not accepted.')
            if mutate and self.headers.get('X-Loop-Workspace') != '1':
                raise ValueError('Use the local workspace to make changes.')

        def do_GET(self):
            try:
                self.local_request()
                path = urllib.parse.urlparse(self.path).path
                if path == '/api/samples':
                    with workspace.lock:
                        return self.send(200, workspace.payload())
                if path == '/api/projects':
                    with workspace.lock:
                        return self.send(200, {'projects': workspace.projects.list(), 'project': workspace.project})
                if path == '/api/player':
                    with workspace.lock:
                        return self.send(200, workspace.player.overview(workspace))
                if path.startswith('/api/player/audio/'):
                    with workspace.lock:
                        parts = path.split('/')
                        if len(parts) != 6:
                            raise ValueError('Invalid player audio URL.')
                        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                        return self.send(200, workspace.player.audio(parts[4], parts[5], query.get('revision', [None])[0]), 'audio/wav')
                if path.startswith('/api/audio/'):
                    identity = path.rsplit('/', 1)[-1]
                    with workspace.lock:
                        wave = workspace.wave(identity)
                        # Browser audition uses a 16-bit PCM proxy. Exports use original bytes.
                        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                        start, end = int(query.get('start', [0])[0]), int(query.get('end', [wave.frames])[0])
                        if not 0 <= start < end <= wave.frames:
                            raise ValueError('Invalid audition range.')
                        raw = preview_wave(wave, start, end)
                    return self.send(200, raw, 'audio/wav')
                files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/split.js': ('split.js', 'text/javascript; charset=utf-8'), '/projects.js': ('projects.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                files.update({'/player': ('player.html', 'text/html; charset=utf-8'),
                              '/flow.js': ('flow.js', 'text/javascript; charset=utf-8'),
                              '/project-player.js': ('project-player.js', 'text/javascript; charset=utf-8'),
                              '/flow.css': ('flow.css', 'text/css; charset=utf-8'),
                              '/player.js': ('player.js', 'text/javascript; charset=utf-8'),
                              '/player-engine.js': ('player-engine.js', 'text/javascript; charset=utf-8'),
                              '/player.css': ('player.css', 'text/css; charset=utf-8')})
                if path in files:
                    file, mime = files[path]
                    return self.send(200, (ROOT / 'static' / file).read_bytes(), mime)
                return self.send(404, {'error': 'Not found.'})
            except ValueError as error:
                self.send(400, {'error': str(error)})
            except Exception:
                self.send(500, {'error': 'Could not read the local workspace.'})

        def do_POST(self):
            try:
                self.local_request(mutate=True)
                path = urllib.parse.urlparse(self.path).path
                length = int(self.headers.get('Content-Length', '0'))
                limit = MAX_BATCH + 16 * 1024 * 1024 if path == '/api/project-import' else MAX_FILE if path == '/api/import' else 16 * 1024 * 1024
                if not 0 < length <= limit:
                    raise ValueError('File is empty or exceeds 300 MB per sample.')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('Upload was interrupted.')
                with workspace.lock:
                    token = self.headers.get('X-Loop-Project')
                    if token is not None and token != workspace.token:
                        raise ValueError('The active project changed in another tab. Refresh this page before editing.')
                    if path == '/api/project-import':
                        workspace.projects.import_file(raw)
                        return self.send(200, workspace.payload())
                    if path == '/api/import':
                        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                        return self.send(200, workspace.import_wave(raw, query.get('name', ['sample.wav'])[0], kind=query.get('kind', ['sample'])[0]))
                    body = json.loads(raw)
                    if path == '/api/approve-batch':
                        return self.send(200, workspace.approve_batch(body.get('ids'), body.get('testing', False)))
                    if path == '/api/sample-kind':
                        identity, kind = body['id'], body.get('kind')
                        if kind not in ('recording', 'sample'):
                            raise ValueError('Invalid import type.')
                        if kind == 'sample' and (workspace.project['drafts'].get(identity, {}).get('regions') or any(r.get('source', {}).get('id') == identity for r in workspace.records.values())):
                            raise ValueError('A source with split drafts or note copies stays in Recordings.')
                        record = workspace.records[identity]
                        if record.get('source'):
                            raise ValueError('A split note stays with its source recording.')
                        record.update(kind=kind, reviewed=False, testApproved=False)
                        if kind == 'sample':
                            workspace.project['drafts'].pop(identity, None)
                        workspace.save()
                        return self.send(200, workspace.payload())
                    if path == '/api/loop-refine':
                        record = workspace.records[body['id']]
                        result = refine_loop(workspace.wave(body['id']), record['markers'], body.get('start'), body.get('end'), body.get('radiusMs', 20), body.get('minimumLoop', .1))
                        return self.send(200, {'candidates': result})
                    if path == '/api/player/publish':
                        return self.send(200, workspace.player.publish(workspace, body.get('name'), body.get('keyShift', 0), body.get('ids')))
                    if path == '/api/player/sync':
                        if body.get('current') is True or (body.get('projectId') is not None and body.get('projectId') == workspace.project['id']):
                            workspace.projects.save()
                            view = workspace
                        else:
                            view = workspace.projects.player_view(body.get('projectId'))
                        config = view.project.get('player', {})
                        scope = config.get('scope', 'all')
                        ids = config.get('sampleIds', []) if scope == 'selected' else [r['id'] for r in view.records.values() if r.get('reviewed')] if scope == 'approved' else None
                        stop = workspace.player.publish(view, body.get('name', config.get('name') or view.project['name']), body.get('keyShift', config.get('keyShift', 0)), ids=ids, linked=True, stop_id=body.get('stopId'))
                        return self.send(200, {'stop': stop, 'project': workspace.project, 'token': workspace.token})
                    if path == '/api/project-details':
                        candidate = {**workspace.project, **{k: v for k, v in body.items() if k in ('name', 'notes', 'settings', 'ui', 'export', 'player')}}
                        workspace.project = clean_project(candidate, workspace.records)
                        workspace.save()
                        return self.send(200, {'project': workspace.project})
                    if path == '/api/project-draft':
                        identity = body['id']
                        if identity not in workspace.records:
                            raise ValueError('The draft source is no longer in this project.')
                        candidate = {**workspace.project, 'drafts': {**workspace.project['drafts'], identity: body['draft']}}
                        workspace.project = clean_project(candidate, workspace.records)
                        workspace.save()
                        return self.send(200, {'project': workspace.project})
                    if path == '/api/project-save':
                        workspace.projects.save(body.get('name'), bool(body.get('copy', False)))
                        return self.send(200, {'project': workspace.project, 'projects': workspace.projects.list(), 'token': workspace.token})
                    if path == '/api/project-new':
                        workspace.projects.new(body.get('name', 'Untitled register'))
                        return self.send(200, workspace.payload())
                    if path == '/api/project-open':
                        workspace.projects.open(body['id'])
                        return self.send(200, workspace.payload())
                    if path == '/api/project-download':
                        path = workspace.projects.save()
                        return self.send(200, path.read_bytes(), 'application/octet-stream', {'Content-Disposition': 'attachment; filename="register.loopproject"'})
                    if path == '/api/split-preview':
                        wave = workspace.wave(body['id'])
                        result = detect_regions(wave, body.get('threshold', -48), body.get('gapMs', 250), body.get('minimumMs', 250), body.get('tailMs', 150), body.get('autoThreshold', True))
                        return self.send(200, result)
                    if path == '/api/split-create':
                        created = workspace.split_recording(body['id'], body.get('regions'))
                        return self.send(200, {'samples': list(workspace.records.values()), 'created': created})
                    if path == '/api/demo':
                        if any(r.get('demo') for r in workspace.records.values()):
                            raise ValueError('The example rank is already in this workspace.')
                        for i, note in enumerate(range(48, 60)):
                            record = workspace.import_wave(demo_wave(note, i % 4), f'{note:03d}-Principal-8.wav', True)
                            record.update(analyze(workspace.wave(record['id']), record['name']))
                        workspace.save()
                        return self.send(200, {'samples': list(workspace.records.values())})
                    if path == '/api/analyze':
                        identity = body['id']
                        if identity in workspace.source_ids():
                            raise ValueError('Split the full recording first. Detection applies to individual notes.')
                        wave = workspace.wave(identity)
                        minimum = float(body.get('minimumLoop', .4))
                        threshold = float(body.get('threshold', -55))
                        if not .1 <= minimum <= 10 or not -90 <= threshold <= -20:
                            raise ValueError('Detection settings are outside the supported range.')
                        record = workspace.records[identity]
                        # Split filenames contain region numbers, which are not MIDI notes.
                        analysis_name = '' if record.get('source') else record['name']
                        record.update(analyze(wave, analysis_name, minimum, threshold))
                        workspace.save()
                        return self.send(200, record)
                    if path == '/api/loop-candidates':
                        record = workspace.records[body['id']]
                        choices = loop_candidates(workspace.wave(body['id']), record['markers'], body.get('minimumLoop', .4))
                        return self.send(200, {'candidates': choices})
                    if path == '/api/update':
                        if body.get('reviewed') and body['id'] in workspace.source_ids():
                            raise ValueError('Full recordings cannot be approved as note samples.')
                        wave = workspace.wave(body['id'])
                        current = workspace.records[body['id']]
                        markers = body.get('markers', current['markers'])
                        validate_markers(wave, markers, require_loop=bool(body.get('reviewed')))
                        note = body.get('note', current['note'])
                        if note is not None and (type(note) is not int or not 0 <= note <= 127):
                            raise ValueError('Root MIDI note must be between 0 and 127.')
                        if body.get('reviewed') and note is None:
                            raise ValueError('Set the root MIDI note before approving this sample.')
                        changed = markers != current['markers'] or note != current['note']
                        current.update(markers=markers, note=note, reviewed=bool(body.get('reviewed', False)),
                                       testApproved=bool(body.get('reviewed') and body.get('testApproval')))
                        if changed:
                            current['manual'] = True
                            current['quality'] = seam_quality(wave, markers)
                        workspace.save()
                        return self.send(200, current)
                    if path == '/api/remove':
                        ids = body.get('ids', [])
                        for identity in ids:
                            if identity not in workspace.records:
                                raise ValueError('Sample no longer exists.')
                        if any(r.get('source', {}).get('id') in ids and r['id'] not in ids for r in workspace.records.values()):
                            raise ValueError('Keep the source recording while its note samples are in the project. Remove the notes first, or remove the source and notes together.')
                        # Only private imported copies with known IDs can be removed.
                        for identity in ids:
                            del workspace.records[identity]
                        workspace.save()
                        for identity in ids:
                            (workspace.folder / (identity + '.wav')).unlink(missing_ok=True)
                        return self.send(200, {'samples': list(workspace.records.values())})
                    if path == '/api/export':
                        ids = list(dict.fromkeys(body.get('ids', [])))
                        if any(identity not in workspace.records for identity in ids):
                            raise ValueError('An exported sample no longer exists.')
                        if any(identity in workspace.source_ids() for identity in ids):
                            raise ValueError('Export individual notes, not full source recordings.')
                        records = ((workspace.records[identity], workspace.wave(identity)) for identity in ids)
                        result = export_zip(records, body.get('mode', 'wav'), bool(body.get('trim', False)))
                        return self.send(200, result, 'application/zip', {'Content-Disposition': 'attachment; filename="LoopAuditioneer-prepared.zip"'})
                    self.send(404, {'error': 'Unknown operation.'})
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                self.send(400, {'error': str(error)})
            except Exception as error:
                print(f'Workspace error: {error}', flush=True)
                self.send(500, {'error': 'The operation could not complete. Your source recordings are unchanged.'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--player', action='store_true', help='Open the browser mini organ instead of the editor')
    parser.add_argument('--workspace', type=Path, default=ROOT / '.workspace')
    args = parser.parse_args()
    workspace = Workspace(args.workspace)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(workspace, args.port))
    server.daemon_threads = True
    url = f'http://127.0.0.1:{args.port}'
    print(f'LoopAuditioneer Workspace: {url}\nLocal session: {args.workspace}\nPress Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        webbrowser.open(url + ('/player' if args.player else ''))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
