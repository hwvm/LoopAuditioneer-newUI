"""Reviewable segmentation of sequential, separated notes. No source separation."""
import math
import struct

import numpy as np

from audio_engine import riff


def estimate_note(wave, start, end):
    """Suggest a monophonic root from agreement between bounded autocorrelation windows."""
    if end - start < wave.rate * .12:
        return None, None
    guesses = []
    size = min(end - start, round(wave.rate * .35))
    for fraction in (.15, .4, .65):
        offset = start + round((end - start - size) * fraction)
        section = wave.samples[offset:offset + size]
        channel = int(np.argmax(np.mean(section ** 2, axis=0)))
        x = section[:, channel].astype(np.float64)
        # Retain enough samples per period for the upper octaves of 4-foot stops.
        factor = max(1, wave.rate // 24000)
        if factor > 1:
            x = x[:len(x) // factor * factor].reshape(-1, factor).mean(axis=1)
        x -= x.mean()
        if np.sqrt(np.mean(x * x)) < 1e-5:
            continue
        rate, n = wave.rate / factor, len(x)
        fft_size = 1 << (2 * n - 1).bit_length()
        spectrum = np.fft.rfft(x, fft_size)
        correlation = np.fft.irfft(spectrum * spectrum.conj(), fft_size)[:n]
        lo, hi = max(2, int(rate / 4500)), min(n // 2, int(rate / 20))
        energy = np.r_[0, np.cumsum(x * x)]
        lags = np.arange(hi + 1)
        denominator = np.sqrt(np.maximum(energy[n - lags] * (energy[n] - energy[lags]), 1e-20))
        correlation = correlation[:hi + 1] / denominator
        candidates = np.array([i for i in range(lo, hi) if correlation[i] > correlation[i - 1] and correlation[i] >= correlation[i + 1]], dtype=int)
        if not len(candidates):
            continue
        best = float(correlation[candidates].max())
        if best < .8:
            continue
        lag = int(candidates[np.flatnonzero(correlation[candidates] >= max(.8, best * .94))[0]])
        a, b, c = correlation[lag - 1:lag + 2]
        adjustment = .5 * (a - c) / (a - 2 * b + c) if abs(a - 2 * b + c) > 1e-12 else 0
        frequency = rate / (lag + max(-.5, min(.5, adjustment)))
        pitch = 69 + 12 * math.log2(frequency / 440)
        note = round(pitch)
        if 0 <= note <= 127 and abs(pitch - note) < .45:
            guesses.append((note, max(0, min(100, round(float(b) * 100)))))
    if len(guesses) < 2:
        return None, None
    notes = [note for note, _ in guesses]
    note = max(set(notes), key=notes.count)
    if notes.count(note) < 2:
        return None, None
    return note, min(score for candidate, score in guesses if candidate == note)


def detect_regions(wave, threshold=-48, gap_ms=250, minimum_ms=250, tail_ms=150, auto_threshold=True):
    settings = [(threshold, -90, -12), (gap_ms, 30, 5000), (minimum_ms, 50, 10000), (tail_ms, 0, 5000)]
    if any(type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high for value, low, high in settings):
        raise ValueError('Split settings are outside the supported range.')
    if type(auto_threshold) is not bool:
        raise ValueError('Automatic threshold must be true or false.')
    hop = max(1, round(wave.rate * .01))
    # Channel maximum retains anti-phase stereo and one-channel recordings.
    envelope = np.array([np.sqrt(np.max(np.mean(wave.samples[i:i + hop].astype(np.float64) ** 2, axis=0)))
                         for i in range(0, wave.frames, hop)])
    # Pauses in microphone recordings contain room/wind noise, not digital silence.
    # The lower decile estimates their level without relying on a silent intro.
    levels_db = 20 * np.log10(np.maximum(envelope, 1e-6))
    noise_db = float(np.percentile(levels_db, 10))
    effective_db = max(-60., noise_db + 8) if auto_threshold else float(threshold)
    # Lower the trigger for quiet pipes without lowering the pause gate into
    # microphone noise (which would merge adjacent notes).
    quiet_db = max(effective_db - 6, noise_db + 6) if auto_threshold else effective_db - 6
    on, off = 10 ** (effective_db / 20), 10 ** (quiet_db / 20)
    gap = max(1, math.ceil(gap_ms * wave.rate / 1000 / hop))
    minimum = round(minimum_ms * wave.rate / 1000)
    raw, start, last = [], None, None
    for i, level in enumerate(envelope):
        if start is None:
            if level >= on:
                start, last = i, i
        else:
            if level >= off:
                last = i
            if i - last >= gap:
                if (last - start + 1) * hop >= minimum:
                    raw.append((start * hop, min(wave.frames, (last + 1) * hop)))
                start, last = None, None
    if start is not None and (last - start + 1) * hop >= minimum:
        raw.append((start * hop, min(wave.frames, (last + 1) * hop)))
    if len(raw) > 500:
        raise ValueError('More than 500 regions found. Increase minimum note length or the required quiet gap.')
    regions = []
    lead, tail = round(wave.rate * .02), round(wave.rate * tail_ms / 1000)
    for index, (start, end) in enumerate(raw):
        lower = (raw[index - 1][1] + start) // 2 if index else 0
        upper = (end + raw[index + 1][0]) // 2 if index + 1 < len(raw) else wave.frames
        note, confidence = estimate_note(wave, start, end)
        # Recover the quieter beginning of an attack before the trigger level.
        attack = start // hop
        earliest = max(math.ceil(lower / hop), attack - math.ceil(.25 * wave.rate / hop))
        while attack > earliest and envelope[attack - 1] >= off:
            attack -= 1
        regions.append({'start': max(lower, attack * hop - lead), 'end': min(upper, end + tail), 'note': note, 'confidence': confidence})
    warnings = ['For one note at a time with quiet gaps. Chords and overlapping tails are not separated.',
                'Pitch labels are suggestions; verify the octave, especially for mutation stops and mixtures.']
    if auto_threshold:
        floor_text = f'{noise_db:.1f} dBFS' if noise_db > -90 else 'below −90 dBFS'
        warnings.insert(0, f'Automatic threshold: {effective_db:.1f} dBFS; estimated background {floor_text}; quiet-gap level {quiet_db:.1f} dBFS. Audio is unchanged.')
        if float(np.percentile(levels_db, 90)) - noise_db < 8:
            warnings.append('Little level contrast was measured. Automatic calibration needs quiet pauses; try a manual threshold or add regions if notes are missing.')
    elif noise_db >= effective_db - 6:
        warnings.insert(0, f'Background is about {noise_db:.1f} dBFS, above the quiet-gap level. Enable Automatic threshold or raise the manual threshold to avoid merged notes.')
    unassigned = sum(r['note'] is None for r in regions)
    if unassigned:
        warnings.append(f'{unassigned} region(s) have no reliable pitch label. Listen and assign their keys, or use a known recording sequence.')
    if len(regions) == 1:
        warnings.append('Only one region found. Lower the quiet-gap duration, adjust the threshold, or divide the region manually.')
    if not regions:
        warnings.append('No notes found. Lower the threshold or minimum note duration, or add a region manually.')
    if regions and regions[-1]['end'] == wave.frames:
        warnings.append('The last region reaches the recording end. Check that its release is complete.')
    return {'regions': regions, 'warnings': warnings,
            'calibration': {'automatic': auto_threshold, 'noiseDb': round(noise_db, 2),
                            'thresholdDb': round(effective_db, 2), 'quietDb': round(quiet_db, 2)}}


def validate_regions(wave, regions):
    if not isinstance(regions, list) or not 1 <= len(regions) <= 500:
        raise ValueError('Provide between 1 and 500 note regions.')
    previous = 0
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            raise ValueError('Invalid note region.')
        start, end, note = region.get('start'), region.get('end'), region.get('note')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= wave.frames or end - start < 16:
            raise ValueError(f'Region {index + 1}: use a valid start/end inside the recording (at least 16 frames).')
        if start < previous:
            raise ValueError(f'Region {index + 1} overlaps the previous region. Adjust or merge the regions.')
        if note is not None and (type(note) is not int or not 0 <= note <= 127):
            raise ValueError(f'Region {index + 1}: MIDI note must be 0–127, or blank for later assignment.')
        previous = end


def slice_wave(wave, region):
    """Create a fresh WAV with exact source frames, discarding stale loop/cue offsets."""
    validate_regions(wave, [region])
    chunks = [(tag, data) for tag, data in wave.chunks if tag == b'fmt ']
    if region.get('note') is not None:
        chunks.append((b'smpl', struct.pack('<9I', 0, 0, round(1e9 / wave.rate), region['note'], 0, 0, 0, 0, 0)))
    if wave.format == 3:
        chunks.append((b'fact', struct.pack('<I', region['end'] - region['start'])))
    chunks.append((b'data', wave.pcm[region['start'] * wave.block:region['end'] * wave.block]))
    return riff(chunks)
