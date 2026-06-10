"""Build Carpet Eater into a single-file EXE, installer, and portable ZIP.

Usage:
    python build.py [--skip-installer] [--skip-zip]

Pipeline:
  1. Verifies Python >= 3.11 and creates .venv\\ if missing.
  2. Installs the project + pyinstaller into the venv.
  3. Downloads vendor\\ffmpeg.exe if missing.
  4. Generates build_icon.ico from public\\closed.png at 7 sizes.
  5. Cleans build/ and dist/, runs PyInstaller.
  6. Builds installer\\Output\\CarpetEater-Setup.exe (skips if Inno Setup missing).
  7. Builds release\\CarpetEater-Portable.zip.

Idempotent: re-running skips steps whose outputs are current.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".carpeteater-build-installed"

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_EXE = ROOT / "vendor" / "ffmpeg.exe"

ICON_PATH = ROOT / "build_icon.ico"
ICON_SRC = ROOT / "public" / "closed.png"

DIST_EXE = ROOT / "dist" / "CarpetEater.exe"
INSTALLER_DIR = ROOT / "installer"
INSTALLER_OUT = INSTALLER_DIR / "Output" / "CarpetEater-Setup.exe"
RELEASE_DIR = ROOT / "release"


def die(msg: str) -> None:
    print(f"build.py: {msg}", file=sys.stderr)
    sys.exit(1)


def step(title: str) -> None:
    print(f"\n== {title} ==")


# ---------- venv + deps ----------

def check_python() -> None:
    if sys.version_info < (3, 11):
        die(f"Python >= 3.11 required, got {sys.version.split()[0]}")


def ensure_venv() -> None:
    if VENV_PY.exists():
        return
    step("creating .venv")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])


def pyproject_mtime() -> str:
    return str((ROOT / "pyproject.toml").stat().st_mtime_ns)


def ensure_deps() -> None:
    want = pyproject_mtime() + ":pyinstaller"
    if STAMP.exists() and STAMP.read_text().strip() == want:
        return
    step("installing project + pyinstaller into .venv")
    subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "-e", str(ROOT), "--quiet"])
    subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "pyinstaller>=6.0", "--quiet"])
    STAMP.write_text(want)


# ---------- ffmpeg ----------

def ensure_ffmpeg() -> None:
    if os.name != "nt":
        die("build.py only produces a Windows EXE; run on Windows or in CI.")
    if FFMPEG_EXE.exists():
        return
    step(f"downloading ffmpeg from {FFMPEG_URL}")
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
        print(f"  wrote {FFMPEG_EXE} ({FFMPEG_EXE.stat().st_size:,} bytes)")
    finally:
        zip_path.unlink(missing_ok=True)
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


# ---------- icon ----------

def make_ico() -> None:
    """Generate build_icon.ico from public/closed.png at multiple sizes."""
    if ICON_PATH.exists() and ICON_PATH.stat().st_mtime >= ICON_SRC.stat().st_mtime:
        return
    step("generating build_icon.ico")
    # Run icon generation in the venv — it has PySide6 installed there.
    code = (
        "import struct, sys\n"
        "from pathlib import Path\n"
        "from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt\n"
        "from PySide6.QtGui import QGuiApplication, QImage\n"
        "QGuiApplication.instance() or QGuiApplication(sys.argv)\n"
        f"src = QImage(r'{ICON_SRC}')\n"
        "if src.isNull(): raise SystemExit('could not load icon source')\n"
        "sizes = [16, 24, 32, 48, 64, 128, 256]\n"
        "pngs = []\n"
        "for s in sizes:\n"
        "    scaled = src.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation)\n"
        "    ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)\n"
        "    scaled.save(buf, 'PNG'); pngs.append(bytes(ba))\n"
        f"out = open(r'{ICON_PATH}', 'wb')\n"
        "out.write(struct.pack('<HHH', 0, 1, len(sizes)))\n"
        "offset = 6 + 16 * len(sizes)\n"
        "for s, data in zip(sizes, pngs):\n"
        "    w = 0 if s >= 256 else s; h = 0 if s >= 256 else s\n"
        "    out.write(struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(data), offset))\n"
        "    offset += len(data)\n"
        "for data in pngs: out.write(data)\n"
        "out.close()\n"
    )
    subprocess.check_call([str(VENV_PY), "-c", code])
    print(f"  wrote {ICON_PATH}")


# ---------- pyinstaller ----------

def run_pyinstaller() -> None:
    step("running PyInstaller")
    if (ROOT / "build").exists():
        shutil.rmtree(ROOT / "build")
    if (ROOT / "dist").exists():
        shutil.rmtree(ROOT / "dist")
    subprocess.check_call(
        [str(VENV_PY), "-m", "PyInstaller", "--noconfirm", "--clean", "build.spec"],
        cwd=str(ROOT),
    )
    if not DIST_EXE.exists():
        die("PyInstaller did not produce dist\\CarpetEater.exe")
    print(f"  built {DIST_EXE} ({DIST_EXE.stat().st_size:,} bytes)")


# ---------- installer ----------

def find_iscc() -> str | None:
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc:
        return iscc
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for cand in (
        Path(pf86) / "Inno Setup 6" / "ISCC.exe",
        Path(pf) / "Inno Setup 6" / "ISCC.exe",
    ):
        if cand.exists():
            return str(cand)
    return None


def app_version() -> str:
    text = (ROOT / "carpeteater" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    die("could not parse __version__ from carpeteater/__init__.py")
    return ""  # unreachable


def build_installer() -> None:
    iscc = find_iscc()
    if iscc is None:
        print("\n-- skipping installer: Inno Setup 6 (ISCC.exe) not on PATH or in Program Files")
        print("   install from https://jrsoftware.org/isdl.php to enable this step")
        return
    step(f"building installer with {iscc}")
    subprocess.check_call(
        [iscc, f"/DAppVersion={app_version()}", str(INSTALLER_DIR / "carpeteater.iss")],
        cwd=str(ROOT),
    )
    if not INSTALLER_OUT.exists():
        die("installer not produced")
    print(f"  built {INSTALLER_OUT} ({INSTALLER_OUT.stat().st_size:,} bytes)")


# ---------- portable zip ----------

def build_portable_zip() -> None:
    step("building portable zip")
    RELEASE_DIR.mkdir(exist_ok=True)
    zip_path = RELEASE_DIR / "CarpetEater-Portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(DIST_EXE, arcname="CarpetEater.exe")
    print(f"  built {zip_path} ({zip_path.stat().st_size:,} bytes)")


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-installer", action="store_true")
    p.add_argument("--skip-zip", action="store_true")
    args = p.parse_args()

    check_python()
    ensure_venv()
    ensure_deps()
    ensure_ffmpeg()
    make_ico()
    run_pyinstaller()
    if not args.skip_installer:
        build_installer()
    if not args.skip_zip:
        build_portable_zip()

    step("done")
    print(f"  EXE       : {DIST_EXE}")
    if not args.skip_installer and INSTALLER_OUT.exists():
        print(f"  Installer : {INSTALLER_OUT}")
    if not args.skip_zip:
        print(f"  Portable  : {RELEASE_DIR / 'CarpetEater-Portable.zip'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
