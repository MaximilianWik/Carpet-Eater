# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Fixed
- **Crash on finishing a chew.** The QThread cleanup chain queued `worker.deleteLater()` on a worker thread whose event loop had already stopped (`thread.finished -> worker.deleteLater`). The deletion event was never processed, leaving a dangling QObject that occasionally faulted on later signal dispatch. Switched to the Qt-recommended pattern: `worker.finished` / `worker.failed` -> `worker.deleteLater` (runs while the loop is still pumping events), `thread.finished` -> `thread.deleteLater` (reaps the thread after it stops).
- `start_processor()` now wires the window's `on_finished` / `on_failed` slots *before* calling `thread.start()`, removing the race window where a fast worker could emit before the caller had connected.
- `closeEvent` defends against the QThread C++ object having already been reaped via `deleteLater` while the Python wrapper is still live (would otherwise raise `RuntimeError: Internal C++ object already deleted`).

### Added
- **Crash log.** A global `sys.excepthook`, `sys.unraisablehook`, and Qt message handler are installed in `__main__.py`. Any unhandled Python exception, unraisable error, or Qt warning/critical/fatal is appended to `%LOCALAPPDATA%\CarpetEater\crash.log` with a timestamp, so future crashes leave evidence even when the app is launched via `pythonw.exe` or the bundled EXE (no console).

### Performance
- **~17x DSP speedup.** A 3:49 audio file now chews in ~6 s instead of ~106 s.
  - `stage_comb` rewritten as a vectorized geometric expansion (`y[i] = x[i] + fb*y[i-D]` -> sum of `fb^k * shift(x, k*D)` until `fb^k` is below threshold). Killed the Python sample-by-sample loop.
  - `stage_reverse_smear` rewritten using `np.convolve` against a truncated geometric kernel for the one-pole low-pass. Same audible result, no scipy dependency.

### Added
- **Multiple DSP chains, picked at random per file.** `audio_fx.CHAINS` registry with five named chains:
  - `standard_mauling` - the original 9-stage chain
  - `wet_slobber` - dark, drowning, heavy reverse-smear, less granular destruction
  - `bone_grinder` - metallic and abrasive, heavy comb + clip, no smear
  - `pulper` - granular shuffle dominant, lots of stutter
  - `stomach_acid` - extreme bitcrush + sample-rate reduction, maximum digital decay
- `chew()` now returns `(chain_name, audio)` so callers can know which chain was used. Chain selection is deterministic per seed and uses an independent sub-stream from the DSP randomness.
- `--chain NAME` and `--list-chains` flags on the CLI.
- **Filename escalation - `carpeteater/naming.py`.**
  - First chew: `song.mp3` -> `song_chewed - tasted NN WORD!!!.wav`
  - Re-chew: `song_chewed.wav` -> `song_incestuous_chewed - tasted NN WORD!!!.wav`
  - Further re-chews stack adjectives: `song_incestuous_putrid_chewed`, `song_putrid_incestuous_necrotic_chewed`, etc.
  - Adjective pool: 20 visceral words; the picker avoids ones already present in the stem.
  - Tasting note pool: ~38 words including a few rare easter eggs ("DELICIOUS", "EXQUISITE").
  - Any prior `" - tasted NN WORD!!!"` suffix is stripped before re-tasting, so the suffix only ever describes the most recent chew.
  - Naming uses an independent sub-seed - changing DSP randomness will not change the filename.
- **Installer + CI release pipeline.**
  - `installer/carpeteater.iss` - Inno Setup script for a per-user installer (`%LOCALAPPDATA%\Programs\CarpetEater`, no admin required, sidesteps AppLocker on locked-down machines). Creates Start Menu entry, optional desktop shortcut, and uninstaller.
  - `.github/workflows/release.yml` - GitHub Actions pipeline that builds the EXE + installer + portable ZIP on every push:
    - Push to `main` updates a rolling `rolling` prerelease (always the latest commit)
    - Push of a `v*` tag creates a numbered GitHub Release marked as *latest*
    - Pull requests build artifacts only, no release published
    - Workflow caches ffmpeg between runs; uses `softprops/action-gh-release@v2`
  - Permanent download URLs via GitHub's `/releases/latest/download/<filename>` redirect, so external sites can link without ever updating the URL.

### Changed
- `processor.py` derives three independent sub-streams from the file seed (chain selection, DSP randomness, naming) via `numpy.random.SeedSequence`, so swapping any one will not perturb the others.
- `AudioProcessor.chain_name` exposes which chain was used after `run()` succeeds (for future UI hooks).
- `README.md` - added a Download table at the top with installer / portable / rolling links; new *Installer* and *Releases* subsections under Build with `iscc` instructions and the tag-and-push release workflow.

---

## [0.1.0] - 2026-06-01

Initial release. Full project built from scratch.

### Added

#### Project skeleton
- `requirements.txt` - PySide6, numpy, soundfile
- `.gitignore` - venv, build artifacts, vendor binaries, test outputs
- `vendor/` directory for bundled `ffmpeg.exe` (not tracked in git)
- `scratch/` directory for dev testing (not tracked in git)

#### GUI - `carpeteater/window.py`
- Frameless, transparent, always-on-top mouth window via `FramelessWindowHint` + `WA_TranslucentBackground`
- Sprite rendered via `paintEvent`; transparent corners are fully see-through
- Left-click drag to move the window anywhere on the desktop (no title bar)
- Ctrl + mouse wheel to resize between 160 px and 900 px (default 400 px)
- Double-click opens the last chewed output in Windows Explorer with file selected
- Right-click context menu: *Always on top* (toggle), *Open last output folder*, *Quit*
- Spit jitter: 8-tick random window shake (+/- 6 px, 40 ms per tick) on SPITTING state
- Error flash: red overlay fades out over ~500 ms on bad drop or decode failure
- Tooltip shows "Drop an audio file to feed me" at idle, filename during chew, output name after spit
- Window icon set from `closed.png`
- Graceful `closeEvent` waits up to 2 s for any in-flight worker thread before quitting

#### Animator - `carpeteater/animator.py`
- `MouthState` enum: `IDLE`, `DRAG_OVER`, `CHEWING`, `SPITTING`, `ERROR`
- `MouthAnimator` QObject drives sprite cycling via `QTimer`
- Chew frames alternate at 180 ms +/- 40 ms random jitter for organic feel
- SPITTING state auto-returns to IDLE after 450 ms
- Emits `frame_changed(str)` and `state_changed(MouthState)` signals

#### Drop handling - `carpeteater/window.py`
- `setAcceptDrops(True)` - window is a standard Qt drop target
- `dragEnterEvent`: accepts if payload contains a local file with an audio extension; switches to `DRAG_OVER`; rejects silently while chewing (mouth full)
- `dragLeaveEvent`: reverts to `IDLE`
- `dropEvent`: takes the first valid audio file, ignores the rest, starts chew
- Accepted extensions: `.mp3 .wav .flac .ogg .oga .m4a .aac .opus .wma .aiff .aif .alac`
- Non-audio drops snap the mouth closed with a red error flash

#### Audio decode and I/O - `carpeteater/processor.py`
- `decode_to_numpy()`: spawns bundled `ffmpeg.exe` with `-f f32le` raw PCM piped to stdout; reads into a `numpy.float32` array shaped `(N, 2)` stereo at 44.1 kHz
- `write_wav()`: clips to `[-1, 1]` then writes 16-bit PCM WAV at 44.1 kHz stereo via soundfile
- `output_path_for()`: places output at `<input_dir>/<stem>_chewed.wav`
- `seed_for_file()`: SHA-1 hash of path + size + mtime collapsed to 32-bit int; same file always produces the same chewed output
- `AudioProcessor` QObject: runs decode -> DSP -> write on a worker thread; emits `finished(Path)` or `failed(str)`
- `start_processor()`: constructs `AudioProcessor` + `QThread`, wires lifecycle (quit/deleteLater on finish/fail), starts thread
- Minimum 2-second theatrical chew enforced in the window regardless of actual processing time; both the timer and the worker must complete before the spit state fires

#### DSP chain - `carpeteater/audio_fx.py`
Nine stages applied in order. All pure numpy - no scipy, no audio framework. Deterministic per seed.

1. **Pitch + speed mangle** - linear-interpolation resample at `2^(semitones/12)` ratio; random shift -7 to +5 semitones; no formant correction (chipmunk/demon artifacts depending on direction)
2. **Granular shuffle** - 50-200 ms grains; 20% of positions randomly swapped with another; 10% of grains time-reversed; 3 ms crossfade at grain boundaries to suppress clicks
3. **Bitcrush** - quantize to 4, 5, or 6 bits (random per file)
4. **Sample-rate reduction** - sample-and-hold decimation to 8-11 kHz (random), held back to 44.1 kHz; aliasing folds back into the audible range
5. **Hard-clip waveshaper** - +18 dB drive into `tanh`; normalize to -0.5 dBFS
6. **Ring modulator** - multiply by sine at 30-120 Hz (random); 30% wet mix
7. **Comb-filter resonance** - feedback comb `y[n] = x[n] + 0.6 * y[n-D]`; delay 2-15 ms (random); metallic ringing
8. **Reverse-tail smear reverb** - reverse, one-pole low-pass (coeff 0.92), reverse back, 40 ms pre-delay, 25% wet mix
9. **Random dropouts** - 20-80 ms mutes at ~2 Hz average rate; 64-sample fade-in/out edges to suppress clicks

Final normalize to -0.5 dBFS before write.

#### CLI test harness - `carpeteater/audio_fx.py`
- `python -m carpeteater.audio_fx <input> <output> [--seed N]`
- Runs the full DSP chain without the GUI; useful for tuning individual stages
- `--seed` overrides the file-hash-derived seed for reproducible comparison between runs

#### Path resolution - `carpeteater/resources.py`
- `sprite_path(name)`: resolves `public/<name>` relative to `sys._MEIPASS` in frozen builds, repo root in dev
- `ffmpeg_path()`: resolves `vendor/ffmpeg.exe` the same way; falls back to PATH lookup in dev
- `open_in_explorer(path)`: opens the parent folder in Windows Explorer with the file selected (`explorer /select,"..."`)

#### Packaging
- `build.spec` - PyInstaller onefile spec; bundles `vendor/ffmpeg.exe` + all four `public/*.png`; excludes unused PySide6 modules (QtNetwork, QtQml, QtQuick, QtWebEngine, QtMultimedia, QtPdf, Qt3D, QtCharts, QtSql, QtTest)
- `make_icon.py` - generates `build_icon.ico` at 7 sizes (16, 24, 32, 48, 64, 128, 256 px) from `public/closed.png` using Qt only; no Pillow dependency
- `build.bat` - one-click build: creates/updates venv, installs deps + PyInstaller, generates icon, cleans previous build, runs spec; output is `dist/CarpetEater.exe` (~80 MB)
- ffmpeg 8.1.1 essentials build (gyan.dev) placed in `vendor/`

#### Documentation
- `README.md` - artist attribution and links, stack table, project layout, behaviour state machine diagram, window mechanics, drop handling reference, I/O and decode internals, all 9 DSP stages with parameters, CLI usage, run-from-source guide, build instructions

#### Desktop shortcut
- `.lnk` shortcut created on the Windows desktop
- Points to `pythonw.exe -m carpeteater` via the project venv (no console window) to work around corporate AppLocker policy that blocks unsigned executables
- Icon set from `build_icon.ico`

---

[0.1.0]: https://github.com/MaximilianWik/carpet-eater/releases/tag/v0.1.0
