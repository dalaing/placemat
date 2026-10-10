"""Hot-set choice and the profile-derived regions the pads protect (DESIGN §6.3 steps 2 and 5).

- ``pick``: functions in order of combined hotness until every benchmark set reaches the target
  share of its in-binary samples.
- ``hot_spans``: each hot function's *hot entry span*, from its entry to the end of its last
  sampled offset under ``max_bytes`` that holds at least ``min_share`` of the function's samples
  (the instruction at that offset included).
- ``hot_loops64``: each hot function's hottest small loop (most samples among its loops of at most
  ``max_loop`` bytes; ties to the shorter, then the earlier), kept when it is at most ``line``
  bytes: the loop a pad can keep inside one cache line.

Offsets come from the profiled binary; they are applied to another build of the same code
(check with ``binary.same_code`` when in doubt: a hot function whose code changed has stale offsets).
"""
from __future__ import annotations

import bisect
import dataclasses

from .. import binary as B
from .profile import hotness, in_binary


@dataclasses.dataclass
class Selection:
    sel: list[str]
    cov: dict[str, list[float]]     # set -> [share of in-binary samples, share of all samples]

    def to_json(self) -> dict:
        return {"sel": self.sel, "cov": self.cov}


def pick(profile: dict, target: float = 0.95, sets=None) -> Selection:
    """Hot functions by combined hotness (ties by name) until each set covers ``target`` of its
    in-binary samples."""
    sets = list(sets or profile["self"].keys())
    shares = {s: in_binary(profile, s) for s in sets}
    tot = {s: sum(v.values()) or 1.0 for s, v in shares.items()}
    h = hotness(profile, sets=sets)
    order = sorted(h, key=lambda f: (-h[f], f))
    sel, cum = [], {s: 0.0 for s in sets}
    for f in order:
        if all(cum[s] / tot[s] >= target for s in sets):
            break
        sel.append(f)
        for s in sets:
            cum[s] += shares[s].get(f, 0.0)
    return Selection(sel, {s: [cum[s] / tot[s], cum[s]] for s in sets})


def _insn_ends(binary):
    """f(func, off) -> the offset just past the instruction at ``off`` (4 bytes on arm64 or
    without a binary)."""
    if binary is None or B.arch(binary) == "arm64":
        return lambda func, off: off + 4
    addrs = [x.addr for x in B.disassemble(binary)]

    def end(func, off):
        i = bisect.bisect_right(addrs, func.start + off)
        return (addrs[i] - func.start) if i < len(addrs) else off + 1
    return end


def hot_spans(profile: dict, binary=None, min_share: float = 0.10, max_bytes: int = 1024, *,
              selection=None) -> dict[str, int]:
    """{function: span length in bytes from its entry} for the hot functions that have one.
    ``selection``: the functions to consider (default: every function with sampled offsets)."""
    names = list(selection.sel if isinstance(selection, Selection) else selection or profile["offs"].keys())
    fm = B.function_map(binary) if binary is not None else {}
    ends = _insn_ends(binary)
    out = {}
    for f in names:
        o = profile["offs"].get(f)
        if not o:
            continue
        o = {int(k): v for k, v in o.items()}
        t = sum(o.values())
        c = [k for k, v in o.items() if 0 <= k < max_bytes and v >= min_share * t]
        if not c:
            continue
        last = max(c)
        out[f] = ends(fm[f], last) if f in fm else last + 4
    return out


def hot_loops64(profile: dict, binary, line: int = 64, max_loop: int = 256, *, selection=None) -> dict[str, list]:
    """{function: [loop start offset, loop end offset, share of the function's samples in it]}."""
    names = list(selection.sel if isinstance(selection, Selection) else selection or profile["offs"].keys())
    fm = B.function_map(binary)
    lo: dict[str, list[tuple[int, int]]] = {}
    for l in B.loops(binary, max_loop):
        if l.func in fm:
            s0 = fm[l.func].start
            lo.setdefault(l.func, []).append((l.start - s0, l.end - s0))
    out = {}
    for f in names:
        o = {int(k): v for k, v in profile["offs"].get(f, {}).items()}
        t = sum(o.values())
        cand = []
        for s, e in lo.get(f, []):
            w = sum(v for k, v in o.items() if s <= k < e)
            if w > 0:
                cand.append((w, -(e - s), -s, e))
        if not cand:
            continue
        w, _, ns, e = max(cand)
        if e + ns <= line:
            out[f] = [-ns, e, w / t]
    return out
