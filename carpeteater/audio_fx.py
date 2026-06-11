"""Distortion DSP chain.

All stages take and return float32 stereo numpy arrays of shape (N, 2)
in [-1, 1]. The full ``chew`` pipeline is deterministic given a seed,
so the same file always produces the same chewed output.

Multiple named chains are defined; ``chew()`` picks one at random per
seed. Each chain has its own ordering and parameters so files come out
sounding different from each other.

Run from CLI:
    python -m carpeteater.audio_fx input.wav output.wav [--seed N]
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
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
    """Linear-interpolation resample. ``ratio`` > 1 stretches longer."""
    n_in = x.shape[0]
    n_out = max(1, int(round(n_in * ratio)))
    src_idx = np.linspace(0, n_in - 1, n_out, dtype=np.float64)
    i0 = np.floor(src_idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, n_in - 1)
    frac = (src_idx - i0).astype(np.float32)[:, None]
    out = x[i0] * (1.0 - frac) + x[i1] * frac
    return out.astype(np.float32)


def _comb_vectorized(x: np.ndarray, D: int, fb: float,
                     threshold: float = 1e-4) -> np.ndarray:
    """Vectorized feedback comb filter ``y[n] = x[n] + fb * y[n - D]``.

    Computed as the geometric sum of fb^k * (x shifted by k*D) for k = 0, 1, ...
    until fb^k drops below ``threshold``. With fb=0.6 this terminates after
    ~18 iterations. With fb=0.95 it takes ~180. Far faster than a Python
    sample-by-sample loop.
    """
    n = x.shape[0]
    y = x.copy()
    coeff = fb
    k = 1
    while abs(coeff) > threshold and k * D < n:
        y[k * D:] += coeff * x[: n - k * D]
        coeff *= fb
        k += 1
    return y


def _one_pole_lp_fast(x: np.ndarray, a: float,
                      tail_threshold: float = 1e-3) -> np.ndarray:
    """Causal one-pole low-pass: ``y[n] = a * y[n-1] + (1-a) * x[n]``.

    Implemented as a finite-impulse-response convolution against the
    truncated geometric kernel ``(1-a) * a^k`` (taps until ``a^k`` is below
    ``tail_threshold``). Pure numpy, no scipy dep.
    """
    if a <= 0.0:
        return ((1 - a) * x).astype(np.float32)
    K = max(1, int(np.ceil(np.log(tail_threshold) / np.log(a))))
    K = min(K, x.shape[0])
    h = (1.0 - a) * (a ** np.arange(K, dtype=np.float64))
    n = x.shape[0]
    out = np.empty_like(x)
    for ch in range(x.shape[1]):
        y = np.convolve(x[:, ch].astype(np.float64), h, mode="full")[:n]
        out[:, ch] = y.astype(np.float32)
    return out


# ============================================================ stages


def stage_pitch_speed(x: np.ndarray, rng: np.random.Generator,
                      lo: float = -7.0, hi: float = 5.0) -> np.ndarray:
    """Random semitone shift via resampling. Length changes with pitch."""
    semitones = float(rng.uniform(lo, hi))
    factor = 2.0 ** (semitones / 12.0)
    return _resample_linear(x, 1.0 / factor)


def stage_granular_shuffle(x: np.ndarray, sr: int,
                           rng: np.random.Generator,
                           grain_lo_ms: float = 50.0,
                           grain_hi_ms: float = 200.0,
                           swap_frac: float = 0.20,
                           reverse_frac: float = 0.10) -> np.ndarray:
    """Slice into grains, swap a fraction of positions, reverse a fraction."""
    n = x.shape[0]
    if n < sr // 10:
        return x
    grain_ms = float(rng.uniform(grain_lo_ms, grain_hi_ms))
    grain_len = max(1, int(sr * grain_ms / 1000.0))
    indices = list(range(0, n, grain_len))
    grains = [x[i:i + grain_len] for i in indices]

    n_swaps = max(1, int(len(grains) * swap_frac))
    for _ in range(n_swaps):
        a = int(rng.integers(0, len(grains)))
        b = int(rng.integers(0, len(grains)))
        grains[a], grains[b] = grains[b], grains[a]

    for i in range(len(grains)):
        if rng.random() < reverse_frac:
            grains[i] = grains[i][::-1]

    fade_len = min(int(sr * 0.003), grain_len // 4)
    if fade_len > 1:
        ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)[:, None]
        for i in range(1, len(grains)):
            cur = grains[i]
            if cur.shape[0] >= fade_len:
                cur = cur.copy()
                cur[:fade_len] = cur[:fade_len] * ramp
                grains[i] = cur
        for i in range(len(grains) - 1):
            cur = grains[i]
            if cur.shape[0] >= fade_len:
                cur = cur.copy()
                cur[-fade_len:] = cur[-fade_len:] * (1.0 - ramp)
                grains[i] = cur

    return np.concatenate(grains, axis=0).astype(np.float32)


def stage_bitcrush(x: np.ndarray, rng: np.random.Generator,
                   bits_lo: int = 4, bits_hi: int = 6) -> np.ndarray:
    bits = int(rng.integers(bits_lo, bits_hi + 1))
    levels = 2 ** (bits - 1)
    return (np.round(x * levels) / levels).astype(np.float32)


def stage_sr_reduction(x: np.ndarray, sr: int,
                       rng: np.random.Generator,
                       target_lo: int = 8000,
                       target_hi: int = 11000) -> np.ndarray:
    """Sample-and-hold decimation to a low rate, held back to the original."""
    target = int(rng.integers(target_lo, target_hi + 1))
    factor = max(1, sr // target)
    if factor <= 1:
        return x
    n = x.shape[0]
    idx = (np.arange(n) // factor) * factor
    idx = np.minimum(idx, n - 1)
    return x[idx].astype(np.float32)


def stage_hard_clip(x: np.ndarray, drive_db: float = 18.0) -> np.ndarray:
    drive = 10.0 ** (drive_db / 20.0)
    y = np.tanh(x * drive)
    return _normalize(y, 0.95)


def stage_ring_mod(x: np.ndarray, sr: int,
                   rng: np.random.Generator,
                   freq_lo: float = 30.0, freq_hi: float = 120.0,
                   mix: float = 0.30) -> np.ndarray:
    freq = float(rng.uniform(freq_lo, freq_hi))
    n = x.shape[0]
    t = np.arange(n, dtype=np.float32) / sr
    car = np.sin(2.0 * np.pi * freq * t).astype(np.float32)[:, None]
    return ((1.0 - mix) * x + mix * (x * car)).astype(np.float32)


def stage_comb(x: np.ndarray, sr: int,
               rng: np.random.Generator,
               delay_lo_ms: float = 2.0, delay_hi_ms: float = 15.0,
               fb: float = 0.6) -> np.ndarray:
    """Short feedback comb filter for metallic ringing. Vectorized."""
    delay_ms = float(rng.uniform(delay_lo_ms, delay_hi_ms))
    D = max(1, int(sr * delay_ms / 1000.0))
    y = _comb_vectorized(x, D, fb)
    return _normalize(y, 0.95)


def stage_reverse_smear(x: np.ndarray, sr: int,
                        rng: np.random.Generator,
                        a: float = 0.92, mix: float = 0.25,
                        pre_delay_s: float = 0.04) -> np.ndarray:
    """Fake reverse-tail reverb via fast vectorized one-pole low-pass."""
    rev = np.ascontiguousarray(x[::-1])
    smear = _one_pole_lp_fast(rev, a)
    tail = smear[::-1]

    delay = int(sr * pre_delay_s)
    if 0 < delay < x.shape[0]:
        padded = np.zeros_like(tail)
        padded[delay:] = tail[: tail.shape[0] - delay]
        tail = padded

    return ((1.0 - mix) * x + mix * tail).astype(np.float32)


def stage_dropouts(x: np.ndarray, sr: int,
                   rng: np.random.Generator,
                   win_lo_ms: float = 20.0, win_hi_ms: float = 80.0,
                   rate_hz: float = 2.0) -> np.ndarray:
    n = x.shape[0]
    duration_s = n / sr
    n_drops = max(1, int(duration_s * rate_hz))
    y = x.copy()
    for _ in range(n_drops):
        win_ms = float(rng.uniform(win_lo_ms, win_hi_ms))
        win = max(1, int(sr * win_ms / 1000.0))
        if n - win <= 0:
            continue
        start = int(rng.integers(0, n - win))
        edge = min(64, win // 4)
        ramp = np.linspace(1.0, 0.0, edge, dtype=np.float32)[:, None]
        y[start:start + edge] *= ramp
        y[start + edge:start + win - edge] = 0.0
        if win - edge < y.shape[0] - start:
            y[start + win - edge:start + win] *= ramp[::-1]
    return y


# ============================================================ chains
#
# Each chain is a function (audio, sr, rng) -> audio.
# All take a *single* rng so the seed fully determines the output.


def chain_standard_mauling(x: np.ndarray, sr: int,
                           rng: np.random.Generator) -> np.ndarray:
    """The full nine-stage chain. Heavy and balanced."""
    x = stage_pitch_speed(x, rng)
    x = stage_granular_shuffle(x, sr, rng)
    x = stage_bitcrush(x, rng)
    x = stage_sr_reduction(x, sr, rng)
    x = stage_hard_clip(x, drive_db=18.0)
    x = stage_ring_mod(x, sr, rng, mix=0.30)
    x = stage_comb(x, sr, rng, fb=0.6)
    x = stage_reverse_smear(x, sr, rng, mix=0.25)
    x = stage_dropouts(x, sr, rng)
    return x


def chain_wet_slobber(x: np.ndarray, sr: int,
                      rng: np.random.Generator) -> np.ndarray:
    """Drowning, smeared, lots of reverb, less granular destruction."""
    x = stage_pitch_speed(x, rng, lo=-9.0, hi=2.0)         # darker
    x = stage_bitcrush(x, rng, bits_lo=5, bits_hi=7)        # gentler crush
    x = stage_sr_reduction(x, sr, rng, target_lo=10000, target_hi=14000)
    x = stage_hard_clip(x, drive_db=10.0)                   # less aggressive
    x = stage_reverse_smear(x, sr, rng, a=0.95, mix=0.55,   # heavy smear
                            pre_delay_s=0.08)
    x = stage_comb(x, sr, rng, delay_lo_ms=12.0, delay_hi_ms=30.0, fb=0.5)
    x = stage_dropouts(x, sr, rng, win_lo_ms=10.0, win_hi_ms=40.0, rate_hz=1.0)
    return x


def chain_bone_grinder(x: np.ndarray, sr: int,
                       rng: np.random.Generator) -> np.ndarray:
    """Metallic, abrasive, no smear, heavy comb + clip."""
    x = stage_pitch_speed(x, rng, lo=-3.0, hi=7.0)          # brighter
    x = stage_granular_shuffle(x, sr, rng, swap_frac=0.10, reverse_frac=0.05)
    x = stage_bitcrush(x, rng, bits_lo=3, bits_hi=5)        # harsher
    x = stage_hard_clip(x, drive_db=22.0)                   # heavy drive
    x = stage_ring_mod(x, sr, rng, freq_lo=80.0, freq_hi=240.0, mix=0.45)
    x = stage_comb(x, sr, rng, delay_lo_ms=1.0, delay_hi_ms=6.0, fb=0.75)
    x = stage_dropouts(x, sr, rng, win_lo_ms=10.0, win_hi_ms=30.0, rate_hz=3.0)
    return x


def chain_pulper(x: np.ndarray, sr: int,
                 rng: np.random.Generator) -> np.ndarray:
    """Granular shuffle dominant. Lots of stutter, no smear, no comb."""
    x = stage_granular_shuffle(x, sr, rng, grain_lo_ms=20.0, grain_hi_ms=100.0,
                               swap_frac=0.45, reverse_frac=0.25)
    x = stage_pitch_speed(x, rng, lo=-5.0, hi=5.0)
    x = stage_granular_shuffle(x, sr, rng, grain_lo_ms=80.0, grain_hi_ms=300.0,
                               swap_frac=0.35, reverse_frac=0.15)
    x = stage_bitcrush(x, rng, bits_lo=5, bits_hi=7)
    x = stage_ring_mod(x, sr, rng, freq_lo=40.0, freq_hi=160.0, mix=0.20)
    x = stage_hard_clip(x, drive_db=12.0)
    x = stage_dropouts(x, sr, rng, win_lo_ms=30.0, win_hi_ms=150.0, rate_hz=4.0)
    return x


def chain_stomach_acid(x: np.ndarray, sr: int,
                       rng: np.random.Generator) -> np.ndarray:
    """Maximum digital decay. Extreme bitcrush + SR reduction + dropouts."""
    x = stage_bitcrush(x, rng, bits_lo=2, bits_hi=4)        # brutal
    x = stage_sr_reduction(x, sr, rng, target_lo=4000, target_hi=8000)
    x = stage_hard_clip(x, drive_db=20.0)
    x = stage_ring_mod(x, sr, rng, freq_lo=60.0, freq_hi=180.0, mix=0.35)
    x = stage_comb(x, sr, rng, delay_lo_ms=4.0, delay_hi_ms=12.0, fb=0.7)
    x = stage_dropouts(x, sr, rng, win_lo_ms=40.0, win_hi_ms=120.0, rate_hz=2.5)
    x = stage_reverse_smear(x, sr, rng, a=0.88, mix=0.20)
    return x



def chain_comb_riser(x: np.ndarray, sr: int,
                     rng: np.random.Generator) -> np.ndarray:
    """Feedback comb at 1210 Hz, resonance rises from 0 to ~100% over the file.

    IIR comb: y[n] = x[n] + fb * y[n-D], D = round(sr/1210) ~= 36 samples.
    Processed in 32 chunks; fb rises linearly 0.0 -> 0.97.
    scipy lfilter zi carries delay-buffer state across chunk boundaries.
    """
    from scipy.signal import lfilter  # noqa: PLC0415
    n, n_ch = x.shape
    D = max(1, int(round(sr / 1210.0)))
    n_chunks = 32
    chunk_len = max(D * 4, (n + n_chunks - 1) // n_chunks)

    b = np.zeros(D + 1, dtype=np.float64); b[0] = 1.0
    a_base = np.zeros(D + 1, dtype=np.float64); a_base[0] = 1.0

    out = np.zeros_like(x, dtype=np.float32)
    zi = np.zeros((D, n_ch), dtype=np.float64)
    i, chunk_idx = 0, 0
    while i < n:
        e = min(i + chunk_len, n)
        seg = x[i:e].astype(np.float64)
        fb = chunk_idx / max(1, n_chunks - 1) * 0.97
        a = a_base.copy(); a[D] = -fb
        y, zi = lfilter(b, a, seg, axis=0, zi=zi)
        out[i:e] = y.astype(np.float32)
        i = e; chunk_idx += 1
    return _normalize(out, 0.95)


def chain_arpegiator(x: np.ndarray, sr: int,
                     rng: np.random.Generator) -> np.ndarray:
    """Duration-preserving pitch-shift through a natural-minor arpeggio.

    Uses pedalboard.PitchShift (PSOLA). Pattern [12,3,3,7,7,3,12,7]
    at 0.25s/step (8th-note @ 120 BPM) matches the reference:
      t=0.00->+12 (656 Hz), t=0.25->+3 (232 Hz), t=0.50->+3,
      t=0.75->+7 (488 Hz), t=1.00->+7, t=1.25->+3, t=1.50->+12.
    """
    from pedalboard import Pedalboard, PitchShift  # noqa: PLC0415
    n = x.shape[0]
    pattern = [12, 3, 3, 7, 7, 3, 12, 7]
    step_len = max(1, sr // 4)          # 0.25 s = 8th note @ 120 BPM
    n_steps = (n + step_len - 1) // step_len
    fade = min(int(sr * 0.005), step_len // 4)
    out = np.zeros_like(x)
    for i in range(n_steps):
        semi = pattern[i % len(pattern)]
        start = i * step_len
        end = n if i == n_steps - 1 else start + step_len
        chunk = x[start:end].astype(np.float32)
        if chunk.shape[0] < 32:
            continue
        board = Pedalboard([PitchShift(semitones=float(semi))])
        shifted = board(chunk.T, sample_rate=sr).T.astype(np.float32)
        if fade > 1:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
            shifted[:fade] *= ramp
            shifted[-fade:] *= ramp[::-1]
        out[start:end] = shifted[:end - start]
    return _normalize(out, 0.95)


def chain_looper(x: np.ndarray, sr: int,
                 rng: np.random.Generator) -> np.ndarray:
    """LPF sweep + phaser on main signal, plus accumulated reverse-reverb loops.

    1. LPF cutoff sweeps 100 Hz <-> 1200 Hz at 1 Hz (slow fader).
       scipy butter sosfilt with state carried across 2048-sample blocks.
    2. pedalboard Phaser: fb=0.25, mix=0.20 (stereo width).
    3. Four 1/4-file loops: each is reverse-reverbed (Reverb on reversed
       chunk, flipped back) and tiled at half volume from its bar start.
    """
    from pedalboard import Pedalboard, Phaser, Reverb  # noqa: PLC0415
    from scipy.signal import butter, sosfilt  # noqa: PLC0415
    n, n_ch = x.shape

    # --- 1. LPF sweep ---
    block = 2048
    main_lpf = np.zeros_like(x, dtype=np.float32)
    zi = None
    for i in range(0, n, block):
        e = min(i + block, n)
        t_mid = (i + (e - i) / 2) / sr
        lfo = 0.5 + 0.5 * np.sin(2.0 * np.pi * 1.0 * t_mid)
        fc = np.exp(np.log(100.0) + lfo * (np.log(1200.0) - np.log(100.0)))
        fc_norm = min(fc / (sr / 2.0), 0.99)
        sos = butter(2, fc_norm, btype='low', output='sos')
        if zi is None:
            zi = np.zeros((sos.shape[0], 2, n_ch))
        chunk = x[i:e].astype(np.float32)
        out_chunk = np.zeros_like(chunk)
        for ch in range(n_ch):
            out_chunk[:, ch], zi[:, :, ch] = sosfilt(sos, chunk[:, ch], zi=zi[:, :, ch])
        main_lpf[i:e] = out_chunk

    # --- 2. Phaser ---
    phaser_board = Pedalboard([Phaser(
        rate_hz=1.0, depth=1.0, centre_frequency_hz=650.0,
        feedback=0.25, mix=0.20,
    )])
    main = phaser_board(main_lpf.T.astype(np.float32), sample_rate=sr).T.astype(np.float32)

    # --- 3. Reverse-reverb loops ---
    reverb_board = Pedalboard([Reverb(room_size=0.85, damping=0.4,
                                      wet_level=0.8, dry_level=0.2)])
    out = main.copy()
    chunk_len = max(64, n // 4)
    stereo_ms = int(sr * 0.005)
    for i in range(4):
        s, e = i * chunk_len, min((i + 1) * chunk_len, n)
        seg = x[s:e].astype(np.float32)
        if seg.shape[0] < 64:
            continue
        wet = reverb_board(np.ascontiguousarray(seg[::-1]).T, sample_rate=sr).T
        rr = np.ascontiguousarray(wet[::-1]).astype(np.float32)
        if rr.shape[1] >= 2 and stereo_ms > 0 and rr.shape[0] > stereo_ms:
            rr = np.stack([rr[:, 0],
                           np.concatenate([np.zeros(stereo_ms, dtype=np.float32),
                                           rr[:-stereo_ms, 1]])], axis=1)
        pos = s
        while pos < n:
            ep = min(pos + rr.shape[0], n)
            out[pos:ep] += 0.5 * rr[:ep - pos]
            pos += rr.shape[0]
    return _normalize(out, 0.95)


# Registry: name -> function. Order doesn't matter; selection is random.
CHAINS: dict[str, Callable[[np.ndarray, int, np.random.Generator], np.ndarray]] = {
    "standard_mauling": chain_standard_mauling,
    "wet_slobber":      chain_wet_slobber,
    "bone_grinder":     chain_bone_grinder,
    "pulper":           chain_pulper,
    "stomach_acid":     chain_stomach_acid,
    "comb_riser":       chain_comb_riser,
    "arpegiator":       chain_arpegiator,
    "looper":           chain_looper,
}


# ============================================================ pipeline


def chew(audio: np.ndarray, sr: int = 44100,
         seed: int | None = None,
         chain: str | None = None) -> tuple[str, np.ndarray]:
    """Apply a randomly-chosen DSP chain. Deterministic given ``seed``.

    Returns ``(chain_name, audio)``. Pass ``chain=name`` to force a chain.
    """
    if seed is None:
        seed = int(np.random.SeedSequence().entropy & 0x7FFFFFFF)

    # Use a SeedSequence so the chain choice and DSP randomness are independent.
    ss = np.random.SeedSequence(seed)
    chain_seed, dsp_seed = ss.generate_state(2, dtype=np.uint32)

    if chain is None:
        chain_rng = np.random.default_rng(int(chain_seed))
        names = sorted(CHAINS.keys())
        chain = names[int(chain_rng.integers(0, len(names)))]
    elif chain not in CHAINS:
        raise ValueError(f"unknown chain {chain!r}; available: {list(CHAINS)}")

    rng = np.random.default_rng(int(dsp_seed))
    x = _ensure_stereo(audio)
    x = CHAINS[chain](x, sr, rng)
    x = _normalize(x, 0.95)
    return chain, x


# ============================================================ CLI


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m carpeteater.audio_fx",
        description="Apply a Carpet Eater DSP chain to an audio file.",
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=None,
                        help="Deterministic seed (default: derived from file).")
    parser.add_argument("--chain", choices=sorted(CHAINS.keys()), default=None,
                        help="Force a specific chain (default: random per seed).")
    parser.add_argument("--list-chains", action="store_true")
    args = parser.parse_args(argv)

    if args.list_chains:
        for name in sorted(CHAINS):
            print(name)
        return 0

    from .processor import decode_to_numpy, seed_for_file, write_wav

    audio = decode_to_numpy(args.input)
    seed = args.seed if args.seed is not None else seed_for_file(args.input)
    print(f"chewing {args.input} (seed={seed}, frames={audio.shape[0]})")
    chain_name, chewed = chew(audio, sr=44100, seed=seed, chain=args.chain)
    print(f"chain: {chain_name}")
    write_wav(args.output, chewed)
    print(f"wrote {args.output} ({chewed.shape[0]} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
