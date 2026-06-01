"""Sprite state machine and animator.

States:
    IDLE      — closed.png
    DRAG_OVER — open.png  (mouth opens when audio file hovers)
    CHEWING   — chew1 ↔ chew2, alternating with randomized timing
    SPITTING  — open.png briefly, paired with a window-shake jitter
    ERROR     — closed.png with a red flash overlay (handled in window)

The animator owns timers and emits ``frame_changed`` whenever the visible
sprite needs to be repainted. The window owns the QLabel/paint logic.
"""
from __future__ import annotations

import random
from enum import Enum, auto

from PySide6.QtCore import QObject, QTimer, Signal


class MouthState(Enum):
    IDLE = auto()
    DRAG_OVER = auto()
    CHEWING = auto()
    SPITTING = auto()
    ERROR = auto()


# Sprite filenames per state. CHEWING cycles through a list; others are static.
_STATIC_SPRITES = {
    MouthState.IDLE: "closed.png",
    MouthState.DRAG_OVER: "open.png",
    MouthState.SPITTING: "open.png",
    MouthState.ERROR: "closed.png",
}
_CHEW_SPRITES = ["chew1.png", "chew2.png"]

# Chew frame timing: base interval ± jitter, in milliseconds.
_CHEW_INTERVAL_MS = 180
_CHEW_JITTER_MS = 40


class MouthAnimator(QObject):
    """Drives sprite cycling for the chew animation.

    Emits:
        frame_changed(str): sprite filename to show now.
        state_changed(MouthState): new state.
    """

    frame_changed = Signal(str)
    state_changed = Signal(MouthState)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = MouthState.IDLE
        self._chew_idx = 0

        self._chew_timer = QTimer(self)
        self._chew_timer.setSingleShot(True)
        self._chew_timer.timeout.connect(self._tick_chew)

        self._spit_timer = QTimer(self)
        self._spit_timer.setSingleShot(True)
        self._spit_timer.timeout.connect(lambda: self.set_state(MouthState.IDLE))

    # ----- public API -----

    @property
    def state(self) -> MouthState:
        return self._state

    def current_sprite(self) -> str:
        if self._state is MouthState.CHEWING:
            return _CHEW_SPRITES[self._chew_idx % len(_CHEW_SPRITES)]
        return _STATIC_SPRITES[self._state]

    def set_state(self, new_state: MouthState) -> None:
        if new_state is self._state:
            return
        self._state = new_state
        self._chew_timer.stop()
        self._spit_timer.stop()

        if new_state is MouthState.CHEWING:
            self._chew_idx = 0
            self._schedule_next_chew()
        elif new_state is MouthState.SPITTING:
            # Hold open briefly then snap back to idle.
            self._spit_timer.start(450)

        self.state_changed.emit(new_state)
        self.frame_changed.emit(self.current_sprite())

    # ----- internal -----

    def _schedule_next_chew(self) -> None:
        jitter = random.randint(-_CHEW_JITTER_MS, _CHEW_JITTER_MS)
        self._chew_timer.start(max(60, _CHEW_INTERVAL_MS + jitter))

    def _tick_chew(self) -> None:
        if self._state is not MouthState.CHEWING:
            return
        self._chew_idx += 1
        self.frame_changed.emit(self.current_sprite())
        self._schedule_next_chew()
