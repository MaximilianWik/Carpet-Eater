# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
