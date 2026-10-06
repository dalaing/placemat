#!/usr/bin/env python3
"""zstd's side of placemat's build protocol (placemat/build.py): builds `zbench` (zbench.c over a
static libzstd) for one variant.

placemat runs this once per variant with PLACEMAT_SRC (the arm's zstd tree), PLACEMAT_OUT,
PLACEMAT_WORK (kept per arm), PLACEMAT_PAD_SOURCE for a padded build, and PLACEMAT_HOOK=1 with
PLACEMAT_CFLAGS (-DPLACEMAT_HOOK -I<placemat/data>) for a hooked one.

- libzstd's objects are compiled once per arm into PLACEMAT_WORK/lib (single-threaded, no legacy
  formats, no asm, zstd's default -O3), so a variant costs a few small compiles and a link.
- Link order: the pad object first (it moves everything after it), then libzstd, then zbench.o,
  then (hooked builds only) placemat_alloc.o. The hooked build's extra code therefore sits after
  libzstd, so libzstd's addresses in a hooked pad-P build are those of an unhooked pad-P build.
- Placement is verified from the linked binary (nm): placemat_pad must be the lowest text symbol
  and the next text symbol must start at least PLACEMAT_PAD bytes after it; libzstd's first function
  must follow it. A failed check fails the build (never trust a linker's silence, DESIGN §6.3).

Contains no zstd source; it only compiles the arm's own files.
"""
import concurrent.futures as cf
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CC = os.environ.get("ZB_CC", "cc")
OPT = ["-O3"]
LIB_FLAGS = OPT + ["-DXXH_NAMESPACE=ZSTD_", "-DZSTD_LEGACY_SUPPORT=0", "-DZSTD_DISABLE_ASM"]
LIB_DIRS = ("common", "compress", "decompress")


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"zstd build: {' '.join(map(str, cmd))} failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return r.stdout


def lib_objects(src: Path, work: Path) -> list[Path]:
    """libzstd's objects for this arm, compiled once (in parallel) and kept in PLACEMAT_WORK/lib."""
    d = work / "lib"
    done = d / ".done"
    srcs = sorted(f for x in LIB_DIRS for f in (src / "lib" / x).glob("*.c"))
    srcs = [f for f in srcs if f.name != "zstdmt_compress.c"]       # single-threaded build
    objs = [d / f"{f.parent.name}_{f.stem}.o" for f in srcs]
    if done.exists() and all(o.exists() for o in objs):
        return objs
    d.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        list(ex.map(lambda so: sh([CC, *LIB_FLAGS, "-I", str(src / "lib"), "-I", str(src / "lib" / "common"),
                                   "-c", str(so[0]), "-o", str(so[1])]), zip(srcs, objs)))
    done.write_text("ok\n")
    return objs


def text_symbols(b: Path) -> list[tuple[int, str]]:
    out = []
    for l in sh(["nm", "-n", str(b)]).splitlines():
        p = l.split()
        if len(p) == 3 and p[1] in "tT":
            out.append((int(p[0], 16), p[2][1:] if sys.platform == "darwin" and p[2].startswith("_") else p[2]))
    return out


def verify(b: Path, pad: int):
    syms = text_symbols(b)
    names = [n for _, n in syms]
    if "placemat_pad" not in names:
        sys.exit(f"zstd build: no placemat_pad in {b} (dead-stripped? not linked?)")
    # The pad must precede all of libzstd. Other code may come before it: the C runtime's (ELF's
    # _start and friends), Mach-O's __mh_execute_header (a T symbol at the segment start), and on
    # ELF with GCC and GNU ld the .text.startup (main) and .text.unlikely (*.cold) input sections,
    # which the default linker script places before .text; those do not move with the pad.
    i = names.index("placemat_pad")
    a = syms[i][0]
    ours = [(x, n) for x, n in syms if n.startswith(("ZSTD_", "FSE_", "HUF_", "HIST_")) and ".cold" not in n]
    early = [n for x, n in ours if x <= a]
    if early or not ours:
        sys.exit(f"zstd build: placemat_pad at {a:#x} does not precede libzstd in {b}: {early[:5] or 'none found'}")
    if any(n == "main" and x <= a for x, n in syms):
        print("zstd build: note: main precedes the pad (ELF .text.startup); it does not move with the pad",
              file=sys.stderr)
    nxt = syms[i + 1][0] if i + 1 < len(syms) else a
    if nxt - a < pad:
        sys.exit(f"zstd build: the symbol after placemat_pad starts {nxt - a} bytes after it, less than the pad {pad}")


def main():
    src, out, work = (Path(os.environ[k]) for k in ("PLACEMAT_SRC", "PLACEMAT_OUT", "PLACEMAT_WORK"))
    pad_src = os.environ.get("PLACEMAT_PAD_SOURCE")
    hooked = os.environ.get("PLACEMAT_HOOK") == "1"
    cflags = os.environ.get("PLACEMAT_CFLAGS", "").split()
    ldflags = os.environ.get("PLACEMAT_LDFLAGS", "").split()
    libs = lib_objects(src, work)
    objs = []
    if pad_src:
        objs.append(out / "placemat_pad.o")
        sh([CC, *OPT, "-c", pad_src, "-o", str(objs[-1])])
    objs += libs
    objs.append(out / "zbench.o")
    sh([CC, *OPT, *cflags, "-I", str(src / "lib"), "-c", str(HERE / "zbench.c"), "-o", str(objs[-1])])
    if hooked:
        inc = Path(os.environ["PLACEMAT_INCLUDE"])
        objs.append(out / "placemat_alloc.o")
        sh([CC, *OPT, *cflags, "-c", str(inc / "placemat_alloc.c"), "-o", str(objs[-1])])
    extra = ["-lm"] + (["-pthread"] if platform.system() == "Linux" else [])
    b = out / "zbench"
    sh([CC, *OPT, "-o", str(b), *map(str, objs), *ldflags, *extra])
    if pad_src:
        verify(b, int(os.environ.get("PLACEMAT_PAD", "0")))
    for o in objs:
        if o.parent == out:
            o.unlink()


if __name__ == "__main__":
    main()
