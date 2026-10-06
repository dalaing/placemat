"""Data placement arithmetic shared by the hook header, the interposer and re-analysis (DESIGN §5.3, §7).

Every function here has a twin in data/placemat.h; tests/test_colour_c.py checks that they agree.

A coloured block k (its position on one fixed granule from one per-process base) gets the offset

    (colour mod colour_span) + step offset

where the step offset is, for hashed steps, unit * (H(seed ^ k) mod (step_span / unit)) with H the
splitmix64 finaliser (its golden-gamma add included) and k taken mod 2^64; for linear steps,
(s * (k + 1)) mod step_span. When the block's spare room is smaller than the offset, the offset is
wrapped modulo the largest whole number of units that fits (0 below one unit).
"""
from __future__ import annotations

M64 = (1 << 64) - 1
GAMMA = 0x9E3779B97F4A7C15


def mix64(z: int) -> int:
    z &= M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


def splitmix64(x: int) -> int:
    """H(x) = mix(x + golden gamma), all mod 2^64."""
    return mix64((x + GAMMA) & M64)


def placemat_k(addr: int, base: int, granule: int) -> int:
    """A block's k: floor((addr - base) / granule), signed (a block can lie below the base)."""
    return (addr - base) // granule


def step_offset(mode: str | None, step: int | None, k: int, unit: int = 64, span: int = 16384) -> int:
    """The step part of block k's offset. mode 'hashed' (step is the seed), 'linear' (step is s), or
    None (no step)."""
    if mode is None or step is None:
        return 0
    if mode == "hashed":
        return unit * (splitmix64((step ^ (k & M64)) & M64) % (span // unit))
    if mode == "linear":
        return (step * (k + 1)) % span
    raise ValueError(f"step mode {mode!r}")


def wrap(offset: int, spare: int, unit: int = 64) -> int:
    """The offset a block with `spare` bytes of room actually gets: unchanged when it fits; otherwise
    wrapped modulo the largest whole number of units that fits (0 below one unit)."""
    if offset <= spare:
        return offset
    n = spare // unit
    if n == 0:
        return 0
    return unit * ((offset // unit) % n)


def block_offset(k: int, colour: int = 0, step_mode: str | None = None, step: int | None = None, *,
                 unit: int = 64, colour_span: int = 16384, step_span: int = 16384,
                 spare: int | None = None) -> int:
    """Block k's offset at data setting (colour, step), before (spare None) or after wrapping."""
    off = colour % colour_span + step_offset(step_mode, step, k, unit, step_span)
    return off if spare is None else wrap(off, spare, unit)
