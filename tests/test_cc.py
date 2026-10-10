"""The compiler-wrapper route of the build protocol (placemat/cc.py, `[build] mode = "cc"`): a plain make
project, unaware of placemat, gets its pad linked first and, when pinned, its order file and alignment."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from placemat import binary as B
from placemat import cc, config
from placemat.build import PAD_SYMBOL, Builder

MAIN = "long work(long);\nlong more(long);\nint main(int c, char **v) { return (int)(work(c) + more(c)) & 1; }\n"
WORK = ("__attribute__((noinline)) long work(long n) { long s = 0; for (long i = 0; i < n * 1000; i++) s += i ^ (i >> 3); return s; }\n"
        "__attribute__((noinline)) long more(long n) { long s = 1; for (long i = 0; i < n * 100; i++) s *= 3; return s; }\n")
MAKEFILE = "toy: main.o work.o\n\t$(CC) -o toy main.o work.o\n%.o: %.c\n\t$(CC) -O2 -c $< -o $@\n"


class CommandTests(unittest.TestCase):
    def test_compile_and_link(self):
        env = {"PLACEMAT_REAL_CC": "clang", "PLACEMAT_CFLAGS": "-DX", "PLACEMAT_PAD_SOURCE": "/p/pad.c"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cc.command(["-c", "a.c", "-o", "a.o"]), ["clang", "-DX", "-c", "a.c", "-o", "a.o"])
            self.assertEqual(cc.command(["-o", "t", "a.o"]), ["clang", "-DX", "/p/pad.c", "-o", "t", "a.o"])
            self.assertEqual(cc.command(["-MM", "a.c"]), ["clang", "-DX", "-MM", "a.c"])
        env.update(PLACEMAT_ORDER_FILE="/p/o", PLACEMAT_ALIGN="16", PLACEMAT_PIN_SOURCE="/p/pins.c",
                   PLACEMAT_LINKER="lld")             # (with GNU ld the wrapper converts the order file)
        with mock.patch.dict(os.environ, env, clear=True):
            link = cc.command(["-o", "t", "a.o"])
            self.assertIn("-falign-functions=16", link)
            self.assertIn("/p/pins.c", link)
            self.assertTrue(any("/p/o" in x for x in link))
            self.assertLess(link.index("/p/pad.c"), link.index("a.o"))

    def test_old_gnu_ld_falls_back_to_lld(self):
        env = {"PLACEMAT_REAL_CC": "gcc", "PLACEMAT_ORDER_FILE": "/p/o"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(cc.sys, "platform", "linux"):
            with mock.patch.object(cc, "_gnu_ordering", lambda real: False), \
                    mock.patch.object(cc.shutil, "which", lambda n: "/usr/bin/ld.lld" if n == "ld.lld" else None):
                link = cc.command(["-o", "t", "a.o"])
                self.assertIn("-fuse-ld=lld", link)
                self.assertIn("-Wl,--symbol-ordering-file,/p/o", link)
            with mock.patch.object(cc, "_gnu_ordering", lambda real: False), \
                    mock.patch.object(cc.shutil, "which", lambda n: None):
                with self.assertRaises(SystemExit):
                    cc.command(["-o", "t", "a.o"])


@unittest.skipUnless(shutil.which("make") and shutil.which("cc"), "needs make and cc")
class MakeProjectTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="placemat-cc-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        src = self.dir / "src"
        src.mkdir()
        (src / "main.c").write_text(MAIN)
        (src / "work.c").write_text(WORK)
        (src / "Makefile").write_text(MAKEFILE)
        (self.dir / "placemat.toml").write_text(
            '[build]\nmode = "cc"\ncommand = ["make", "-s"]\nbinary = "toy"\ninputs = ["*.c", "Makefile"]\n')
        self.cfg = config.load(self.dir / "placemat.toml")

    def test_pad_moves_the_code(self):
        Bd = Builder(self.cfg, self.dir / "work")
        arm = Bd.arm("a", str(self.dir / "src"))
        b0, b1 = Bd.binary(arm, 100), Bd.binary(arm, 600)
        f0, f1 = B.function_map(b0), B.function_map(b1)
        self.assertLess(f0[PAD_SYMBOL].start, f0["work"].start)
        self.assertGreaterEqual(f1["work"].start - f0["work"].start, 500 - 16)
        self.assertEqual(Bd.warnings, [])
        self.assertFalse((self.dir / "src" / "toy").exists())       # the arm's own tree is never built in

    def test_pinned_order(self):
        fmt = "macho" if B.fmt(shutil.which("cc")) == "macho" else "elf"
        order = self.dir / "o.order"
        order.write_text("\n".join(B.link_name(fmt, n) for n in ("more", "work", "main")) + "\n")
        Bd = Builder(self.cfg, self.dir / "work")
        arm = Bd.arm("p", str(self.dir / "src"), order=str(order))
        b = Bd.binary(arm, None)
        fm = B.function_map(b)
        self.assertLess(fm["more"].start, fm["work"].start)
        self.assertLess(fm["work"].start, fm["main"].start)
        self.assertEqual(fm["more"].start % 16, 0)


if __name__ == "__main__":
    unittest.main()
