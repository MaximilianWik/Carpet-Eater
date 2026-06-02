"""Entry point: ``python -m carpeteater``.

Sets up file logging to ``%LOCALAPPDATA%\\CarpetEater\\carpet-eater.log``
before anything else, so unhandled exceptions, unraisable errors, and
Qt warnings all flow into the same file. Without this, a windowed
pythonw.exe / PyInstaller build silently swallows tracebacks and the
user only sees "the mouth disappeared".
"""
from __future__ import annotations

import logging
import sys
import traceback

from PySide6.QtCore import qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication

from . import log
from .window import MouthWindow


def _install_excepthook() -> None:
    crash_log = logging.getLogger("carpeteater.crash")

    def hook(exc_type, exc_value, tb) -> None:
        msg = "".join(traceback.format_exception(exc_type, exc_value, tb))
        crash_log.error("unhandled exception:\n%s", msg)
        sys.__excepthook__(exc_type, exc_value, tb)
    sys.excepthook = hook

    def unraisable(args):  # noqa: ANN001 — sys.unraisablehook signature
        msg = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        crash_log.error("unraisable in %r:\n%s", args.object, msg)
    sys.unraisablehook = unraisable


def _install_qt_message_handler() -> None:
    """Capture Qt's own warnings — including 'Internal C++ object deleted'."""
    qt_log = logging.getLogger("carpeteater.qt")

    def handler(msg_type, _ctx, message) -> None:
        text = str(message)
        if msg_type == QtMsgType.QtFatalMsg:
            qt_log.error("FATAL: %s", text)
        elif msg_type == QtMsgType.QtCriticalMsg:
            qt_log.error("CRITICAL: %s", text)
        elif msg_type == QtMsgType.QtWarningMsg:
            qt_log.warning("WARNING: %s", text)
    qInstallMessageHandler(handler)


def main() -> int:
    log.setup()
    _install_excepthook()
    _install_qt_message_handler()

    main_log = log.get_logger("carpeteater.main")
    main_log.info("starting; log file: %s", log.log_path())

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = MouthWindow()
    win.show()

    code = app.exec()
    main_log.info("exec returned with code=%s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
