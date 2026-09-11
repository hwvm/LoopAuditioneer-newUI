[![Upstream release](https://img.shields.io/github/v/release/GrandOrgue/LoopAuditioneer)](https://github.com/GrandOrgue/LoopAuditioneer/releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

# Gui on top of all that of below - no credits apart from GUI :)

# LoopAuditioneer

LoopAuditioneer is a software for evaluating loops and cues existing in wav 
file metadata as well as generally being helpful in sample preparation for
usage in virtual pipe organs, like the
[GrandOrgue software](https://github.com/GrandOrgue/grandorgue).

## About this fork

This repository builds on [GrandOrgue/LoopAuditioneer](https://github.com/GrandOrgue/LoopAuditioneer),
created by Lars Palo and its contributors. The original desktop application,
credits, and build instructions are retained below. The release badge above
links to the upstream project's releases.

This fork adds **LoopAuditioneer Workspace** in [`modern-ui/`](modern-ui/README.md):
a local browser interface for preparing batches of samples for virtual organs
and other samplers. It currently runs as a standalone Python application with
its own audio analysis; it is not yet connected to the classic C++ engine.

| Application | How to run |
| --- | --- |
| Modern workspace — Python + browser | Follow the quick start below. No C++ build is needed. |
| Original desktop editor — C++ + wxWidgets | Follow [BUILD.md](BUILD.md), including native dependencies and submodules. |

## Run the modern workspace

### Requirements

- Git, to clone the repository.
- Python **3.10 or newer**, with `pip` and `venv` available.
- A current browser with Web Audio support, such as Chrome, Edge, or Firefox.

NumPy is the only Python dependency. Node.js is optional for JavaScript syntax
checks; no npm install, frontend build, account, or hosted service is needed.

### Clone the repository

```sh
git clone https://github.com/hwvm/LoopAuditioneer-newUI.git
cd LoopAuditioneer-newUI/modern-ui
```

The workspace does not require the C++ submodules. To also build the original
desktop application, run `git submodule update --init --recursive` from the
repository root and follow [BUILD.md](BUILD.md).

### Windows / PowerShell

From the `modern-ui` directory, create an isolated environment and start the app:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

These commands do not require activating the environment or changing PowerShell's
execution policy. If Windows provides the Python launcher instead of a `python`
command, use `py -3 -m venv .venv` for the first command.

Alternatively, with Python on PATH, double-click
[`modern-ui/start-workspace.cmd`](modern-ui/start-workspace.cmd). This shortcut
uses Python on PATH and installs NumPy into that environment if it is missing.

### macOS / Linux

From the `modern-ui` directory:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python server.py
```

If creating the environment fails, install your operating system's Python `venv`
and `pip` support, then retry.

### Open, stop, and restart

The app opens your default browser at **http://127.0.0.1:8765**. You can also open
that address manually. Keep the server terminal running while using the app;
press **Ctrl+C** in that terminal to stop it.

For later launches, run only the final `server.py` command for your platform.
After updating the code, stop the server, install the requirements again if they
changed, restart it, and refresh the browser.

The server also accepts these options:

| Option | Purpose |
| --- | --- |
| `--port 8766` | Use another port if 8765 is already occupied. Open the address printed by the server. |
| `--no-browser` | Start without automatically opening a browser. |
| `--workspace "path/to/session"` | Use a different directory for local recordings and saved edits. |

Append these options to the `server.py` command. Run one server per workspace
directory to avoid competing writes to the same session.

## Play the recordings from a MIDI keyboard

The workspace now includes a **Mini organ player** in its sidebar. Layer one or
more stops, adjust their levels, and play with a MIDI keyboard, touch, or typing
keys. Prepared register snapshots stay available when you switch editor projects.
Click **Start audio**, then **Connect MIDI keyboard** and enable the stops.
See the [player guide](modern-ui/README.md#browser-mini-organ) for MIDI access and playback limits.

For the Academie Bourdon 16, Bourdon 8, and Prestant 4 recordings, run from `modern-ui`:

```powershell
python prepare_academie.py --source "D:\Audio\0_Academie"
python server.py --workspace .workspace/academie --port 8766 --player
```

This creates a separate workspace with 142 draft pipes and three saved projects.
The player opens at **http://127.0.0.1:8766/player**. On later launches, use only
the second command or **start-mini-player.cmd**. Preparation refuses existing
destination sessions. See the [recording audit](modern-ui/ACADEMIE-AUDIT.md) for
pitch-review flags and testing results. Originals are preserved.

## Save and continue register projects

Use one project per register (stop), starting with its full recording or a batch
of individual samples. Name it in the **Register project** field at the top.

- **Save project** (or **Ctrl+S / Cmd+S**) saves the audio, split drafts, note
  markers, pitches, approvals, detection/export settings, and current selection.
- **New** starts another register and saves the current one first. **Projects** lists
  saved projects and lets you continue a register later.
- **Projects → Save a copy** creates an independent alternative, preserving the
  original project's latest progress.
- **Projects → Download project** creates a portable `.loopproject` file containing
  the recordings and edits. **Import project** opens that file as a separate
  working copy on this or another computer.

Current edits autosave locally, including unfinished split drafts. Reopening the
splitter restores its saved regions instead of detecting them again. **Save project**
also creates an explicit saved project in the local library. The Projects dialog shows source/note counts, approvals, and player update status. Switching projects
saves the current register first; invalid project imports leave it unchanged.

The full recording is retained while its note samples exist. Splits create new
copies, marker edits leave their PCM untouched, and final instrument exports are
separate from project saves. You can return to the full recording and revise its
split draft; creating samples from that revised draft adds a new set of copies.
Existing note samples are not automatically replaced.

Projects and the working session live under `modern-ui/.workspace/`; saved
archives are in its `projects/` directory. These files and `.loopproject` downloads
are ignored by Git. Each saved project includes audio, so allow additional disk
space and keep portable backups somewhere separate from the working directory.

### Iterate with the player

Use **Update player** beside the project header, then **Play**. This saves the
project and updates its linked stop. Repeat after editing loops or trims; the
player refreshes on return and keeps drawn stops and levels. Unchanged pipes
reuse their decoded audio. **Refresh instrument** provides a manual refresh.

In **Export & play**, set the stop name and keyboard mapping. **Notes for the
player** can include all playable notes, approved notes, or checked takes. For
multiple takes of the same key, choose Checked notes and check one take per key
in Loops & trim before updating. The player remembers that project selection.
Draft pipes remain labeled and do not require approval to audition.

The player can add or update any saved register project without switching the
editor's active project. **Edit project** on a stop returns to its source project.
Older independent snapshots offer **Link a project**. A failed update preserves
the existing playable version. Saved projects and the player library belong to
the workspace directory selected at server startup; separate workspace directories
remain separate libraries.

## Prepare a batch

Use the five tabs; you can return to any stage without losing its saved draft.

1. **Import** a full recording or individual notes. Full sequences have their own
   Source recordings list and never enter the note checkbox list or batch actions.
2. **Split**: choose a source and use **Find notes** with automatic or manual noise
   thresholds. Opening the tab restores drafts without rerunning detection.
3. **Review splits**: listen, zoom to a region, adjust cut times and keys, and keep
   or exclude sounds. Add, divide, or merge regions manually. **Create note samples**
   advances to the loop editor. Unless **Also detect loops now** is checked, use
   **Detect loops** there; newly split notes are labeled **Not analyzed**.
   Repeating creation adds new copies.
4. **Loops & trim**: detect notes, or place attack, loop, release, and tail markers
   manually with the fields or waveform cursor. **Add or refine a loop near chosen
   points** searches within a chosen distance of both proposed endpoints. Search
   leaves markers untouched; **Try & listen** applies a result and Undo restores it.
   **Use these exact points** can create a loop even when detection found none.
   **Trimmed sample** auditions only the attack-to-tail range.
5. **Export & play**: export approved notes as marked WAVs, separate attack/release
   WAVs, or SFZ; enable the trim option to export the trimmed range. The mini player
   can combine snapshots of multiple stops.
   Exported notes use Hauptwerk names such as `036-C.wav` and `037-C#.wav`, based
   on the assigned MIDI key. Select one take per key; attack and release folders
   use matching names. Original filenames remain in the preparation report.

In **Loops & trim**, choose **Listening review complete** or **Testing only** and
use bulk approval. It applies to checked notes, or the visible filtered list when
nothing is checked. Invalid loops and missing keys are skipped with explanations.
Test approval stays distinct from listening review. Batch detection protects
approved and manually edited notes. Demo material is in [media/demo-01-youtube.md](media/demo-01-youtube.md).

Imported source recordings remain intact. Edits and imported copies persist in
`modern-ui/.workspace/`, which is ignored by Git. Back up that directory to keep
your local sessions. Audio processing stays on your computer.

The workspace currently supports mono/stereo PCM and 32-bit float WAVs, with a
300 MB per-file limit and a 1 GB / 500-sample workspace limit. Note splitting
creates additional copies within that budget. Detection results require listening
and review; exported samples still need instrument authoring and verification in
the target sampler. The workspace does not generate Hauptwerk or GrandOrgue organ
definitions, and its simple SFZ mapping uses an envelope release rather than
recorded release triggers.

See the [workspace guide](modern-ui/README.md) for editing controls, export details,
supported formats, and current limitations.

## Check the workspace

Run the tests from `modern-ui`, using the environment created above:

```powershell
# Windows
.\.venv\Scripts\python.exe -m unittest -v
```

```sh
# macOS / Linux
./.venv/bin/python -m unittest -v
```

If Node.js is installed, also check the browser scripts:

```sh
node --check static/app.js
node --check static/split.js
node --check static/projects.js
node --check static/flow.js
node --check static/project-player.js
node --check static/player.js
node --check static/player-engine.js
node --test test_player.cjs
```

## Features

The following features describe the original LoopAuditioneer desktop application.

- Load .wav sample files and display existing cue and smpl metadata
- See the waveform with loops and cues drawn upon it
- Zoom in/out on amplitude of the waveform
- Play back the loops and cues for aural evaluation
- Choose which loops/cues to keep when saved or saved as...
- Enjoy bit true data handling, even if header is re-written due to changes,
  there will be no degrading of the audio data unless actual changes are made
  to the audio data itself
- Edit/create cue (release marker) position directly on the waveform with
  automatic adjustment to find the position with lowest RMS power closest to
  where the user clicked
- Edit/create new loops manually
- Autosearch for good, natural loop points with a high degree of
  configurational control over the loopsearching process
- Perform crossfades of loops when it's difficult to find natural seamless ones
- View the waveform at the looppoints in detail
- Batch process all .wav files in any source directory with freely selectable
  target directory to either overwrite the existing files or create copies of
  them
- Autodetect pitch of sample and store information in file
- Edit dwMIDIUnityNote and dwMIDIPitchFraction
- View the FFT power spectrum and set pitch from chosen peak
- Perform cut and fade in/out both in single file mode and in batch mode
- Trim away unused wav data from looped samples in batch mode
- Export audio data from cue marker as separate release in batch mode
- Export audio data to after last loop as separate attack in batch mode

## Credit to others code

The only code in the src/ directory that's not written by the author is the
FFT.h and FFT.cpp files that are slightly modified versions of those in Audacity
source code. The pitch detection algorithm for the time domain is taken and
adapted from a discussion on the GrandOrgue mailing list.

Other external libraries that LoopAuditioneer is dependant on and use as
submodules are [Libsndfile](https://github.com/GrandOrgue/libsndfile) and
[Libsamplerate](https://github.com/libsndfile/libsamplerate) both originally
written by Erik de Castro Lopo. Libsndfile, is provided from a fork of the
official version to allow exact reading and writing of pitch fraction
metadata information. [RtAudio](https://github.com/thestk/rtaudio), by Gary P.
Scavone is used for audio output.

## Graphical credits

In the resources/free-pixel-icons/ directory resides the icons used by the
program for the toolbar. The complete set is available at 
http://www.small-icons.com/packs/24x24-free-pixel-icons.htm and distributed
under a Creative Commons 3.0 license. The icons in resources/icons are created
by the author (Lars Palo) except for the macOS adaptation which was done by
[vpoguru](https://github.com/vpoguru).

## Building

Basic compilation instructions are available in the BUILD.md file. At the
moment the program is developed and mostly tested under Linux (Ubuntu 24.04) 64
bit. The Windows binaries are produced by cross-compilation with
x86_64-w64-mingw32. Since june 2024 builds for macOS, both Intel and arm64 are
also available. All releases of this software here on Github are created on
action runners on Github, Ubuntu 22.04 except for macOS builds.

LoopAuditioneer requires wxWidgets 3.0+, available at http://www.wxwidgets.org/.
The repository unicode version of wxWidgets that Ubuntu offer should work as
well. Other build dependencies are documented in the BUILD.md file. Both macOS
and Windows builds have wxWidgets included in them which means that they need
nothing extra installed in order to run.

## Help and documentation

At https://loopauditioneer.sourceforge.io/userguide.html older official
documentation on how to use the software can be found. Inside the software the
help can be consulted for additional insights.

## License and attribution

The original application and the workspace additions are distributed under the
repository's [GNU GPL v3 license](LICENSE.txt). See [AUTHORS](AUTHORS) for the
original author and contributor credits. Existing copyright notices and the
third-party credits above are retained.
