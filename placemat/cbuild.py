"""Build placemat's C pieces: the malloc interposer (data/interpose.c + data/placemat_alloc.c).

The hook header is used in place by projects (`-I header_dir() -DPLACEMAT_HOOK`); the interposer is
compiled here into a shared library, cached by a hash of its sources, compiler and flags.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SOURCES = ("interpose.c", "placemat_alloc.c")
_HEADERS = ("placemat.h", "placemat_alloc.h")
_CFLAGS = ["-O2", "-fPIC", "-fvisibility=hidden", "-DPLACEMAT_HOOK", "-DPLACEMAT_ALLOC_INTERPOSER",
           "-Wall", "-Wextra"]


def header_dir() -> Path:
    """The directory holding placemat.h and placemat_alloc.h (pass it with -I)."""
    return Path(__file__).resolve().parent / "data"


def find_cc(cc: str | None = None) -> str | None:
    """A C compiler on PATH: `cc`, then $CC, then clang and gcc; None if there is none."""
    for c in ([cc] if cc else []) + ["cc", os.environ.get("CC", ""), "clang", "gcc"]:
        if c and shutil.which(c):
            return c
    return None


def _platform_flags() -> tuple[list[str], list[str], str]:
    """(flags before the sources, flags after them, library suffix)."""
    if sys.platform == "darwin":
        return ["-dynamiclib"], [], ".dylib"
    return ["-shared"], ["-ldl", "-pthread"], ".so"


def build_interposer(out_dir: Path, cc: str = "cc") -> Path:
    """Compile the interposer into out_dir (a .dylib on macOS, a .so elsewhere) and return its path.

    The file name carries a hash of the sources, the compiler's identity and the flags, so a
    rebuild happens only when one of them changes, and two platforms sharing one directory do not
    collide. Raises subprocess.CalledProcessError (with the compiler's output) on failure.
    """
    src = header_dir()
    pre, post, ext = _platform_flags()
    try:
        ccid = subprocess.run([cc, "--version"], capture_output=True, text=True, check=False).stdout
    except OSError:
        ccid = ""
    h = hashlib.sha256()
    for name in _SOURCES + _HEADERS:
        h.update(name.encode() + b"\0" + (src / name).read_bytes() + b"\0")
    h.update("\0".join([cc, ccid, sys.platform, os.uname().machine, *pre, *_CFLAGS, *post]).encode())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lib = out_dir / f"libplacemat-{h.hexdigest()[:16]}{ext}"
    if lib.exists():
        return lib
    fd, tmp = tempfile.mkstemp(prefix=".libplacemat-", suffix=ext, dir=out_dir)
    os.close(fd)
    try:
        cmd = [cc, *pre, *_CFLAGS, "-I", str(src), *(str(src / s) for s in _SOURCES), "-o", tmp, *post]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
        os.replace(tmp, lib)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return lib


def preload_env(lib: Path) -> dict:
    """The environment variable that loads the interposer into a child process.

    macOS: DYLD_INSERT_LIBRARIES (stripped by System Integrity Protection for protected binaries,
    such as /bin/sh, and ignored by hardened-runtime binaries: run the benchmark binary directly).
    Elsewhere: LD_PRELOAD. An existing value in os.environ is kept after the interposer.
    """
    var = "DYLD_INSERT_LIBRARIES" if sys.platform == "darwin" else "LD_PRELOAD"
    old = os.environ.get(var)
    return {var: f"{lib}:{old}" if old else str(lib)}
