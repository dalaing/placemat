#!/usr/bin/env python3
"""Hardware event counts across code-axis pads, for Lua's interpreter (DESIGN 5.2; Linux, with counters):

    python3 scripts/probes/counters.py --lua LUA_SRC_DIR [--runs N] [--json OUT]

Builds Lua with each C compiler found (objects once; only the link changes), with a code-axis pad of 0 to
60 bytes in 4-byte steps linked first, and runs a recursive-fib script N times per build under pmustat,
counting instructions, cycles and the front-end events of the CPU's PMU (arm64 PMUv3: L1I and iTLB
refills, branch mispredictions, front-end stall cycles). Reports each pad's medians and, grouped by
luaV_execute's address mod 8 and mod 16, how the counts move with the interpreter's address phase: the
M2's Lua survey found a 2-5% effect of that phase (DESIGN 5.2), and counts show which front-end mechanism
carries it without timing anything. Exits quietly when the machine cannot count (perf_event_paranoid > 2,
or no CPU PMU, as on the hosted x86 runners).

Writes Markdown to stdout (and to $GITHUB_STEP_SUMMARY when set) and, with --json, the counts."""
from __future__ import annotations

import argparse
import json
import os
import platform
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
from run import build, compilers  # noqa: E402

PADS = list(range(0, 64, 4))
FIB = "local function fib(n) if n < 2 then return n end return fib(n - 1) + fib(n - 2) end\nprint(fib(30))\n"
GENERIC = [("instructions", 0, 1), ("cycles", 0, 0)]
ARM = [("l1i_refill", 4, 0x01), ("itlb_refill", 4, 0x02), ("br_mispred", 4, 0x10), ("stall_frontend", 4, 0x23)]


def events() -> list[tuple[str, int, int]]:
    return GENERIC + (ARM if platform.machine() in ("aarch64", "arm64") else [])


def count(stat: Path, exe: Path, script: Path, ev) -> dict:
    r = subprocess.run([str(stat)] + [f"{n}={t}:{c:x}" for n, t, c in ev] + ["--", str(exe), str(script)],
                       capture_output=True, text=True)
    out = {}
    for l in r.stderr.splitlines():
        f = l.split()
        if len(f) >= 2 and f[0] in {n for n, _, _ in ev}:
            out[f[0]] = None if f[1] == "error" else int(f[1])
    return out


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--lua", required=True)
    a.add_argument("--runs", type=int, default=5)
    a.add_argument("--json")
    a = a.parse_args()
    ev = events()
    rows = []
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        stat = build(compilers()[0], HERE / "pmustat.c", t / "pmustat")
        script = t / "fib.lua"
        script.write_text(FIB)
        for cc in compilers():
            objs = compile_objects(cc, "lua", Path(a.lua), t / cc / "obj")
            for p in PADS:
                exe = link(cc, objs, p, t / cc / f"lua{p}")
                fn = B.function_map(exe).get("luaV_execute")
                runs = [count(stat, exe, script, ev) for _ in range(a.runs)]
                if any(v is None for v in runs[0].values()) or not runs[0]:
                    msg = "Counters unavailable here: " + ", ".join(f"{k}" for k, v in runs[0].items() if v is None)
                    print(msg)
                    return 0
                med = {k: st.median(r[k] for r in runs) for k in runs[0]}
                rows.append({"cc": cc, "pad": p, "luaV_execute_mod64": fn.start % 64 if fn else None, **med})
    r = {"machine": platform.machine(), "events": [n for n, _, _ in ev], "runs": a.runs, "rows": rows}
    md = markdown(r)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1))
    return 0


def rel(v: float, base: float) -> str:
    return f"{v / base - 1:+.1%}" if base else f"{v:.0f} (base 0)"


def markdown(r: dict) -> str:
    keys = [k for k in r["events"]]
    L = [f"## Lua fib(30): hardware events across code-axis pads ({r['machine']}, median of {r['runs']} runs)", "",
         "| compiler | pad | luaV_execute mod 64 | " + " | ".join(keys) + " |", "|---|---|---|" + "---|" * len(keys)]
    for x in r["rows"]:
        L.append(f"| {x['cc']} | {x['pad']} | {x['luaV_execute_mod64']} | " + " | ".join(f"{x[k]:.0f}" for k in keys) + " |")
    L += ["", "By luaV_execute's address phase (each event's median over pads with that phase, relative to the "
          "median over all pads):", ""]
    for cc in sorted({x["cc"] for x in r["rows"]}):
        rs = [x for x in r["rows"] if x["cc"] == cc and x["luaV_execute_mod64"] is not None]
        for mod in (8, 16):
            ph = sorted({x["luaV_execute_mod64"] % mod for x in rs})
            if len(ph) < 2:
                continue
            L.append(f"- `{cc}`, mod {mod}: " + "; ".join(
                f"{q}: " + ", ".join(f"{k} {rel(st.median(x[k] for x in rs if x['luaV_execute_mod64'] % mod == q), st.median(x[k] for x in rs))}"
                                     for k in keys if k != "instructions")
                for q in ph))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
