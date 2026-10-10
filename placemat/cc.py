"""placemat-cc: the compiler wrapper of the build protocol (DESIGN §8.2, route 1).

placemat sets CC (and CXX) to a shim that runs `python3 -m placemat.cc`, with the real compiler in
PLACEMAT_REAL_CC (PLACEMAT_REAL_CXX for C++). Every compile gets PLACEMAT_CFLAGS (and, for a pinned arm,
-falign-functions=$PLACEMAT_ALIGN, plus -ffunction-sections on ELF). A link (no -c, -S, -E or -M*) also
gets, in this order: the code-axis pad source as the first input, so the pad is linked before the code
under study; the pinned arm's pin pads; the order file (for GNU ld before binutils 2.43, which lacks
--section-ordering-file, the link uses lld when installed); and PLACEMAT_LDFLAGS. Everything else passes
through unchanged, so make, CMake and Meson builds work as long as they honour CC.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys


def _is_link(args: list[str]) -> bool:
    return not any(a in ("-c", "-S", "-E") or a.startswith("-M") for a in args) and "--version" not in args \
        and "-v" not in args and not any(a.startswith("-print") for a in args)


def _gnu_order(order: str) -> str:
    """The order file (one symbol per line) as GNU ld's section-ordering file, next to it."""
    from .pin.order import order_text
    names = [l.strip() for l in open(order) if l.strip() and not l.startswith("#")]
    out = order + ".gnu"
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(order):
        with open(out, "w") as f:
            f.write(order_text(names, "gnu"))
    return out


def _gnu_ordering(real: str) -> bool:
    """Whether the compiler's GNU ld takes --section-ordering-file (binutils 2.43 and later)."""
    try:
        ld = subprocess.run(shlex.split(real) + ["-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
        return "--section-ordering-file" in subprocess.run([ld or "ld", "--help"], capture_output=True, text=True).stdout
    except OSError:
        return False


def command(argv: list[str], cxx: bool = False) -> list[str]:
    real = os.environ.get("PLACEMAT_REAL_CXX" if cxx else "PLACEMAT_REAL_CC") or ("c++" if cxx else "cc")
    args = list(argv)
    extra = shlex.split(os.environ.get("PLACEMAT_CFLAGS", ""))
    order = os.environ.get("PLACEMAT_ORDER_FILE")
    if order:
        extra.append(f"-falign-functions={os.environ.get('PLACEMAT_ALIGN', '16')}")
        if sys.platform != "darwin":
            extra.append("-ffunction-sections")
    if not _is_link(args):
        return shlex.split(real) + extra + args
    first = [os.environ["PLACEMAT_PAD_SOURCE"]] if os.environ.get("PLACEMAT_PAD_SOURCE") else []
    pins = [os.environ["PLACEMAT_PIN_SOURCE"]] if os.environ.get("PLACEMAT_PIN_SOURCE") else []
    tail = []
    if order:
        if sys.platform == "darwin":
            tail.append(f"-Wl,-order_file,{order}")
        elif any("lld" in a for a in args + extra) or os.environ.get("PLACEMAT_LINKER") == "lld":
            tail.append(f"-Wl,--symbol-ordering-file,{order}")
        elif _gnu_ordering(real):                   # GNU ld: a section-ordering file of .text.<name> sections
            tail.append(f"-Wl,--section-ordering-file,{_gnu_order(order)}")
        elif shutil.which("ld.lld"):                # an older GNU ld (before binutils 2.43): link with lld
            tail += ["-fuse-ld=lld", f"-Wl,--symbol-ordering-file,{order}"]
        else:
            sys.exit("placemat-cc: this GNU ld cannot order functions (--section-ordering-file needs binutils "
                     "2.43 or later) and no lld was found: install lld, or set PLACEMAT_LINKER=lld with an lld link")
    tail += shlex.split(os.environ.get("PLACEMAT_LDFLAGS", ""))
    if first and cxx:                          # a C file in a C++ link: compile it as C
        first = ["-x", "c"] + first + ["-x", "none"]
    return shlex.split(real) + extra + first + args + pins + tail


def main() -> int:
    cxx = os.environ.get("PLACEMAT_CC_MODE") == "cxx"
    cmd = command(sys.argv[1:], cxx)
    os.execvp(cmd[0], cmd)
    return 127


if __name__ == "__main__":
    sys.exit(main())
