"""Reports: Markdown and the JSON behind it, always recomputable from raw timings (DESIGN §8.3)."""
from __future__ import annotations

import json
import math
import random
import statistics
import textwrap
from pathlib import Path

from .analysis import FAMILY_NAMES, Thresholds, analyse, flags, one_pad, verdict
from .design import COLOUR, KIND_NAMES, STEP, STOCK, Design


def load_raw(path: Path) -> dict:
    r = json.loads(Path(path).read_text())
    r["times"] = {k: {a: {s: [tuple(x) for x in v] for s, v in w.items()} for a, w in t.items()}
                  for k, t in r["times"].items()}
    r["plan"] = {k: tuple(v) for k, v in r["plan"].items()}
    return r


def rebase(raw: dict, base: str) -> dict:
    """The raw data with arm `base` first, so every other arm is compared with it."""
    names = [a["name"] for a in raw["arms"]]
    if base not in names:
        raise SystemExit(f"placemat: no arm {base!r} (arms: {', '.join(names)})")
    raw = dict(raw)
    raw["arms"] = sorted(raw["arms"], key=lambda a: a["name"] != base)
    return raw


def compute(raw: dict) -> dict:
    """Every number the report shows, from raw timings alone."""
    D = Design.from_json(raw["design"])
    th = Thresholds(**raw.get("thresholds", {}))
    arms = [a["name"] for a in raw["arms"]]
    rnd = random.Random(D.seed)
    pooled = raw.get("covariate", "base") != "khash"
    res = {k: analyse(raw["times"][k], arms, D, raw["K"][k], th, rnd, covariate_pooled=pooled) for k in raw["plan"]}
    F = flags(res, arms, D, th)
    V = {k: {a: verdict(k, r, a, F, D, raw.get("stock_placement", True)) for a in arms[1:]}
         for k, r in res.items() if r}
    from .stats import bh as _bh
    for a in arms[1:]:                  # does a stock-builds difference survive FDR across cases? (reported only)
        ps = {k: r.get("stock_p", {}).get(a) for k, r in res.items() if r and r.get("stock_p", {}).get(a) is not None}
        keep = {k for k in _bh(ps, th.q) if ps[k] <= th.pmax} if ps else set()
        for k in V:
            if a in V[k]:
                v = V[k][a]
                v["stock_p"] = ps.get(k)
                v["stock_fdr"] = k in keep
                v["weak"] = (v["verdict"] == "placement" and v["stock_measurable"] and not v["stock_fdr"]
                             and not any(k in F["combined"][f] for f in F["combined"]))
    return {"D": D, "th": th, "arms": arms, "res": res, "flags": F, "verdicts": V}


def pct(x: float) -> str:
    return f"{x:+.1%}"


def ci(t) -> str:
    return f"{pct(t[0] - 1)} ({pct(t[1] - 1)} to {pct(t[2] - 1)})"


def markdown(raw: dict, C: dict | None = None, title: str | None = None) -> str:
    C = C or compute(raw)
    D, arms, res, F, V = C["D"], C["arms"], C["res"], C["flags"], C["verdicts"]
    th = C["th"]
    offs = [k for k in D.kinds if k != STOCK]
    L = [f"# {title or 'placemat: ' + raw.get('project', '')}", ""]
    L += [f"Arms: " + "; ".join(f"**{a['name']}** `{a['source']}`" for a in raw["arms"]) + f" (base: {arms[0]})."]
    L += [""] + textwrap.wrap(
        f"Design: data axis {D.data}"
        + (f" ({D.step_mode} steps)" if STEP in D.kinds else "")
        + f" via {raw.get('data_method', 'none')}; {len(D.kinds)} data setting(s) per pad; {raw['rounds']} rounds "
        f"per batch after a discarded execution of each binary and setting; pads {D.start}, then {D.batch_step} at a time "
        f"while the interval's half-width exceeds {raw.get('target', 0.01):.1%}, up to {D.cap}"
        + (f"; explicit pads first: {', '.join(map(str, D.pads))}" if D.pads else "") + ". "
        "Change: per variant the median over rounds of the paired ratio, per pad the geometric mean of its variants, "
        "a t interval over pads (pads - 1 df). Flags: Benjamini-Hochberg across cases per family "
        f"(q = {th.q}), p <= {th.pmax}, and the family's effect over the threshold ({th.threshold:.0%}; "
        f"{th.threshold_fast:.0%} under {th.fast_us / 1000:g} ms). Intervals over layouts are only approximately nominal "
        "under adaptive stopping, and assume pads exchangeable within a batch. Cases timed in one execution share it, so a disturbed "
        "execution moves them together: their results are not independent.", 112)
    counts = {f: len(F["combined"].get(f, ())) for f in F["combined"]}
    nv = {v: sum(1 for k in V for a in V[k] if V[k][a]["verdict"] == v) for v in ("change", "placement", "noise")}
    L += ["", f"Cases {sum(1 for r in res.values() if r)} of {len(res)}; flags: "
          + ", ".join(f"{FAMILY_NAMES[f]} {n}" for f, n in counts.items())
          + f"; verdicts: change {nv['change']}, placement {nv['placement']}, noise {nv['noise']}; intervals containing "
          f"no change: {sum(1 for k in V for a in V[k] if V[k][a]['covers_1'])}.", ""]
    from .stats import tci
    L += ["| Arm against " + arms[0] + " | Mean over cases (95% t interval over pads) | pads | change | placement | noise |",
          "|---|---|---|---|---|---|"]
    for a in arms[1:]:
        ks = [k for k in V if a in V[k]]
        if not ks:
            continue
        m = min(res[k]["m"] for k in ks)          # the pads every case has
        per_pad = []
        for j in range(m):                         # cases share pads and rounds, so the pad is the unit
            ys = [statistics.fmean(res[k]["cmp"][a]["y"][v] for v in D.variants(j)) for k in ks]
            per_pad.append(statistics.fmean(ys))
        g = tci(per_pad)
        cnt = {v: sum(1 for k in ks if V[k][a]["verdict"] == v) for v in ("change", "placement", "noise")}
        L.append(f"| {a} | {pct(g[0] - 1)} ({pct(g[1] - 1)} to {pct(g[2] - 1)}) | {m} | {cnt['change']} | "
                 f"{cnt['placement']} | {cnt['noise']} |")
    L += ["", "(The geometric mean over cases of each pad's ratio, with a t interval over the pads all cases share. "
          "To compare the arms with another arm: `placemat reanalyse RAW --base NAME`.)", ""]
    hdr = ["Case", "Arm", "Stock builds", "Change", "95% interval", "Pads"]
    hdr += [KIND_NAMES[k].capitalize() + " setting" for k in D.kinds] if len(D.kinds) > 1 else []
    hdr += ["Code (spread, p: base / arm)"] + [("Colour" if k == COLOUR else "Step") + " (typ / max, p)" for k in offs]
    hdr += ["Run", "Attributed", "Verdict"]
    L += ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    pc = F["p"]
    for k in raw["plan"]:
        r = res[k]
        if not r:
            L.append(f"| `{k}` | (missing timings) |")
            continue
        ser = " (series)" if k in raw.get("series", {}) else ""
        for a in arms[1:]:
            c, v = r["cmp"][a], V[k][a]
            s = r["stock"].get(a)
            row = [f"`{k}`{ser}", a, (ci(s) + (" **" if v["stock_measurable"] else "")) if s else "-",
                   pct(c["est"] - 1), f"{pct(c['lo'] - 1)} to {pct(c['hi'] - 1)}", str(r["m"])]
            if len(D.kinds) > 1:
                row += [ci(c[kk]) for kk in D.kinds]
            shown = [arms[0], a]                       # this row's two arms; with more arms, others in a note
            others = [x for x in arms if x not in shown]
            code = " / ".join(f"{r['arms'][x]['code_max']:.1%} (p {r['arms'][x]['code_p']:.2g}"
                              + (f", rank p {r['arms'][x]['code_p_rank']:.2g}" if "code_p_rank" in r["arms"][x] else "")
                              + (", **flag**" if k in F["per_arm"][x].get("code", ()) else "") + ")" for x in shown)
            if others:
                fl = [x for x in others if k in F["per_arm"][x].get("code", ())]
                code += f"; other arms up to {max(r['arms'][x]['code_max'] for x in others):.1%}" + (
                    f", flagged: {', '.join(fl)}" if fl else "")
            row.append(code + (" **code**" if k in F["combined"]["code"] else ""))
            for kk in offs:
                row.append(" / ".join(f"{pct(r['arms'][x][kk + '_typ'])}, {r['arms'][x][kk + '_max']:.1%}" for x in shown)
                           + f" (p {pc[kk][k]:.2g})" + (f" **{FAMILY_NAMES[kk]}**" if k in F["combined"][kk] else "")
                           + (" (one pad)" if k in F["combined"][kk] and one_pad(r, kk) else ""))
            eff, setting = r["run_eff"]
            row.append((f"{pct(math.expm1(eff))} at {setting} " if r["adjusted"] else "") + f"(p {r['run_p']:.2g})"
                       + (" **run**" if k in F["combined"]["run"] else "") + (", adjusted" if r["adjusted"] else ""))
            row.append("+".join(v["attributed"]) or "-")
            row.append(("**change**" if v["verdict"] == "change" else v["verdict"]) + (" (weak: the stock difference does not "
                       "survive FDR across cases)" if v.get("weak") else "")
                       + (", " + ", ".join(v["qualifiers"]) if v["qualifiers"] else ""))
            L.append("| " + " | ".join(row) + " |")
    m = max(raw["K"].values()) if raw["K"] else D.start
    if not raw.get("complete", True):
        pt = raw.get("partial")
        L += ["", "**Incomplete run** (it stopped or was killed): these are the timings of the finished batches"
              + (f"; batch {pt['batch']} (pads {pt['pads'][0]}-{pt['pads'][1]}, {pt['cases']} case{'s' * (pt['cases'] != 1)}) stopped after "
                 f"{pt['rounds']} of {raw['rounds']} rounds, and its timings are kept in the raw file but not analysed"
                 if pt else "") + (". No batch finished, so no case can be analysed." if m == 0 else ".")]
    if raw.get("build_warnings"):
        L += ["", "**Build warnings:** " + "; ".join(raw["build_warnings"][:10])
              + (f" (and {len(raw['build_warnings']) - 10} more)" if len(raw["build_warnings"]) > 10 else "") + "."]
    dist = disturbed(raw, D)
    if dist:
        L += ["", "**Disturbed rounds** (the whole machine slow: the median slot more than 10% over its own lower quartile): "
              + "; ".join(f"batch {d['batch']} round {d['round'] + 1} {pct(d['slow'])} ({d['slots_slow']:.0%} of slots slow)"
                          for d in dist[:12]) + (f"; and {len(dist) - 12} more" if len(dist) > 12 else "")
              + ". Paired ratios cancel these, but the code tests' per-pad summaries do not; the rank test is robust to them."]
    L += noisy_lines(raw)
    sel = raw.get("log", {}).get("selection")
    if sel:
        L += ["", f"**Affected-case selection** ({sel['profile']}): " + "; ".join(sel["notes"])
              + f". Timed {len(sel['timed'])} cases; skipped {len(sel['skipped'])}."]
    L += ignored(raw, C)
    L += ["", "<details><summary>Design, variants and costs</summary>", "", *D.describe(m), ""]
    geo = raw.get("geometry", {})
    if geo:
        L += ["Shift: the most common move of a function against the arm's pad-0 build (mod the boundary), and the "
              "share of all functions not moved (a pinned region placed before the pad, and anything linked before it, such as a C runtime); loops: small loops crossing the boundary.", "",
              "| Pad | Bytes | " + " | ".join(f"{a} shift | {a} loops" for a in arms) + " |",
              "|---|---|" + "---|---|" * len(arms)]
        for j in sorted({j for a in geo for j in geo[a]}, key=lambda x: (x == "stock", int(x) if x != "stock" else 0)):
            cells = []
            for a in arms:
                g = geo.get(a, {}).get(j, {})
                cells += [f"{g.get('shift', 0) % D.period} ({g.get('unmoved', 0):.0%} unmoved)" if "shift" in g else "-",
                          str(g.get("crossing_loops", "-"))]
            L.append(f"| {j} | {D.pad(int(j)) if j != 'stock' else '-'} | " + " | ".join(cells) + " |")
    unavail = sorted({a for r in res.values() if r for a in arms if not r["arms"][a]["run"].get("available", True)})
    if unavail:
        L += ["", f"Run covariate ({raw.get('covariate')}) unavailable for {', '.join(unavail)} in some cases: its "
              "values did not repeat (most common value seen fewer than 3 times or in under a fifth of executions), "
              "so no run test was made there."]
    runs = [r for r in res.values() if r and r["arms"][arms[0]]["run"]["mode"] is not None]
    if runs:
        r0 = runs[0]["arms"][arms[0]]["run"]
        mode = r0["mode"]
        L += ["", f"Run covariate ({raw.get('covariate')}): most common {mode:#x} in {r0['share']:.0%} of the first case's "
              f"{arms[0]} runs."]
    lg = raw.get("log", {})
    if lg:
        L += ["", f"Batches (pads, cases): {', '.join(f'{x} x {n}' for x, n in lg.get('batches', []))}; "
              f"pads per case: " + ", ".join(f"{x}: {list(raw['K'].values()).count(x)}" for x in sorted(set(raw['K'].values())))
              + f". Builds {lg.get('build_s', 0):.0f} s" + (f" (and {lg['build_wait_s']:.0f} s waiting for the shared lock)" if lg.get("build_wait_s") else "")
              + f"; timing {lg.get('time_s', 0):.0f} s ({lg.get('runs', 0)} executions); "
              f"waiting for the machine lock {lg.get('lock_wait_s', 0):.0f} s; "
              "load " + ", ".join(f"{x:.2f}" for x in lg.get("loads", [])) + " (start and end of each batch)."]
        if lg.get("probes"):
            L.append("Noise probe at the start and end of each batch's timing (spread of a fixed CPU workload; a quiet "
                     "Apple M2 gives about 1-3%; with the probe's time in ms): " + ", ".join(f"{x['spread']:.1%}/{x.get('end_spread', 0):.1%} ({x.get('probe_ms', 0):.1f} ms)"
                                                       + (f" after {x['waited_s']} s" if x.get("waited_s", 0) > 30 else "")
                                                       for x in lg["probes"]) + ".")
        if lg.get("data_execs") and not lg.get("coloured"):
            L.append(f"**Warning: no block was coloured in any of {lg['data_execs']} executions at a data setting"
                     + (f" ({lg['no_log']} wrote no log at all: is the hook compiled in, or is the interposer blocked, e.g. by "
                        "macOS SIP or the hardened runtime?)" if lg.get("no_log") else " (are any allocations above the minimum size?)")
                     + ". The data axis measured nothing.**")
        elif lg.get("no_log"):
            L.append(f"Warning: {lg['no_log']} of {lg['data_execs']} executions at a data setting wrote no data log.")
        if lg.get("coloured"):
            share = lg.get("wrapped", 0) / lg["coloured"]
            L.append(f"Coloured blocks {lg['coloured']}, wrapped {lg.get('wrapped', 0)} ({share:.1%})"
                     + (" **over 10%: offsets were often not the variant's exact colour or step**" if share > 0.1 else "") + ".")
    L += dimensioning(raw)
    L += ["", "</details>", ""]
    return "\n".join(L)


def dimensioning(raw: dict, build_s: float | None = None, alpha: float = 0.05) -> list[str]:
    """Kalibera & Jones (ISMM 2013), two levels: rounds within a pad, pads. Per case, from the base
    arm's stock-setting log times: the within-variant variance over rounds (S1) and the between-pad
    variance (S2, the variance of pad means less S1/r). The rounds per pad that minimise cost for a
    given precision are r* = sqrt((c2 / c1) * S1 / S2), per arm: c1 the cost of one round of a pad (an
    execution per data setting) and c2 the cost of adding a pad (its build and one discarded execution
    per data setting), the build time without lock waits. No advice where S2 is not significantly
    above 0 (a one-way F test over pads): r* would be infinite or rest on noise. Advice only."""
    import math as _m
    from .stats import f_sf
    D = Design.from_json(raw["design"])
    arm = raw["arms"][0]["name"]
    lg = raw.get("log", {})
    nexec = max(1, lg.get("runs", 1))
    c1 = lg.get("time_s", 0) / nexec if lg.get("time_s") else None
    if build_s is None and "build_wait_s" in lg:     # older raw files counted lock waits as build time
        nb = (sum(1 for a in raw.get("geometry", {}).values() for _ in a)
              or len(raw["arms"]) * (max(raw["K"].values(), default=0) + 1))
        build_s = lg.get("build_s", 0) / nb
    rows = []
    for k in raw["plan"]:
        t = raw["times"][k][arm]
        groups = [[_m.log(v) for v, _ in t.get(f"{j}.{STOCK}", []) if v] for j in range(raw["K"][k])]
        groups = [g for g in groups if len(g) >= 2]
        if len(groups) < 3:
            continue
        r = statistics.median(len(g) for g in groups)
        s1 = statistics.fmean(statistics.variance(g) for g in groups)
        vm = statistics.variance([statistics.fmean(g) for g in groups])
        s2 = max(vm - s1 / r, 0.0)
        p = f_sf(r * vm / s1, len(groups) - 1, sum(len(g) - 1 for g in groups)) if s1 > 0 else 1.0
        rows.append((k, s1, s2, r, p))
    if not rows:
        return []
    nk = len(D.kinds)
    c2 = build_s + c1 * nk if c1 and build_s is not None else None
    L = ["", "Dimensioning (Kalibera & Jones 2013; advice): per case, the within-pad (rounds) and between-pad variance "
         "of the base arm's stock-setting log times, and the F test's p that S2 > 0" + (
             f"; the rounds per pad r* = sqrt((c2/c1) S1/S2), per arm, with c1 = {c1 * nk:.2f} s per round of a pad "
             f"({nk} execution(s) of {c1:.2f} s) and c2 = {c2:.1f} s per extra pad (its build, without lock waits, and "
             f"{nk} discarded execution(s))" if c2 is not None else
             "; no r*: this raw file's build time includes lock waits" if c1 and "build_wait_s" not in lg else "") + ":", "",
         "| Case | S1 (rounds) | S2 (pads) | S2 p | rounds used | r* |", "|---|---|---|---|---|---|"]
    for k, s1, s2, r, p in rows:
        rs = "-"
        if c2 is not None:
            rs = ("cannot advise (no measurable pad variance)" if s2 <= 0 or p > alpha
                  else f"{max(1, round(_m.sqrt(c2 / (c1 * nk) * s1 / s2)))}")
        L.append(f"| `{k}` | {s1:.2e} | {s2:.2e} | {p:.2g} | {r:g} | {rs} |")
    return L


def noise_samples(raw: dict) -> list[dict]:
    """The continuous noise record over the run: kept in the raw file by newer runs, else read from the
    noise log (P012). Only this machine's samples, when both name a host."""
    lg = raw.get("log", {})
    xs = lg.get("noise") or []
    if not xs:
        ts = [t for w in lg.get("batch_t", []) for t in w] + [t for b in lg.get("round_t", []) for w in b for t in w]
        if not ts:
            return []                                  # an older raw file: no epoch times to join on
        try:
            import importlib
            xs = importlib.import_module("placemat.noiselog").read(start=min(ts), end=max(ts))
        except Exception:                              # no noise logger, or an unreadable log
            return []
    short = lambda h: str(h).split(".")[0].lower()
    host = raw.get("host")
    return [x for x in xs if not (host and x.get("host")) or short(x["host"]) == short(host)]


def batches(raw: dict, samples: list[dict] | None = None) -> list[dict]:
    """Per timed batch, what the gate and the noise record saw (P012): the gate went ahead after its wait
    (older raw files: its start probe over max_noise), the end-of-batch probe was over max_noise, and the
    noise record's samples during the batch, marked noisy when their median spread is over max_noise
    (a single reading jumps on an idle machine). The disturbed rounds, from the timings, are finer."""
    lg = raw.get("log", {})
    lim = raw.get("machine", {}).get("max_noise", 0) or 0
    probes, win, done = lg.get("probes", []), lg.get("batch_t", []), lg.get("batches", [])
    samples = noise_samples(raw) if samples is None else samples
    pt = raw.get("partial")
    out, lo = [], 0
    for b, pr in enumerate(probes):
        e = {"batch": b, "start_spread": pr.get("spread"), "end_spread": pr.get("end_spread")}
        if b < len(done):
            e.update(pads=[lo, done[b][0] - 1], cases=done[b][1])
            lo = done[b][0]
        elif pt and pt.get("batch") == b:
            e.update(pads=pt["pads"], cases=pt["cases"], unfinished=True)
        e["went_ahead"] = bool(pr["went_ahead"]) if "went_ahead" in pr else lim > 0 and pr.get("spread", 0) > lim
        e["end_over"] = lim > 0 and (pr.get("end_spread") or 0) > lim
        w = win[b] if b < len(win) else None
        if w is None and pt and pt.get("batch") == b and lg.get("round_t"):
            w = [pt["start"], (lg["round_t"][-1] or [[pt["start"]] * 2])[-1][1]]
        e["window"] = w
        e["noise"] = None
        if w and samples:
            sp = [x["spread"] for x in samples if w[0] <= x.get("t", 0) <= w[1] and x.get("spread") is not None]
            if sp:
                e["noise"] = {"n": len(sp), "over": sum(1 for x in sp if lim > 0 and x > lim),
                              "median": statistics.median(sp), "max": max(sp)}
        e["noisy_record"] = bool(e["noise"]) and lim > 0 and e["noise"]["median"] > lim
        e["marked"] = e["went_ahead"] or e["end_over"] or e["noisy_record"]
        out.append(e)
    return out


def noisy_lines(raw: dict, B: list[dict] | None = None) -> list[str]:
    """The report's paragraph on noisy batches: one line per marked batch, or one sentence when none is."""
    B = batches(raw) if B is None else B
    lim = raw.get("machine", {}).get("max_noise", 0) or 0
    if not B or lim <= 0:
        return []
    ns = [x["noise"] for x in B if x["noise"]]
    rec = (f"the noise record has {sum(n['n'] for n in ns)} samples during the timing, the largest spread "
           f"{max(n['max'] for n in ns):.1%}" if ns else "no noise record for this run")
    marked = [x for x in B if x["marked"]]
    if not marked:
        return ["", f"**Noisy batches:** none (no batch went ahead at the gate's limit or ended with its probe over "
                f"{lim:.0%}; {rec})."]
    L = ["", f"**Noisy batches** ({len(marked)} of {len(B)}: the gate went ahead after its wait, the end-of-batch probe "
         f"was over max_noise {lim:.0%}, or the noise record's median spread during the batch was over it; read their "
         f"cases' results with that in mind; {rec}):", ""]
    for x in marked:
        what = ([f"went ahead after the wait (start probe {x['start_spread']:.1%})"] if x["went_ahead"] else []) + (
            [f"ended at {x['end_spread']:.1%}"] if x["end_over"] else []) + (
            [f"noise record {x['noise']['n']} sample(s), {x['noise']['over']} over, median {x['noise']['median']:.1%}, "
             f"max {x['noise']['max']:.1%}" + (" (noisy)" if x["noisy_record"] else "")] if x["noise"] else [])
        pads = (f" (pads {x['pads'][0]}-{x['pads'][1]}, {x['cases']} case{'s' * (x['cases'] != 1)}"
                + (", unfinished)" if x.get("unfinished") else ")")) if "pads" in x else ""
        L.append(f"- batch {x['batch']}{pads}: " + "; ".join(what))
    return L


def disturbed(raw: dict, D: Design, limit: float = 0.10) -> list[dict]:
    """Rounds in which the whole machine was slow (DESIGN §11 item 3): for each batch and round, the median
    over every case, arm and slot of that round's time over the slot's lower quartile. A disturbance shows in
    every slot at once (both arms slow together), unlike placement, which differs between slots. Rounds
    over `limit` are reported; the noise probe at the start and end of a batch cannot see them."""
    import collections
    ratios = collections.defaultdict(list)
    for k, t in raw["times"].items():
        for a, slots in t.items():
            for slot, vs in slots.items():
                v = [x[0] for x in vs if x[0]]
                if len(v) < 3:
                    continue
                m = sorted(v)[len(v) // 4]           # the lower quartile: robust when most rounds are disturbed
                b = 0 if slot == "stock" else int(slot[1:]) if slot.startswith("a") else D.batch(D.parse(slot)[0])
                for r, x in enumerate(vs):
                    if x[0]:
                        ratios[(b, r)].append(x[0] / m)
    out = []
    for (b, r), xs in sorted(ratios.items()):
        med = statistics.median(xs)
        if med - 1 > limit:
            out.append({"batch": b, "round": r, "slow": med - 1, "slots_slow": sum(1 for x in xs if x > 1 + limit) / len(xs)})
    return out


def ignored(raw: dict, C: dict) -> list[str]:
    """How bad could it get if placement were ignored? A one-build-per-arm comparison sees one layout:
    one pad at the stock setting. Per case and arm, the range of results such a comparison could have
    reported (over this run's pads), and whether it could have shown a false change, or missed or
    reversed a real one; and how much each case's own time depends on layout."""
    D, arms, res, V = C["D"], C["arms"], C["res"], C["verdicts"]
    rows, false_ch, missed, total = [], 0, 0, 0
    for k in raw["plan"]:
        r = res.get(k)
        if not r:
            continue
        thr = r["thr"]
        for a in arms[1:]:
            c, v = r["cmp"][a], V[k][a]
            single = [math.exp(c["y"][f"{j}.{STOCK}"]) for j in range(r["m"])]
            lo, hi = min(single), max(single)
            total += 1
            real = v["verdict"] == "change"
            fc = not real and max(abs(lo - 1), abs(hi - 1)) > thr
            ms = real and (min(abs(x - 1) for x in single) <= thr or (lo - 1) * (hi - 1) < 0)
            false_ch += fc
            missed += ms
            if fc or ms:
                err = max(abs(lo - c["est"]), abs(hi - c["est"]))
                rows.append((err, k, a, c["est"], lo, hi, "false change possible" if fc else "real change could be missed or reversed"))
    worst = []
    for k in raw["plan"]:
        r = res.get(k)
        if r:
            for x in arms:
                sm = list(r["arms"][x]["stock_meds"].values())
                worst.append((max(sm) / min(sm) - 1, k, x))
    worst.sort(reverse=True)
    L = ["", "### If placement were ignored", "",
         f"A comparison timing one build of each arm sees one layout (one pad, at the stock setting). Over this run's "
         f"pads, such a comparison could have reported a false change in **{false_ch}** of {total} case-arm pairs, and "
         f"missed or reversed a real change in **{missed}**."]
    if rows:
        rows.sort(reverse=True)
        L += ["", "| Case | Arm | Layout-averaged | One layout could show | Risk |", "|---|---|---|---|---|"]
        for err, k, a, est, lo, hi, what in rows[:15]:
            L.append(f"| `{k}` | {a} | {pct(est - 1)} | {pct(lo - 1)} to {pct(hi - 1)} | {what} |")
        if len(rows) > 15:
            L.append(f"| ({len(rows) - 15} more) | | | | |")
    if worst:
        L += ["", "Most layout-dependent cases (slowest pad over fastest, at the stock setting): "
              + "; ".join(f"`{k}` ({x}) {w:+.0%}" for w, k, x in worst[:5]) + "."]
    return L


def to_json(raw: dict, C: dict | None = None) -> dict:
    C = C or compute(raw)
    rt = raw.get("log", {}).get("round_t", [])
    dist = [{**d, **({"t": rt[d["batch"]][d["round"]]} if d["batch"] < len(rt) and d["round"] < len(rt[d["batch"]]) else {})}
            for d in disturbed(raw, C["D"])]
    out = {"project": raw.get("project"), "arms": raw["arms"], "design": raw["design"], "cases": {},
           "complete": raw.get("complete", True), "partial": raw.get("partial"), "disturbed_rounds": dist,
           "batches": batches(raw)}
    for k, r in C["res"].items():
        if not r:
            out["cases"][k] = None
            continue
        e = {"pads": r["m"], "threshold": r["thr"], "base_us": r["base_us"], "run_p": r["run_p"],
             "adjusted": r["adjusted"], "arms": {}, "compare": {}}
        for a in C["arms"]:
            A = r["arms"][a]
            e["arms"][a] = {x: A[x] for x in A if x in ("code_p", "code_p_rank", "code_max", "worst", "range", "colour_p", "c_p",
                                                        "t_p", "c_max", "t_max", "c_typ", "t_typ", "c_lead", "t_lead")}
            e["arms"][a]["flags"] = sorted(FAMILY_NAMES[f] for f, ks in C["flags"]["per_arm"][a].items() if k in ks)
            e["arms"][a]["run"] = {"p": A["run"]["p"], "mode": A["run"]["mode"], "share": A["run"]["share"]}
        for a in C["arms"][1:]:
            c = r["cmp"][a]
            e["compare"][a] = {"est": c["est"], "lo": c["lo"], "hi": c["hi"],
                               "settings": {KIND_NAMES[kk]: c[kk][:3] for kk in C["D"].kinds},
                               "dependence": {KIND_NAMES[kk]: c["dep_" + kk] for kk in C["D"].kinds if kk != STOCK},
                               "stock_builds": r["stock"].get(a), **C["verdicts"][k][a]}
        e["flags"] = sorted(FAMILY_NAMES[f] for f, ks in C["flags"]["combined"].items() if k in ks)
        e["p"] = {FAMILY_NAMES[f]: C["flags"]["p"][f].get(k) for f in C["flags"]["p"]}
        out["cases"][k] = e
    return out


def write(raw: dict, md: Path, title: str | None = None) -> dict:
    C = compute(raw)
    md.write_text(markdown(raw, C, title))
    j = to_json(raw, C)
    md.with_suffix(".json").write_text(json.dumps(j, indent=1, default=str))
    return j


def twospeed(raw: dict, pats=()) -> list[str]:
    """Run-to-run spread per case and arm: per variant each round's value over the variant's median,
    pooled over variants; p90/p10, the share of rounds over 15% slow, the largest within-variant max/min
    (Amber's col-twospeed.py)."""
    arms = [a["name"] for a in raw["arms"]]
    L = ["| case | " + " | ".join(f"{a} p90/p10 | {a} slow rounds | {a} max/min" for a in arms) + " |",
         "|---|" + "---|---|---|" * len(arms)]
    for key, t in raw["times"].items():
        if pats and not any(p in key for p in pats):
            continue
        row = [key]
        for a in arms:
            rel, mm = [], []
            for slot, vs in t[a].items():
                v = [x[0] for x in vs if x[0]]
                if len(v) < 3:
                    continue
                m = statistics.median(v)
                rel += [x / m for x in v]
                mm.append(max(v) / min(v))
            rel.sort()
            n = len(rel)
            if not n:
                row += ["-", "-", "-"]
                continue
            p = lambda q: rel[min(n - 1, int(q * n))]
            row += [f"{p(0.9) / p(0.1):.3f}", f"{sum(x > 1.15 for x in rel)}/{n}", f"{max(mm):.2f}"]
        L.append("| " + " | ".join(row) + " |")
    return L
