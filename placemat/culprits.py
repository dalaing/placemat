"""Culprits for a code-flagged case (DESIGN §6.2, §5.6): the small loops that cross the code boundary
in every slow stock-setting variant and in no fast one (static candidates for the cause), the share of
code layouts in which any of them crosses (their crossing windows over one period, from the pad-0
build: the weight of the bad layouts), the observed cost there, and pads inside and outside each
window to time as targeted variants (`placemat run --pads`).

Before that, the address phase (DESIGN §11 item 11): whether the slow pads' code shifts fall in phases
mod some power of two that the fast pads' do not, with a permutation p over pads (the slow/fast labels
shuffled over the pads), corrected for the moduli tried. With as many phases as pads any split
separates, and one slow pad of four separates at the first modulus where its phase is unique, so a
separation alone means nothing; only a small corrected p is called an effect.

Pads are compared within their batch: each pad's time against pad 0 (batch 0) or its batch's anchor
(pad 0 timed again) in the same rounds, so a slow batch does not make its pads look slow.
The phase test needs only the raw file; the loop candidates need the run's binaries, and are skipped
(with a note) when they are gone.
"""
from __future__ import annotations

import bisect
import itertools
import math
import random
import statistics
from pathlib import Path

from . import binary as BI
from .design import STOCK, Design
from .report import load_raw

ALPHA = 0.05         # a phase is called an effect when its corrected permutation p is at most this
MAX_EXACT = 20000    # enumerate every split up to this many; sample this many beyond


def _rel_loops(path: Path, boundary: int) -> set[tuple[str, int, int]]:
    fs = BI.functions(path)
    starts = [f.start for f in fs]
    out = set()
    for lp in BI.loops(path):
        if not BI.crosses(lp.start, lp.end, boundary):
            continue
        i = bisect.bisect_right(starts, lp.start) - 1
        if i >= 0:
            out.add((fs[i].name, lp.start - fs[i].start, lp.end - lp.start))
    return out


# ---- pad levels, within batches -----------------------------------------------------------------

def _ratio(xs: list, ref: list) -> float | None:
    """The median over rounds of xs[r] / ref[r] (rounds where both have a time), or None."""
    rs = [x[0] / y[0] for x, y in zip(xs, ref) if x[0] and y[0] and x[0] > 0 and y[0] > 0]
    return statistics.median(rs) if rs else None


def pad_levels(t: dict, D: Design, m: int) -> tuple[dict[int, float], list[str]]:
    """Each pad's stock-setting time on pad 0's scale, from one arm's raw times `t` (slot -> [(time, cov)]),
    and notes. A pad is compared with the reference timed in the same rounds of its own batch: pad 0 in
    batch 0, the batch's anchor ('a<b>', pad 0 timed again) in later batches. So drift between batches
    (and within one, round by round) cancels. A batch without an anchor (an old raw file) keeps its own
    scale, and the notes say so."""
    p0 = t.get(f"0.{STOCK}", [])
    v0 = [v for v, _ in p0 if v]
    if not v0:
        return {}, ["pad 0 has no times"]
    c0 = statistics.median(v0)
    out, raw_scale = {}, []
    for j in range(m):
        xs = t.get(f"{j}.{STOCK}", [])
        if not [v for v, _ in xs if v]:
            continue
        b = D.batch(j)
        ref = p0 if b == 0 else t.get(f"a{b}", [])
        r = _ratio(xs, ref) if ref else None
        if r is None:                          # no anchor: this batch on its own scale
            raw_scale.append(j)
            r = statistics.median(v for v, _ in xs if v) / c0
        out[j] = r * c0
    notes = []
    if raw_scale:
        notes.append(f"no anchor in the batches of pads {', '.join(str(D.pad(j)) for j in raw_scale)}: "
                     "their times are not corrected for drift between batches")
    return out, notes


# ---- the phase test --------------------------------------------------------------------------------

def _errors(cls: list[int], lab: tuple, ncls: int) -> int:
    """The pads the best phase rule misplaces: per phase class, the smaller of its slow and fast counts."""
    s, n = [0] * ncls, [0] * ncls
    for c, x in zip(cls, lab):
        n[c] += 1
        s[c] += x
    return sum(min(a, b - a) for a, b in zip(s, n))


def phase_test(shift: dict[int, int], slow: list[int], fast: list[int], period: int, alpha: float = ALPHA,
               seed: int = 0) -> dict | None:
    """Do the slow pads' code shifts fall in phases (mod m, m a power of two from 4 to `period`) that the fast
    pads' do not, more than a random split of the same pads would? The statistic at each m: the pads the best
    phase rule misplaces (per phase, the smaller of its slow and fast counts; 0: the phases separate). Its
    permutation p: the share of the splits of these pads into as many slow and fast pads (all of them, or a
    sample) that misplace as few. The moduli are corrected together by the permutation distribution of the
    smallest p (Westfall-Young min-p). `floor`: the corrected p a perfect separation would get at best; above
    `alpha`, these pads cannot tell (one slow pad of four, or as many phases as pads)."""
    pads = [j for j in slow + fast if j in shift]
    k = sum(1 for j in pads if j in slow)
    n = len(pads)
    if k == 0 or k == n:
        return None
    mods = []
    m = 4
    while m <= period:
        mods.append(m)
        m *= 2
    cls = {}
    for m in mods:
        ph = sorted({shift[j] % m for j in pads})
        cls[m] = [ph.index(shift[j] % m) for j in pads]
    obs = tuple(1 if j in slow else 0 for j in pads)
    total = math.comb(n, k)
    if total <= MAX_EXACT:
        splits = [tuple(1 if i in c else 0 for i in range(n)) for c in itertools.combinations(range(n), k)]
        exact = True
    else:
        rnd = random.Random(seed)
        splits = [obs] + [tuple(rnd.sample(obs, n)) for _ in range(MAX_EXACT - 1)]
        exact = False
    N = len(splits)
    E = {m: [_errors(cls[m], s, max(cls[m]) + 1) for s in splits] for m in mods}
    Eo = {m: _errors(cls[m], obs, max(cls[m]) + 1) for m in mods}
    # per modulus: p of a misplacement count e is the share of splits with at most e
    cdf = {}
    for m in mods:
        cnt = [0] * (n + 1)
        for e in E[m]:
            cnt[e] += 1
        acc, c = 0, []
        for x in cnt:
            acc += x
            c.append(acc / N)
        cdf[m] = c
    p = {m: cdf[m][Eo[m]] for m in mods}
    minp = sorted(min(cdf[m][E[m][i]] for m in mods) for i in range(N))
    adj = {m: bisect.bisect_right(minp, p[m] + 1e-12) / N for m in mods}
    best = min(mods, key=lambda m: (adj[m], m))
    pfloor = {m: cdf[m][min(E[m])] for m in mods}
    floor = min(bisect.bisect_right(minp, pfloor[m] + 1e-12) / N for m in mods)
    return {"m": best, "errors": Eo[best], "p": p[best], "p_adj": adj[best], "floor": floor, "n": n, "k": k,
            "splits": total, "exact": exact, "moduli": len(mods),
            "slow_phases": sorted({shift[j] % best for j in slow if j in shift}),
            "fast_phases": sorted({shift[j] % best for j in fast if j in shift}),
            "separates": [(m, cdf[m][0]) for m in mods if Eo[m] == 0], "alpha": alpha}


def phase_lines(t: dict | None) -> list[str]:
    """The phase test's result in words."""
    if t is None:
        return ["address phase: needs slow and fast pads with known shifts"]
    how = (f"exact over all {t['splits']} splits" if t["exact"] else f"{MAX_EXACT} random splits of {t['splits']}")
    sizes = f"{t['k']} slow and {t['n'] - t['k']} fast pads"
    if t["floor"] > t["alpha"]:
        sep = t["separates"]
        why = (f"they separate at mod {sep[0][0]}, but so do {sep[0][1]:.0%} of the splits of these pads into as many "
               "slow and fast" if sep else "")
        return [f"address phase: too few pads to tell ({sizes}: even a perfect separation at the best modulus would "
                f"have a corrected permutation p of {t['floor']:.2g}, over {t['alpha']}){'; ' + why if why else ''}. "
                "Time more pads (a larger cap) before naming a phase."]
    m, sp, fp = t["m"], t["slow_phases"], t["fast_phases"]
    detail = (f"slow pads shift the code by {sp} mod {m}, fast pads by {fp} mod {m}"
              + (f" ({t['errors']} of {t['n']} pads off that rule)" if t["errors"] else ""))
    stat = (f"permutation p {t['p']:.2g} ({how}), {t['p_adj']:.2g} corrected for the {t['moduli']} moduli tried "
            f"(4 to the period)")
    if t["p_adj"] <= t["alpha"]:
        kind = (" (an alignment effect: look at the hot functions' alignment, e.g. build with -falign-functions)"
                if m <= 64 else " (a position effect within the period)")
        return [f"address phase: {detail}; {stat}{kind}"]
    return [f"address phase: no modulus separates the slow pads from the fast ones better than a random split "
            f"(best: {detail}; {stat})"]


# ---- culprits -------------------------------------------------------------------------------------

def culprits(raw_path: Path, key: str, arm: str | None = None, hot: Path | None = None,
             alpha: float = ALPHA) -> list[str]:
    raw = load_raw(raw_path)
    D = Design.from_json(raw["design"])
    arms = [a["name"] for a in raw["arms"]]
    arm = arm or arms[0]
    if key not in raw["times"]:
        hits = [k for k in raw["times"] if k.endswith(": " + key)]
        if len(hits) != 1:
            return [f"no case {key!r} in {raw_path}"]
        key = hits[0]
    if arm not in raw["times"][key]:
        return [f"no arm {arm!r} for `{key}` (arms: {', '.join(arms)})"]
    geo = raw.get("geometry", {}).get(arm, {})
    m = raw["K"][key]
    boundary = D.period
    meds, notes = pad_levels(raw["times"][key][arm], D, m)
    if not meds:
        return [f"`{key}` ({arm}): no stock-setting times."]
    nb = D.batch(m - 1) + 1
    notes = [("pad times compared within their batch (drift between batches removed): each pad against pad 0, "
              f"or its batch's anchor, in the same rounds ({nb} batches)") if nb > 1 else
             "pad times compared round by round with pad 0's (one batch)"] + notes
    c = statistics.median(meds.values())
    th = raw.get("thresholds", {})
    thr = th.get("threshold", 0.03) if c >= th.get("fast_us", 5000) else th.get("threshold_fast", 0.10)
    slow = [j for j, v in meds.items() if v > c * (1 + thr / 2)]
    fast = [j for j, v in meds.items() if v <= c * (1 + thr / 4)]
    head = [f"`{key}` ({arm}): " + "; ".join(notes) + "."]
    if not slow:
        return head + [f"`{key}` ({arm}): no slow stock-setting variant (median {c:.0f})."]
    # the code shift of each pad against pad 0: the run's geometry, else the pad bytes' difference
    shift = {j: (geo[str(j)]["shift"] if geo.get(str(j), {}).get("shift") is not None else D.pad(j) - D.pad(0))
             for j in meds}
    phase = phase_lines(phase_test(shift, slow, fast, D.period, alpha))
    cost = statistics.median(meds[j] for j in slow) / c - 1
    worst = max(meds.values()) / c - 1
    summary = (f"`{key}` ({arm}): slow pads {', '.join(str(D.pad(j)) for j in slow)} of {len(meds)} "
               f"(+{cost:.1%} at their median; worst {worst:+.1%})")
    path = lambda j: Path(geo[str(j)]["path"])
    missing = sorted({j for j in slow + fast + [0] if str(j) not in geo or not path(j).exists()})
    if missing:
        return head + phase + [summary + f"; loop candidates skipped: the binaries of pads "
                               f"{', '.join(str(D.pad(j)) for j in missing)} are gone (the run's work directory "
                               "was cleaned?)."]
    cand = set.intersection(*(_rel_loops(path(j), boundary) for j in slow))
    for j in fast:
        cand -= _rel_loops(path(j), boundary)
    if hot:
        hs = set(BI.read_order(hot))
        cand = {x for x in cand if x[0] in hs}
    f0 = BI.function_map(path(0))
    spans = [(f0[fn].start + off, n) for fn, off, n in cand if fn in f0]
    bad = BI.crossing_shifts(spans, boundary, 4)
    share = len(bad) / (boundary // 4)
    p0 = D.pad(0)
    inside = sorted({(p0 + d) % boundary for d in bad})
    outside = sorted({(p0 + d) % boundary for d in range(0, boundary, 4) if d not in bad
                      and any(abs(d - b) <= 64 for b in bad)})
    pick = lambda xs: [xs[len(xs) * i // 4] for i in range(4)] if len(xs) >= 4 else xs
    if not cand:
        return head + phase + [summary + "; no candidate loops (no small loop" + (" of a hot function" if hot else "")
                               + f" crosses {boundary} B in every slow pad and in no fast one)."]
    return head + phase + [
        summary + f"; {len(cand)} candidate loops: "
        + ", ".join(f"{fn}+{off} ({n} B)" for fn, off, n in sorted(cand)[:12]) + ("..." if len(cand) > 12 else "")
        + f"; they cross in {share:.1%} of code layouts (4-byte steps over {boundary} B): layout-weighted cost "
        f"{share * cost:+.2%}, worst observed {worst:+.1%}.",
        "targeted pads to time (--pads): inside " + ",".join(map(str, pick(inside)))
        + "; just outside " + ",".join(map(str, pick(outside)))]
