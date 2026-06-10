# Carpet Eater

A desktop tool made for the artist **Carpet Eater**.

Drop an audio file into the open mouth. It chews. A horrifyingly distorted version appears next to the original.

> Instagram: [@i.am.carpet.eater](https://www.instagram.com/i.am.carpet.eater/)
> SoundCloud: [carpet_eater](https://soundcloud.com/carpet_eater)
> Website: [carpeteater.net](https://carpeteater.net/)

---

## Install

Both downloads are unsigned. Click **More info → Run anyway** on the SmartScreen warning. The SHA-256 is on the release page if you want to verify.

<details>
<summary><strong>Installer (recommended)</strong> — per-user, no admin needed</summary>

[**Download CarpetEater-Setup.exe**](https://github.com/MaximilianWik/Carpet-Eater/releases/latest/download/CarpetEater-Setup.exe), double-click, follow the wizard.

Installs to `%LOCALAPPDATA%\Programs\CarpetEater\`. Adds Start Menu entry, optional desktop shortcut, registered uninstaller (Settings → Apps → Carpet Eater). Re-running a newer installer upgrades in place. ~80 MB on disk.
</details>

<details>
<summary><strong>Portable ZIP</strong> — no installer</summary>

[**Download CarpetEater-Portable.zip**](https://github.com/MaximilianWik/Carpet-Eater/releases/latest/download/CarpetEater-Portable.zip), unzip, run `CarpetEater.exe`. Use this if your IT has blocked the installer.
</details>

[Latest dev build](https://github.com/MaximilianWik/Carpet-Eater/releases/tag/rolling) — auto-built from `main`, expect rough edges.

---

## Use

- **Drop audio** — drag any `.mp3 .wav .flac .ogg .m4a .aac .opus .wma .aiff` onto the mouth. Chewed file appears next to the original.
- **Move** — left-click + drag.
- **Resize** — Ctrl + mouse wheel (160–900 px).
- **Open last output** — double-click.
- **Right-click** — *Always on top*, *Open last output folder*, *Quit*.

Output filenames escalate every time you re-feed a chewed file:

```
song.mp3                     →  song_chewed - tasted 47 GAMEY!!!.wav
song_chewed.wav              →  song_incestuous_chewed - tasted 12 PUTRID!!!.wav
song_incestuous_chewed.wav   →  song_putrid_incestuous_chewed - tasted 89 RANCID!!!.wav
```

Same input always produces the same output (path + size + mtime hash into the seed).

---

## From source

Requires Python 3.11+. Both scripts auto-create `.venv\`, install deps, and download `ffmpeg.exe` to `vendor\` on first run.

Run the GUI:

```powershell
python run.py
```

Build the EXE + installer + portable ZIP:

```powershell
python build.py
```

Outputs land in `dist\CarpetEater.exe`, `installer\Output\CarpetEater-Setup.exe`, and `release\CarpetEater-Portable.zip`. The installer step needs [Inno Setup 6](https://jrsoftware.org/isdl.php) on PATH; it's skipped with a friendly note otherwise.

---

## Troubleshooting

**Installer blocked by AppLocker?** Try the portable ZIP. Both blocked? Run from source — Python is normally trusted.

**Crashes / mouth disappears?** Logs at `%LOCALAPPDATA%\CarpetEater\carpet-eater.log`. Last successful entry pinpoints the failure.

**No sound after chewing?** Output isn't played; it's written next to the input. Double-click the mouth to open the output folder.

---

For the deep technical write-up — DSP chains, stage math, state machine, determinism — see [ARCHITECTURE.md](ARCHITECTURE.md).
