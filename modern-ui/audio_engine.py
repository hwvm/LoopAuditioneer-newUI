"""Local, non-destructive WAV preparation. All marker offsets are frames.

Loop end and trim end are exclusive internally; RIFF smpl end is inclusive.
Analysis suggests boundaries, never approves them on the user's behalf.
"""
from dataclasses import dataclass
import io
import json
import math
import re
import struct
import zipfile

import numpy as np


def chunk(tag, data):
    return tag + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')


def riff(chunks):
    body = b'WAVE' + b''.join(chunk(tag, data) for tag, data in chunks)
    if len(body) > 0xFFFFFFFF:
        raise ValueError('WAV exceeds the RIFF size limit.')
    return b'RIFF' + struct.pack('<I', len(body)) + body


@dataclass
class Wave:
    chunks: list
    pcm: bytes
    rate: int
    channels: int
    bits: int
    format: int
    samples: np.ndarray
    note: int | None
    loop: tuple | None
    cue: int | None

    @property
    def frames(self):
        return len(self.samples)

    @property
    def block(self):
        return self.channels * (self.bits // 8)


def read_wave(raw):
    if len(raw) < 44 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('Use an uncompressed RIFF WAV file (PCM or 32-bit float).')
    limit = struct.unpack_from('<I', raw, 4)[0] + 8
    if limit > len(raw):
        raise ValueError('Truncated WAV: RIFF size exceeds the file.')
    chunks, pos = [], 12
    while pos + 8 <= limit:
        tag, size = struct.unpack_from('<4sI', raw, pos)
        if pos + 8 + size > limit:
            raise ValueError('Truncated WAV chunk.')
        chunks.append((tag, raw[pos + 8:pos + 8 + size]))
        pos += 8 + size + size % 2
    fmts = [data for tag, data in chunks if tag == b'fmt ']
    payloads = [data for tag, data in chunks if tag == b'data']
    if len(fmts) != 1 or len(fmts[0]) < 16 or len(payloads) != 1:
        raise ValueError('Expected one format chunk and one audio data chunk.')
    fmt, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', fmts[0])
    if fmt == 0xFFFE:
        if len(fmts[0]) < 40:
            raise ValueError('Incomplete extensible WAV format.')
        subtype = fmts[0][24:40]
        if subtype[2:] != bytes.fromhex('000000001000800000aa00389b71'):
            raise ValueError('Unsupported extensible WAV subtype.')
        fmt = struct.unpack_from('<H', subtype)[0]
        valid_bits = struct.unpack_from('<H', fmts[0], 18)[0]
        if valid_bits not in (0, bits):
            raise ValueError('Packed valid-bit WAV formats are not supported yet.')
    if channels not in (1, 2) or not 8000 <= rate <= 384000:
        raise ValueError('Supported: mono/stereo WAV, 8–384 kHz.')
    if (fmt == 1 and bits not in (8, 16, 24, 32)) or (fmt == 3 and bits != 32) or fmt not in (1, 3):
        raise ValueError('Supported: 8/16/24/32-bit PCM or 32-bit float WAV.')
    pcm = payloads[0]
    if align != channels * bits // 8 or byte_rate != rate * align or len(pcm) % align:
        raise ValueError('Invalid WAV frame alignment.')
    if len(pcm) // align < 16:
        raise ValueError('The sample is too short (minimum 16 frames).')
    if fmt == 3:
        samples = np.frombuffer(pcm, '<f4').copy()
    elif bits == 8:
        samples = (np.frombuffer(pcm, 'u1').astype(np.float32) - 128) / 128
    elif bits == 24:
        b = np.frombuffer(pcm, 'u1').reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16
        samples = ((v ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        samples = np.frombuffer(pcm, '<i' + str(bits // 8)).astype(np.float32) / 2 ** (bits - 1)
    samples = samples.reshape(-1, channels)
    if not np.all(np.isfinite(samples)):
        raise ValueError('The WAV contains non-finite audio values.')
    note, loop, cue = None, None, None
    for tag, data in chunks:
        if tag == b'smpl' and len(data) >= 36:
            candidate = struct.unpack_from('<I', data, 12)[0]
            note = candidate if candidate <= 127 else None
            count = struct.unpack_from('<I', data, 28)[0]
            if count and len(data) >= 60:
                _, kind, start, end, _, _ = struct.unpack_from('<6I', data, 36)
                if kind == 0 and 0 <= start < end + 1 <= len(samples):
                    loop = (start, end + 1)
        if tag == b'cue ' and len(data) >= 28 and struct.unpack_from('<I', data)[0]:
            offset = struct.unpack_from('<I', data, 24)[0]
            if offset < len(samples):
                cue = offset
    return Wave(chunks, pcm, rate, channels, bits, fmt, samples, note, loop, cue)


def note_name(note):
    if note is None:
        return '—'
    return ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'][note % 12] + str(note // 12 - 1)


def filename_note(name):
    match = re.search(r'(?:^|[_\- ])(\d{3})(?=[_\- .])', name)
    if match and int(match[1]) <= 127:
        return int(match[1])
    match = re.search(r'(?:^|[_\- ])([A-Ga-g])([#b]?)(-?\d)(?=[_\- .]|$)', name)
    if match:
        value = (int(match[3]) + 1) * 12 + dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)[match[1].upper()]
        value += {'': 0, '#': 1, 'b': -1}[match[2]]
        if 0 <= value <= 127:
            return value
    return None


def peaks(wave, count=1100):
    edges = np.linspace(0, wave.frames, min(count, wave.frames) + 1, dtype=int)
    return [[[round(float(part.min()), 5), round(float(part.max()), 5)]
             for a, b in zip(edges[:-1], edges[1:])
             for part in [wave.samples[a:b, c]]] for c in range(wave.channels)]


def preview_wave(wave, start=0, end=None):
    """Headroom-safe 16-bit audition proxy; never modify or hard-clip float PCM.

    Gain is based on the whole source, so seeking/cropping does not pump volume.
    """
    end = wave.frames if end is None else end
    if not 0 <= start < end <= wave.frames:
        raise ValueError('Invalid audition range.')
    gain = min(1., .98 / max(float(np.max(np.abs(wave.samples))), 1e-12))
    pcm = (wave.samples[start:end] * gain * 32767).astype('<i2').tobytes()
    return riff([(b'fmt ', struct.pack('<HHIIHH', 1, wave.channels, wave.rate,
                                     wave.rate * wave.channels * 2, wave.channels * 2, 16)),
                 (b'data', pcm)])


def analyze(wave, name='', minimum_loop=0.4, threshold_db=-55):
    rate, n, x = wave.rate, wave.frames, wave.samples
    hop = max(1, round(rate * .01))
    envelope = np.array([np.sqrt(np.mean(x[i:i + hop].astype(np.float64) ** 2)) for i in range(0, n, hop)])
    maximum = float(envelope.max())
    audible = np.flatnonzero(envelope > max(10 ** (threshold_db / 20), maximum * .002))
    warnings = []
    note = wave.note if wave.note is not None else filename_note(name)
    attack, end = 0, n
    release = max(1, n - 1)
    loop_start, loop_end, quality = None, None, None
    if not len(audible):
        warnings.append('No clear signal above the silence threshold.')
    else:
        attack = min(n - 2, max(0, int(audible[0]) * hop - round(rate * .01)))
        end = min(n, (int(audible[-1]) + 2) * hop + round(rate * .05))
        # A handling click can be much louder than every pipe frame. Use a
        # sustained level instead of the single highest 10 ms block as reference.
        sustained = float(np.percentile(envelope[audible], 85))
        strong = np.flatnonzero(envelope > sustained * .65)
        # Estimate the start of the final decay, not its end. Keep a little
        # sustain before it for the sampler's release crossfade.
        release = min(end - 1, max(attack + 1, int(strong[-1]) * hop - round(rate * .025)))
        search_start = max(attack + round(rate * .18), int(strong[0]) * hop + round(rate * .1))
        search_end = release - round(rate * .08)
        minimum = round(minimum_loop * rate)
        if search_end - search_start > minimum + 64:
            dominant = int(np.argmax(np.mean(x[search_start:search_end] ** 2, axis=0)))
            mono = x[:, dominant]
            crossings = np.flatnonzero((mono[search_start:search_end - 1] <= 0) & (mono[search_start + 1:search_end] > 0)) + search_start + 1
            if len(crossings) > 320:
                crossings = crossings[np.linspace(0, len(crossings) - 1, 320, dtype=int)]
            window = min(128, max(16, rate // 1000))
            best = None
            for start in crossings:
                ends = crossings[crossings >= start + minimum]
                if not len(ends):
                    continue
                reference = x[start - window:start + window].astype(np.float64)
                comparison = np.stack([x[e - window:e + window] for e in ends]).astype(np.float64)
                error = np.mean((comparison - reference) ** 2, axis=(1, 2)) / max(float(np.mean(reference ** 2)), 1e-12)
                idx = int(np.argmin(error))
                if best is None or error[idx] < best[0]:
                    best = (float(error[idx]), int(start), int(ends[idx]))
            if best:
                error, loop_start, loop_end = best
                quality = round(max(0, min(100, 100 * (1 - math.sqrt(error)))))
                if quality < 85:
                    warnings.append('Loop seam needs listening: the two boundaries differ.')
        if loop_start is None:
            warnings.append('No reliable sustain loop found. Place loop markers manually.')
        if end == n and envelope[-1] > maximum * .08:
            warnings.append('The recording ends before a clear decay. Check the release and tail.')
        warnings.append('Release is an estimate; audition the transition and room decay.')
    if wave.loop and wave.cue is not None and wave.loop[1] <= wave.cue:
        loop_start, loop_end = wave.loop
        release = wave.cue
        attack = min(attack, loop_start)
        end = max(end, release + 1)
        warnings = ['Existing loop and release cue retained. Audition before approval.']
        quality = None
    if float(np.max(np.abs(x))) > 1 and wave.format == 3:
        warnings.append('Float audio exceeds 0 dBFS. Preview gain is reduced to avoid clipping; original float samples are preserved.')
    elif float(np.max(np.abs(x))) >= .9999:
        warnings.append('Signal reaches full scale; check for clipping.')
    if note is None:
        warnings.append('Root note was not found in the filename or WAV metadata.')
    # Always measure sounding pitch independently of keyboard/root metadata.
    # A 16-foot or 4-foot stop intentionally sounds an octave away from its key.
    from note_splitter import estimate_note
    estimated_note, pitch_confidence = estimate_note(wave, attack, end)
    return {'markers': {'attack': attack, 'loopStart': loop_start, 'loopEnd': loop_end, 'release': release, 'end': end},
            'note': note, 'estimatedNote': estimated_note, 'pitchConfidence': pitch_confidence,
            'quality': quality, 'warnings': warnings, 'reviewed': False, 'testApproved': False, 'analyzed': True, 'manual': False}


def loop_candidates(wave, markers, minimum_loop=.4, limit=5):
    """Offer distinct seams inside the user's attack/release bounds without editing."""
    validate_markers(wave, markers, require_loop=False)
    if type(minimum_loop) not in (int, float) or not math.isfinite(minimum_loop) or not .1 <= minimum_loop <= 10:
        raise ValueError('Minimum loop length must be between 0.1 and 10 seconds.')
    rate, x = wave.rate, wave.samples
    window = min(128, max(16, rate // 1000))
    lower = max(window, markers['attack'] + round(rate * .18))
    upper = min(wave.frames - window, markers['release'] - round(rate * .08))
    minimum = round(rate * minimum_loop)
    if upper - lower < minimum:
        return []
    dominant = int(np.argmax(np.mean(x[lower:upper] ** 2, axis=0)))
    mono = x[:, dominant]
    crossings = np.flatnonzero((mono[lower:upper - 1] <= 0) & (mono[lower + 1:upper] > 0)) + lower + 1
    if len(crossings) > 320:
        crossings = crossings[np.linspace(0, len(crossings) - 1, 320, dtype=int)]
    ranked = []
    for start in crossings:
        ends = crossings[crossings >= start + minimum]
        if not len(ends):
            continue
        reference = x[start - window:start + window].astype(np.float64)
        if float(np.mean(reference ** 2)) < 1e-12:
            continue
        comparison = np.stack([x[e - window:e + window] for e in ends]).astype(np.float64)
        errors = np.mean((comparison - reference) ** 2, axis=(1, 2)) / float(np.mean(reference ** 2))
        ranked.extend((float(error), int(start), int(end)) for error, end in zip(errors, ends))
    choices = []
    for error, start, end in sorted(ranked):
        if markers['loopStart'] is not None and abs(start - markers['loopStart']) + abs(end - markers['loopEnd']) < .08 * rate:
            continue
        if any(abs(start - c['loopStart']) + abs(end - c['loopEnd']) < .08 * rate for c in choices):
            continue
        choices.append({'loopStart': start, 'loopEnd': end,
                        'quality': round(max(0, min(100, 100 * (1 - math.sqrt(error)))))})
        if len(choices) >= limit:
            break
    return choices


def refine_loop(wave, markers, start, end, radius_ms=20, minimum_loop=.1, limit=5):
    """Find stereo seams near BOTH user anchors, without altering any markers."""
    proposed = {**markers, 'loopStart': start, 'loopEnd': end}
    validate_markers(wave, proposed)
    if type(radius_ms) not in (int, float) or not math.isfinite(radius_ms) or not 1 <= radius_ms <= 250:
        raise ValueError('Search distance must be between 1 and 250 ms.')
    if type(minimum_loop) not in (int, float) or not math.isfinite(minimum_loop) or not .1 <= minimum_loop <= 10:
        raise ValueError('Minimum loop length must be between 0.1 and 10 seconds.')
    radius, minimum = round(radius_ms * wave.rate / 1000), round(minimum_loop * wave.rate)
    window = min(128, max(16, wave.rate // 1000))
    lower, upper = max(window, markers['attack']), min(wave.frames - window, markers['release'])
    dominant = int(np.argmax(np.mean(wave.samples[start:end].astype(np.float64) ** 2, axis=0)))
    mono = wave.samples[:, dominant]

    def nearby(anchor):
        lo, hi = max(lower, anchor - radius), min(upper, anchor + radius)
        if lo > hi:
            return np.array([], dtype=int)
        crossings = np.flatnonzero((mono[lo - 1:hi] <= 0) & (mono[lo:hi + 1] > 0)) + lo
        # Keep closest zero crossings, not a subsample that can skip the anchor.
        crossings = crossings[np.argsort(np.abs(crossings - anchor), kind='stable')[:160]]
        return np.unique(np.r_[crossings, anchor if lo <= anchor <= hi else lo]).astype(int)

    starts, ends = nearby(start), nearby(end)
    ranked = []
    for a in starts:
        eligible = ends[ends >= a + minimum]
        if not len(eligible):
            continue
        reference = wave.samples[a - window:a + window].astype(np.float64)
        power = float(np.mean(reference ** 2))
        if power < 1e-12:
            continue
        comparison = np.stack([wave.samples[b - window:b + window] for b in eligible]).astype(np.float64)
        errors = np.mean((comparison - reference) ** 2, axis=(1, 2)) / power
        for error, b in zip(errors, eligible):
            distance = (abs(int(a) - start) + abs(int(b) - end)) / max(1, 2 * radius)
            ranked.append((float(error) + .03 * distance, float(error), int(a), int(b)))
    choices = []
    for _, error, a, b in sorted(ranked):
        if any(abs(a - c['loopStart']) + abs(b - c['loopEnd']) < wave.rate * .001 for c in choices):
            continue
        choices.append({'loopStart': a, 'loopEnd': b,
                        'quality': round(max(0, min(100, 100 * (1 - math.sqrt(error))))),
                        'startShiftMs': round((a - start) / wave.rate * 1000, 3),
                        'endShiftMs': round((b - end) / wave.rate * 1000, 3)})
        if len(choices) >= limit:
            break
    return choices


def seam_quality(wave, markers):
    start, end = markers['loopStart'], markers['loopEnd']
    if start is None or end is None:
        return None
    window = min(128, max(16, wave.rate // 1000), start, wave.frames - end)
    if window < 2:
        return None
    a = wave.samples[start - window:start + window].astype(np.float64)
    b = wave.samples[end - window:end + window].astype(np.float64)
    error = float(np.mean((a - b) ** 2)) / max(float(np.mean(a ** 2)), 1e-12)
    return round(max(0, min(100, 100 * (1 - math.sqrt(error)))))


def validate_markers(wave, markers, require_loop=True):
    keys = ['attack', 'loopStart', 'loopEnd', 'release', 'end']
    if not isinstance(markers, dict) or set(markers) != set(keys):
        raise ValueError('All five marker fields are required.')
    if not require_loop and markers['loopStart'] is None and markers['loopEnd'] is None:
        keys = ['attack', 'release', 'end']
    values = [markers[k] for k in keys]
    if any(type(v) is not int for v in values):
        raise ValueError('Place a valid sustain loop before approving or exporting.')
    if not 0 <= values[0] or values[-1] > wave.frames:
        raise ValueError('Markers must be inside the recording.')
    if require_loop or len(keys) == 5:
        a, s, e, r, t = values
        if not a <= s < e <= r < t:
            raise ValueError('Use attack ≤ loop start < loop end ≤ release < tail end.')
    elif not values[0] <= values[1] < values[2]:
        raise ValueError('Use attack ≤ release < tail end.')


def write_wave(wave, state, trim=False, part=None):
    m = state['markers']
    validate_markers(wave, m)
    start, end = (m['attack'], m['end']) if trim else (0, wave.frames)
    if part == 'attack':
        start, end = m['attack'], m['loopEnd']
    elif part == 'release':
        start, end = m['release'], m['end']
    # Full-length metadata writes preserve unrelated chunks and exact PCM.
    # Trimming invalidates time-referenced metadata; retain only format data.
    kept = [(tag, data) for tag, data in wave.chunks
            if tag not in (b'data', b'smpl', b'cue ', b'LIST') and (start == 0 and end == wave.frames or tag == b'fmt ')]
    if start == 0 and end == wave.frames:
        kept += [(tag, data) for tag, data in wave.chunks if tag == b'LIST' and data[:4] != b'adtl']
    note = state.get('note')
    if note is None:
        raise ValueError('Set a root MIDI note before export.')
    fraction = next((struct.unpack_from('<I', d, 16)[0] for t, d in wave.chunks if t == b'smpl' and len(d) >= 36), 0)
    has_loop = part != 'release'
    sampler = struct.pack('<9I', 0, 0, round(1e9 / wave.rate), note, fraction, 0, 0, int(has_loop), 0)
    if has_loop:
        sampler += struct.pack('<6I', 1, 0, m['loopStart'] - start, m['loopEnd'] - start - 1, 0, 0)
    kept.append((b'smpl', sampler))
    if part is None:
        kept.append((b'cue ', struct.pack('<II I4sIII', 1, 1, m['release'] - start, b'data', 0, 0, m['release'] - start)))
    kept.append((b'data', wave.pcm[start * wave.block:end * wave.block]))
    return riff(kept)


def hauptwerk_filename(note):
    """CODM filename: three-digit assigned MIDI key and sharp pitch class, no octave."""
    if type(note) is not int or not 0 <= note <= 127:
        raise ValueError('Set a valid root MIDI note (0–127) before export.')
    pitch = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')[note % 12]
    return f'{note:03d}-{pitch}.wav'


def export_zip(records, mode='wav', trim=False):
    if mode not in ('wav', 'split', 'sfz'):
        raise ValueError('Unknown export format.')
    records = list(records)
    used = {}
    for record, wave in records:
        if not record.get('reviewed'):
            raise ValueError('Only reviewed samples can be exported.')
        validate_markers(wave, record['markers'])
        filename = hauptwerk_filename(record.get('note'))
        if filename in used:
            raise ValueError(f'Duplicate MIDI key {record["note"]}: "{used[filename]}" and "{record["name"]}" both export as {filename}. Select one take per key, or export registers separately.')
        used[filename] = record['name']
    output, report, sfz = io.BytesIO(), [], []
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for record, wave in records:
            name = hauptwerk_filename(record['note'])
            exported_files = []
            if mode == 'split':
                for part in ('attack', 'release'):
                    filename = f'{part}/{name}'
                    archive.writestr(filename, write_wave(wave, record, part=part))
                    exported_files.append(filename)
            else:
                filename = f'samples/{name}'
                archive.writestr(filename, write_wave(wave, record, trim=trim))
                exported_files.append(filename)
                if mode == 'sfz':
                    m, offset = record['markers'], record['markers']['attack'] if trim else 0
                    sfz.append(f'<region> sample={filename} key={record["note"]} pitch_keycenter={record["note"]} loop_mode=loop_sustain loop_start={m["loopStart"] - offset} loop_end={m["loopEnd"] - offset - 1} ampeg_release=0.5')
            report.append({**{k: v for k, v in record.items() if k not in ('peaks', 'id')}, 'exportedFiles': exported_files})
        if not report:
            raise ValueError('Select at least one reviewed sample.')
        archive.writestr('preparation.json', json.dumps({'version': 1, 'markerUnits': 'source frames; loopEnd/end exclusive', 'format': mode, 'trimmed': trim or mode == 'split', 'samples': report}, indent=2))
        archive.writestr('README.txt', 'Prepared with LoopAuditioneer Workspace.\nHauptwerk filenames use the assigned MIDI key: 036-C.wav, 037-C#.wav, etc.\nAttack and release folders use matching filenames. preparation.json maps original names to exportedFiles.\nWAV smpl loop ends are inclusive; release is stored as a cue point.\nOriginal sample rate, bit depth and PCM values are preserved.\nSplit attack files contain attack + looped sustain; release files contain the tail.\nSFZ maps each sample to its root key, with a 0.5 s envelope release. It does not trigger recorded release tails.\nHauptwerk / GrandOrgue organ definition files are not generated.\nListen to your exports in the target sampler before building an instrument.\n')
        if mode == 'sfz':
            archive.writestr('instrument.sfz', '\n'.join(sfz) + '\n')
    return output.getvalue()


def demo_wave(note, variant=0):
    rate, duration = 44100, 5.8 + variant * .17
    t = np.arange(round(rate * duration)) / rate
    frequency = 440 * 2 ** ((note - 69) / 12)
    onset, release = .09, duration - 1.65
    envelope = np.clip((t - onset) / .13, 0, 1) * np.exp(-np.maximum(t - release, 0) * 4)
    channels = []
    for phase in (0, .09):
        tone = sum(np.sin(2 * np.pi * frequency * harmonic * t + phase) / harmonic ** 2 for harmonic in range(1, 7))
        tone *= envelope * .36 * (1 + .018 * np.sin(2 * np.pi * 1.7 * t))
        channels.append(tone)
    pcm = (np.stack(channels, axis=1) * 32767).astype('<i2').tobytes()
    return riff([(b'fmt ', struct.pack('<HHIIHH', 1, 2, rate, rate * 4, 4, 16)), (b'data', pcm)])
