#!/usr/bin/env python3
"""Check placemat's Amber regression runs against DESIGN.md §10's pass criteria (a)-(e).

    python3.12 examples/amber/check.py RUNS_DIR

RUNS_DIR holds the runs made by the Amber fork's pbt/placemat/validate.sh: a1 a2 (main against
main), b1 b2 (#69 against its base), c11 c12 (windows, main against main), c21 c22 (the window
prototype against its base), d1 d2 (pinned against main, targeted pads, no data axis), e1 e2 (the
coloured build against main, no data axis). Each criterion prints PASS, FAIL or MISSING with the
evidence; a FAIL is a finding to explain, not automatically a regression (DESIGN §10).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from placemat import report  # noqa: E402

PINNED = ["microbench: setdictbig", "microbench: setdictbigall", "microbench: setdictbigname",
          "microbench: chargrade", "microbench: takekeys10x10"]
CONTROLS = ["microbench: grade", "microbench: fsum", "fnd.k: sc_member"]
WIN = ["win.k: s_msum100", "win.k: s_mavg100", "win.k: s_mdev100"]
COLWIN = WIN + ["bench-std.k: msum  ms", "bench-std.k: mavg  ms", "bench-std.k: mdev  ms"]
COLFLAT = ["win.k: s_mmin100", "win.k: s_mmax100", "bench-std.k: mmin  ms", "bench-std.k: mmax  ms"]


def load(d: Path, n: str):
    p = d / f"{n}.json"
    return json.loads(p.read_text()) if p.exists() else None


def arm2(j):
    return [a["name"] for a in j["arms"]][1]


def say(ok, what, ev):
    tag = "MISSING" if ok is None else ("PASS" if ok else "FAIL")
    print(f"[{tag}] {what}")
    for e in ev:
        print("     " + e)
    return ok


def crit_a(d):
    out = []
    for n in ("a1", "a2"):
        j = load(d, n)
        if not j:
            return say(None, "(a) main against main", [f"{n} missing"])
        a = arm2(j)
        ch = [k for k, c in j["cases"].items() if c and c["compare"][a]["verdict"] == "change"]
        code = [k for k, c in j["cases"].items() if c and "code" in c["flags"]]
        mp = [k for k, c in j["cases"].items() if c and k.split(": ")[-1] in ("maxprior", "minprior")
              and ({"colour", "step"} & set(c["flags"]))]
        other = {k: c["flags"] for k, c in j["cases"].items() if c and c["flags"]}
        out.append((not ch and not code and not mp,
                    f"{n}: changes {ch or 'none'}; code flags {code or 'none'}; maxprior/minprior data flags "
                    f"{mp or 'none'}; other flags {other or 'none'}"))
    return say(all(o for o, _ in out), "(a) main against main: no change verdicts, no code flags, no maxprior/minprior data flags",
               [e for _, e in out])


def crit_b(d):
    out = []
    for n in ("b1", "b2"):
        j = load(d, n)
        if not j:
            return say(None, "(b) #69 against its base", [f"{n} missing"])
        a = arm2(j)
        ev, ok = [], True
        for k in ("microbench: grade", "microbench: igradedown"):
            c = j["cases"].get(k)
            v = c and c["compare"][a]
            good = bool(v) and v["verdict"] == "placement" and "stock code layout" in v["attributed"]
            ok &= good
            ev.append(f"{k}: " + (f"{v['verdict']} ({'+'.join(v['attributed']) or '-'}), stock builds "
                                  f"{v['stock_builds'][0] - 1:+.1%}, averaged {v['est'] - 1:+.1%}" if v else "missing"))
        c = j["cases"].get("microbench: setdictnest")
        v = c and c["compare"][a]
        ok &= bool(v) and v["verdict"] == "change"
        ev.append("setdictnest: " + (f"{v['verdict']} {v['est'] - 1:+.1%} ({v['lo'] - 1:+.1%} to {v['hi'] - 1:+.1%})" if v else "missing"))
        out.append((ok, f"{n}: " + "; ".join(ev)))
    return say(all(o for o, _ in out), "(b) grade/igradedown placement from the stock code layout; setdictnest a change",
               [e for _, e in out])


def crit_c(d):
    ev, ok = [], True
    runf = {k: 0 for k in WIN}
    for n in ("c11", "c12"):
        j = load(d, n)
        if not j:
            return say(None, "(c) windows", [f"{n} missing"])
        a = arm2(j)
        for k in WIN:
            c = j["cases"].get(k)
            if not c:
                ok = False
                ev.append(f"{n} {k}: missing")
                continue
            v = c["compare"][a]
            st = "step" in c["flags"]
            runf[k] += "run" in c["flags"]
            cov = v["lo"] <= 1 <= v["hi"]
            ok &= st and cov
            ev.append(f"{n} {k}: flags {c['flags'] or '-'}; change {v['est'] - 1:+.1%} ({v['lo'] - 1:+.1%} to {v['hi'] - 1:+.1%})")
    for k, nrun in runf.items():
        ok &= nrun >= 1
        ev.append(f"{k}: run-flagged in {nrun} of 2 main-against-main runs")
    for n in ("c21", "c22"):
        j = load(d, n)
        if not j:
            return say(None, "(c) windows", [f"{n} missing"])
        a = arm2(j)
        for k in WIN + ["win.k: s_mmin100"]:
            c = j["cases"].get(k)
            v = c and c["compare"][a]
            if not v:
                ok = False
                ev.append(f"{n} {k}: missing")
                continue
            dep = v["dependence"].get("stepped", {}).get("sig", False)
            want = k != "win.k: s_mmin100"
            ok &= v["verdict"] == "change" and dep == want
            ev.append(f"{n} {k}: {v['verdict']} {v['est'] - 1:+.1%}; step-dependent {dep} (want {want}); "
                      f"settings " + ", ".join(f"{s} {t[0] - 1:+.1%}" for s, t in v["settings"].items()))
    return say(ok, "(c) windows: main/main step-flagged in both runs, run-flagged in one, covering 1; prototype a "
                   "change, step-dependent except mmin100", ev)


def crit_d(d):
    ev, ok = [], True
    for n in ("d1", "d2"):
        j = load(d, n)
        if not j:
            return say(None, "(d) pinned against main", [f"{n} missing"])
        base, pin = [a["name"] for a in j["arms"]][:2]
        for k in PINNED + CONTROLS:
            c = j["cases"].get(k)
            if not c:
                ok &= k in CONTROLS
                ev.append(f"{n} {k}: missing")
                continue
            A, P = c["arms"][base], c["arms"][pin]
            if k in PINNED:
                good = "code" in A["flags"] and "code" not in P["flags"] and P["code_max"] < A["code_max"] / 2
                ok &= good
            ev.append(f"{n} {k}{' (control)' if k in CONTROLS else ''}: main spread {A['code_max']:.1%} p {A['code_p']:.2g} "
                      f"flags {A['flags'] or '-'}; pinned {P['code_max']:.1%} p {P['code_p']:.2g} flags {P['flags'] or '-'}")
    return say(ok, "(d) the five cases code-flagged on main and not on the pinned arm (per arm), pinned spread well below main's", ev)


def crit_e(d):
    ev, ok = [], True
    for n in ("e1", "e2"):
        p = d / f"{n}.raw.json"
        if not p.exists():
            return say(None, "(e) coloured build", [f"{n} missing"])
        raw = report.load_raw(p)
        rows = report.twospeed(raw)
        arms = [a["name"] for a in raw["arms"]]
        for r in rows[2:]:
            cells = [x.strip() for x in r.strip("|").split("|")]
            k = cells[0]
            if k not in COLWIN + COLFLAT:
                continue
            b, c = float(cells[1]), float(cells[4])
            if k in COLWIN:
                good = c <= 1.15 * 1.02
                ok &= good
                ev.append(f"{n} {k}: p90/p10 {arms[0]} {b:.3f} -> {arms[1]} {c:.3f} (want <= ~1.15; main 1.4-2.6 in the reference)")
            else:
                good = b <= 1.17 * 1.02 and c <= 1.17 * 1.02
                ok &= good
                ev.append(f"{n} {k}: p90/p10 {b:.3f} / {c:.3f} (want <= ~1.17 both)")
    return say(ok, "(e) the coloured build ends the windows' two-speed switching", ev)


def main():
    d = Path(sys.argv[1])
    res = [f(d) for f in (crit_a, crit_b, crit_c, crit_d, crit_e)]
    print("\nsummary: " + ", ".join(f"({x}) {'PASS' if r else 'MISSING' if r is None else 'FAIL'}"
                                    for x, r in zip("abcde", res)))
    return 0 if all(r for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
