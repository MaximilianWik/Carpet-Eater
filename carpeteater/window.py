"""MouthWindow — the desktop pet itself.

Frameless, transparent, draggable. Renders the current sprite from the
animator. Accepts audio file drops. Owns the AudioProcessor thread.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import log
from .animator import MouthAnimator, MouthState
from .audio_fx import CHAINS
from .processor import start_processor
from .resources import open_in_explorer, sprite_path

_log = log.get_logger("carpeteater.window")

AUDIO_EXTS = {
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac",
    ".opus", ".wma", ".aiff", ".aif", ".alac",
}

_DEFAULT_SIZE = 400
_MIN_SIZE = 160
_MAX_SIZE = 900


class MouthWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        # Frameless, transparent, optional always-on-top.
        self._always_on_top = True
        self._apply_window_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setWindowTitle("Carpet Eater")
        self.setWindowIcon(QIcon(str(sprite_path("closed.png"))))
        self.resize(_DEFAULT_SIZE, _DEFAULT_SIZE)
        self.setToolTip("Drop an audio file to feed me.")

        # Sprite cache and animator.
        self._sprite_cache: dict[str, QPixmap] = {}
        self._animator = MouthAnimator(self)
        self._animator.frame_changed.connect(self._on_frame_changed)
        self._animator.state_changed.connect(self._on_state_changed)

        # Drag-to-move bookkeeping.
        self._drag_anchor: QPoint | None = None

        # Spit-jitter offset (applied during paint).
        self._jitter = QPoint(0, 0)
        self._jitter_timer = QTimer(self)
        self._jitter_timer.timeout.connect(self._tick_jitter)
        self._jitter_ticks_left = 0

        # Error flash.
        self._error_alpha = 0.0
        self._error_timer = QTimer(self)
        self._error_timer.timeout.connect(self._tick_error_flash)

        # Last output path (for double-click reveal).
        self._last_output: Path | None = None

        # Forced DSP chain (None = random per seed).
        self._forced_chain: str | None = None

        # Active worker bookkeeping.
        self._worker_thread = None
        self._worker = None
        self._chew_started_at: float = 0.0
        self._pending_result: tuple[bool, Path | str] | None = None
        self._pending_flag_apply: bool = False
        self._min_chew_timer = QTimer(self)
        self._min_chew_timer.setSingleShot(True)
        self._min_chew_timer.timeout.connect(self._maybe_finalize)

        # Center on screen on first show.
        self._center_on_screen()

    # ----------------------------------------------------------------- flags
    def _apply_window_flags(self) -> None:
        """Initial flag setup. Only safe to call before ``show()`` — calling
        ``setWindowFlags()`` on a visible window recreates the native HWND on
        Windows, which corrupts Qt's state when a worker thread has queued
        events targeting the widget. Use :py:meth:`_set_topmost_native` for
        runtime toggling instead.
        """
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def _set_topmost_native(self, on: bool) -> bool:
        """Flip ``WS_EX_TOPMOST`` on the existing HWND via Win32.

        Returns True on success. Avoids the Qt setWindowFlags() path which
        destroys and re-creates the native window — that recreation, when it
        happens mid-chew, is what was crashing the app at the end of a run.
        """
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            user32 = ctypes.windll.user32
            user32.SetWindowPos.restype = ctypes.c_bool
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint,
            ]
            hwnd = int(self.winId())
            flag = HWND_TOPMOST if on else HWND_NOTOPMOST
            ok = bool(user32.SetWindowPos(
                hwnd, flag, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            ))
            _log.info("SetWindowPos topmost=%s -> %s", on, ok)
            return ok
        except Exception:
            _log.exception("SetWindowPos failed")
            return False

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )

    # -------------------------------------------------------------- sprites
    def _pixmap(self, name: str) -> QPixmap:
        pm = self._sprite_cache.get(name)
        if pm is None:
            pm = QPixmap(str(sprite_path(name)))
            self._sprite_cache[name] = pm
        return pm

    def _on_frame_changed(self, _name: str) -> None:
        self.update()

    def _on_state_changed(self, state: MouthState) -> None:
        _log.info("state changed: %s", state.name)
        try:
            if state is MouthState.SPITTING:
                self._start_jitter()
            elif state is MouthState.IDLE and self._pending_flag_apply:
                self._pending_flag_apply = False
                self._apply_window_flags()
        except Exception:
            _log.exception("error in _on_state_changed")

    # --------------------------------------------------------------- paint
    def paintEvent(self, _event: QPaintEvent) -> None:
        sprite_name = self._animator.current_sprite()
        try:
            pm = self._pixmap(sprite_name)
        except Exception:
            return
        if pm is None or pm.isNull():
            return
        scaled = pm.scaled(
            self.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if scaled.isNull():
            return
        x = (self.width() - scaled.width()) // 2 + self._jitter.x()
        y = (self.height() - scaled.height()) // 2 + self._jitter.y()

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(x, y, scaled)
            if self._error_alpha > 0:
                color = QColor(255, 40, 40, int(self._error_alpha * 180))
                p.fillRect(self.rect(), color)
        finally:
            p.end()

    # -------------------------------------------------------------- jitter
    def _start_jitter(self) -> None:
        _log.info("starting spit jitter")
        try:
            self._jitter_ticks_left = 8
            self._jitter_timer.start(40)
        except Exception:
            _log.exception("error in _start_jitter")

    def _tick_jitter(self) -> None:
        try:
            import random
            self._jitter_ticks_left -= 1
            if self._jitter_ticks_left <= 0:
                self._jitter_timer.stop()
                self._jitter = QPoint(0, 0)
                _log.info("spit jitter complete")
            else:
                mag = 6
                self._jitter = QPoint(
                    random.randint(-mag, mag),
                    random.randint(-mag, mag),
                )
            self.update()
        except Exception:
            _log.exception("error in _tick_jitter")

    # --------------------------------------------------------- error flash
    def flash_error(self) -> None:
        self._error_alpha = 1.0
        self._error_timer.start(40)

    def _tick_error_flash(self) -> None:
        self._error_alpha -= 0.08
        if self._error_alpha <= 0:
            self._error_alpha = 0.0
            self._error_timer.stop()
        self.update()

    # ----------------------------------------------------------- mouse drag
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            global_pos = event.globalPosition().toPoint()
            self._drag_anchor = global_pos - self.frameGeometry().topLeft()
            event.accept()
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_anchor is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_anchor)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_anchor = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._last_output is not None:
            open_in_explorer(self._last_output)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Ctrl+wheel resizes the mouth.
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            step = 20 if delta > 0 else -20
            new_size = max(_MIN_SIZE, min(_MAX_SIZE, self.width() + step))
            center = self.frameGeometry().center()
            self.resize(new_size, new_size)
            self.move(center.x() - new_size // 2, center.y() - new_size // 2)
            event.accept()
        else:
            super().wheelEvent(event)

    # -------------------------------------------------------- context menu
    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)

        a_top = QAction("Always on top", self)
        a_top.setCheckable(True)
        a_top.setChecked(self._always_on_top)
        a_top.toggled.connect(self._toggle_always_on_top)
        menu.addAction(a_top)

        a_open = QAction("Open last output folder", self)
        a_open.setEnabled(self._last_output is not None)
        a_open.triggered.connect(
            lambda: self._last_output and open_in_explorer(self._last_output)
        )
        menu.addAction(a_open)

        menu.addSeparator()

        # Chain submenu
        chain_menu = QMenu("Chain", self)
        a_random = QAction("Random (default)", self)
        a_random.setCheckable(True)
        a_random.setChecked(self._forced_chain is None)
        a_random.triggered.connect(lambda: self._set_forced_chain(None))
        chain_menu.addAction(a_random)
        chain_menu.addSeparator()
        for name in sorted(CHAINS.keys()):
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(self._forced_chain == name)
            a.triggered.connect(lambda checked, n=name: self._set_forced_chain(n))
            chain_menu.addAction(a)
        menu.addMenu(chain_menu)

        menu.addSeparator()
        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self.close)
        menu.addAction(a_quit)

        menu.exec(global_pos)

    def _set_forced_chain(self, name: str | None) -> None:
        self._forced_chain = name
        _log.info("forced chain set to: %s", name or "random")

    def _toggle_always_on_top(self, on: bool) -> None:
        _log.info("toggle always-on-top: %s (state=%s)",
                  on, self._animator.state.name)
        self._always_on_top = on
        # Use the Win32 path so the native HWND is *not* recreated. The
        # previous setWindowFlags-based toggle would tear down and rebuild
        # the HWND, which crashed the app when triggered mid-chew (queued
        # events from the worker thread landed on a transient state).
        if self._set_topmost_native(on):
            return
        # Fallback for non-Windows hosts (or if SetWindowPos refused).
        # Only safe when the chew has finished, so defer if still busy.
        if self._animator.state in (MouthState.CHEWING, MouthState.SPITTING):
            _log.warning("native toggle unavailable; deferring until IDLE")
            self._pending_flag_apply = True
            return
        self._apply_window_flags()

    # ------------------------------------------------------- drag and drop
    def _drop_has_audio(self, urls: list[QUrl]) -> Path | None:
        for url in urls:
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.suffix.lower() in AUDIO_EXTS and p.is_file():
                return p
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._animator.state is MouthState.CHEWING:
            event.ignore()
            return
        md = event.mimeData()
        if not md.hasUrls():
            event.ignore()
            return
        if self._drop_has_audio(list(md.urls())) is not None:
            self._animator.set_state(MouthState.DRAG_OVER)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        if self._animator.state is MouthState.DRAG_OVER:
            self._animator.set_state(MouthState.IDLE)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._drop_has_audio(list(event.mimeData().urls()))
        if path is None:
            self._animator.set_state(MouthState.IDLE)
            self.flash_error()
            event.ignore()
            return
        event.acceptProposedAction()
        _log.info("drop accepted: %s", path)
        self._begin_chew(path)

    # --------------------------------------------------------------- chew
    # Minimum theatrical chew so short clips still feel chewed.
    _MIN_CHEW_MS = 2000

    def _begin_chew(self, path: Path) -> None:
        """Hand the file off to the audio processor and start chewing."""
        import time
        self._animator.set_state(MouthState.CHEWING)
        self._chew_started_at = time.monotonic()
        self._pending_result = None
        self._min_chew_timer.start(self._MIN_CHEW_MS)
        chain_label = f" [{self._forced_chain}]" if self._forced_chain else ""
        self.setToolTip(f"Chewing: {path.name}{chain_label}")

        thread, worker = start_processor(
            path, self,
            on_finished=self._on_worker_finished,
            on_failed=self._on_worker_failed,
            forced_chain=self._forced_chain,
        )
        self._worker_thread = thread
        self._worker = worker

    def _on_worker_finished(self, output: Path) -> None:
        _log.info("on_worker_finished: %s", output)
        self._pending_result = (True, output)
        self._maybe_finalize()

    def _on_worker_failed(self, message: str) -> None:
        _log.warning("on_worker_failed: %s", message)
        self._pending_result = (False, message)
        self._maybe_finalize()

    def _maybe_finalize(self) -> None:
        """Wait for both the min-chew timer and the worker before spitting."""
        if self._pending_result is None:
            return
        if self._min_chew_timer.isActive():
            _log.debug("finalize deferred — min-chew timer still active")
            return
        ok, payload = self._pending_result
        self._pending_result = None
        self._worker_thread = None
        self._worker = None
        _log.info("finalize: ok=%s", ok)
        if ok:
            self._finish_chew(payload)  # type: ignore[arg-type]
        else:
            self._fail_chew(str(payload))

    def _finish_chew(self, output: Path) -> None:
        _log.info("finish_chew: output=%s", output)
        try:
            self._last_output = output
        except Exception:
            _log.exception("setting last_output failed")
        try:
            self.setToolTip(f"Spat: {output.name}")
        except Exception:
            _log.exception("setToolTip failed")
        try:
            self._animator.set_state(MouthState.SPITTING)
        except Exception:
            _log.exception("set_state(SPITTING) failed")

    def _fail_chew(self, message: str) -> None:
        self.setToolTip(f"Choked: {message}")
        self._animator.set_state(MouthState.IDLE)
        self.flash_error()

    # ---------------------------------------------------------------- close
    def closeEvent(self, event) -> None:
        # Wait briefly for any in-flight worker so we don't drop an output.
        # The QThread C++ object may already be in deleteLater limbo even when
        # our Python ref is still live — guard against the resulting
        # RuntimeError ("Internal C++ object already deleted").
        thread = self._worker_thread
        self._worker_thread = None
        self._worker = None
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(2000)
            except RuntimeError:
                pass  # already cleaned up by Qt
        super().closeEvent(event)
