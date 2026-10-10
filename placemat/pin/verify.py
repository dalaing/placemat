"""Placement check after linking (DESIGN §6.3 step 6, §3.4 detector 2, §8.2).

Never trust the linker's silence: ld64 only warns about order-file entries it cannot find, and a
build script may discard the warning. ``verify`` reads the linked binary and checks that every
ordered symbol (pads included) is present, in order, contiguous (nothing else between them, no
gap of a whole alignment unit) and aligned; that no small loop in a listed function and no hot
entry span crosses the boundary; optionally that each hottest loop stays inside its line and
that functions sit at a plan's offsets.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .. import binary as B
from .pads import Plan, is_pad


@dataclasses.dataclass
class Report:
    ok: bool
    listed: int
    missing: list[str]
    out_of_order: list[tuple[str, str]]
    gaps: list[tuple[str, str, str]]                 # (before, after, what lies between)
    misaligned: list[tuple[str, int]]
    loop_crossings: list[tuple[str, int, int]]       # (function, loop start, loop end) addresses
    span_crossings: list[tuple[str, int, int]]       # (function, start, span length)
    line_crossings: list[tuple[str, int, int]] = dataclasses.field(default_factory=list)   # informational
    misplaced: list[tuple[str, int, int]] = dataclasses.field(default_factory=list)       # (function, offset, planned)
    listed_planned: int = 0
    boundary: int = 0                                # the plan's boundary (offsets are taken mod it)

    def lines(self) -> list[str]:
        out = [f"{'OK' if self.ok else 'FAILED'}: {self.listed} ordered symbols"]
        if self.missing:
            out.append(f"missing ({len(self.missing)}): {' '.join(self.missing[:20])}")
        for a, b in self.out_of_order[:20]:
            out.append(f"out of order: {b} before {a}")
        for a, b, w in self.gaps[:20]:
            out.append(f"not contiguous: {a} .. {b}: {w}")
        for n, s in self.misaligned[:20]:
            out.append(f"misaligned: {n} at {s:#x}")
        for n, s, e in self.loop_crossings[:20]:
            out.append(f"loop crosses: {n} {s:#x}..{e:#x} ({e - s} B)")
        for n, s, ln in self.span_crossings[:20]:
            out.append(f"hot span crosses: {n} {s:#x}+{ln}")
        if self.misplaced:
            P = self.boundary or 1 << 62
            shifts = {(x - p) % P for _, x, p in self.misplaced}
            if len(shifts) == 1 and len(self.misplaced) == self.listed_planned:
                s = shifts.pop()
                out.append(f"the whole ordered region moved by {s if s <= P // 2 else s - P:+d} bytes from the plan: "
                           "something before it changed size (on ELF, link with -z separate-code)")
        for n, x, p in self.misplaced[:20]:
            out.append(f"misplaced: {n} at offset {x} (planned {p})")
        for n, s, e in self.line_crossings[:20]:
            out.append(f"(info) hottest loop crosses a line: {n} {s:#x}..{e:#x}")
        return out

    def __str__(self) -> str:
        return "\n".join(self.lines())


def verify(binary, order_file, boundary: int = 4096, spans=None, max_loop: int = 256, align: int | None = 16,
           hot64=None, line: int = 64, plan: Plan | None = None) -> Report:
    """Check the linked ``binary`` against ``order_file`` (a path in any style, or a name list).
    ``spans``: {function: hot entry span} or a JSON file. ``hot64``: hottest loops (informational
    line check). ``plan``: a pads Plan whose offsets modulo its boundary must hold. ``align=None``
    skips the alignment check."""
    names = list(order_file) if isinstance(order_file, (list, tuple)) else B.read_order(order_file, binary)
    spans = json.loads(Path(spans).read_text()) if isinstance(spans, (str, Path)) else (spans or {})
    hot64 = json.loads(Path(hot64).read_text()) if isinstance(hot64, (str, Path)) else (hot64 or {})
    F = B.function_map(binary)
    present = [n for n in names if n in F]
    missing = [n for n in names if n not in F]
    ooo = [(a, b) for a, b in zip(present, present[1:]) if F[b].start <= F[a].start]
    loc = B._Locator(B.functions(binary))
    gaps = []
    for a, b in zip(present, present[1:]):
        if F[b].start <= F[a].start:
            continue
        between = sorted({f.name for f in loc.funcs if F[a].start < f.start < F[b].start} - {a, b})
        gap = F[b].start - F[a].end
        if between:
            gaps.append((a, b, ", ".join(between[:5]) + ("..." if len(between) > 5 else "")))
        elif align and gap >= align:
            gaps.append((a, b, f"{gap} bytes"))
    mis = [(n, F[n].start) for n in present if align and F[n].start % align]
    listed = {n for n in present if not is_pad(n)}
    lc = [(l.func, l.start, l.end) for l in B.loops(binary, max_loop)
          if l.func in listed and B.crosses(l.start, l.end, boundary)]
    sc = [(n, F[n].start, ln) for n, ln in spans.items()
          if n in listed and B.crosses(F[n].start, F[n].start + ln, boundary)]
    hc = [(n, F[n].start + v[0], F[n].start + v[1]) for n, v in hot64.items()
          if n in listed and B.crosses(F[n].start + v[0], F[n].start + v[1], line)]
    misplaced = []
    if plan is not None:
        for e in plan.entries:
            if e.name in F and F[e.name].start % plan.boundary != e.offset:
                misplaced.append((e.name, F[e.name].start % plan.boundary, e.offset))
    ok = not (missing or ooo or gaps or mis or lc or sc or misplaced)
    return Report(ok, len(names), missing, ooo, gaps, mis, lc, sc, hc, misplaced,
                  len(plan.entries) if plan is not None else 0, plan.boundary if plan is not None else 0)
