#!/usr/bin/env python3
"""Lua's side of placemat's build protocol (placemat/build.py; DESIGN §8.2). placemat examples, MIT.

placemat runs this once per variant, with PLACEMAT_SRC (a Lua release tree: src/*.c), PLACEMAT_OUT,
PLACEMAT_WORK (kept per arm), PLACEMAT_CFLAGS / PLACEMAT_LDFLAGS, PLACEMAT_PAD_SOURCE for a padded
build and PLACEMAT_HOOK=1 for a hooked one. It leaves PLACEMAT_OUT/luabench.

Lua's makefile builds liblua.a and links lua.o against it. Here the same sources, with the
makefile's flags (-std=gnu99 -O2 -Wall -Wextra -DLUA_COMPAT_5_3 and the platform's LUA_USE_*), are
compiled directly and linked as objects in this order:

    [pad] lapi ... lzio (CORE_O)  lauxlib ... linit (LIB_O)  luabench  [placemat_alloc]

An arm's tree may hold a file `placemat-cflags` with extra compiler flags for every compile (an
experiment such as -falign-functions=16; it is not part of Lua's release).

The pad (when there is one) is the first code in __text / .text and moves all of Lua; the
benchmark host and, in hooked builds, placemat's colouring allocator come after Lua, so that adding
them does not move Lua's code. Lua's objects are compiled once per arm and set of flags (cached in
PLACEMAT_WORK); a variant compiles the pad and the host and relinks.

After linking it checks the placement: in a padded build, placemat_pad must be the lowest text
symbol and the next function must start at least PLACEMAT_PAD bytes after it; in the stock build
there must be no pad. It prints the result on stderr (placemat shows it only if the build fails).
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = "lapi lcode lctype ldebug ldo ldump lfunc lgc llex lmem lobject lopcodes lparser lstate lstring ltable ltm lundump lvm lzio".split()
LIB = "lauxlib lbaselib lcorolib ldblib liolib lmathlib loadlib loslib lstrlib ltablib lutf8lib linit".split()
CC = os.environ.get("LUA_CC", "cc")
DARWIN = sys.platform == "darwin"
CFLAGS = ["-std=gnu99", "-O2", "-Wall", "-Wextra", "-DLUA_COMPAT_5_3",
          "-DLUA_USE_MACOSX" if DARWIN else "-DLUA_USE_LINUX"]
LIBS = ["-lm"] + ([] if DARWIN else ["-ldl"])


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"luabench build: {' '.join(map(str, cmd))} failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return r.stdout


def text_symbols(b: Path) -> list[tuple[int, str]]:
    out = []
    for l in sh(["nm", "-n", str(b)]).splitlines():
        p = l.split()
        if len(p) == 3 and p[1] in "tT":
            out.append((int(p[0], 16), p[2][1:] if DARWIN and p[2].startswith("_") else p[2]))
    return out


def main():
    src = Path(os.environ["PLACEMAT_SRC"]) / "src"
    out, work = Path(os.environ["PLACEMAT_OUT"]), Path(os.environ["PLACEMAT_WORK"])
    extra = os.environ.get("PLACEMAT_CFLAGS", "").split()
    ldextra = os.environ.get("PLACEMAT_LDFLAGS", "").split()
    pad = os.environ.get("PLACEMAT_PAD_SOURCE")
    hooked = os.environ.get("PLACEMAT_HOOK") == "1"
    inc = os.environ.get("PLACEMAT_INCLUDE", "")
    armflags = Path(os.environ["PLACEMAT_SRC"]) / "placemat-cflags"   # optional, per arm (e.g. -falign-functions=16)
    flags = CFLAGS + (armflags.read_text().split() if armflags.exists() else []) + extra
    ccid = sh([CC, "--version"])
    tag = hashlib.sha1((" ".join([CC] + flags) + "\0" + ccid).encode()).hexdigest()[:10]
    objdir = work / f"obj-{tag}"
    objdir.mkdir(parents=True, exist_ok=True)
    objs = []
    for m in CORE + LIB:                         # Lua's objects: compiled once per arm and flags
        o = objdir / f"{m}.o"
        if not o.exists():
            tmp = objdir / f".{m}.o.tmp"
            sh([CC, *flags, "-c", str(src / f"{m}.c"), "-o", str(tmp)])
            os.replace(tmp, o)
        objs.append(str(o))
    first = []
    if pad:
        sh([CC, *flags, "-c", pad, "-o", str(out / "pad.o")])
        first = [str(out / "pad.o")]
    sh([CC, *flags, f"-I{src}", "-c", str(HERE / "luabench.c"), "-o", str(out / "luabench.o")])
    last = [str(out / "luabench.o")]
    if hooked:
        sh([CC, *flags, "-c", str(Path(inc) / "placemat_alloc.c"), "-o", str(out / "placemat_alloc.o")])
        last.append(str(out / "placemat_alloc.o"))
    b = out / "luabench"
    sh([CC, "-o", str(b), *first, *objs, *last, *LIBS, *ldextra])
    syms = text_symbols(b)
    names = [n for _, n in syms]
    if pad:
        n = int(os.environ["PLACEMAT_PAD"])
        if "placemat_pad" not in names:
            sys.exit("luabench build: no placemat_pad in the binary")
        i = names.index("placemat_pad")
        pa = syms[i][0]
        lua = [(a, s) for a, s in syms if s.startswith("lua")]
        before = [s for a, s in lua if a < pa]
        if before:                               # crt code may precede it on ELF; Lua's code may not
            sys.exit(f"luabench build: Lua functions before placemat_pad: {before[:5]}")
        gap = syms[i + 1][0] - pa
        if gap < n:
            sys.exit(f"luabench build: {syms[i + 1][1]} starts {gap} B after placemat_pad, < pad {n}")
        print(f"luabench build: pad {n} B at {pa:#x}; {syms[i + 1][1]} at +{gap}; "
              f"luaV_execute at {syms[names.index('luaV_execute')][0]:#x}", file=sys.stderr)
    elif "placemat_pad" in names:
        sys.exit("luabench build: placemat_pad in a build without a pad")
    if hooked and "placemat_malloc" not in names:
        sys.exit("luabench build: hooked build without placemat_malloc")
    for f in out.glob("*.o"):
        f.unlink()


if __name__ == "__main__":
    main()
