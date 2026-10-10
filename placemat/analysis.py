"""Per-case analysis, flags and verdicts (DESIGN §5.7).

Raw timings: times[arm][slot] = [(value, covariate) per round], where a slot is a variant id
'<pad>.<kind>' (kind s: stock setting, c: coloured, t: stepped), 'stock' (the true stock build,
first batch only) or 'a<b>' (batch b's anchor: pad 0 at the stock setting timed again).
Arm 0 is the base; every other arm is compared with it.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

from . import stats as S
from .design import COLOUR, STEP, STOCK, Design


@dataclass
class Thresholds:
    threshold: float = 0.03       # an effect must exceed this ...
    threshold_fast: float = 0.10  # ... or this, for cases under fast_us
    fast_us: float = 5000.0
    q: float = 0.05               # Benjamini-Hochberg false-discovery rate, per family
    pmax: float = 0.01            # and p <= pmax
    run_alpha: float = 0.05       # the run covariate adjusts the tests when its p is below this
    dep_alpha: float = 0.01       # data dependence: split over the offset kinds compared

    def thr(self, base_us: float) -> float:
        return self.threshold if base_us >= self.fast_us else self.threshold_fast


def ok(x):
    return [(v, c) for v, c in x if v is not None and v > 0]


def _setting_key(D: Design, vid: str) -> str:
    j, k = D.parse(vid)
    if k == STOCK:
        return "stock"
    return f"c{D.colour(j)}" if k == COLOUR else f"t{D.step(j)}"


MODE_MIN_COUNT, MODE_MIN_SHARE = 3, 0.2


def _mode(xs):
    """The most common value, or None when the covariate is unavailable: no values, or a most common
    value seen fewer than 3 times or in under a fifth of the executions (a covariate that does not
    repeat, such as a hash of k values under threads, DESIGN §5.3, §5.8)."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    m = statistics.mode(xs)
    n = xs.count(m)
    return m if n >= MODE_MIN_COUNT and n >= MODE_MIN_SHARE * len(xs) else None


def aligned(series: list[list], rows_of: list[int] | None = None) -> list[list[float]]:
    """Rows (one per round) of the log values of several slots' timings, the rounds where all have one."""
    n = min(len(s) for s in series)
    rows = []
    for r in range(n):
        vs = [s[r][0] for s in series]
        if all(v is not None and v > 0 for v in vs):
            rows.append([math.log(v) for v in vs])
    return rows


def analyse(times: dict, arms: list[str], D: Design, m: int, th: Thresholds, rnd: random.Random,
            full: bool = True, covariate_pooled: bool = True) -> dict | None:
    """Numbers for one case over its first m pads; None when timings are missing.
    covariate_pooled: post-stratify by the mode over all arms pooled (a covariate comparable across
    arms, such as the region base); otherwise each run is categorised against its own arm's mode."""
    if m < 1:                               # no finished batch (an incomplete run)
        return None
    vids = [v for j in range(m) for v in D.variants(j)]
    g = {a: {v: list(times.get(a, {}).get(v, [])) for v in vids} for a in arms}
    if any(len(ok(g[a][v])) < 2 for a in arms for v in vids):
        return None
    nb = D.batch(m - 1) + 1
    out: dict = {"m": m, "arms": {}, "cmp": {}}
    # ---- the run covariate, per arm ----
    modes = {}
    for a in arms:
        covs = [c for v in vids for _, c in ok(g[a][v])]
        mode = _mode(covs)
        modes[a] = mode
        groups: dict = {}
        if mode is not None:
            for v in vids:
                x = ok(g[a][v])
                if x and all(c is not None for _, c in x):
                    groups.setdefault(_setting_key(D, v), []).append(([math.log(y) for y, _ in x],
                                                                      [int(c == mode) for _, c in x]))
        est, p = S.run_test(groups, rnd, 2000 if full else 300) if groups else ({}, 1.0)
        known = [c for c in covs]
        share = sum(c == mode for c in known) / len(known) if known and mode is not None else 0.0
        out["arms"][a] = {"run": {"est": {k: e for k, (e, _) in est.items()}, "z": {k: z for k, (_, z) in est.items()},
                                  "p": p, "mode": mode, "share": share,
                                  "available": mode is not None or not any(c is not None for c in covs)}}
    out["run_p"] = min(1.0, len(arms) * min(out["arms"][a]["run"]["p"] for a in arms))
    out["adjusted"] = out["run_p"] < th.run_alpha
    effs = [(e, k) for a in arms for k, e in out["arms"][a]["run"]["est"].items()]
    out["run_eff"] = max(effs, key=lambda x: abs(x[0])) if effs else (0.0, None)
    g0 = g
    if out["adjusted"]:                     # for the tests: each run at the mode moved to the other category
        def adj(a):
            est, mode = out["arms"][a]["run"]["est"], modes[a]
            return {v: [((y * math.exp(-est[_setting_key(D, v)]) if y and _setting_key(D, v) in est and c == mode
                          else y), c) for y, c in x] for v, x in g[a].items()}
        g = {a: adj(a) for a in arms}
    pooled = _mode([c for a in arms for v in vids for _, c in ok(g0[a][v])]) if covariate_pooled else None
    base_meds = [statistics.median(y for y, _ in ok(g[arms[0]][f"{j}.{STOCK}"])) for j in range(m)]
    base_us = statistics.median(base_meds)
    thr = th.thr(base_us)
    out.update(base_us=base_us, thr=thr)
    # ---- the change, per compared arm ----
    for a in arms[1:]:
        ys = {}
        for v in vids:
            r = [yb / ya for (ya, _), (yb, _) in zip(g[arms[0]][v], g[a][v]) if ya and yb and ya > 0 and yb > 0]
            if len(r) < 2:
                return None
            y = math.log(statistics.median(r))
            if out["adjusted"]:
                ma = pooled if covariate_pooled else modes[arms[0]]
                mb = pooled if covariate_pooled else modes[a]
                y = S.strat_ratio([(yv, c == ma) for yv, c in ok(g0[arms[0]][v])],
                                  [(yv, c == mb) for yv, c in ok(g0[a][v])], y)
            ys[v] = y
        per_pad = [statistics.fmean(ys[v] for v in D.variants(j)) for j in range(m)]
        est, lo, hi, hw = S.tci(per_pad)
        c = {"est": est, "lo": lo, "hi": hi, "hw": hw, "y": ys}
        for k in D.kinds:
            c[k] = S.tci([ys[f"{j}.{k}"] for j in range(m)])
        offs = [k for k in D.kinds if k != STOCK]
        for k in offs:                      # data dependence: the stock setting against this offset, paired over pads
            ds = [ys[f"{j}.{STOCK}"] - ys[f"{j}.{k}"] for j in range(m)]
            mu, p, sig = S.paired_t(ds, th.dep_alpha / len(offs))
            c["dep_" + k] = {"mean": mu, "p": p, "sig": sig and abs(math.expm1(mu)) > thr}
        out["cmp"][a] = c
    if not full:
        return out
    # ---- sensitivity, per arm ----
    anchors = {a: {b: ok(times.get(a, {}).get(f"a{b}", [])) for b in range(1, nb)} for a in arms}
    for a in arms:
        A = out["arms"][a]
        meds = {v: statistics.median(y for y, _ in ok(g[a][v])) for v in vids}
        have = all(len(anchors[a][b]) >= 2 for b in anchors[a])
        scale = {0: 1.0}
        for b in anchors[a]:
            scale[b] = (meds[f"0.{STOCK}"] / statistics.median(y for y, _ in anchors[a][b])) if have else 1.0
        sm = {j: meds[f"{j}.{STOCK}"] * scale[D.batch(j)] for j in range(m)}
        cen = statistics.median(sm.values())
        A["stock_meds"] = sm
        A["worst"] = max(sm.values()) / cen - 1
        if have:
            A["code_max"] = (max(sm.values()) - min(sm.values())) / cen
        else:                               # no anchors (cap reached in one batch, or an old raw file): within batches
            A["code_max"] = max(((max(xs) - min(xs)) / cen if len(xs) > 1 else 0.0)
                                for xs in ([sm[j] for j in range(m) if D.batch(j) == b] for b in range(nb)))
        A["all_meds"] = {v: meds[v] * scale[D.batch(D.parse(v)[0])] for v in vids}
        av = list(A["all_meds"].values())
        A["range"] = (max(av) - min(av)) / statistics.median(av)
        blocks = []
        for b in range(nb):
            cols = [g[a][f"{j}.{STOCK}"] for j in range(m) if D.batch(j) == b]
            if b and have:
                cols.append(anchors[a][b])
            if len(cols) > 1:
                blocks.append(aligned(cols))
        A["code_p"] = S.spread_perm(blocks, rnd)
        A["code_p_rank"] = S.rank_perm(blocks, rnd)    # reported beside the flag's test (DESIGN §11 item 10)
        for k in (COLOUR, STEP):
            if k not in D.kinds:
                continue
            cs, effs = [], []
            for j in range(m):
                rows = aligned([g[a][f"{j}.{STOCK}"], g[a][f"{j}.{k}"]])
                cs.append([r[1] - r[0] for r in rows])
                effs.append(meds[f"{j}.{k}"] / meds[f"{j}.{STOCK}"] - 1)
            A[k + "_p"] = S.signflip(cs, rnd)
            A[k + "_max"] = max(abs(e) for e in effs)
            A[k + "_typ"] = statistics.median(effs)
            t2 = [S._t2(c) for c in cs if len(c) >= 3]
            A[k + "_lead"] = max(t2) / sum(t2) if t2 and sum(t2) > 0 else 0.0   # the largest pad's share of the test statistic
    # ---- the stock builds' own paired result ----
    out["stock"] = {}
    for a in arms[1:]:
        sa = [y for y, _ in times.get(arms[0], {}).get("stock", [])]
        sb = [y for y, _ in times.get(a, {}).get("stock", [])]
        rr = [y / x for x, y in zip(sa, sb) if x and y]
        if len(rr) >= 2:
            out["stock"][a] = S.bootstrap_median_ratio(rr)
            out.setdefault("stock_p", {})[a] = S.bootstrap_p(rr)
    return out


FAMILIES = ("code", COLOUR, STEP, "run")
FAMILY_NAMES = {"code": "code", COLOUR: "colour", STEP: "step", "run": "run"}


def flags(res: dict, arms: list[str], D: Design, th: Thresholds) -> dict:
    """Flags across cases. A flag needs the Benjamini-Hochberg cut (q, per family), p <= pmax, and
    the family's effect over the case's threshold (BH alone is lenient over few cases, and cases timed
    in the same runs share their evidence). Combined over arms: the smallest p times the number of
    arms. Also per arm (each arm's own p-values and effects), which the pinned-build check needs."""
    ok_ = {k: r for k, r in res.items() if r}
    fams = ["code"] + [k for k in (COLOUR, STEP) if k in D.kinds] + ["run"]
    out = {"combined": {}, "per_arm": {a: {} for a in arms}, "p": {}}

    def eff(r, fam, a=None):
        if fam == "run":
            return abs(math.expm1(r["run_eff"][0]))
        key = "code_max" if fam == "code" else fam + "_max"
        return max(r["arms"][x][key] for x in ([a] if a else arms))

    def sig(ps, fam, a=None):
        return {k for k in S.bh(ps, th.q) if ps[k] <= th.pmax and eff(ok_[k], fam, a) > ok_[k]["thr"]}
    for fam in fams:
        if fam == "run":
            ps = {k: r["run_p"] for k, r in ok_.items()}
        else:
            key = "code_p" if fam == "code" else fam + "_p"
            ps = {k: min(1.0, len(arms) * min(r["arms"][a][key] for a in arms)) for k, r in ok_.items()}
            for a in arms:
                pa = {k: r["arms"][a][key] for k, r in ok_.items()}
                out["per_arm"][a][fam] = sig(pa, fam, a) if pa else set()
        out["p"][fam] = ps
        out["combined"][fam] = sig(ps, fam) if ps else set()
    return out


ONE_PAD_SHARE = 0.5


def one_pad(r: dict, kind: str) -> bool:
    """A colour or step flag resting on one pad (the zstd Linux survey: one bad pad, the typical effect
    under 1%): in every arm whose largest effect passes the threshold, one pad gives at least half of the
    sign-flip statistic and the typical (median) effect is under the threshold. A count of pads over the
    threshold would not do: with a 1-2% within-pad spread, some null pad passes 3% by chance. The flag
    rule is unchanged; this qualifies what the flag means."""
    A = [x for x in r["arms"].values() if kind + "_lead" in x and x[kind + "_max"] > r["thr"]]
    return bool(A) and all(x[kind + "_lead"] >= ONE_PAD_SHARE and abs(x[kind + "_typ"]) < r["thr"] for x in A)


def verdict(key: str, r: dict, a: str, F: dict, D: Design, stock_placement: bool) -> dict:
    """The verdict for arm a against the base on one case."""
    c, thr = r["cmp"][a], r["thr"]
    flagged = [FAMILY_NAMES[f] for f in F["combined"] if key in F["combined"][f]]
    s = r.get("stock", {}).get(a)
    smeas = bool(s) and (s[1] > 1 or s[2] < 1) and abs(s[0] - 1) > thr
    ref = c[STOCK]
    stock_attr = smeas and (s[1] > ref[2] or s[2] < ref[1])
    real = (c["lo"] > 1 or c["hi"] < 1) and abs(c["est"] - 1) > thr
    deps = [("colour" if k == COLOUR else "step") + "-dependent"
            for k in (COLOUR, STEP) if k in D.kinds and c["dep_" + k]["sig"]]
    v = "change" if real else "placement" if (smeas or flagged) else "noise"
    attributed = flagged + ([("stock code layout" if stock_placement else "stock build")] if stock_attr else [])
    lone = [f"{FAMILY_NAMES[k]} flag on one pad" for k in (COLOUR, STEP)
            if k in F["combined"] and key in F["combined"][k] and one_pad(r, k)]
    return {"verdict": v, "qualifiers": deps + (["data-dependent"] if deps else []) + lone, "attributed": attributed,
            "stock_measurable": smeas, "covers_1": c["lo"] <= 1 <= c["hi"]}
