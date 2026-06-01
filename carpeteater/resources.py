"""Path resolution for sprites and bundled binaries.

In dev: paths resolve relative to the repo root.
In a PyInstaller frozen build: paths resolve relative to ``sys._MEIPASS``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    # PyInstaller sets _MEIPASS to the temp extraction dir at runtime.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    # Dev: repo root is two levels up from this file (carpeteater/resources.py).
    return Path(__file__).resolve().parent.parent


def sprite_path(name: str) -> Path:
    """Return absolute path to a sprite under public/."""
    return _base_dir() / "public" / name


def ffmpeg_path() -> str:
    """Return path to bundled ffmpeg, falling back to PATH lookup in dev."""
    bundled = _base_dir() / "vendor" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    # Dev fallback: rely on PATH.
    return "ffmpeg"


def open_in_explorer(path: Path | str) -> None:
    """Open the given path's folder in Windows Explorer with the file selected."""
    p = Path(path)
    if p.is_file():
        os.system(f'explorer /select,"{p}"')
    else:
        os.system(f'explorer "{p}"')
