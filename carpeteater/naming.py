"""Output filename mangling.

Each chew escalates the input filename:

    song.mp3                       ->  song_chewed - tasted 47 GAMEY!!!.wav
    song_chewed.wav                ->  song_incestuous_chewed - tasted 12 PUTRID!!!.wav
    song_incestuous_chewed.wav     ->  song_putrid_incestuous_chewed - tasted 89 RANCID!!!.wav

Rules:
    * Any pre-existing ``" - tasted NN WORD!!!"`` suffix on the stem is
      stripped before parsing — the suffix only ever describes the most
      recent chew.
    * If the (cleaned) stem ends in ``_chewed``, a new escalation adjective
      is inserted just before ``_chewed``. Otherwise ``_chewed`` is simply
      appended (first chew never gets an adjective).
    * Each escalation picks an adjective the stem does not already contain;
      once all are exhausted, duplicates may appear.

All choices are driven by a numpy ``Generator`` so naming is deterministic
given the seed. The DSP chain and the naming use independent sub-streams
(see :func:`carpeteater.audio_fx.chew`) so changing one will not affect the
other.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# Adjectives prepended on each successive chew. Order doesn't matter —
# the chosen one is random, weighted only by what's already in the stem.
ESCALATION_WORDS = (
    "incestuous", "putrid", "gangrenous", "necrotic", "rancid",
    "fetid", "moldering", "decayed", "septic", "vile",
    "depraved", "festering", "rotten", "diseased", "macerated",
    "foetal", "bilious", "verminous", "weeping", "abscessed",
)

# Tasting notes appended in the suffix. Mostly visceral, with the occasional
# odd one out for variety.
TASTE_WORDS = (
    "HORRID", "GAMEY", "PUTRID", "REVOLTING", "FOUL", "RANCID",
    "RIPE", "ACRID", "METALLIC", "ASHEN", "BITTER", "SOUR",
    "SLIMY", "PUNGENT", "OFFAL", "FERMENTED", "MUSTY", "BURNT",
    "GREASY", "BLOATED", "VENOUS", "FERAL", "BILE", "VISCID",
    "CHALKY", "TANGY", "GAMY", "CHEESY", "EARTHY", "BOGGY",
    "GRISTLY", "GLANDULAR", "WORMY", "MARROWY", "SEPTIC",
    # rare easter eggs
    "DELICIOUS", "EXQUISITE", "MOREISH",
)

# Pattern: " - tasted 47 GAMEY!!!" at the end of a stem.
# Description must be uppercase letters only.
_TASTED_RE = re.compile(r" - tasted \d+ [A-Z]+!!!$")
_CHEWED_SUFFIX = "_chewed"


# Characters that NTFS forbids in filenames. ``!`` is allowed.
_FORBIDDEN = set('<>:"/\\|?*')


def _strip_tasted_suffix(stem: str) -> str:
    """Remove any prior ``" - tasted NN WORD!!!"`` suffix from a stem."""
    return _TASTED_RE.sub("", stem)


def _pick_adjective(prefix: str, rng: np.random.Generator) -> str:
    """Pick an escalation adjective the stem does not already contain."""
    parts = set(prefix.split("_"))
    available = [w for w in ESCALATION_WORDS if w not in parts]
    if not available:
        available = list(ESCALATION_WORDS)
    return available[int(rng.integers(0, len(available)))]


def escalate_chewed_stem(stem: str, rng: np.random.Generator) -> str:
    """Compute the escalated stem for the next chew.

    Strips any prior tasted suffix, then either appends ``_chewed`` (first
    chew) or inserts a new escalation adjective just before ``_chewed``.
    """
    stem = _strip_tasted_suffix(stem)

    if stem.endswith(_CHEWED_SUFFIX):
        prefix = stem[: -len(_CHEWED_SUFFIX)]
        adj = _pick_adjective(prefix, rng)
        if not prefix:
            return f"{adj}{_CHEWED_SUFFIX}"
        return f"{prefix}_{adj}{_CHEWED_SUFFIX}"
    return f"{stem}{_CHEWED_SUFFIX}"


def taste_suffix(rng: np.random.Generator) -> str:
    """Generate the ``" - tasted NN WORD!!!"`` suffix."""
    n = int(rng.integers(0, 100))
    word = TASTE_WORDS[int(rng.integers(0, len(TASTE_WORDS)))]
    return f" - tasted {n:02d} {word}!!!"


def sanitize(name: str) -> str:
    """Drop NTFS-forbidden characters. Preserves spaces and ``!``."""
    return "".join("_" if c in _FORBIDDEN else c for c in name)


def output_path_for(input_path: Path, rng: np.random.Generator,
                    extension: str = ".wav") -> Path:
    """Compute the output filename for a chew.

    ``rng`` should be a sub-stream dedicated to naming so changes to the
    DSP randomness don't perturb the filename.
    """
    new_stem = escalate_chewed_stem(input_path.stem, rng)
    suffix = taste_suffix(rng)
    name = sanitize(f"{new_stem}{suffix}{extension}")
    return input_path.with_name(name)
