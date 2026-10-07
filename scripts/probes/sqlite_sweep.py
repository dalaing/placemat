#!/usr/bin/env python3
"""SQLite's B-tree pages at fixed strides, timed in rounds (DESIGN 5.3; examples/sqlite/README.md, "Page
placement"):

    python3 scripts/probes/sqlite_sweep.py --sqlite SQLITE_DIR [--rounds N] [--json OUT]

The M2's stride sweep found page headers crowding into few L1D sets at power-of-two strides (16,384 B:
+2.8% over 25 cases, del_rows +17.5%) and none at malloc's own placement (4,608 B: 32 sets on M2). On a
4 KB L1D set stride (x86 and many arm64 cores) 4,608 B puts the headers in 8 sets, so malloc's placement
might no longer be safe there. Builds sqlite3.c once (clang if found, else cc) and examples/sqlite/
sqlbench.c once per arm: malloc's pages, and a page-cache slab at 4,416, 4,608, 8,192, 8,256, 16,384 and
16,448 B (8,256 and 16,448 are controls with the same footprint as 8,192 and 16,384 and spread headers).
Each round runs every arm once in a fresh random order (one discarded run of each first); each case's
per-round ratio to malloc in the same round gives a median ratio and how many rounds agree in sign; each
arm's mean log ratio over cases gets a t interval over rounds. The noise probe is read before each round.

Writes Markdown to stdout (and to $GITHUB_STEP_SUMMARY when set) and, with --json, every run."""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import shutil
import statistics as st
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

ARMS = [("malloc", None), ("s4416", 4416), ("s4608", 4608), ("s8192", 8192), ("s8256", 8256),
        ("s16384", 16384), ("s16448", 16448)]
FLAGS = ["-O2", "-DSQLITE_THREADSAFE=0", "-DSQLITE_DEFAULT_MEMSTATUS=0", "-DSQLITE_DQS=0",
         "-DSQLITE_OMIT_LOAD_EXTENSION"]
KEY = ["del_rows", "ins_3idx", "upd_rows", "upd_between", "integrity", "ipk_lookup"]
T975 = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36, 9: 2.31, 10: 2.26}


def build(cc: str, src: Path, t: Path) -> dict:
    core = t / "sqlite3.o"
    subprocess.run([cc] + FLAGS + [f"-I{src}", "-c", str(src / "sqlite3.c"), "-o", str(core)], check=True)
    exes = {}
    for name, stride in ARMS:
        extra = [] if stride is None else ["-DSQLBENCH_PAGECACHE", f"-DSQLBENCH_PC_STRIDE={stride}"]
        o = t / f"sqlbench-{name}.o"
        subprocess.run([cc] + FLAGS + extra + [f"-I{src}", "-c", str(REPO / "examples/sqlite/sqlbench.c"), "-o", str(o)],
                       check=True)
        exes[name] = t / f"sqlbench-{name}"
        subprocess.run([cc, "-O2", str(o), str(core), "-lm", "-o", str(exes[name])], check=True)
    return exes


def run(exe: Path) -> dict:
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
    res = {}
    for l in out.splitlines():
        f = l.split("\t")
        if len(f) >= 2 and not l.startswith("#"):
            res[f[0]] = float(f[1])
    return res


def analyse(runs: list[dict]) -> dict:
    rounds = sorted({x["round"] for x in runs})
    by = {(x["round"], x["arm"]): x["times"] for x in runs}
    cases = sorted(by[(rounds[0], "malloc")])
    out = {}
    for name, _ in ARMS[1:]:
        per_case = {}
        for c in cases:
            rs = [by[(r, name)][c] / by[(r, "malloc")][c] for r in rounds if c in by.get((r, name), {})]
            m = st.median(rs)
            per_case[c] = {"ratio": m, "agree": sum((x > 1) == (m > 1) for x in rs), "n": len(rs)}
        per_round = [st.mean(math.log(by[(r, name)][c] / by[(r, "malloc")][c]) for c in cases) for r in rounds]
        n = len(per_round)
        mean = st.mean(per_round)
        half = T975.get(n, 2.0) * (st.stdev(per_round) / math.sqrt(n)) if n > 1 else float("nan")
        out[name] = {"mean": math.exp(mean) - 1, "lo": math.exp(mean - half) - 1, "hi": math.exp(mean + half) - 1,
                     "cases": per_case}
    return out


def markdown(r: dict) -> str:
    L = [f"## SQLite 3.53.0 page strides against malloc: {r['machine']}, {r.get('cpu', '?')}, `{r['cc']}` ({r['rounds']} rounds, arms "
         "in random order per round)", ""]
    if r.get("probe"):
        sp = [x["spread"] for x in r["probe"]]
        L += [f"Noise probe before each round: spread median {st.median(sp):.2%}, max {max(sp):.2%}.", ""]
    keys = [k for k in KEY if k in next(iter(r["analysis"].values()))["cases"]]
    L += ["| arm | mean over cases (95% t over rounds) | " + " | ".join(keys) + " |", "|---|---|" + "---|" * len(keys)]
    for name, a in r["analysis"].items():
        cells = []
        for k in keys:
            c = a["cases"][k]
            cells.append(f"{c['ratio'] - 1:+.1%} ({c['agree']}/{c['n']})")
        L.append(f"| {name} | {a['mean']:+.2%} ({a['lo']:+.2%} to {a['hi']:+.2%}) | " + " | ".join(cells) + " |")
    L += ["", "(Per case: the median over rounds of the ratio to malloc in the same round, and how many rounds agree "
          "in sign.)"]
    return "\n".join(L) + "\n"


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--sqlite", required=True)
    a.add_argument("--rounds", type=int, default=7)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--json")
    a = a.parse_args()
    from placemat import lock
    cc = "clang" if shutil.which("clang") else "cc"
    runs, probe = [], []
    with tempfile.TemporaryDirectory() as t:
        exes = build(cc, Path(a.sqlite), Path(t))
        for e in exes.values():
            run(e)
        rnd = random.Random(a.seed)
        for rd in range(a.rounds):
            probe.append(lock.probe_detail())
            order = list(exes)
            rnd.shuffle(order)
            for name in order:
                runs.append({"round": rd, "arm": name, "times": run(exes[name])})
    from run import cpu_name
    r = {"machine": platform.machine(), "cpu": cpu_name(), "cc": cc, "rounds": a.rounds, "probe": probe, "analysis": analyse(runs),
         "runs": runs}
    md = markdown(r)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
