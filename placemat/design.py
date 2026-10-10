"""Layout variants: code pads, data colours and steps from one Kronecker sequence (DESIGN §5.2-5.3).

A *pad* is one code-layout build of an arm. Each pad is timed at one, two or three *data settings*:
the stock setting (no offset), its colour, and its step. A *variant* is one (pad, setting) pair.
Pads are the independent units of the interval; batches are counted in pads (§5.7).

Seeds come from one run seed through Python's random.Random, drawn in the Amber stage's order:
u and w2 by random(), then v by randrange(16), then w1 by random(). So seed 0 reproduces the Amber
stage's pads, and its steps in linear mode (Amber's "colours" were linear steps).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

PHI_INV = 2 / (1 + 5 ** .5)      # pads: golden ratio, the best 1-D gaps
SQRT2 = 2 ** .5                   # steps: 2s and 3s stay spread (blocks used together are 1-3 slots apart)
SQRT3 = 3 ** .5                   # colours: independent of both
M64 = (1 << 64) - 1

STOCK, COLOUR, STEP = "s", "c", "t"     # data-setting kinds within a pad
KIND_NAMES = {STOCK: "stock", COLOUR: "coloured", STEP: "stepped"}
DATA_MODES = {"none": (STOCK,), "colour": (STOCK, COLOUR), "step": (STOCK, STEP), "both": (STOCK, COLOUR, STEP)}


@dataclass(frozen=True)
class Setting:
    """A data setting: colour c (0: none) and a step (None: off; else a seed (hashed) or s (linear))."""
    kind: str
    colour: int = 0
    step: int | None = None

    def env(self, step_mode: str) -> dict[str, str]:
        e = {}
        if self.colour:
            e["PLACEMAT_COLOUR"] = str(self.colour)
        if self.step is not None:
            e["PLACEMAT_STEP_MODE"] = step_mode
            e["PLACEMAT_STEP_SEED" if step_mode == "hashed" else "PLACEMAT_STEP"] = str(self.step)
        return e

    def label(self) -> str:
        if self.kind == STOCK:
            return "0, off"
        if self.kind == COLOUR:
            return f"c {self.colour}, off"
        return f"0, step {self.step:#x}" if self.step is not None and self.step > 1 << 20 else f"0, step {self.step}"


@dataclass
class Design:
    seed: int = 0
    data: str = "both"                 # none | colour | step | both
    step_mode: str = "hashed"          # hashed | linear
    unit: int = 64                     # data offsets in whole units (never finer than the alignment guarantee)
    colour_span: int = 16384
    step_span: int = 16384
    period: int = 4096                 # code boundary the pads cover
    line: int = 64                     # the fine phase the pads also cover
    pads: list[int] = field(default_factory=list)   # explicit pads first (targeted variants, §5.6)
    start: int | None = None           # pads in the first batch
    batch_step: int | None = None      # pads added per later batch
    cap: int | None = None             # most pads per case

    def __post_init__(self):
        if self.data not in DATA_MODES:
            raise ValueError(f"data mode {self.data!r}: one of {', '.join(DATA_MODES)}")
        r = random.Random(self.seed)
        self.u, self.w2 = r.random(), r.random()
        self.v = r.randrange(16)
        self.w1 = r.random()
        one = len(self.kinds) == 1
        if self.start is None:
            self.start = 8 if one else 4
        if self.batch_step is None:
            self.batch_step = 4 if one else 2
        if self.cap is None:
            self.cap = 24 if one else 12
        self.cap = max(self.cap, self.start)
        self.pads = [int(p) for p in self.pads]

    @property
    def kinds(self) -> tuple[str, ...]:
        return DATA_MODES[self.data]

    # ---- the sequence --------------------------------------------------------------------------

    def pad(self, j: int) -> int:
        """Pad j in bytes: P_j = line*floor((period/line)*frac(u + j/phi)) + 4*((7j + v) mod (line/4)),
        after any explicit pads."""
        if j < len(self.pads):
            return self.pads[j]
        j -= len(self.pads)
        nl = self.period // self.line
        return self.line * int(nl * ((self.u + j * PHI_INV) % 1)) + 4 * ((7 * j + self.v) % (self.line // 4))

    def colour(self, j: int) -> int:
        """c_j = unit*floor((colour_span/unit)*frac(w1 + j*sqrt 3)), never 0 (one unit instead)."""
        c = self.unit * int((self.colour_span // self.unit) * ((self.w1 + j * SQRT3) % 1))
        return c or self.unit

    def step(self, j: int) -> int:
        """Hashed: seed_j = floor(2^64 * frac(w2 + j*sqrt 2)). Linear: s_j = unit*floor((step_span/unit)
        * frac(w2 + j*sqrt 2)), never 0 (one unit instead)."""
        f = (self.w2 + j * SQRT2) % 1
        if self.step_mode == "hashed":
            return int(f * 2.0 ** 64) & M64
        s = self.unit * int((self.step_span // self.unit) * f)
        return s or self.unit

    def setting(self, j: int, kind: str) -> Setting:
        if kind == STOCK:
            return Setting(STOCK)
        if kind == COLOUR:
            return Setting(COLOUR, colour=self.colour(j))
        return Setting(STEP, step=self.step(j))

    # ---- variants and batches ------------------------------------------------------------------

    def variants(self, j: int) -> list[str]:
        """The variant ids of pad j: '<j>.<kind>'."""
        return [f"{j}.{k}" for k in self.kinds]

    @staticmethod
    def parse(vid: str) -> tuple[int, str]:
        j, k = vid.split(".")
        return int(j), k

    def batch(self, j: int) -> int:
        """The batch pad j was timed in (variants of different batches ran minutes apart)."""
        return 0 if j < self.start else 1 + (j - self.start) // self.batch_step

    def batch_pads(self, b: int) -> range:
        if b == 0:
            return range(0, self.start)
        lo = self.start + (b - 1) * self.batch_step
        return range(lo, min(lo + self.batch_step, self.cap))

    def describe(self, m: int) -> list[str]:
        """The design's coverage over its first m pads."""
        pads = [self.pad(j) for j in range(m)]

        def gap(xs, n):
            xs = sorted(x % n for x in xs)
            return max([b - a for a, b in zip(xs, xs[1:])] + [xs[0] + n - xs[-1]]) if xs else n
        out = [f"{m} pads: {', '.join(map(str, pads))}; largest gap mod {self.period} {gap(pads, self.period)} B "
               f"(even: {self.period // max(1, m)}); {len({p % self.line for p in pads})} of {self.line // 4} phases "
               f"mod {self.line}, {len({p % 16 for p in pads})} of 4 mod 16."]
        if COLOUR in self.kinds:
            cs = [self.colour(j) for j in range(m)]
            out.append(f"colours (span {self.colour_span}, unit {self.unit}): {', '.join(map(str, cs))}; "
                       f"largest gap {gap(cs + [0], self.colour_span)} B.")
        if STEP in self.kinds:
            ss = [self.step(j) for j in range(m)]
            if self.step_mode == "hashed":
                out.append(f"hashed step seeds (span {self.step_span}, unit {self.unit}): "
                           + ", ".join(f"{s:#018x}" for s in ss) + ".")
            else:
                out.append(f"linear steps (span {self.step_span}, unit {self.unit}): {', '.join(map(str, ss))}; "
                           f"largest gap {gap(ss + [0], self.step_span)} B.")
        out.append(f"seeds: run seed {self.seed}; u {self.u:.6f}, v {self.v}, w1 {self.w1:.6f}, w2 {self.w2:.6f}.")
        return out

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in ("seed", "data", "step_mode", "unit", "colour_span", "step_span",
                                              "period", "line", "pads", "start", "batch_step", "cap")}

    @classmethod
    def from_json(cls, d: dict) -> "Design":
        return cls(**d)

