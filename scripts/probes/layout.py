#!/usr/bin/env python3
"""Static layout of real code at the code axis's pads (DESIGN 5.2; no timing):

    python3 scripts/probes/layout.py --lua LUA_SRC_DIR --sqlite SQLITE_DIR [--json OUT]

Builds Lua (its src/ directory) and SQLite (a directory holding sqlite3.c, linked with placemat's
examples/sqlite/sqlbench.c) with each C compiler found, at -O2, with a code-axis pad of 0, 16, 32 and 48
bytes linked first (the objects are compiled once; only the link changes). For each build it counts
small loops (backward branches within 256 B) crossing a 32-byte and a 64-byte window, and branches the
Intel JCC erratum affects (a jump, call or return, or a fused compare-and-jump, that crosses or ends on a
32-byte boundary), over the whole binary and in the hot function (Lua's luaV_execute, SQLite's
sqlite3VdbeExec). Counts that change across pads show what the pads vary on this platform.

Writes Markdown to stdout (and to $GITHUB_STEP_SUMMARY when set) and, with --json, the counts."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from placemat import binary as B  # noqa: E402
from placemat.build import pad_source  # noqa: E402

sys.path.insert(0, str(HERE))
from run import compilers  # noqa: E402

PADS = [0, 16, 32, 48]
FUSIBLE = ("cmp", "test", "add", "sub", "and", "inc", "dec")
HOT = {"lua": "luaV_execute", "sqlite": "sqlite3VdbeExec"}
SQLITE_FLAGS = ["-DSQLITE_THREADSAFE=0", "-DSQLITE_DEFAULT_MEMSTATUS=0", "-DSQLITE_DQS=0",
                "-DSQLITE_OMIT_LOAD_EXTENSION"]


def compile_objects(cc: str, project: str, src: Path, out: Path, per_file: dict | None = None) -> list[Path]:
    """Objects for `project`; `per_file` maps a source file's name to extra flags for it alone."""
    out.mkdir(parents=True, exist_ok=True)
    if project == "lua":
        files = [f for f in sorted(src.glob("*.c")) if f.name not in ("luac.c", "onelua.c")]
        flags = ["-DLUA_USE_LINUX"] if sys.platform.startswith("linux") else ["-DLUA_USE_MACOSX"]
    else:
        files = [src / "sqlite3.c", REPO / "examples" / "sqlite" / "sqlbench.c"]
        flags = SQLITE_FLAGS + [f"-I{src}"]
    objs = []
    for f in files:
        o = out / (f.stem + ".o")
        subprocess.run([cc, "-O2"] + flags + (per_file or {}).get(f.name, []) + ["-c", str(f), "-o", str(o)], check=True)
        objs.append(o)
    return objs


def link(cc: str, objs: list[Path], pad: int, out: Path) -> Path:
    ps = out.parent / f"pad{pad}.c"
    ps.write_text(pad_source(pad))
    po = out.parent / f"pad{pad}.o"
    subprocess.run([cc, "-O2", "-c", str(ps), "-o", str(po)], check=True)
    libs = ["-lm"] + (["-ldl"] if sys.platform.startswith("linux") else [])
    subprocess.run([cc, "-O2", str(po)] + [str(o) for o in objs] + libs + ["-o", str(out)], check=True)
    return out


def jcc_affected(path: Path, lo: int = 0, hi: int = 1 << 62) -> int:
    ins = [x for x in B._insns(path)]
    n = 0
    for i, x in enumerate(ins[:-1]):
        if not (lo <= x.addr < hi):
            continue
        m = x.mnem
        if not (m.startswith("j") or m.startswith("call") or m.startswith("ret")):
            continue
        start = x.addr
        if i and m.startswith("j") and m != "jmp" and ins[i - 1].mnem.startswith(FUSIBLE):
            start = ins[i - 1].addr                     # macro-fused with the compare before it
        end = ins[i + 1].addr
        if end - start > 16:                            # past a function's end: not one instruction
            continue
        if start // 32 != (end - 1) // 32 or end % 32 == 0:
            n += 1
    return n


def scan(path: Path, hot: str) -> dict:
    fm = B.function_map(path)
    h = fm.get(hot)
    ls = B.loops(path)
    hl = [l for l in ls if h and h.start <= l.start < h.end]
    is_x86 = B.info(path).arch == "x86_64"
    return {"loops": len(ls), "loops_x32": sum(B.crosses(l.start, l.end, 32) for l in ls),
            "loops_x64": sum(B.crosses(l.start, l.end, 64) for l in ls),
            "jcc_affected": jcc_affected(path) if is_x86 else None,
            "hot_start_mod64": h.start % 64 if h else None, "hot_loops": len(hl),
            "hot_loops_x32": sum(B.crosses(l.start, l.end, 32) for l in hl),
            "hot_loops_x64": sum(B.crosses(l.start, l.end, 64) for l in hl),
            "hot_jcc_affected": jcc_affected(path, h.start, h.end) if (h and is_x86) else None}


def markdown(r: dict) -> str:
    L = ["## Static layout at the code axis's pads", "",
         "| project | compiler | pad | hot fn start mod 64 | loops ×32 / ×64 (of) | hot loops ×32 / ×64 (of) | JCC-affected branches (hot) |",
         "|---|---|---|---|---|---|---|"]
    for row in r["rows"]:
        x = row["scan"]
        j = "-" if x["jcc_affected"] is None else f"{x['jcc_affected']} ({x['hot_jcc_affected']})"
        L.append(f"| {row['project']} | {row['cc']} | {row['pad']} | {x['hot_start_mod64']} | "
                 f"{x['loops_x32']} / {x['loops_x64']} ({x['loops']}) | "
                 f"{x['hot_loops_x32']} / {x['hot_loops_x64']} ({x['hot_loops']}) | {j} |")
    return "\n".join(L) + "\n"


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--lua")
    a.add_argument("--sqlite")
    a.add_argument("--json")
    a = a.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for project, src in (("lua", a.lua), ("sqlite", a.sqlite)):
            if not src:
                continue
            for cc in compilers():
                objs = compile_objects(cc, project, Path(src), t / project / cc / "obj")
                for p in PADS:
                    exe = link(cc, objs, p, t / project / cc / f"bin{p}")
                    rows.append({"project": project, "cc": cc, "pad": p, "scan": scan(exe, HOT[project])})
    r = {"rows": rows}
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
