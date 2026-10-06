"""Pad functions between the ordered hot functions (DESIGN §6.3 step 5; Amber's ``pads.py``).

An exact dynamic programme over the start address modulo the boundary, in steps of the function
alignment, choosing a pad (0 or more alignment units) before each ordered function so as to
minimise the weighted cost

    w_loop * (small loops of a listed function straddling a boundary)
  + w_span * (hot entry spans straddling a boundary)
  + w_line * (hottest small loops straddling a ``line``-byte line)
  + w_byte * (pad bytes)

with the default weights (10**6, 10**4, 10**3, 1): loop crossings are effectively forbidden (any
start costs at most boundary - align pad bytes), a hot-span crossing survives only when avoiding it
would make a loop cross. Amber's build C2 is spans without lines; build D added lines.

Input: a binary built with the order file, no pads and ``-falign-functions=align``, so the
ordered functions are contiguous and aligned (checked). Nothing inside a function may be aligned
more than ``align`` (gcc aligns loops to 32 bytes by default on arm64, which makes each function's
section 32-aligned under ``-ffunction-sections``: add ``-falign-loops=align``), or the layout of
a function would depend on its address beyond what the search models. Output: an order file with pad functions
``placemat_pin_pad_<n>`` interleaved, and a C file defining them. A pad function holds ``.space
(pad - align)`` plus the compiler's own few bytes (a return; perhaps ``bti``/``endbr64``, a frame
setup at -O0), so with every function aligned to ``align`` its footprint is exactly the planned
pad provided that overhead is between 1 and ``align`` bytes. ``verify`` checks the result.
Regenerate the pads on every build: they belong to the build, not the source.

The search takes the first ordered function's address in the input binary as fixed. On Mach-O,
``__text`` follows the load commands. On ELF, lld and GNU ld place read-only sections before
``.text``, so link with ``-z separate-code`` (``.text`` then starts on a page, or after the fixed
``.init``/``.plt``), or the pads' own unwind info moves the ordered region.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .. import binary as B
from .order import order_text

PAD_PREFIX = "placemat_pin_pad_"
LEGACY_PAD_PREFIXES = ("am_of_pad_",)


def is_pad(name: str) -> bool:
    return name.startswith((PAD_PREFIX,) + LEGACY_PAD_PREFIXES)


@dataclasses.dataclass
class Entry:
    name: str
    pad: int          # bytes of pad placed just before this function
    offset: int       # the function's start address modulo the boundary


@dataclasses.dataclass
class Plan:
    entries: list[Entry]
    boundary: int
    align: int
    cost: float
    loop_crossings: list[tuple[str, int, int]]      # (function, loop start, loop end) offsets in the function
    span_crossings: list[tuple[str, int]]           # (function, span length)
    line_crossings: list[tuple[str, int, int]]      # (function, loop start, loop end)
    counts: dict                                     # loops / spans / lines considered
    notes: list[str] = dataclasses.field(default_factory=list)
    unavoidable: list[tuple[str, int, int]] = dataclasses.field(default_factory=list)   # loops longer than the boundary

    @property
    def pad_bytes(self) -> int:
        return sum(e.pad for e in self.entries)

    @property
    def pads(self) -> list[tuple[str, int]]:
        """(pad function name, planned footprint), in order."""
        out, k = [], 0
        for e in self.entries:
            if e.pad:
                k += 1
                out.append((f"{PAD_PREFIX}{k}", e.pad))
        return out

    def order(self) -> list[str]:
        """Function names with the pad functions interleaved."""
        out, k = [], 0
        for e in self.entries:
            if e.pad:
                k += 1
                out.append(f"{PAD_PREFIX}{k}")
            out.append(e.name)
        return out

    def summary(self) -> str:
        return (f"{len(self.entries)} functions, {len(self.pads)} pads, {self.pad_bytes} pad bytes; "
                f"residual crossings: {len(self.loop_crossings)} of {self.counts['loops']} small loops, "
                f"{len(self.span_crossings)} of {self.counts['spans']} hot spans, "
                f"{len(self.line_crossings)} of {self.counts['lines']} hottest loops across a line"
                + (f"; {len(self.unavoidable)} small loops longer than the boundary cross wherever placed"
                   if self.unavoidable else ""))

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        d["pad_bytes"] = self.pad_bytes
        d["pads"] = self.pads
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Plan":
        """A plan written by to_json (`placemat pin pads --plan`)."""
        tup = lambda xs: [tuple(x) for x in xs]
        return cls([Entry(**e) for e in d["entries"]], d["boundary"], d["align"], d["cost"],
                   tup(d["loop_crossings"]), tup(d["span_crossings"]), tup(d["line_crossings"]), d["counts"],
                   list(d.get("notes", [])), tup(d.get("unavoidable", [])))


def pad_source(plan: Plan, note: str = "") -> str:
    """C source defining the plan's pad functions."""
    a = plan.align
    lines = [
        "/* Generated by placemat pin pads: pad functions placed by the order file between hot functions.",
        f"   boundary {plan.boundary}, function alignment {a}; build with -falign-functions={a}"
        " (and -ffunction-sections on ELF).",
        f"   Each pad's footprint is .space + the compiler's 1..{a} bytes, rounded up to {a}."
        + (f"\n   {note}" if note else "") + " */",
        "#if defined(__ELF__) && defined(__has_attribute)",
        "#if __has_attribute(retain)",
        "#define PLACEMAT_PIN_KEEP __attribute__((used, retain))",
        "#endif",
        "#endif",
        "#ifndef PLACEMAT_PIN_KEEP",
        "#define PLACEMAT_PIN_KEEP __attribute__((used))",
        "#endif",
    ]
    for name, size in plan.pads:
        lines.append(f'PLACEMAT_PIN_KEEP void {name}(void) {{ __asm__ volatile(".space {size - a}"); }}')
    return "\n".join(lines) + "\n"


def _read_order(order, binary) -> list[str]:
    if isinstance(order, (list, tuple)):
        names = list(order)
    else:
        names = B.read_order(order, binary)
    return [n for n in names if not is_pad(n)]


def pads(binary, order, boundary: int = 4096, align: int = 16, max_loop: int = 256, spans=None, hot64=None,
         weights=(10**6, 10**4, 10**3, 1), line: int = 64, out_order=None, out_c=None, linker=None,
         loops=None, strict: bool = True) -> Plan:
    """Plan pads for ``order`` (names, or an order file of any style; pads already in it are
    dropped) on ``binary`` (built with that order, no pads, ``-falign-functions=align``).
    ``spans``: {function: hot entry span length} or a JSON file; ``hot64``: {function: [start,
    end, share]} or a JSON file (each hottest loop kept inside one ``line``); ``weights``: (loop
    crossing, span crossing, line crossing, pad byte). Writes ``out_order`` (style from ``linker``
    or the binary's format) and ``out_c`` when given. ``loops``: a precomputed loop list (default:
    ``binary.loops(binary, max_loop)``). ``strict``: raise when the ordered functions are not
    contiguous, in order and aligned in ``binary``."""
    if boundary % align:
        raise ValueError("boundary must be a multiple of align")
    spans = json.loads(Path(spans).read_text()) if isinstance(spans, (str, Path)) else (spans or {})
    hot64 = json.loads(Path(hot64).read_text()) if isinstance(hot64, (str, Path)) else (hot64 or {})
    w_loop, w_span, w_line, w_byte = weights
    names = _read_order(order, binary)
    F = B.function_map(binary)
    missing = [n for n in names if n not in F]
    if missing:
        raise ValueError(f"ordered functions missing from {binary}: {missing[:10]}")
    problems = []
    for n in names:
        if F[n].start % align:
            problems.append(f"{n} at {F[n].start:#x} is not {align}-aligned")
    for a, b in zip(names, names[1:]):
        if F[b].start < F[a].start:
            problems.append(f"{b} precedes {a}")
    loc = B._Locator(B.functions(binary))
    for a, b in zip(names, names[1:]):
        if F[b].start > F[a].start:
            between = [f.name for f in loc.funcs if F[a].start < f.start < F[b].start]
            if between or F[b].start - F[a].end >= align:
                problems.append(f"gap between {a} and {b}: {', '.join(between[:3]) or F[b].start - F[a].end}")
    if problems and strict:
        raise ValueError("binary does not hold the ordered functions contiguously in order and aligned "
                         f"(build it with the order file, no pads, -falign-functions={align}; gaps of "
                         f"{align} bytes or more usually mean code inside a function is aligned more "
                         f"than {align}, e.g. gcc's default -falign-loops=32 on arm64, which raises its "
                         f"section's alignment: add -falign-loops={align} or use a larger align): "
                         + "; ".join(problems[:8]))
    P, U = boundary, align
    NS = P // U
    # footprint: distance to the next ordered function (alignment gaps included), else rounded size
    size = {}
    for i, n in enumerate(names):
        if i + 1 < len(names) and F[names[i + 1]].start > F[n].start:
            size[n] = F[names[i + 1]].start - F[n].start
        else:
            size[n] = F[n].size
        size[n] = -(-size[n] // U) * U
    lo: dict[str, list[tuple[int, int]]] = {n: [] for n in names}
    unavoidable = []                        # longer than the boundary: they cross wherever they are placed
    for l in (loops if loops is not None else B.loops(binary, max_loop)):
        if l.func in lo and l.end - l.start <= max_loop:
            s, e = l.start - F[l.func].start, l.end - F[l.func].start
            if e - s > P:
                unavoidable.append((l.func, s, e))
            else:
                lo[l.func].append((s, e))

    def cost_loops(n, x):
        return sum(1 for s, e in lo[n] if (x + s) // P != (x + e - 1) // P)

    def cost_span(n, x):
        return int(n in spans and x // P != (x + spans[n] - 1) // P)

    def cost_line(n, x):
        if n not in hot64:
            return 0
        s, e = hot64[n][0], hot64[n][1]
        return int((x + s) // line != (x + e - 1) // line)

    INF = float("inf")
    S0 = F[names[0]].start
    cur = [INF] * NS
    cur[(S0 % P) // U] = 0
    back = []
    for n in names:
        nb = cur[:]
        arg = list(range(NS))
        for it in range(2 * NS):            # circular sweep: nb[x] = min(cur[x], nb[x-1] + one unit)
            x = it % NS
            y = (x - 1) % NS
            if nb[y] + U * w_byte < nb[x]:
                nb[x] = nb[y] + U * w_byte
                arg[x] = arg[y]
        nxt = [INF] * NS
        choice = [None] * NS
        su = size[n] // U
        for x in range(NS):
            if nb[x] == INF:
                continue
            xb = x * U
            c = nb[x] + w_loop * cost_loops(n, xb) + w_line * cost_line(n, xb) + w_span * cost_span(n, xb)
            e = (x + su) % NS
            if c < nxt[e]:
                nxt[e] = c
                choice[e] = (x, arg[x])
        back.append(choice)
        cur = nxt
    e = min(range(NS), key=lambda i: cur[i])
    total = cur[e]
    entries = []
    for n, choice in zip(reversed(names), reversed(back)):
        x, y = choice[e]
        entries.append(Entry(n, (x - y) % NS * U, x * U))
        e = y
    entries.reverse()
    lc, sc, hc = [], [], []
    for en in entries:
        x = en.offset
        for s, e2 in lo[en.name]:
            if (x + s) // P != (x + e2 - 1) // P:
                lc.append((en.name, s, e2))
        if cost_span(en.name, x):
            sc.append((en.name, spans[en.name]))
        if cost_line(en.name, x):
            hc.append((en.name, hot64[en.name][0], hot64[en.name][1]))
    notes = []
    bi = B.info(binary)
    if bi.fmt == "elf" and S0 % 4096 and S0 - bi.text[0] < P:
        notes.append(f"the ordered region starts at {S0:#x}, not on a page boundary: on ELF, check that it "
                     "does not move between builds (read-only sections placed before .text, such as "
                     ".eh_frame, grow with the pads' unwind info; -z separate-code avoids that); "
                     "verify(plan=...) checks")
    if problems:
        notes += problems
    plan = Plan(entries, P, U, total, lc, sc, hc,
                {"loops": sum(len(v) for v in lo.values()), "spans": sum(1 for n in names if n in spans),
                 "lines": sum(1 for n in names if n in hot64), "unavoidable": len(unavoidable)}, notes, unavoidable)
    if out_order:
        Path(out_order).write_text(order_text(plan.order(), linker or binary))
    if out_c:
        Path(out_c).write_text(pad_source(plan))
    return plan
