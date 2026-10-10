#!/usr/bin/env python3
"""SQLite's side of placemat's build protocol on Linux (ELF): a copy of ../build.py with the
placement check rewritten for ELF. MIT, no SQLite source here.

Run inside the Linux target (placemat's [target] prefix), with the same environment as ../build.py:
PLACEMAT_SRC (an amalgamation directory), PLACEMAT_OUT, PLACEMAT_WORK, PLACEMAT_PAD_SOURCE for a
padded build, PLACEMAT_HOOK=1 and PLACEMAT_CFLAGS for a hooked one.

    sqlite3.o          compiled once per arm and compiler, cached in PLACEMAT_WORK
    sqlbench.o         the harness (../sqlbench.c), with PLACEMAT_CFLAGS
    pad.o              PLACEMAT_PAD_SOURCE, linked first among the objects: GNU ld and lld lay out
                       .text input sections in command-line order (no LTO), after the C runtime's
                       start files (crt1.o, crti.o, crtbegin.o), so the pad moves all of SQLite
    placemat_alloc.o   hooked builds only, linked last, so the hook moves no SQLite code

    link: $CC -o sqlbench [pad.o] sqlite3.o sqlbench.o [placemat_alloc.o] -lm

Changed from ../build.py: on ELF the C runtime's code (_start, call_weak_fn, deregister_tm_clones,
frame_dummy, ...) precedes the pad, so the check is that no SQLite function precedes placemat_pad,
that the next text symbol after it comes from sqlite3.o, and that it starts at least the pad's size
after it. The harness is ../sqlbench.c (portable: CLOCK_MONOTONIC off macOS).

Environment overrides: SQLBENCH_CC (default cc; the Linux survey sets clang in placemat.toml),
SQLBENCH_CFLAGS (default below, as ../build.py).
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent / "sqlbench.c"
CC = os.environ.get("SQLBENCH_CC", "cc")
CFLAGS = os.environ.get("SQLBENCH_CFLAGS",
                        "-O2 -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DQS=0 "
                        "-DSQLITE_OMIT_LOAD_EXTENSION").split()


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"sqlite linux/build.py: {' '.join(map(str, cmd))} failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return r.stdout


def cc_id() -> str:
    return subprocess.run([CC, "--version"], capture_output=True, text=True).stdout


def text_symbols(b: Path) -> list[tuple[int, str]]:
    out = []
    for l in sh(["nm", "-n", str(b)]).splitlines():
        p = l.split()
        if len(p) == 3 and p[1] in "tT":
            out.append((int(p[0], 16), p[2]))
    return out


def main():
    src, out, work = (Path(os.environ[k]) for k in ("PLACEMAT_SRC", "PLACEMAT_OUT", "PLACEMAT_WORK"))
    pad = os.environ.get("PLACEMAT_PAD_SOURCE")
    hooked = os.environ.get("PLACEMAT_HOOK") == "1"
    extra = os.environ.get("PLACEMAT_CFLAGS", "").split()
    ldflags = os.environ.get("PLACEMAT_LDFLAGS", "").split()
    amalg = src / "sqlite3.c"
    if not amalg.exists():
        sys.exit(f"sqlite linux/build.py: no sqlite3.c in {src}")

    key = hashlib.sha1(amalg.read_bytes() + (src / "sqlite3.h").read_bytes()
                       + " ".join([CC, *CFLAGS]).encode() + cc_id().encode()).hexdigest()[:12]
    lib = work / f"sqlite3-{key}.o"
    if not lib.exists():
        tmp = work / f".sqlite3-{key}-{os.getpid()}.o"
        sh([CC, *CFLAGS, "-I", str(src), "-c", str(amalg), "-o", str(tmp)])
        os.replace(tmp, lib)

    objs = []
    if pad:
        sh([CC, "-O2", "-c", pad, "-o", str(out / "pad.o")])
        objs.append(out / "pad.o")
    objs.append(lib)
    sh([CC, *CFLAGS, *extra, "-I", str(src), "-c", str(HARNESS), "-o", str(out / "sqlbench.o")])
    objs.append(out / "sqlbench.o")
    if hooked:
        inc = os.environ["PLACEMAT_INCLUDE"]
        sh([CC, *CFLAGS, *extra, "-I", inc, "-c", str(Path(inc) / "placemat_alloc.c"), "-o", str(out / "placemat_alloc.o")])
        objs.append(out / "placemat_alloc.o")
    binary = out / "sqlbench"
    sh([CC, "-o", str(binary), *map(str, objs), "-lm", *ldflags])

    if pad:
        syms = text_symbols(binary)
        names = [n for _, n in syms]
        if "placemat_pad" not in names:
            sys.exit("sqlite linux/build.py: no placemat_pad in the binary")
        i = names.index("placemat_pad")
        pa = syms[i][0]
        lib_syms = {l.split()[2] for l in sh(["nm", str(lib)]).splitlines()
                    if len(l.split()) == 3 and l.split()[1] in "tT"}
        before = [n for a, n in syms if n in lib_syms and a < pa]
        if before:
            sys.exit(f"sqlite linux/build.py: SQLite functions before placemat_pad: {before[:5]}")
        if i + 1 >= len(syms) or names[i + 1] not in lib_syms:
            sys.exit(f"sqlite linux/build.py: the symbol after placemat_pad ({names[i + 1:i + 2]}) is not from sqlite3.o")
        d = syms[i + 1][0] - pa
        n = int(os.environ.get("PLACEMAT_PAD", "0"))
        if d < n:
            sys.exit(f"sqlite linux/build.py: {names[i + 1]} starts {d} B after placemat_pad, < pad {n}")
        print(f"sqlite linux/build.py: pad {n} B moves SQLite's code by {d} B ({names[i + 1]})", file=sys.stderr)
    for o in out.glob("*.o"):
        o.unlink()


if __name__ == "__main__":
    main()
