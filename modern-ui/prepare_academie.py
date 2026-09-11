"""Audit the Academie takes and create three draft register projects + a player.

Run with --source PATH. Originals are only read. Use a fresh --workspace for
each run; existing sessions are refused, so preparation cannot replace edits.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from audio_engine import analyze, validate_markers
from note_splitter import detect_regions
from server import ROOT, Workspace

STOPS = [('Bourdon 16', -12), ('Bourdon 8', 0), ('Prestant 4', 12)]


def prepare(source, folder, first_key=36):
    if (folder / 'session.json').exists() or (folder / 'player' / 'library.json').exists():
        raise ValueError('This workspace already contains edits. Choose a new --workspace directory.')
    files = []
    for name, shift in STOPS:
        matches = [p for p in (source / name).iterdir() if p.suffix.lower() == '.wav']
        if len(matches) != 1:
            raise ValueError(f'Expected one WAV in {source / name}. Found {len(matches)}.')
        files.append((name, shift, matches[0]))
    workspace, report = Workspace(folder), {'source': str(source.resolve()), 'firstKeyboardKey': first_key, 'stops': []}
    for name, shift, path in files:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        workspace.projects.new(f'Academie · {name}')
        parent = workspace.import_wave(raw, path.name)
        wave = workspace.wave(parent['id'])
        result = detect_regions(wave)
        # These three confirmed sequential takes contain sustained notes. Keep
        # short handling sounds in the review draft, but do not map them to keys.
        kept = [r for r in result['regions'] if r['end'] - r['start'] >= 1.5 * wave.rate]
        if first_key + len(kept) > 128:
            raise ValueError('The chromatic sequence exceeds the MIDI range.')
        regions, comparisons = [], []
        for i, region in enumerate(kept):
            key, expected = first_key + i, first_key + i + shift
            comparisons.append({'key': key, 'expectedSoundingNote': expected,
                                'estimatedSoundingNote': region['note'], 'confidence': region['confidence'],
                                'needsPitchReview': region['note'] != expected,
                                'startSeconds': round(region['start'] / wave.rate, 3),
                                'endSeconds': round(region['end'] / wave.rate, 3)})
            regions.append({**region, 'note': key})
        key_by_start = {r['start']: r['note'] for r in regions}
        workspace.project['drafts'][parent['id']] = {
            'regions': [{**r, 'note': key_by_start.get(r['start']), 'keep': r['start'] in key_by_start} for r in result['regions']],
            'settings': {'autoThreshold': True, 'first': first_key, 'step': 1},
            'warnings': result['warnings'] + ['Keyboard keys follow the confirmed chromatic low-C sequence. Independent sounding-pitch estimates and disagreements are in academie-report.json. Short handling sounds are unchecked.'],
            'analyzeAfter': True,
        }
        created = workspace.split_recording(parent['id'], regions)
        valid, scores = 0, []
        for identity, comparison in zip(created, comparisons):
            record = workspace.records[identity]
            child = workspace.wave(identity)
            record.update(analyze(child))
            record['estimatedNote'] = comparison['estimatedSoundingNote']
            record['pitchConfidence'] = comparison['confidence']
            if comparison['needsPitchReview']:
                record['warnings'].insert(0, f"Pitch review: expected sounding MIDI {comparison['expectedSoundingNote']}, estimated {comparison['estimatedSoundingNote']}. Keyboard key {comparison['key']} follows the confirmed recording sequence.")
            comparison['markers'] = record['markers']
            comparison['loopQuality'] = record['quality']
            try:
                validate_markers(child, record['markers'])
                valid += 1
            except ValueError:
                pass
            if record['quality'] is not None:
                scores.append(record['quality'])
        workspace.project['notes'] = ('Recorded chromatically from low C (keyboard MIDI 36). '
            'Every region was independently pitch-estimated; disagreement warnings need listening review. '
            'Original 32-bit float audio is retained. Player uses level-balanced, loop-smoothed preview copies and an envelope release.')
        workspace.project['ui'].update(activeId=created[0] if created else parent['id'], selected=created)
        stop_name = name.replace('16', '16′').replace('8', '8′').replace('4', '4′')
        workspace.project['player'] = {'name': stop_name, 'keyShift': 0, 'scope': 'selected', 'sampleIds': created}
        workspace.save()
        archive = workspace.projects.save()
        stop = workspace.player.publish(workspace, stop_name, ids=created, linked=True)
        unchanged = hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if not unchanged:
            raise ValueError(f'The source changed during the audit: {path}')
        summary = {'name': name, 'file': str(path.resolve()), 'sha256': digest, 'originalUnchanged': unchanged,
                   'duration': wave.frames / wave.rate, 'rate': wave.rate, 'bits': wave.bits,
                   'format': wave.format, 'peak': float(np.max(np.abs(wave.samples))),
                   'calibration': result['calibration'], 'detectedRegions': len(result['regions']),
                   'excludedShortRegions': len(result['regions']) - len(kept), 'pipes': len(created),
                   'validLoops': valid, 'medianLoopQuality': float(np.median(scores)) if scores else None,
                   'pitchDisagreements': sum(c['needsPitchReview'] for c in comparisons),
                   'project': str(archive.resolve()), 'playerId': stop['id'], 'notes': comparisons}
        report['stops'].append(summary)
        print(f"{name}: {len(created)} pipes, {valid} valid loops, {summary['pitchDisagreements']} pitch reviews", flush=True)
    (folder / 'academie-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), 'utf-8')
    print(f'Prepared library: {folder.resolve()}\nStart: python server.py --workspace "{folder}" --port 8766', flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, default=ROOT / '.workspace' / 'academie')
    parser.add_argument('--first-midi', type=int, default=36)
    args = parser.parse_args()
    if not 0 <= args.first_midi <= 127:
        parser.error('--first-midi must be between 0 and 127')
    prepare(args.source, args.workspace, args.first_midi)
