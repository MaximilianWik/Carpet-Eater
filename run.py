"""Run Carpet Eater from source with zero manual setup.

Usage:
    python run.py [args passed to carpeteater]

Does, in order:
  1. Verifies Python >= 3.11.
  2. Creates .venv\\ if missing.
  3. Installs the project (and deps) into the venv.
  4. On Windows, downloads vendor\\ffmpeg.exe if missing.
  5. Adds the venv's site-packages to sys.path in-process (no subprocess
     re-exec, so no extra CMD window appears).
  6. Imports and launches the GUI.

Re-running is fast: every step is idempotent and skipped if already done.
"""
from __future__ import annotations

import os
import shutil
import site
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".carpeteater-installed"  # marker: deps installed for current pyproject.toml

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_EXE = ROOT / "vendor" / "ffmpeg.exe"


def die(msg: str) -> None:
    print(f"run.py: {msg}", file=sys.stderr)
    sys.exit(1)


def check_python() -> None:
    if sys.version_info < (3, 11):
        die(f"Python >= 3.11 required, got {sys.version.split()[0]}")


def ensure_venv() -> None:
    if VENV_PY.exists():
        return
    print("Creating .venv ...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])


def pyproject_mtime() -> str:
    return str((ROOT / "pyproject.toml").stat().st_mtime_ns)


def ensure_deps() -> None:
    want = pyproject_mtime()
    if STAMP.exists() and STAMP.read_text().strip() == want:
        return
    print("Installing project into .venv ...")
    subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "-e", str(ROOT), "--quiet"])
    STAMP.write_text(want)


def ensure_ffmpeg() -> None:
    """Download ffmpeg.exe into vendor/ on Windows. Other OSes use system PATH."""
    if os.name != "nt":
        if shutil.which("ffmpeg") is None:
            print("warning: ffmpeg not on PATH (install via your package manager).", file=sys.stderr)
        return
    if FFMPEG_EXE.exists():
        return
    print(f"Downloading ffmpeg from {FFMPEG_URL} (~80 MB, one-time) ...")
    FFMPEG_EXE.parent.mkdir(parents=True, exist_ok=True)
    zip_path = ROOT / "ffmpeg-download.zip"
    extract_dir = ROOT / "ffmpeg-extracted"
    try:
        urlretrieve(FFMPEG_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        found = next(extract_dir.rglob("ffmpeg.exe"), None)
        if found is None:
            die("ffmpeg.exe not found inside downloaded archive")
        shutil.copy2(found, FFMPEG_EXE)
        print(f"Wrote {FFMPEG_EXE} ({FFMPEG_EXE.stat().st_size:,} bytes)")
    finally:
        zip_path.unlink(missing_ok=True)
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


def activate_venv() -> None:
    """Add the venv's site-packages to sys.path so we can import deps.

    Uses site.addsitedir() which also processes .pth files — needed for
    editable installs (``pip install -e .`` writes a .pth pointing at the
    source tree).
    """
    if os.name == "nt":
        site_pkgs = VENV / "Lib" / "site-packages"
    else:
        py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site_pkgs = VENV / "lib" / py_ver / "site-packages"
    if not site_pkgs.exists():
        die(f"venv site-packages not found at {site_pkgs}. Try deleting .venv\\ and re-running.")
    # Insert before system packages so the venv's versions take precedence.
    if str(site_pkgs) not in sys.path:
        site.addsitedir(str(site_pkgs))


def main() -> int:
    check_python()
    ensure_venv()
    ensure_deps()
    ensure_ffmpeg()
    activate_venv()

    from carpeteater.__main__ import main as app_main  # noqa: PLC0415
    return app_main() or 0


if __name__ == "__main__":
    sys.exit(main())
