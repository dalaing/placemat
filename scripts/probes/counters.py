#!/usr/bin/env python3
"""Lua's interpreter across code-axis pads: timings, and hardware event counts where the machine can count
(DESIGN 5.2; Linux):

    python3 scripts/probes/counters.py --lua LUA_SRC_DIR [--rounds N] [--json OUT]

Builds Lua with each C compiler found (objects once; only the link changes), with a code-axis pad of 0 to
60 bytes in 4-byte steps linked first, then runs a recursive-fib script in rounds: each round runs every
(compiler, pad) build once, in a fresh random order (as placemat's rounds do, so drift cannot line up with
a pad), under pmustat, which reports the child's wall and user time and, where perf_event_paranoid and a
CPU PMU allow, its instructions, cycles and front-end events (arm64 PMUv3: L1I and iTLB refills, branch
mispredictions, front-end stall cycles).

The question is whether the code axis's effect cuts through a shared machine's noise. Per compiler: each
pad's median wall time; the run-to-run noise within a pad against the spread between pads; a permutation
p-value for that spread (runs shuffled among pads within each round); the time by luaV_execute's address
mod 8 and mod 16 (the phase behind a 2-5% effect on the M2); and, with counters, how well each pad's
median counts track its median time (a pad that stalls more and also runs slower is more likely a real
effect than noise). A second set of builds compiles lvm.c (luaV_execute's file) with -falign-functions=64:
the pads still move the rest of the code, but luaV_execute stays at 0 mod 64, so if its phase is the cause
the spread collapses (and the level shows phase 0's speed): whether aligning the hot function, the pin
rule's fix, works here. The noise probe (placemat.lock) is read before every round, to set its spread
beside the measured noise (DESIGN 11 item 3).

Writes Markdown to stdout (and to $GITHUB_STEP_SUMMARY when set) and, with --json, every run."""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics as st
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from placemat import binary as B  # noqa: E402
from layout import compile_objects, link  # noqa: E402
from run import build, compilers, cpu_name  # noqa: E402

PADS = list(range(0, 64, 4))
VARIANTS = [("", {}), ("+align64", {"lvm.c": ["-falign-functions=64"]})]
FIB = "local function fib(n) if n < 2 then return n end return fib(n - 1) + fib(n - 2) end\nprint(fib(30))\n"
GENERIC = [("instructions", 0, 1), ("cycles", 0, 0)]
ARM = [("l1i_refill", 4, 0x01), ("itlb_refill", 4, 0x02), ("br_mispred", 4, 0x10), ("stall_frontend", 4, 0x23)]
TIMES = ("wall_ns", "user_us")


def events() -> list[tuple[str, int, int]]:
    return GENERIC + (ARM if platform.machine() in ("aarch64", "arm64") else [])


def run_once(stat: Path, exe: Path, script: Path, ev) -> dict:
    r = subprocess.run([str(stat)] + [f"{n}={t}:{c:x}" for n, t, c in ev] + ["--", str(exe), str(script)],
                       capture_output=True, text=True)
    out = {}
    names = {n for n, _, _ in ev} | set(TIMES)
    for l in r.stderr.splitlines():
        f = l.split()
        if len(f) >= 2 and f[0] in names and f[1] != "error":
            out[f[0]] = int(f[1])
    return out


def mad(xs: list[float]) -> float:
    m = st.median(xs)
    return st.median(abs(x - m) for x in xs)


def spread_stat(per_pad: dict) -> float:
    meds = [st.median(v) for v in per_pad.values()]
    return st.pvariance(meds) if len(meds) > 1 else 0.0


def perm_p(runs: list[dict], key: str, perms: int = 2000, seed: int = 1) -> float:
    """p for the between-pad variance of medians, permuting pad labels within each round."""
    by_round: dict[int, list[tuple[int, float]]] = {}
    for x in runs:
        by_round.setdefault(x["round"], []).append((x["pad"], x[key]))
    def stat(assign):
        per = {}
        for pairs in assign.values():
            for p, v in pairs:
                per.setdefault(p, []).append(v)
        return spread_stat(per)
    obs = stat(by_round)
    rnd = random.Random(seed)
    hits = 0
    for _ in range(perms):
        sh = {}
        for r, pairs in by_round.items():
            pads = [p for p, _ in pairs]
            rnd.shuffle(pads)
            sh[r] = [(p, v) for p, (_, v) in zip(pads, pairs)]
        hits += stat(sh) >= obs
    return (hits + 1) / (perms + 1)


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(x):
        o = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for k, i in enumerate(o):
            r[i] = k
        return r
    ra, rb = ranks(a), ranks(b)
    return st.correlation(ra, rb) if len(a) > 2 and st.pstdev(ra) and st.pstdev(rb) else float("nan")


def analyse(runs: list[dict], cc: str, counted: list[str]) -> dict:
    rs = [x for x in runs if x["cc"] == cc and "wall_ns" in x]
    base = [x["wall_ns"] for x in runs if x["cc"] == cc.split("+")[0] and "wall_ns" in x]
    pads = sorted({x["pad"] for x in rs})
    per = {p: [x["wall_ns"] for x in rs if x["pad"] == p] for p in pads}
    med = {p: st.median(v) for p, v in per.items()}
    allmed = st.median(med.values())
    phase = {x["pad"]: x["mod64"] for x in rs}
    out = {"pads": len(pads), "rounds": max(len(v) for v in per.values()),
           "within_noise": st.median(mad(v) / st.median(v) for v in per.values()),
           "between_range": (max(med.values()) - min(med.values())) / allmed,
           "between_sd": st.pstdev(med.values()) / allmed, "p_wall": perm_p(rs, "wall_ns"),
           "level_vs_default": allmed / st.median(base) - 1 if "+" in cc and base else None,
           "per_pad": {p: {"mod64": phase[p], "wall": med[p] / allmed - 1,
                           **{k: st.median(x[k] for x in rs if x["pad"] == p and k in x) for k in counted}}
                       for p in pads},
           "by_phase": {}}
    for mod in (8, 16):
        ph = sorted({phase[p] % mod for p in pads if phase[p] is not None})
        if len(ph) > 1:
            out["by_phase"][mod] = {q: st.median(med[p] for p in pads if phase[p] % mod == q) / allmed - 1 for q in ph}
    out["track"] = {k: spearman([med[p] for p in pads], [out["per_pad"][p][k] for p in pads])
                    for k in counted if k not in ("instructions",)}
    return out


def markdown(r: dict) -> str:
    L = [f"## Lua fib(30) across code-axis pads: {r['machine']}, {r.get('cpu', '?')} ({r['rounds']} rounds, pads in random order per round)", ""]
    if r.get("probe"):
        sp = [x["spread"] for x in r["probe"]]
        L += [f"Noise probe before each round: spread median {st.median(sp):.2%}, max {max(sp):.2%} "
              f"(median {st.median(x['median_ms'] for x in r['probe']):.1f} ms).", ""]
    for cc, a in r["analysis"].items():
        lv = f"; level {a['level_vs_default']:+.2%} against the default builds" if a.get("level_vs_default") is not None else ""
        L += [f"**`{cc}`:** within-pad noise (MAD/median) {a['within_noise']:.2%}; between-pad spread: range "
              f"{a['between_range']:.2%}, sd {a['between_sd']:.2%} of the median; permutation p {a['p_wall']:.3f} "
              f"({a['pads']} pads){lv}."]
        for mod, d in a["by_phase"].items():
            L.append(f"- wall time by luaV_execute mod {mod}: " + ", ".join(f"{q}: {v:+.2%}" for q, v in d.items()))
        if a["track"]:
            L.append("- pads' median counts against their median wall time (Spearman): "
                     + ", ".join(f"{k} {v:+.2f}" for k, v in a["track"].items()))
        keys = [k for k in r["counted"] if k != "instructions"]
        cols = ["pad", "luaV_execute mod 64", "wall vs median"] + keys
        L += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for p, x in a["per_pad"].items():
            L.append("| " + " | ".join([str(p), str(x["mod64"]), f"{x['wall']:+.2%}"] + [f"{x[k]:.0f}" for k in keys]) + " |")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--lua", required=True)
    a.add_argument("--rounds", type=int, default=7)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--json")
    a = a.parse_args()
    if not sys.platform.startswith("linux"):
        print("counters.py needs Linux (perf_event_open, wait4)")
        return 0
    ev = events()
    builds, runs = [], []
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        stat = build(compilers()[0], HERE / "pmustat.c", t / "pmustat")
        script = t / "fib.lua"
        script.write_text(FIB)
        for cc in compilers():
            for vname, per_file in VARIANTS:
                label = cc + vname
                objs = compile_objects(cc, "lua", Path(a.lua), t / label / "obj", per_file)
                for p in PADS:
                    exe = link(cc, objs, p, t / label / f"lua{p}")
                    fn = B.function_map(exe).get("luaV_execute")
                    builds.append((label, p, exe, fn.start % 64 if fn else None))
        for (cc, p, exe, m) in builds:                      # one discarded run of each build
            run_once(stat, exe, script, ev)
        from placemat import lock
        probe = []
        rnd = random.Random(a.seed)
        for rd in range(a.rounds):
            probe.append(lock.probe_detail())
            order = builds[:]
            rnd.shuffle(order)
            for (cc, p, exe, m) in order:
                runs.append({"round": rd, "cc": cc, "pad": p, "mod64": m, **run_once(stat, exe, script, ev)})
    counted = [n for n, _, _ in ev if all(n in x for x in runs)]
    r = {"machine": platform.machine(), "cpu": cpu_name(), "rounds": a.rounds, "counted": counted, "probe": probe,
         "analysis": {cc: analyse(runs, cc, counted) for cc in sorted({x["cc"] for x in runs})}, "runs": runs}
    md = markdown(r)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
