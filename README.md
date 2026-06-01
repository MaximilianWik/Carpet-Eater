# Carpet Eater

A desktop tool made for the artist **Carpet Eater**.

Drop an audio file into the open mouth. It chews. A horrifyingly distorted version appears next to the original.

> Instagram: [@i.am.carpet.eater](https://www.instagram.com/i.am.carpet.eater/)
> SoundCloud: [carpet_eater](https://soundcloud.com/carpet_eater)

---

## Download

| | Link | What it is |
|---|---|---|
| **Installer** | [CarpetEater-Setup.exe](https://github.com/MaximilianWik/Carpet-Eater/releases/latest/download/CarpetEater-Setup.exe) | Per-user install (no admin). Adds Start Menu shortcut + uninstaller. |
| **Portable** | [CarpetEater-Portable.zip](https://github.com/MaximilianWik/Carpet-Eater/releases/latest/download/CarpetEater-Portable.zip) | Single EXE, no install. Drop anywhere and run. |
| **Latest dev** | [Rolling main build](https://github.com/MaximilianWik/Carpet-Eater/releases/tag/rolling) | Auto-built from the latest commit on `main`. |

Both downloads are unsigned, so Windows SmartScreen will show *"Windows protected your PC"*. Click **More info → Run anyway**. Once.

---

## What it is

A frameless, transparent, mouth-shaped window that sits on top of your desktop. Drag any audio file onto it — the mouth opens, chews, and spits the file back out as `<name>_chewed.wav` in the same folder. The distortion is deterministic: the same file always produces the same output, as if the mouth has formed an opinion about it.

---

## Stack

| Layer | Library | Why |
|---|---|---|
| GUI | PySide6 (Qt 6) | True alpha-channel-shaped windows, drag-and-drop, threading, timers. LGPL — fine for distribution. |
| Audio I/O | soundfile | Fast WAV/FLAC read-write via libsndfile. No dependency hell. |
| Decode | ffmpeg (bundled binary) | Reads anything: mp3, m4a, aac, opus, ogg, wma, flac, wav, aiff. Piped to stdout as raw f32le PCM. |
| DSP | numpy | All processing is pure numpy. No scipy, no audio framework. |
| Packaging | PyInstaller | Single-file `.exe`. ffmpeg + sprites bundled at build time. |

---

## Project layout

```
Carpet Eater/
├── carpeteater/
│   ├── __init__.py
│   ├── __main__.py       — entry point (python -m carpeteater)
│   ├── window.py         — MouthWindow: frameless, transparent, draggable, drop target
│   ├── animator.py       — sprite state machine + QTimer cycling
│   ├── processor.py      — QThread worker: decode → DSP → write
│   ├── audio_fx.py       — 9-stage numpy DSP chain (also a CLI tool)
│   └── resources.py      — path resolution (handles PyInstaller _MEIPASS)
├── public/
│   ├── closed.png        — idle state
│   ├── open.png          — drag-over and spit
│   ├── chew1.png         — chew frame A
│   └── chew2.png         — chew frame B
├── vendor/
│   └── ffmpeg.exe        — bundled at build time (not in git)
├── requirements.txt
├── build.spec            — PyInstaller spec
├── build.bat             — one-click build
├── make_icon.py          — generates build_icon.ico from closed.png (no Pillow needed)
└── README.md
```

---

## Behaviour

The mouth runs a simple state machine:

```
IDLE        closed.png
  │
  │ audio file hovers
  ▼
DRAG_OVER   open.png
  │
  │ file dropped
  ▼
CHEWING     chew1.png ↔ chew2.png  (alternating, ~180 ms per frame ± 40 ms random jitter)
  │
  │ DSP done + minimum 2-second theatrical chew elapsed
  ▼
SPITTING    open.png + window shake (jitter, 8 ticks × 40 ms)
  │
  │ 450 ms
  ▼
IDLE
```

If something goes wrong (bad file, ffmpeg not found) the mouth snaps closed and flashes red.

**Chew duration** is floored at 2 seconds regardless of audio length. For typical files the DSP finishes faster than that; for very long files the chew runs until processing completes. The UI stays responsive throughout — all processing runs on a worker thread.

---

## Window mechanics

- Frameless, transparent: `FramelessWindowHint` + `WA_TranslucentBackground`. Transparent corners are fully clickthrough; the visible sprite area acts as the window.
- **Always-on-top** by default, so the mouth floats above whatever you're working on.
- **Drag to move**: left-click and drag anywhere on the sprite.
- **Resize**: Ctrl + mouse wheel scales the window from 160 px to 900 px. Default is 400 px.
- **Double-click**: opens the folder of the last chewed file in Windows Explorer with the file selected.
- **Right-click** opens a context menu:
  - *Always on top* (toggle, checked by default)
  - *Open last output folder* (grayed out until a file has been chewed)
  - *Quit*

---

## Drop handling

`setAcceptDrops(True)` — the window is a standard Qt drop target.

| Event | Behaviour |
|---|---|
| `dragEnterEvent` | Accepts if the payload contains at least one local file with an audio extension. Switches to DRAG_OVER state. Rejects (mouth stays closed) if there is no audio file or if a chew is already in progress (mouth full). |
| `dragLeaveEvent` | Reverts to IDLE. |
| `dropEvent` | Takes the first audio file from the drop, starts the chew. Ignores any additional files in the same drop. |

Accepted extensions: `.mp3 .wav .flac .ogg .oga .m4a .aac .opus .wma .aiff .aif .alac`

Non-audio drops snap the mouth closed with a red flash. There is no queue — drops are ignored while chewing.

---

## I/O and decode

### Decode (any format → numpy)

The bundled `ffmpeg.exe` decodes the input to raw 32-bit floating-point PCM, piped directly to stdout:

```
ffmpeg -hide_banner -loglevel error
       -i <input>
       -ac 2 -ar 44100
       -f f32le
       -
```

`stdout` is read into a `numpy.float32` array and reshaped to `(N, 2)` stereo. This handles every format ffmpeg can read — mp3, aac, opus, ogg vorbis, flac, wav, aiff, wma, m4a — without any Python audio library needing to understand the format.

### Output format

Output is written by `soundfile` as **16-bit PCM WAV at 44.1 kHz stereo**, next to the input file:

```
<input_dir>/<input_stem>_chewed.wav
```

WAV was chosen over mp3/ogg for universality — it can be imported into any DAW, sample editor, or mastering chain without re-encoding.

### Determinism

Every chew is seeded by a SHA-1 hash of the input file's path + size + mtime, collapsed to a 32-bit integer. The same file always produces the same mangled output. Drop it twice and you get exactly the same result.

---

## DSP chain

Nine stages applied in order. All pure numpy — no scipy, no librosa, no audio framework. Each stage is independently tunable.

### 1. Pitch + speed mangle

Random semitone shift in the range **−7 to +5 semitones**, skewed slightly downward for a demon-leaning timbre. Implemented by resampling with linear interpolation (`ratio = 2^(semitones/12)`). No formant correction — pitch and playback speed are coupled, giving chipmunk and demon artifacts depending on direction.

### 2. Granular shuffle

Slices the audio into grains of **50–200 ms** (randomly chosen). Approximately **20%** of grain positions are swapped with a random other position. An additional **10%** of grains are time-reversed. A short 3 ms crossfade is applied at each grain boundary to suppress clicks. The result is an unsettling scrambling of the phrase structure while preserving local timbre.

### 3. Bitcrush

Quantizes the signal to **4, 5, or 6 bits** (randomly chosen). At 4 bits the quantization noise floor is about −24 dBFS, which creates thick, gritty artifacts without completely destroying intelligibility.

### 4. Sample-rate reduction

Sample-and-hold decimation to a target sample rate of **8–11 kHz** (randomly chosen), then held back to 44.1 kHz. Unlike a proper low-pass filter + downsample, sample-and-hold introduces severe aliasing artifacts — spectral images fold back into the audible range, adding a harsh digital shimmer.

### 5. Hard-clip waveshaper

Drives the signal by **+18 dB** into a `tanh` saturator (`tanh(x * drive)`), then normalizes to −0.5 dBFS. This creates extreme harmonic distortion — the waveform is close to a square wave in loud sections.

### 6. Ring modulator

Multiplies the audio by a sine wave at **30–120 Hz** (randomly chosen), then mixes the result in at **30% wet**. Ring modulation creates sum-and-difference sidebands that bear no harmonic relationship to the original, turning tonal material into clangorous metallic clusters.

### 7. Comb-filter resonance

A feedback comb filter with a delay of **2–15 ms** and feedback coefficient **0.6**:

```
y[n] = x[n] + 0.6 * y[n - D]
```

At these short delay times the comb produces a metallic, phone-booth resonance. The randomly chosen delay means the resonant frequency changes per-file.

### 8. Reverse-tail smear reverb

A fake reverse reverb: the signal is reversed, a one-pole low-pass smear (coefficient 0.92) is applied, then reversed back. A 40 ms pre-delay is added so the smeared tail arrives just before each transient rather than after. Mixed in at **25% wet**. The result is an eerie, backwards-sucking quality on attacks.

### 9. Random dropouts

Mutes windows of **20–80 ms** at an average rate of **2 Hz** for the duration of the audio. Short fade-in/out ramps (64 samples) are applied at each dropout edge to avoid click artifacts. The number of dropouts scales with audio length.

After all stages, the output is normalized to **−0.5 dBFS** and written as 16-bit PCM WAV.

---

## CLI test harness

You can run the DSP chain directly on a file without the GUI:

```powershell
python -m carpeteater.audio_fx input.wav output.wav
python -m carpeteater.audio_fx input.wav output.wav --seed 42
```

`--seed` forces a specific seed, overriding the file-hash-derived one. Useful for comparing the effect of individual stages.

---

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Place ffmpeg.exe in vendor/ (see Build below)

python -m carpeteater
```

---

## Build

1. Download the ffmpeg **essentials** build for Windows from [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/).
2. Place `ffmpeg.exe` in `vendor\`.
3. Run:

```bat
build.bat
```

This will:
- Create/update the venv and install `PySide6`, `numpy`, `soundfile`, and `pyinstaller`.
- Generate `build_icon.ico` from `public\closed.png` at 7 sizes (16 → 256 px) using Qt — no Pillow dependency.
- Run PyInstaller with `build.spec` to produce a **single-file EXE**.

Output: `dist\CarpetEater.exe` (~80 MB — PySide6 and ffmpeg are the bulk).

The bundled binary includes:
- All four sprites (`public\*.png`) resolved at runtime via `sys._MEIPASS`.
- `vendor\ffmpeg.exe` resolved the same way.

No install needed. Drop `CarpetEater.exe` anywhere and run it.

### Installer (Inno Setup)

`installer\carpeteater.iss` produces a per-user installer (`%LOCALAPPDATA%\Programs\CarpetEater`, no admin required). Build it locally with:

```bat
iscc installer\carpeteater.iss
```

Output: `installer\Output\CarpetEater-Setup.exe`. Requires [Inno Setup 6](https://jrsoftware.org/isdl.php) and an existing `dist\CarpetEater.exe`.

---

## Releases

GitHub Actions builds the EXE, installer, and portable ZIP on every push:

| Trigger | Result |
|---|---|
| Push to `main` | Builds artifacts, updates the [`rolling`](https://github.com/MaximilianWik/Carpet-Eater/releases/tag/rolling) prerelease (always the latest commit). |
| Push of `v*` tag | Builds artifacts, creates a numbered GitHub Release tagged as *latest*. |
| Pull request | Builds artifacts as workflow artifacts only — no release published. |

Releasing a new stable version:

```powershell
# 1. Bump the version in carpeteater\__init__.py
# 2. Update CHANGELOG.md
# 3. Commit, tag, push:
git tag v0.1.1
git push origin main --tags
```

The download links at the top of this README point at `/releases/latest/download/...`, which always resolves to the most recent stable release — no website edits needed when a new version ships.
