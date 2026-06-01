"""Entry point: ``python -m carpeteater``.

Installs a global excepthook so unhandled exceptions are written to
``%LOCALAPPDATA%\\CarpetEater\\crash.log`` before the process dies.
Without this, the windowed pythonw.exe / PyInstaller build silently
swallows tracebacks and the user sees only "the mouth disappeared".
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication

from .window import MouthWindow


def _crash_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = Path(base) / "CarpetEater"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "crash.log"


def _write_crash(prefix: str, message: str) -> None:
    try:
        path = _crash_log_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat()} | {prefix} ===\n")
            f.write(message)
            f.write("\n")
    except Exception:
        # Last resort — never let logging itself crash the process.
        pass


def _install_excepthook() -> None:
    def hook(exc_type, exc_value, tb) -> None:
        msg = "".join(traceback.format_exception(exc_type, exc_value, tb))
        _write_crash("python excepthook", msg)
        # Still print to stderr in case we're running from a console.
        sys.__excepthook__(exc_type, exc_value, tb)
    sys.excepthook = hook

    # PEP 657 — unraisable exceptions (errors during GC, signal slots, etc.)
    def unraisable(args):  # noqa: ANN001 — sys.unraisablehook signature
        msg = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        _write_crash(f"unraisable in {args.object!r}", msg)
    sys.unraisablehook = unraisable


def _install_qt_message_handler() -> None:
    """Capture Qt's own warnings (which include "Internal C++ object deleted")."""
    def handler(msg_type, _ctx, message) -> None:
        if msg_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg,
                        QtMsgType.QtFatalMsg):
            _write_crash(f"Qt {msg_type.name}", str(message))
    qInstallMessageHandler(handler)


def main() -> int:
    _install_excepthook()
    _install_qt_message_handler()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = MouthWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
