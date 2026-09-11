# LoopAuditioneer Workspace

A modern, local companion to the classic C++ application. Import a batch,
detect attack / sustain loop / release / tail boundaries, listen, adjust individual
samples, and export reviewed copies. This is a working standalone Python + browser
workspace, not yet a replacement front end connected to the C++ engine.

## Run

For cloning and isolated environment setup on Windows, macOS, or Linux, follow
the repository [quick start](../README.md#run-the-modern-workspace).

On Windows, double-click **start-workspace.cmd**. Requires Python 3.10+ on PATH.
The launcher installs NumPy if missing and opens your default browser.

Or, from this directory:

```powershell
python -m pip install -r requirements.txt
python server.py
```

## Browser mini organ

Open **Mini organ player** in the workspace sidebar, or visit `/player` on the
same local server. Click **Start audio**, enable one or more stop knobs, then
play using MIDI, the on-screen keyboard, or the typing keys shown below it.
Use **Connect MIDI keyboard** and allow the browser's MIDI permission request.
Chrome or Edge on localhost is a practical starting point; unsupported browsers
still offer the on-screen keyboard. Web MIDI needs a secure context and browser
permission ([Web MIDI documentation](https://developer.mozilla.org/en-US/docs/Web/API/Web_MIDI_API)).

- Stops combine polyphonically, with independent levels and a master volume.
  MIDI channels and devices own their notes independently. Sustain pedal (CC64),
  note-off, velocity-zero note-on, and all-notes-off (CC120/123) are supported.
  Pipe volume is fixed rather than velocity-sensitive.
- **Esc / All notes off**, loss of page focus, and MIDI device changes release
  held notes. Typing octave buttons affect the computer keyboard only.
- Recorded keys are the default. **Missing keys** optionally borrows the nearest
  pipe within two semitones or an octave; its speed and timbre change accordingly.
  Gray keys indicate no sample for the current registration.
- Playback uses a 16-bit copy, gentle attack, up to 20 ms loop smoothing,
  per-pipe level balancing capped at +12 dB, and an adjustable envelope release.
  It does not trigger recorded release tails or denoise the recording.
  Source PCM and export audio are unchanged. Float recordings above 0 dBFS are
  attenuated for browser preview instead of hard-clipped.
- Up to 16 stop snapshots, 192 simultaneous voices, and 512 MB decoded audio.
  The page loads the library before accepting notes, avoiding late notes during
  decoding. Drawn stops, levels and playing controls are remembered in the player tab; Start audio is still required after a page reload.

Use **Update player** in the editor to save the register and update its linked
stop, then **Play**. Repeated updates replace that stop instead of adding duplicate
snapshots. Returning to the player or clicking **Refresh instrument** loads the
new version, preserves drawn stops and levels, and reuses unchanged decoded
pipes. Held notes are released during a refresh.

In **Export & play**, set a stop name and keyboard mapping. Existing keyboard
assignments need no offset. Choose all playable notes, approved notes (including
testing), or checked takes. For duplicate keys, choose Checked notes and check
one take per key in Loops & trim. That selection travels with the project, even
when an archive is reopened with fresh internal file IDs. Unreviewed pipes remain
labeled as drafts; invalid loops or missing keys are reported and skipped.

The player's **Register projects** picker adds saved projects without switching
the active editor. Each linked stop shows whether its project has newer edits,
and offers **Update from project** and **Edit project**. Names, mapping and note
selection come from the project's settings. Older independent snapshots can be
linked using **Link a project**, then choosing the source project and updating.

Player audio lives under the workspace's `player/` directory. Updates publish
immutable audio revisions before replacing the library entry, so an existing
player can finish loading its previous version. A failed update leaves the old
version playable; an unchanged update reuses its current revision. Prior audio
revisions remain locally, so allow disk space when iterating on large registers.
Separate `--workspace` directories have independent project/player libraries.

### Academie recordings

The reproducible preparation command below expects one WAV in each of
`Bourdon 16/`, `Bourdon 8/`, and `Prestant 4/`. Install requirements first.

```powershell
python prepare_academie.py --source "D:\Audio\0_Academie"
python server.py --workspace .workspace/academie --port 8766 --player
```

After preparation, **start-mini-player.cmd** runs the second command. If the
server is already running, open **http://127.0.0.1:8766/player** directly.
The original workspace on port 8765 is independent. To stop a foreground server,
press Ctrl+C in its terminal.

Preparation assumes the confirmed chromatic sequence from keyboard low C
(MIDI 36; configurable with `--first-midi`). It independently estimates every
region's sounding pitch, compares it with the stop footage, and flags mismatches.
Short regions under 1.5 seconds are excluded as handling sounds for these takes
but remain unchecked in each saved split draft. This duration rule is specific
to this preparation command; the general splitter remains adjustable.

The three draft projects, source copies, player snapshots, and detailed
`academie-report.json` live in `.workspace/academie/`, ignored by Git. Use **Projects**
in that workspace to inspect each register. Source SHA-256 hashes are verified
after preparation. Existing destination sessions are refused; choose a fresh
`--workspace` to repeat the audit. Back up that whole folder to retain the player
as well as projects. `.loopproject` downloads contain one register, not the player
library. See [the recording audit](ACADEMIE-AUDIT.md) for measured results.

Open **http://127.0.0.1:8765**. Keep the terminal running; Ctrl+C stops the server.
Use `python server.py --port 8766` if the default port is occupied.
`--no-browser` suppresses automatic browser opening. `--workspace PATH` chooses
the local session directory.

## Register projects

Start a project per register with **New**, give it a name, and import the full
recording. **Save project** (Ctrl+S / Cmd+S) keeps the source audio, split drafts,
sample edits, root notes, approvals, settings, and selected sample together.
**Projects** shows source/note counts, approvals and player status, with actions to edit a register or update its stop. The current session also autosaves;
starting or opening a different project saves the register you are leaving first.

The **Projects** dialog also offers **Save a copy** to branch from your latest edits,
**Download project** for a self-contained `.loopproject` backup, and **Import project**
to continue from that backup on another computer. Imported projects become separate
working copies; use Save project to add them to the local library. Audio integrity,
format version, marker ranges and archive paths are validated before an imported
project replaces the current session.

Projects include original recordings and generated note WAVs with their exact
PCM bytes. Split decisions and markers are editable metadata. A saved split draft
reopens as you left it, including excluded regions and incomplete fields. Saving
a project does not require approving or exporting every sample. Final WAV/SFZ
exports remain a separate operation.

To change an earlier split, reopen its full source recording. Creating samples
from the revised draft adds new copies; it does not replace already prepared
notes. The source cannot be removed while its note samples remain in the project.

Saved projects live in `.workspace/projects/`; `session.json` holds the current
autosaved work. Existing sessions from older versions load automatically as an
untitled register. Project saves use temporary files and atomic replacement, so
an interrupted archive write preserves the previous saved project. Older working
audio copies may remain locally after switching projects; archives also include
audio and require additional disk space. Back up projects with Download project.

Undo history is limited to the current editing session. Detection settings,
export preferences and split drafts persist in the project. Use one server per
workspace directory. A tab with an outdated project session must refresh before
it can make changes after another tab switches registers.

## The flow

### A recording containing all notes

Use **Import → Import full recording**. The file appears in **Source recordings**,
separate from the note checklist. Choose **Split settings**, then **Find notes**.
The **Review splits** tab holds listening, editable cuts, note assignment and
manual region tools. The selected tab and source are saved with the project.
The splitter is intended for **one note at a time with pauses**.

- **Automatic threshold** measures the recording's background level so microphone
  noise during pauses does not merge the whole register into one note. The preview
  reports the measured background and threshold. Disable it to set a **Manual
  threshold**. The mode is saved with the split draft.
- **Find notes** locates quiet gaps, traces back into the quieter attack, adds
  20 ms of lead-in and an adjustable tail margin, and proposes root pitches.
  Quiet-gap duration, minimum note length and extra tail remain adjustable.
  Padding never overlaps neighboring regions. Lower manual thresholds retain
  quieter decay; a larger required gap avoids dividing brief level dips.
  Existing drafts stay as saved until you explicitly use Find notes again.
- Review the overview and **Listen** to each proposed cut. Edit start/end times
  or MIDI notes in the table. Uncheck unwanted sounds. Click the waveform to place
  a cursor, then **Divide at cursor**; **Merge with next** repairs over-splitting.
  **Add region** can cover missed audio. Draft edits autosave without creating
  note files; **Save project** is also available inside the splitter.
- For recordings made in order, use **First MIDI** and **Step** to label the kept
  regions (for example, 36 and 1 for C2 upward chromatically; negative steps work
  for descending recordings). Pitch estimates are suggestions, not guaranteed
  key assignments, especially with mixtures or mutation stops.
- **Create note samples** adds separate WAV copies and keeps the source recording.
  The new samples are selected automatically. **Detect loops after splitting**
  feeds those samples into the existing batch workflow. Listen and approve them
  before final export.

Splitting preserves sample rate, bit depth, stereo alignment and exact source PCM
frames. Stale loop/cue metadata is removed; assigned root notes are written into
each new WAV. The preparation report retains the source filename and source-frame
range. Invalid or overlapping kept regions are rejected before writing; failures
roll back the new batch. Previewing does not create sample files.

This is pause-based segmentation, not polyphonic separation: chords, overlapping
notes and reverberation that masks every gap require different recordings or
manual boundaries. Region timing does not reconstruct a tail obscured by the next
note. The existing 300 MB import / 1 GB total workspace limits also apply; creating
note copies needs additional workspace space. Source files stay available after
splitting, so use the automatically selected children for batch detection/export.

Automatic calibration uses the lower 10% of 10 ms RMS levels as an estimated
background, starts notes 8 dB above that estimate (at least −60 dBFS), and waits
for a gap at the higher of background +6 dB or trigger −6 dB. This catches
quieter pipes without lowering the pause gate into room noise. It works best when pauses occupy at
least roughly 10% of the take. Continuous playing, changing background noise or
notes close to the noise floor can need a manual threshold and boundary edits.
Calibration does not remove noise or change audio. Pitch near a semitone boundary
may remain unassigned; use the known key sequence or listen and enter the key.

### Individual samples and batches

1. Choose **Import individual notes**, or set the drop-down to **Individual notes**
   before dropping WAVs. **Explore an example rank** creates 12 clearly
   labeled synthetic stereo pipe recordings so you can try everything locally.
2. **Detect batch**, or tick files to detect a selection. Approved and manually
   edited samples are protected from batch detection. Use the inspector's
   **Detect this sample again** to explicitly replace their markers.
3. Select a row in **Loops & trim**. Audition the trimmed sample, loop, or release. Drag colored waveform
   markers or enter milliseconds. Set the root MIDI note if it wasn't found in
   existing metadata or a filename such as `060-Principal.wav` / `Principal_C4.wav`.
   Undo restores previous markers for the active sample during the current session.
4. **Approve sample** after listening. Subsequent edits remove approval. Invalid
   marker order or a missing root note prevents approval.
   For bulk approval, choose **Listening review complete** or **Testing only**,
   then **Approve selected notes** / **Approve visible notes**. Checked notes take
   precedence; without checks, the current search/filter defines the batch.
   Invalid loops and missing keys are skipped and listed with explanations.
   Testing approvals show **Test approved**;
   that status persists in saved projects and is included in export reports.
   Use **Approve after listening** to record normal individual approval.
5. **Export samples** creates a ZIP of approved files, limited to checked files
   when a selection exists. Unapproved samples are excluded.

Space plays/stops; left/right arrows select samples when focus is outside a
control. `+`/`−` zoom the waveform around the loop or playhead. Click it to seek
in trimmed-sample mode. The waveform is a peak overview, not a sample-level drawing;
the numerical marker fields retain frame precision.

Full recordings, including legacy sources identified by split drafts or child
notes, are excluded from note selection, detection, approval, export, and player
snapshots. A mistakenly imported sequence can be checked in the note list and
moved to Sources. An unsplit recording with no nonempty draft can be reclassified
as an individual note in Import. These changes never rewrite its audio.

### Manual loops, nearby refinement, and trimming

All five marker fields remain editable even before detection. Click the waveform
to place a cursor, choose a marker in **Place**, then **Set at cursor**; existing
markers can also be dragged. Attack and tail define the non-destructive trim.
**Trimmed sample** plays only that range. To carry this trim into WAV or SFZ
exports, enable **Trim outside attack and tail markers** in Export & play.

Expand **Add or refine a loop near chosen points** for a separate proposed start
and end, in milliseconds. Copy either from the waveform cursor, enter them, or
use the current loop / a suggested range. **Use these exact points** saves that
pair, including when no loop has been detected. **Find best nearby seam** searches
within ±1–250 ms of **each** proposed endpoint (20 ms by default), respecting
attack, release, and the minimum loop length in Detection settings. Suggestions
compare both channels and balance seam quality with distance from your points.
Search is read-only. **Try & listen** applies the selected pair; Undo restores
the previous markers. Edits remove approval. The broader **Find alternative
loops** search remains available for exploring the entire sustain.

Review splits also offers **Zoom to selected region** and **Whole recording**.
Typing cut boundaries, assigning keys, adding, dividing and merging regions
remain available without automatic detection. Creating a revised draft adds new
note copies, with an explicit **Create new copies** label when copies already exist.

If a loop sounds wrong, use **Find alternative loops** in the Sustain loop panel.
Choose one of up to five different seams and click **Try & listen**. This saves
only the two loop markers and starts loop playback. **Undo** restores the previous
markers; trying an alternative removes approval. Attack, release, tail and MIDI
key stay as set. The search respects these boundaries and the minimum loop length
in Detection settings. If no alternatives fit, adjust those bounds or the minimum
length. Suggestions compare both channels around zero crossings; scores measure
seam similarity, so audition movement and repetition as well as clicks.

## Export formats

| Format | Output |
| --- | --- |
| Sampler-ready WAV | Original WAV audio with one forward `smpl` loop, root pitch, and a release `cue ` point. Intended for Hauptwerk / GrandOrgue sample preparation. |
| Separate attack & release | Attack + looped sustain through loop end; separate recorded release from the release marker through tail end. |
| SFZ + WAV | One region at each root key with explicit loop endpoints. Uses a 0.5-second envelope release; does not trigger the recorded release tail. |

Every ZIP contains `preparation.json` and notes. Full WAV export preserves source
PCM bytes, sample rate, bit depth, and unrelated metadata. The audio is not
normalized, resampled, faded, or crossfaded. Trimming is opt-in and copies exact
frame ranges; it drops unrelated metadata whose positions could become invalid.
Split export always cuts to its marked ranges. Old cue labels are removed when
replacing cues. Export replaces existing loops/cues with the single reviewed
loop and release cue shown in this workspace; use the classic editor to retain
or edit multiple loops and cues. Existing pitch fraction is preserved.

All export modes use Hauptwerk-style filenames based on the assigned MIDI key:
`036-C.wav`, `037-C#.wav`, …, `060-C.wav` (three-digit MIDI number, sharp pitch
class, no octave suffix). WAV and SFZ exports put them in `samples/`; split exports
use matching names such as `attack/036-C.wav` and `release/036-C.wav`. SFZ paths
follow the renamed files. `preparation.json` keeps original names and provenance
and lists each sample's `exportedFiles`. Project and imported filenames stay intact.
Select one approved take per MIDI key; duplicate keys block export with an error
identifying both samples. Export separate registers separately. Set the intended
keyboard key before approving, especially for mutation stops.
This naming follows the [Hauptwerk Custom Organ Design Module guide](https://www.hauptwerk.com/wp-content/uploads/dlm_uploads/2023/08/HauptwerkCustomOrganDesignModuleUserGuide.pdf).

Internally and in the report, markers are **source sample frames**, with
**exclusive** loop and tail ends. Export converts the WAV `smpl` and SFZ loop
end to **inclusive** offsets. Both stereo channels always use identical cuts.

## Detection and limits

- RMS envelope estimates attack, final decay onset, and tail. Release onset is
  approximate: noisy rooms, tremulants, percussive sounds and changing dynamics
  need manual review. It is never automatically approved.
- Loop detection compares windows around ascending zero crossings in the
  strongest channel, scoring the seam across **both** channels. Candidates are
  bounded to keep processing predictable. A seam score is a similarity metric,
  not a guarantee of an inaudible loop. No crossfade is applied.
- Valid existing forward loop and release metadata are retained during detection.
- Sounding pitch is estimated independently even when a root MIDI key is already
  assigned. The inspector shows both; 16′ and 4′ stops intentionally differ from
  their keyboard keys. Pitch confidence measures periodic agreement, not certainty.
- Supports mono/stereo RIFF WAV: PCM 8/16/24/32-bit and float 32-bit, including
  corresponding WAVE_FORMAT_EXTENSIBLE files. 8–384 kHz. Compressed WAV, RF64,
  surround, non-finite float audio and unusual valid-bit packing are rejected.
- 300 MB per file, 500 samples and 1 GB of original WAVs per local workspace.
  Import/detection proceed one file at a time and report failures per file.
- Browser listening uses a 16-bit proxy and the browser's audio output rate.
  Exports always use the original PCM. The preview is for musical review, not
  bit-perfect output measurement.
- No organ definition/package generation, multi-loop editing,
  crossfade processing or native C++ engine integration yet. SFZ maps only
  recorded keys; key ranges, velocity layers and release-trigger regions need
  further instrument authoring.

Files and edits persist in `.workspace/` (git-ignored). The server binds only to
127.0.0.1. No external services, fonts, telemetry or uploads are used. “Remove”
deletes only imported workspace copies and their edits. Originals outside the
workspace are never touched. Back up `.workspace/` to preserve the local library,
or use **Download project** for a portable register backup.

## Validation

```powershell
python -m unittest -v
node --check static/app.js
node --check static/split.js
node --check static/projects.js
node --check static/flow.js
node --check static/project-player.js
node --check static/player.js
node --check static/player-engine.js
node --test test_player.cjs
```

Tests cover PCM preservation across bit depths, inclusive/exclusive endpoints,
trim offsets, split files, stereo phase cancellation, silence and malformed WAVs,
SFZ packaging, approval gates, persistence, and local HTTP origin restrictions.
Note-splitting tests cover repeated pitches, room-noise calibration, manual mode,
quiet notes, recordings without a silent intro, noise alone, anti-phase stereo,
pitch suggestions across octaves, padding, invalid splits, exact PCM cuts,
cropped audition and transaction rollback.
Project tests cover save/reopen, portable restoration, legacy session migration,
split draft persistence, independent project copies, archive integrity checks,
failed-write recovery and protection against stale tabs editing another project.
Real Hauptwerk/GrandOrgue instrument loading is not tested here.

Hauptwerk sample requirements: [Custom Organ Design Module guide](https://www.hauptwerk.com/wp-content/uploads/dlm_uploads/2023/08/HauptwerkCustomOrganDesignModuleUserGuide.pdf).
The parent repository's GPL-3.0 license applies.

Newly split notes are not analyzed automatically unless **Also detect loops now**
is checked. In Loops & trim, the pending-detection banner offers **Detect
unanalyzed notes**. **Not analyzed** means detection has not run; **No loop found**
means it ran but the sample needs a different range, minimum length, or manual
loop. Detection reports the number of actual loops found.
