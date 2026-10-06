#!/usr/bin/env python3
"""Do the region lottery and the stack offset change speed here? (DESIGN 5.4, 5.5)

    python3 scripts/probes/lottery.py [--runs N] [--json OUT]

Runs lotteryprobe N times (fresh process each: a fresh region base and stack offset under address
randomisation) and asks whether its window kernel's time depends on the region's base, and its
stack-heavy kernel's time on the stack's offset: rank correlations with permutation p-values, and the
median time per class (the base's 2 MB-page index mod 8 and whether it is the most common base; the stack
offset mod 64 and its 1 KB slice mod 4 KB), against the run-to-run noise. On Linux both draws are random
per execution, so placemat can only record them; this says whether recording them is worth anything.

Writes Markdown to stdout (and to $GITHUB_STEP_SUMMARY when set) and, with --json, every run."""
from __future__ import annotations

import argparse
import collections
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
sys.path.insert(0, str(HERE))
from counters import spearman  # noqa: E402
from run import build, compilers  # noqa: E402


def perm_p(x: list[float], y: list[float], perms: int = 2000, seed: int = 1) -> float:
    obs = abs(spearman(x, y))
    rnd = random.Random(seed)
    yy = y[:]
    hits = 0
    for _ in range(perms):
        rnd.shuffle(yy)
        hits += abs(spearman(x, yy)) >= obs
    return (hits + 1) / (perms + 1)


def classes(xs: list[int], ys: list[float]) -> dict:
    by = collections.defaultdict(list)
    for x, y in zip(xs, ys):
        by[x].append(y)
    m = st.median(ys)
    return {k: (st.median(v) / m - 1, len(v)) for k, v in sorted(by.items()) if len(v) >= 3}


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--runs", type=int, default=200)
    a.add_argument("--json")
    a = a.parse_args()
    with tempfile.TemporaryDirectory() as t:
        exe = build(compilers()[0], HERE / "lotteryprobe.c", Path(t) / "lotteryprobe")
        subprocess.run([str(exe)], capture_output=True)            # warm-up
        rows = []
        for _ in range(a.runs):
            f = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()
            rows.append({"base": int(f[0]), "stack": int(f[1]), "window_ns": int(f[2]), "stack_ns": int(f[3])})
    base = [x["base"] for x in rows]
    stk = [x["stack"] for x in rows]
    win = [x["window_ns"] for x in rows]
    stt = [x["stack_ns"] for x in rows]
    mode = collections.Counter(base).most_common(1)[0]
    noise = {k: st.median(abs(v - st.median(vs)) for v in vs) / st.median(vs) for k, vs in (("window", win), ("stack", stt))}
    r = {"machine": platform.machine(), "system": platform.system(), "runs": a.runs,
         "noise": noise, "base_mode": [hex(mode[0]), mode[1]],
         "window_vs_base": {"spearman": spearman(base, win), "p": perm_p(base, win)},
         "window_by_page_mod8": classes([(b >> 21) % 8 for b in base], win),
         "window_by_mode": classes([int(b == mode[0]) for b in base], win) if mode[1] > 2 else {},
         "stack_vs_offset4k": {"spearman": spearman([s % 4096 for s in stk], stt), "p": perm_p([s % 4096 for s in stk], stt)},
         "stack_by_mod64": classes([s % 64 for s in stk], stt),
         "stack_by_1k": classes([(s % 4096) // 1024 for s in stk], stt),
         "rows": rows}
    md = markdown(r)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1))
    return 0


def markdown(r: dict) -> str:
    def cl(d):
        return ", ".join(f"{k}: {v:+.2%} (n={n})" for k, (v, n) in d.items()) or "-"
    L = [f"## Region lottery and stack offset against speed: {r['system']} {r['machine']} ({r['runs']} executions)", "",
         f"Run-to-run noise (MAD/median): window kernel {r['noise']['window']:.2%}, stack kernel {r['noise']['stack']:.2%}. "
         f"Most common region base {r['base_mode'][0]} ({r['base_mode'][1]} of {r['runs']}).", "",
         f"- window time against the region base: Spearman {r['window_vs_base']['spearman']:+.2f} (p {r['window_vs_base']['p']:.3f}); "
         f"by 2 MB-page index mod 8: {cl(r['window_by_page_mod8'])}; at the most common base (1) or not (0): {cl(r['window_by_mode'])}",
         f"- stack-kernel time against the stack offset mod 4 KB: Spearman {r['stack_vs_offset4k']['spearman']:+.2f} "
         f"(p {r['stack_vs_offset4k']['p']:.3f}); by offset mod 64: {cl(r['stack_by_mod64'])}; by 1 KB slice: {cl(r['stack_by_1k'])}"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
