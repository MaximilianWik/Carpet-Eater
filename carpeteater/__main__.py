"""Entry point: ``python -m carpeteater``."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .window import MouthWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = MouthWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
