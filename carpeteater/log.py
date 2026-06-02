"""Simple file logger.

Writes to ``%LOCALAPPDATA%\\CarpetEater\\carpet-eater.log``. Both
informational traces and crash dumps go to the same file so a single
place tells you what happened.

Usage::

    from .log import log_path, setup, get_logger
    setup()
    log = get_logger(__name__)
    log.info("did the thing")
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_INITIALISED = False


def log_path() -> Path:
    """Return the path to ``carpet-eater.log``, creating the parent dir."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = Path(base) / "CarpetEater"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "carpet-eater.log"


def setup(level: int = logging.INFO) -> None:
    """Configure the root logger to append to the carpet-eater log file.

    Idempotent — safe to call more than once. Rotates at 1 MiB, keeps 3
    backups, so the file never grows unbounded.
    """
    global _INITIALISED
    if _INITIALISED:
        return

    try:
        handler = RotatingFileHandler(
            log_path(),
            maxBytes=1_048_576,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        # If we cannot open the log file, fall back to stderr so the app
        # itself still runs.
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _INITIALISED = True

    logging.getLogger("carpeteater").info(
        "logger initialised; pid=%s argv=%s", os.getpid(), sys.argv,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
