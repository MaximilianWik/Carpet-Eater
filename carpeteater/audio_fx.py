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
    """Feedforward FIR comb sweep — smooth rise without metallic resonance.

    IIR feedback combs ring like a metal pipe (the "can" sound).
    FIR feedforward comb  y[n] = x[n] + fb * x[n-D]  creates the same
    sweeping comb coloration without any resonant buildup.

    D sweeps from 441 samples (100 Hz) down to 36 (1210 Hz) over 64 chunks.
    fb also rises from 0.30 → 0.85 so the effect builds gradually.
    Mixed 35 % wet so the source always stays clearly audible.
    """
    n, n_ch = x.shape
    fb_lo   = 0.40
    fb_hi   = 0.95
    D_start = max(1, int(round(sr / 100.0)))    # 441 samples — 100 Hz
    D_end   = max(1, int(round(sr / 1210.0)))   #  36 samples — 1210 Hz
    n_ch_   = n_ch
    n_chunks = 64
    chunk_n  = max(D_start, (n + n_chunks - 1) // n_chunks)

    ibuf = np.zeros((D_start, n_ch_), dtype=np.float64)
    out  = np.zeros_like(x, dtype=np.float32)

    ci, i = 0, 0
    while i < n:
        e       = min(i + chunk_n, n)
        seg     = x[i:e].astype(np.float64)
        seg_len = e - i

        prog = ci / max(1, n_chunks - 1)                    # 0 → 1
        D    = max(D_end, int(round(D_start - (D_start - D_end) * prog)))
        fb   = fb_lo + (fb_hi - fb_lo) * prog

        # Delayed version x[t-D] built from history buffer + current chunk
        extended = np.vstack([ibuf, seg])                    # (D_start+seg_len, n_ch)
        delayed  = extended[D_start - D : D_start + seg_len - D]
        y        = seg + fb * delayed

        out[i:e] = y.astype(np.float32)
        ibuf[:]  = extended[-D_start:]                       # roll history
        ci += 1
        i   = e

    mixed = 0.20 * x + 0.80 * out
    return _normalize(mixed, 0.95)

def chain_arpegiator(x: np.ndarray, sr: int,
                     rng: np.random.Generator) -> np.ndarray:
    """Natural-minor arpeggio via Hann-windowed gating of pre-shifted copies.

    Instead of hard segment cuts (which sound choppy and expose BPM
    assumptions), each scale degree is pitch-shifted across the whole
    file and then gated on/off with an overlapping Hann window centred
    at its step position.  Adjacent steps overlap by 50%, giving smooth
    pitch transitions with no clicks.  Steps are proportional to file
    length — tempo-agnostic.  Mixed 55% wet to keep the source audible.
    """
    from pedalboard import Pedalboard, PitchShift  # noqa: PLC0415
    n = x.shape[0]

    # Natural-minor i7 arpeggio: root, b3, 5, b7, octave, back down
    pattern  = [0, 3, 7, 10, 12, 10, 7, 3]
    n_steps  = len(pattern)
    step_len = n / n_steps          # float — window centre spacing
    # Window width = 2 × step spacing (50% overlap with neighbours)
    sigma    = step_len / 2.5       # Gaussian std ≈ half-step

    out = np.zeros_like(x, dtype=np.float64)

    for idx, semi in enumerate(pattern):
        board   = Pedalboard([PitchShift(semitones=float(semi))])
        shifted = board(x.T.astype(np.float32),
                        sample_rate=sr).T.astype(np.float64)   # (n, ch)

        # Gaussian envelope centred at this step
        center = (idx + 0.5) * step_len
        t      = np.arange(n, dtype=np.float64)
        env    = np.exp(-0.5 * ((t - center) / sigma) ** 2)
        out   += env[:, None] * shifted

    # Normalise the summed envelope so peak env = 1 (prevents loudness jump)
    env_sum = np.zeros(n, dtype=np.float64)
    for idx in range(n_steps):
        center   = (idx + 0.5) * step_len
        t        = np.arange(n, dtype=np.float64)
        env_sum += np.exp(-0.5 * ((t - center) / sigma) ** 2)
    env_sum = np.maximum(env_sum, 1e-9)
    out = (out / env_sum[:, None]).astype(np.float32)

    # Mix 55% arpeggio + 45% dry — source remains present
    mixed = 0.45 * x + 0.55 * out
    return _normalize(mixed, 0.95)


def chain_looper(x: np.ndarray, sr: int,
                 rng: np.random.Generator) -> np.ndarray:
    """ShaperBox FilterShaper (LP, subtle) + PanShaper + reverse-reverb loops.

    Reference analysis shows frequency content nearly identical to raw —
    the filter/phaser effect is subtle.  Main character: four 1/4-file
    reverse-reverb loops accumulate from bar 1-4, each at half volume.
    Slight 5 ms L/R delay on loops for stereo width.
    """
    from pedalboard import Pedalboard, Phaser, Reverb  # noqa: PLC0415
    from scipy.signal import butter, sosfilt  # noqa: PLC0415
    n, n_ch = x.shape

    # Very subtle LP sweep — barely audible but present (10 % wet)
    block, lp_mix = 2048, 0.30
    main_lpf = np.zeros_like(x, dtype=np.float32)
    zi = None
    for i in range(0, n, block):
        e = min(i + block, n)
        t_mid = (i + (e - i) / 2) / sr
        lfo   = 0.5 + 0.5 * np.sin(2.0 * np.pi * 0.5 * t_mid)  # 0.5 Hz
        fc    = np.exp(np.log(100.0) + lfo * (np.log(1200.0) - np.log(100.0)))
        fc_n  = min(fc / (sr / 2.0), 0.99)
        sos   = butter(2, fc_n, btype='low', output='sos')
        if zi is None:
            zi = np.zeros((sos.shape[0], 2, n_ch))
        chunk     = x[i:e].astype(np.float32)
        out_chunk = np.zeros_like(chunk)
        for ch in range(n_ch):
            out_chunk[:, ch], zi[:, :, ch] = sosfilt(
                sos, chunk[:, ch], zi=zi[:, :, ch])
        main_lpf[i:e] = out_chunk

    main = (1 - lp_mix) * x + lp_mix * main_lpf

    # Gentle phaser for PanShaper-style stereo movement
    ph = Pedalboard([Phaser(rate_hz=0.5, depth=1.0, centre_frequency_hz=650.0,
                            feedback=0.50, mix=0.40)])
    main = ph(main.T.astype(np.float32), sample_rate=sr).T.astype(np.float32)

    # Reverse-reverb loops (1/4 file each), tiled from bar start, at -6 dB
    reverb_board = Pedalboard([Reverb(room_size=0.85, damping=0.3,
                                      wet_level=0.70, dry_level=0.30)])
    loop_layer = np.zeros_like(main)   # loop accumulation separate from main
    chunk_n  = max(64, n // 4)
    smear_ms = int(sr * 0.005)

    for i in range(8):
        s, e = i * chunk_n, min((i + 1) * chunk_n, n)
        seg  = x[s:e].astype(np.float32)
        if seg.shape[0] < 64:
            continue
        wet = reverb_board(np.ascontiguousarray(seg[::-1]).T,
                           sample_rate=sr).T
        rr = np.ascontiguousarray(wet[::-1]).astype(np.float32)

        # Make rr circular: crossfade the last 1 s back into the first 1 s.
        # This eliminates the quiet tail → loud head jump at every tile seam
        # without shortening the loop (which made it sound choppy).
        xfade    = min(int(sr * 1.5), rr.shape[0] // 4)
        fade_out = np.linspace(1.0, 0.0, xfade, dtype=np.float32)[:, None]
        fade_in  = np.linspace(0.0, 1.0, xfade, dtype=np.float32)[:, None]
        rr[-xfade:] = rr[-xfade:] * fade_out + rr[:xfade] * fade_in

        # Tile seamlessly — no per-tile fade needed since rr is circular.
        # Only the very first tile of each new bar fades in (bar entry).
        fi_n   = min(int(sr * 0.100), rr.shape[0] // 8)   # 100 ms bar intro
        fi_env = np.linspace(0.0, 1.0, fi_n, dtype=np.float32)[:, None]
        pos = s
        ti  = 0
        while pos < n:
            ep   = min(pos + rr.shape[0], n)
            tile = rr[:ep - pos].copy()
            if ti == 0 and fi_n > 1 and len(tile) > fi_n:
                tile[:fi_n] *= fi_env
            loop_layer[pos:ep] += 0.85 * tile
            pos += rr.shape[0]
            ti  += 1

    # Smooth fade-out on the loop layer over the last loop-length so the
    # final tiled repetition decays to silence instead of cutting off hard.
    fade_len = min(chunk_n, n)
    fade_start = n - fade_len
    t_fade = np.linspace(0.0, np.pi / 2.0, fade_len, dtype=np.float32)
    fade_env = np.cos(t_fade) ** 2          # 1 → 0 cosine curve
    loop_layer[fade_start:] *= fade_env[:, None]

    out = main + loop_layer

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
