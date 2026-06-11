"""ffmpeg decode + WAV write + AudioProcessor thread.

Decoding strategy:
    Spawn ``ffmpeg -i <input> -ac 2 -ar 44100 -f f32le -`` and read stdout
    into a numpy float32 array shaped (N, 2). Works for any format ffmpeg
    can read.

Output:
    16-bit PCM WAV at 44.1 kHz written via soundfile next to the input.
    Filename is mangled by :mod:`carpeteater.naming` — escalating ``_chewed``
    prefix and a ``" - tasted NN WORD!!!"`` suffix.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QThread, Signal

from . import log
from .resources import ffmpeg_path

_log = log.get_logger("carpeteater.processor")

SAMPLE_RATE = 44100
CHANNELS = 2


class DecodeError(RuntimeError):
    pass


def decode_to_numpy(path: Path, sample_rate: int = SAMPLE_RATE,
                   channels: int = CHANNELS) -> np.ndarray:
    """Decode any audio file to float32 stereo numpy array via ffmpeg.

    Returns shape ``(N, channels)`` in [-1, 1].
    """
    cmd = [
        ffmpeg_path(),
        "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise DecodeError(
            f"ffmpeg not found at {ffmpeg_path()!r}. "
            "Place ffmpeg.exe in vendor/ or on PATH."
        ) from e

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise DecodeError(f"ffmpeg failed: {err or 'no stderr output'}")

    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    if raw.size == 0:
        raise DecodeError("ffmpeg produced empty output")
    if raw.size % channels != 0:
        # Trim partial frame.
        raw = raw[: (raw.size // channels) * channels]
    return raw.reshape(-1, channels).copy()


def write_wav(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write a stereo (or mono) numpy float array as 16-bit PCM WAV."""
    # Clip to [-1, 1] before quantization to avoid wraparound.
    audio = np.clip(audio, -1.0, 1.0)
    sf.write(str(path), audio, sample_rate, subtype="PCM_16")


def output_path_for(input_path: Path, rng: np.random.Generator) -> Path:
    """Compute the chewed-output filename. See :mod:`carpeteater.naming`."""
    from .naming import output_path_for as _name
    return _name(input_path, rng)


def seed_for_file(path: Path) -> int:
    """Deterministic 32-bit seed derived from the input path + size + mtime."""
    h = hashlib.sha1()
    h.update(str(path.resolve()).encode("utf-8", errors="replace"))
    try:
        st = path.stat()
        h.update(str(st.st_size).encode())
        h.update(str(int(st.st_mtime)).encode())
    except OSError:
        pass
    return int.from_bytes(h.digest()[:4], "big") & 0x7FFFFFFF


# --------------------------------------------------------------- QThread worker


class AudioProcessor(QObject):
    """Runs decode -> DSP -> write on a worker thread.

    Signals:
        finished(Path): output path written successfully.
        failed(str): human-readable error message.
    """

    finished = Signal(object)  # Path
    failed = Signal(str)

    def __init__(self, input_path: Path, parent: QObject | None = None,
                 forced_chain: str | None = None) -> None:
        super().__init__(parent)
        self.input_path = input_path
        self.forced_chain = forced_chain
        self.chain_name: str | None = None  # set after run() succeeds

    def run(self) -> None:
        # Initialize COM on this worker thread before any audio processing.
        # Windows requires CoInitializeEx on every thread that calls into COM-
        # backed audio APIs (WASAPI, DirectSound, JUCE, pedalboard).  Qt worker
        # threads do NOT do this automatically; missing it causes JUCE to hit an
        # assertion and call abort(), silently killing the process.
        _co_token: int | None = None
        if __import__("sys").platform == "win32":
            try:
                import ctypes
                COINIT_MULTITHREADED = 0x0
                hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
                # S_OK (0) = initialized; S_FALSE (1) = already init on this thread.
                # RPC_E_CHANGED_MODE (0x80010106) means it was already init as STA —
                # that's fine, don't uninit in that case.
                _co_token = int(hr) if hr in (0, 1) else None
                _log.debug("CoInitializeEx hr=0x%08x token=%s", hr & 0xFFFFFFFF, _co_token)
            except Exception:
                _log.debug("CoInitializeEx unavailable", exc_info=True)

        try:
            _log.info("worker.run start: %s", self.input_path)
            audio = decode_to_numpy(self.input_path)
            _log.info("decode done: shape=%s", audio.shape)
            # Lazy imports so the GUI doesn't pay the DSP cost at startup.
            from .audio_fx import chew

            seed = seed_for_file(self.input_path)
            ss = np.random.SeedSequence(seed)
            naming_seed = int(ss.spawn(1)[0].generate_state(1, dtype=np.uint32)[0])
            naming_rng = np.random.default_rng(naming_seed)

            _log.info("DSP start: seed=%s forced_chain=%s", seed, self.forced_chain)
            chain_name, chewed = chew(audio, SAMPLE_RATE, seed=seed, chain=self.forced_chain)
            self.chain_name = chain_name
            _log.info("DSP done: chain=%s output_shape=%s", chain_name, chewed.shape)

            out = output_path_for(self.input_path, naming_rng)
            _log.info("write start: %s", out)
            write_wav(out, chewed)
            _log.info("write done: %s", out)
            self.finished.emit(out)
            _log.info("finished signal emitted")
        except DecodeError as e:
            _log.warning("DecodeError: %s", e)
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 — surface any DSP failure
            _log.exception("worker.run failed")
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            if _co_token is not None:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass


def start_processor(input_path: Path, parent: QObject,
                    on_finished, on_failed,
                    forced_chain: str | None = None) -> tuple[QThread, AudioProcessor]:
    """Construct a worker + thread, wire signals, start it.

    All connections — including the caller's ``on_finished`` / ``on_failed``
    slots — are made *before* the thread starts so a fast-finishing worker
    cannot beat the connection setup.

    Cleanup follows the Qt-recommended pattern: ``worker.deleteLater`` is
    connected to the worker's own ``finished`` / ``failed`` signals (so it
    runs while the worker's thread is still pumping events) and
    ``thread.deleteLater`` is connected to ``thread.finished`` (so the
    thread is reaped after its loop has stopped). The previous wiring —
    ``thread.finished -> worker.deleteLater`` — queued the worker's
    deletion on a thread whose event loop had already exited, leaving a
    dangling QObject that occasionally crashed on later signal dispatch.
    """
    thread = QThread(parent)
    worker = AudioProcessor(input_path, forced_chain=forced_chain)
    worker.moveToThread(thread)

    # User callbacks first.
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)

    # Cleanup chain.
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.started.connect(worker.run)
    thread.start()
    return thread, worker
