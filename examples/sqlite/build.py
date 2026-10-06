#!/usr/bin/env python3
"""SQLite's side of placemat's build protocol (placemat/build.py; MIT, no SQLite source here).

An arm is a directory holding an SQLite amalgamation (sqlite3.c, sqlite3.h), e.g. one unpacked
sqlite-amalgamation-NNNNNNN.zip. placemat calls this once per variant with PLACEMAT_SRC (that
directory), PLACEMAT_OUT, PLACEMAT_WORK (kept per arm) and, for a padded build, PLACEMAT_PAD_SOURCE;
PLACEMAT_HOOK=1 asks for the data hook (placemat's colouring allocator installed through
sqlite3_config(SQLITE_CONFIG_MALLOC), see sqlbench.c).

    sqlite3.o     compiled once per arm (the amalgamation, ~30 s), cached in PLACEMAT_WORK; it is
                  compiled without PLACEMAT_CFLAGS, which only concern the harness and the allocator
    sqlbench.o    the harness (this directory), with PLACEMAT_CFLAGS
    pad.o         PLACEMAT_PAD_SOURCE, LINKED FIRST: ld64 lays out __text in command-line order
                  (no LTO), so the pad moves all of SQLite's code by its size (rounded up to
                  sqlite3.o's section alignment)
    placemat_alloc.o   hooked builds only, linked LAST, so the hook moves no SQLite code

    link: cc -o sqlbench [pad.o] sqlite3.o sqlbench.o [placemat_alloc.o]

Placement is verified after every link (nm): the pad must be the first text symbol, SQLite's
functions must follow it, and the distance from the pad to SQLite's first function
(the pad rounded up to sqlite3.o's alignment) printed on stderr. The stock build is the unpadded, unhooked link.

Page placement (README.md): `build.py --pagecache` compiles hooked builds with -DSQLBENCH_PAGECACHE, so
SQLite's pages sit in one slab whose slot stride follows placemat's step setting (placemat-pc.toml).
An arm directory may also hold `sqlbench.cfg`, extra -D flags for sqlbench.c in every build of that arm
(the stride sweep's arms: `-DSQLBENCH_PAGECACHE -DSQLBENCH_PC_STRIDE=8192`; placemat-sweep.toml).

Environment overrides: SQLBENCH_CC (default cc), SQLBENCH_CFLAGS (default below).
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CC = os.environ.get("SQLBENCH_CC", "cc")
# A typical embedded build: single-threaded, no memory statistics, no double-quoted strings.
CFLAGS = os.environ.get("SQLBENCH_CFLAGS",
                        "-O2 -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DQS=0 "
                        "-DSQLITE_OMIT_LOAD_EXTENSION").split()


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"sqlite build.py: {' '.join(map(str, cmd))} failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return r.stdout


def cc_id() -> str:
    return subprocess.run([CC, "--version"], capture_output=True, text=True).stdout


def text_symbols(b: Path) -> list[tuple[int, str]]:
    out = []
    for l in sh(["nm", "-n", str(b)]).splitlines():
        p = l.split()
        if len(p) == 3 and p[1] in "tT" and not p[2].lstrip("_").startswith("mh_"):   # not the Mach-O header
            out.append((int(p[0], 16), p[2].lstrip("_") if sys.platform == "darwin" else p[2]))
    return out


def main():
    src, out, work = (Path(os.environ[k]) for k in ("PLACEMAT_SRC", "PLACEMAT_OUT", "PLACEMAT_WORK"))
    pad = os.environ.get("PLACEMAT_PAD_SOURCE")
    hooked = os.environ.get("PLACEMAT_HOOK") == "1"
    extra = os.environ.get("PLACEMAT_CFLAGS", "").split()
    if hooked and "--pagecache" in sys.argv[1:]:
        extra.append("-DSQLBENCH_PAGECACHE")
    if (src / "sqlbench.cfg").exists():
        extra += [f for f in (src / "sqlbench.cfg").read_text().split() if f.startswith("-D")]
    ldflags = os.environ.get("PLACEMAT_LDFLAGS", "").split()
    amalg = src / "sqlite3.c"
    if not amalg.exists():
        sys.exit(f"sqlite build.py: no sqlite3.c in {src}")

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
    sh([CC, *CFLAGS, *extra, "-I", str(src), "-c", str(HERE / "sqlbench.c"), "-o", str(out / "sqlbench.o")])
    objs.append(out / "sqlbench.o")
    if hooked:
        inc = os.environ["PLACEMAT_INCLUDE"]
        sh([CC, *CFLAGS, *extra, "-I", inc, "-c", str(Path(inc) / "placemat_alloc.c"), "-o", str(out / "placemat_alloc.o")])
        objs.append(out / "placemat_alloc.o")
    binary = out / "sqlbench"
    sh([CC, "-o", str(binary), *map(str, objs), *ldflags])

    # verify placement: the pad first, SQLite's code right after it
    syms = text_symbols(binary)
    names = [n for _, n in syms]
    addr = dict((n, a) for a, n in syms)
    if pad:
        if not names or names[0] != "placemat_pad":
            sys.exit(f"sqlite build.py: placemat_pad is not the first text symbol (first: {names[:3]})")
        lib_syms = {l.split()[2].lstrip("_") for l in sh(["nm", str(lib)]).splitlines()
                    if len(l.split()) == 3 and l.split()[1] in "tT"}
        if names[1] not in lib_syms:
            sys.exit(f"sqlite build.py: the symbol after placemat_pad ({names[1]}) is not from sqlite3.o")
        d = addr[names[1]] - addr["placemat_pad"]
        print(f"sqlite build.py: pad {os.environ.get('PLACEMAT_PAD')} B moves SQLite's code by {d} B", file=sys.stderr)


if __name__ == "__main__":
    main()
