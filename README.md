# Carpet Eater

A frameless, transparent, mouth-shaped desktop pet that eats audio files and spits them back out, horrifyingly distorted.

Drop any audio file (mp3, wav, flac, ogg, m4a, opus...) onto the open mouth. It chews. The mangled output appears next to the original as `<name>_chewed.wav`.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# place an ffmpeg.exe in vendor/ (see Build)

python -m carpeteater
```

## DSP CLI

Test the distortion chain on a file without the GUI:

```powershell
python -m carpeteater.audio_fx input.wav output.wav
```

## Build .exe

1. Drop `ffmpeg.exe` into `vendor\` (download from https://www.gyan.dev/ffmpeg/builds/ — "essentials" build).
2. Run `build.bat`.
3. `dist\CarpetEater.exe` is the single-file app.

## Behavior

- **Idle** — closed mouth.
- **Drag over** — opens.
- **Chew** — alternates between two chewing sprites for a duration that scales with the audio length.
- **Spit** — flashes open with a small jitter and writes the chewed file beside the original.
- **Right-click** — Always on top, Open output folder, Quit.
- **Double-click** — Open the folder of the last output.

## Project layout

```
carpeteater/
  __main__.py     entry
  window.py       MouthWindow (frameless, transparent, draggable, drop target)
  animator.py     sprite state machine
  processor.py    QThread audio worker
  audio_fx.py     numpy DSP chain (also runnable as CLI)
  resources.py    path resolution (handles PyInstaller _MEIPASS)
public/           sprites
vendor/           bundled ffmpeg.exe (build-time)
```
