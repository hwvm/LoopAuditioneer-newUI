# Academie recording audit — 11 September 2026

Source: `D:\Audio\0_Academie`. The user confirmed chromatic recording from low C.
All three WAVs are stereo, 48 kHz, 32-bit float. Originals were read and their
SHA-256 hashes checked after preparation; none changed. Private audio and the
per-pipe report stay in the ignored `.workspace/academie/` directory.

| Stop | Recording | Prepared pipes | Keyboard range | Pitch estimates needing review | Median seam score |
| --- | ---: | ---: | --- | ---: | ---: |
| Bourdon 16′ | 124 s | 30 | MIDI 36–65 (C2–F4) | 8 | 94 |
| Bourdon 8′ | 184 s | 56 | MIDI 36–91 (C2–G6) | 1 | 97 |
| Prestant 4′ | 167 s | 56 | MIDI 36–91 (C2–G6) | 2 | 97 |

All 142 prepared pipes have valid ordered loop markers. This checks marker
structure and seam similarity, not musical approval: all remain drafts.
One short region at the start of Bourdon 16 and one at the end of Prestant 4
were excluded as handling sounds. Their boundaries remain in the split drafts.

Changes prompted by the recordings:

- The automatic splitter's +12 dB trigger missed quiet pipes. A +8 dB trigger
  with a separate +6 dB pause gate recovers them without lowering the pause
  gate into microphone noise. The old default found 26 regions in Bourdon 16;
  the new one finds 31, including its excluded opening noise region.
- Pitch autocorrelation raises its decimation target from 12 to 24 kHz,
  retaining more samples per period. This reduces false lower-octave estimates in
  high 4′ pipes. Every note still receives an independent estimate; confirmed
  chromatic key assignments do not count as measured pitch.
- Bourdon 16 has float peaks of about 6.37 (above 0 dBFS). Its original float
  values are valid and preserved. Browser proxies scale for headroom instead
  of clipping those values into a 16-bit file.
- Loop detection uses a sustained level percentile so a short handling click
  does not become the reference level for the whole note.
- Player copies use a short loop blend, capped level balancing, conservative
  master gain and a compressor. These audition changes do not affect exports.

Review the following **keyboard MIDI keys**, comparing the actual sound with
the expected stop pitch:

- Bourdon 16′: 39, 40, 50, 51, 52, 53, 62, 63.
- Bourdon 8′: 78.
- Prestant 4′: 57, 81.

An estimate can disagree because of noise, harmonics, tuning, or an actual
performance/pipe issue. The sequence supplies the keyboard assignment, not a
guarantee that the recorded pipe sounds at that pitch. No pitch correction,
denoising, or reconstruction of missing release tails has been applied.

Validation: **81 Python tests and 6 JavaScript tests pass**, along with all five
browser-script syntax checks. Python regressions cover noisy splitting, float headroom,
independent pitch estimates, source preservation, project persistence, atomic
player snapshots, and HTTP routes. JavaScript tests cover layered stops, MIDI
device/channel ownership, sustain, velocity-zero note-off, repeated notes,
stop changes, voice limits, panic, and stereo loop smoothing.

Chrome loaded all 142 real pipes. An OfflineAudioContext render at 48 kHz of
C3–E3–G3 through all three stops produced nine voices, peak amplitude 0.4454,
RMS 0.09756 during the measured sustain, and zero output after release. This is
a digital render check, not a listening review or a physical MIDI keyboard test.
Desktop and 390-pixel layouts were inspected in Chrome; the page has no horizontal
overflow, and the on-screen keyboard scrolls within its own area on small screens.

Reproduce with the commands in the [workspace guide](README.md#academie-recordings).
