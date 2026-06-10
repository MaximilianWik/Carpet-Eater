# Architecture

Deep dive on Carpet Eater's internals. For install/use, see [README.md](README.md).

---

## Stack

| Layer | Library | Why |
|---|---|---|
| GUI | PySide6 (Qt 6) | True alpha-channel-shaped windows, drag-and-drop, threading, timers. LGPL — fine for distribution. |
| Audio I/O | soundfile | Fast WAV/FLAC read-write via libsndfile. No dependency hell. |
| Decode | ffmpeg (bundled binary) | Reads anything: mp3, m4a, aac, opus, ogg, wma, flac, wav, aiff. Piped to stdout as raw f32le PCM. |
| DSP | numpy | All processing is pure numpy. No scipy, no audio framework. |
| Packaging | PyInstaller + Inno Setup | Single-file EXE; per-user installer with no admin requirement. |

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
│   ├── audio_fx.py       — DSP stages + 5 named chains (also a CLI tool)
│   ├── naming.py         — output filename escalation + tasting suffix
│   ├── log.py            — file logger → %LOCALAPPDATA%\CarpetEater\carpet-eater.log
│   └── resources.py      — path resolution (handles PyInstaller _MEIPASS)
├── public/               — sprites: closed, open, chew1, chew2
├── vendor/
│   └── ffmpeg.exe        — bundled at build time (auto-downloaded; gitignored)
├── installer/
│   └── carpeteater.iss   — Inno Setup script (per-user installer)
├── .github/workflows/
│   └── release.yml       — CI: build EXE + installer + portable on every push
├── pyproject.toml
├── build.spec            — PyInstaller spec
├── run.py                — `python run.py` to run from source
├── build.py              — `python build.py` to build EXE + installer + zip
├── CHANGELOG.md
├── ARCHITECTURE.md       — this file
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

### Output format and naming

Output is written by `soundfile` as **16-bit PCM WAV at 44.1 kHz stereo**, next to the input file. Filenames escalate per chew:

```
First chew:    <name>.<ext>                 →  <name>_chewed - tasted NN WORD!!!.wav
Second chew:   <name>_chewed.wav            →  <name>_<adj>_chewed - tasted NN WORD!!!.wav
Third chew:    <name>_<adj>_chewed.wav      →  <name>_<adj2>_<adj>_chewed - tasted NN WORD!!!.wav
…
```

Adjectives are picked from a pool of 20 visceral words (`incestuous`, `putrid`, `gangrenous`, `necrotic`, `rancid`, `fetid`, `moldering`, `decayed`, `septic`, `vile`, `depraved`, `festering`, `rotten`, `diseased`, `macerated`, `foetal`, `bilious`, `verminous`, `weeping`, `abscessed`); the picker avoids ones already in the stem. Tasting notes draw from ~38 descriptors with a few rare easter eggs.

Any prior `" - tasted NN WORD!!!"` suffix is stripped before re-tasting, so the suffix only ever describes the most recent chew.

WAV was chosen over mp3/ogg for universality — it can be imported into any DAW, sample editor, or mastering chain without re-encoding.

### Determinism

Every chew is seeded by a SHA-1 hash of the input file's path + size + mtime, collapsed to a 32-bit integer. The seed is split into three independent sub-streams via `numpy.random.SeedSequence`:

1. **Chain selection** — which of the five chains to apply.
2. **DSP randomness** — all per-stage parameter choices.
3. **Naming** — adjective pick + tasting suffix.

So the same input always produces the same chain, same audio, and same filename. Tweaking the DSP parameters won't change the filename; tweaking the naming won't change the audio.

---

## DSP chains

Five named chains, picked at random per file (deterministically from the seed). Each reuses a shared library of stages with different ordering and parameters.

| Chain | Character |
|---|---|
| **standard_mauling** | The original 9-stage chain. Heavy and balanced. |
| **wet_slobber** | Dark, drowning. Heavy reverse-smear, gentler bitcrush, less granular destruction. |
| **bone_grinder** | Metallic and abrasive. Heavy comb + clip, no smear, brighter pitch range. |
| **pulper** | Granular shuffle dominant. Lots of stutter, two passes of grain shuffle. |
| **stomach_acid** | Maximum digital decay. Brutal bitcrush (2–4 bits) + extreme SR reduction (4–8 kHz). |

---

## Stages (used by the chains above)

All pure numpy. No scipy, no librosa, no audio framework.

### 1. Pitch + speed mangle

Linear-interpolation resample at `2^(semitones/12)` ratio. Random shift, range varies per chain (typical: −7 to +5 semitones). No formant correction — pitch and playback speed are coupled, giving chipmunk and demon artifacts depending on direction.

### 2. Granular shuffle

Slices the audio into grains of **50–200 ms** (range varies per chain). Roughly **20%** of grain positions are swapped with random other positions; another **10%** are time-reversed. A 3 ms crossfade at each grain boundary suppresses clicks. The result is a scrambling of phrase structure while preserving local timbre.

### 3. Bitcrush

Quantizes the signal to **2–7 bits** depending on the chain (4–6 by default). At 4 bits the quantization noise floor is about −24 dBFS — thick, gritty artifacts without completely destroying intelligibility. At 2–3 bits everything sounds destroyed.

### 4. Sample-rate reduction

Sample-and-hold decimation to a target sample rate (8–11 kHz default; some chains go as low as 4 kHz), then held back to 44.1 kHz. Unlike a proper low-pass + downsample, sample-and-hold introduces severe aliasing — spectral images fold back into the audible range as a harsh digital shimmer.

### 5. Hard-clip waveshaper

Drives the signal by **+10 to +22 dB** (per chain) into `tanh`, then normalizes to −0.5 dBFS. Heavy drive turns the waveform near-square in loud sections.

### 6. Ring modulator

Multiplies the audio by a sine wave at **30–240 Hz** (per chain), mixed in at **20–45% wet**. Ring modulation creates sum-and-difference sidebands with no harmonic relationship to the original, turning tones into clangorous metallic clusters.

### 7. Comb-filter resonance

Feedback comb `y[n] = x[n] + fb * y[n-D]` with delay **1–30 ms** and feedback **0.5–0.75** (per chain). Vectorized as a geometric expansion (`fb^k * shift(x, k*D)` for `k = 0, 1, …` until `fb^k` is below threshold) — no Python sample loop. Short delays produce metallic phone-booth resonance.

### 8. Reverse-tail smear reverb

Reverse the signal, apply a one-pole low-pass (coefficient ~0.92), reverse back. Implemented as `np.convolve` against a truncated geometric kernel — pure numpy, no scipy, no Python sample loop. A 40 ms (or 80 ms in the wet chain) pre-delay puts the smear before each transient. Mixed at **20–55% wet** depending on chain.

### 9. Random dropouts

Mutes windows of **10–150 ms** at **1–4 Hz** average rate (per chain). Short fade-in/out ramps at each dropout edge avoid click artifacts. Number of dropouts scales with audio length.

After all stages, the output is normalized to **−0.5 dBFS** before write.

### Performance

A 3:49 audio file chews in **~6 seconds** end-to-end. Earlier versions had Python sample-loops in the comb and reverse-smear stages (~106 s for the same file). Both are now fully vectorized.

---

## CLI test harness

Run the DSP chain on a file without the GUI:

```powershell
python -m carpeteater.audio_fx input.wav output.wav
python -m carpeteater.audio_fx input.wav output.wav --seed 42
python -m carpeteater.audio_fx input.wav output.wav --chain bone_grinder
python -m carpeteater.audio_fx --list-chains
```

- `--seed N` forces a specific seed, overriding the file-hash-derived one.
- `--chain NAME` forces a specific chain (default: random per seed).
- `--list-chains` prints the available chain names.

Useful for tuning individual stages and A/B-ing chains.

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

The download links in the README point at `/releases/latest/download/...`, which always resolves to the most recent stable release — no website edits needed when a new version ships.
