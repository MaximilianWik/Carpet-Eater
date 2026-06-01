"""Distortion DSP chain.

All stages take and return float32 stereo numpy arrays of shape (N, 2)
in [-1, 1]. The whole chain is deterministic given a seed, so the same
file always produces the same chewed output.

Pipeline:
    1. Pitch + speed mangle (resample, no formant correction)
    2. Granular shuffle (50–200 ms grains, 20% reorder, occasional reverse)
    3. Bitcrush (4–6 bits)
    4. Sample-rate reduction with aliasing
    5. Hard-clip waveshaper (tanh drive)
    6. Ring modulator (30–120 Hz, 30% wet)
    7. Comb-filter resonance (2–15 ms, fb 0.6)
    8. Reverse-tail smear reverb
    9. Random dropouts

Run from CLI:
    python -m carpeteater.audio_fx input.wav output.wav [--seed N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# ============================================================ helpers


def _ensure_stereo(x: np.ndarray) -> np.ndarray:
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    elif x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    elif x.shape[1] > 2:
        x = x[:, :2]
    return np.ascontiguousarray(x.astype(np.float32))


def _normalize(x: np.ndarray, target: float = 0.95) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 1e-9:
        return x
    return (x * (target / peak)).astype(np.float32)


def _resample_linear(x: np.ndarray, ratio: float) -> np.ndarray:
    """Linear-interpolation resample. ratio > 1 stretches (longer/lower-pitch
    when used as a pitch shift the naive way; here we use it for length change).
    Returns a stereo array."""
    n_in = x.shape[0]
    n_out = max(1, int(round(n_in * ratio)))
    src_idx = np.linspace(0, n_in - 1, n_out, dtype=np.float64)
    i0 = np.floor(src_idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, n_in - 1)
    frac = (src_idx - i0).astype(np.float32)[:, None]
    out = x[i0] * (1.0 - frac) + x[i1] * frac
    return out.astype(np.float32)


# ============================================================ stages


def stage_pitch_speed(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Random pitch shift in semitones via resampling. Length changes too.

    Range: -7 to +5 semitones (skewed slightly negative for "demon" feel).
    """
    semitones = float(rng.uniform(-7.0, 5.0))
    factor = 2.0 ** (semitones / 12.0)
    # Resample by 1/factor: higher pitch = compressed = ratio < 1.
    return _resample_linear(x, 1.0 / factor)


def stage_granular_shuffle(x: np.ndarray, sr: int,
                            rng: np.random.Generator) -> np.ndarray:
    """Slice into 50–200 ms grains, reorder ~20%, occasionally reverse."""
    n = x.shape[0]
    if n < sr // 10:
        return x
    grain_ms = float(rng.uniform(50.0, 200.0))
    grain_len = max(1, int(sr * grain_ms / 1000.0))
    indices = list(range(0, n, grain_len))
    grains = [x[i:i + grain_len] for i in indices]

    # Pick 20% of grain positions to swap with a random other position.
    n_swaps = max(1, int(len(grains) * 0.2))
    for _ in range(n_swaps):
        a = int(rng.integers(0, len(grains)))
        b = int(rng.integers(0, len(grains)))
        grains[a], grains[b] = grains[b], grains[a]

    # ~10% of grains get reversed.
    for i in range(len(grains)):
        if rng.random() < 0.10:
            grains[i] = grains[i][::-1]

    # Apply a tiny crossfade between grains (3 ms) to avoid clicks.
    fade_len = min(int(sr * 0.003), grain_len // 4)
    if fade_len > 1:
        ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)[:, None]
        for i in range(1, len(grains)):
            cur = grains[i]
            if cur.shape[0] >= fade_len:
                cur[:fade_len] = cur[:fade_len] * ramp
                grains[i] = cur
        for i in range(len(grains) - 1):
            cur = grains[i]
            if cur.shape[0] >= fade_len:
                cur[-fade_len:] = cur[-fade_len:] * (1.0 - ramp)
                grains[i] = cur

    return np.concatenate(grains, axis=0).astype(np.float32)


def stage_bitcrush(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    bits = int(rng.integers(4, 7))  # 4, 5, or 6
    levels = 2 ** (bits - 1)
    return (np.round(x * levels) / levels).astype(np.float32)


def stage_sr_reduction(x: np.ndarray, sr: int,
                        rng: np.random.Generator) -> np.ndarray:
    """Decimate to 8–11 kHz (sample-and-hold) then upsample back."""
    target = int(rng.integers(8000, 11001))
    factor = max(1, sr // target)
    if factor <= 1:
        return x
    n = x.shape[0]
    # Sample-and-hold: take every Nth sample, repeat to original length.
    idx = (np.arange(n) // factor) * factor
    idx = np.minimum(idx, n - 1)
    return x[idx].astype(np.float32)


def stage_hard_clip(x: np.ndarray, drive_db: float = 18.0) -> np.ndarray:
    drive = 10.0 ** (drive_db / 20.0)
    y = np.tanh(x * drive)
    return _normalize(y, 0.95)


def stage_ring_mod(x: np.ndarray, sr: int,
                    rng: np.random.Generator, mix: float = 0.30) -> np.ndarray:
    freq = float(rng.uniform(30.0, 120.0))
    n = x.shape[0]
    t = np.arange(n, dtype=np.float32) / sr
    car = np.sin(2.0 * np.pi * freq * t).astype(np.float32)[:, None]
    return ((1.0 - mix) * x + mix * (x * car)).astype(np.float32)


def stage_comb(x: np.ndarray, sr: int,
                rng: np.random.Generator, fb: float = 0.6) -> np.ndarray:
    """Short feedback comb filter — metallic ringing.

    y[n] = x[n] + fb * y[n - D]
    """
    delay_ms = float(rng.uniform(2.0, 15.0))
    D = max(1, int(sr * delay_ms / 1000.0))
    n = x.shape[0]
    y = x.copy()
    # Process per-channel; vectorize across channels.
    for i in range(D, n):
        y[i] = x[i] + fb * y[i - D]
    return _normalize(y, 0.95)


def stage_reverse_smear(x: np.ndarray, sr: int,
                         rng: np.random.Generator,
                         mix: float = 0.25) -> np.ndarray:
    """Fake reverse-tail reverb: reverse, low-pass smear, reverse, mix in."""
    rev = x[::-1].copy()
    # One-pole low-pass smear.
    a = 0.92
    smear = np.empty_like(rev)
    smear[0] = rev[0]
    for i in range(1, rev.shape[0]):
        smear[i] = a * smear[i - 1] + (1.0 - a) * rev[i]
    tail = smear[::-1]
    # Slight delay so the tail leads the dry hit.
    delay = int(sr * 0.04)
    if delay > 0 and delay < x.shape[0]:
        padded = np.zeros_like(tail)
        padded[delay:] = tail[: tail.shape[0] - delay]
        tail = padded
    return ((1.0 - mix) * x + mix * tail).astype(np.float32)


def stage_dropouts(x: np.ndarray, sr: int,
                    rng: np.random.Generator) -> np.ndarray:
    """Mute 20–80 ms windows at ~2 Hz."""
    n = x.shape[0]
    duration_s = n / sr
    n_drops = max(1, int(duration_s * 2.0))
    y = x.copy()
    for _ in range(n_drops):
        win_ms = float(rng.uniform(20.0, 80.0))
        win = max(1, int(sr * win_ms / 1000.0))
        if n - win <= 0:
            continue
        start = int(rng.integers(0, n - win))
        # Quick fade in/out on the dropout edges to avoid clicks.
        edge = min(64, win // 4)
        ramp = np.linspace(1.0, 0.0, edge, dtype=np.float32)[:, None]
        y[start:start + edge] *= ramp
        y[start + edge:start + win - edge] = 0.0
        if win - edge < y.shape[0] - start:
            y[start + win - edge:start + win] *= ramp[::-1]
    return y


# ============================================================ pipeline


def chew(audio: np.ndarray, sr: int = 44100, seed: int | None = None) -> np.ndarray:
    """Apply the full distortion chain. Deterministic given ``seed``."""
    if seed is None:
        seed = int(np.random.SeedSequence().entropy & 0x7FFFFFFF)
    rng = np.random.default_rng(seed)

    x = _ensure_stereo(audio)

    x = stage_pitch_speed(x, rng)
    x = stage_granular_shuffle(x, sr, rng)
    x = stage_bitcrush(x, rng)
    x = stage_sr_reduction(x, sr, rng)
    x = stage_hard_clip(x, drive_db=18.0)
    x = stage_ring_mod(x, sr, rng, mix=0.30)
    x = stage_comb(x, sr, rng, fb=0.6)
    x = stage_reverse_smear(x, sr, rng, mix=0.25)
    x = stage_dropouts(x, sr, rng)

    return _normalize(x, 0.95)


# ============================================================ CLI


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m carpeteater.audio_fx",
        description="Apply the Carpet Eater DSP chain to an audio file.",
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=None,
                        help="Deterministic seed (default: derived from file).")
    args = parser.parse_args(argv)

    # Local imports so importing audio_fx as a library doesn't drag these in.
    from .processor import decode_to_numpy, seed_for_file, write_wav

    audio = decode_to_numpy(args.input)
    seed = args.seed if args.seed is not None else seed_for_file(args.input)
    print(f"chewing {args.input} (seed={seed}, frames={audio.shape[0]})")
    chewed = chew(audio, sr=44100, seed=seed)
    write_wav(args.output, chewed)
    print(f"wrote {args.output} ({chewed.shape[0]} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
