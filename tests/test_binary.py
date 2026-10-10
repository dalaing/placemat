"""Tests for placemat.binary: compile tests/fixtures/pinme.c with every toolchain available here
(macOS: ld64, arm64 and x86_64; Linux: clang + lld, gcc + GNU ld, clang + lld cross to x86-64)
into a temporary directory and check functions, loops, crossings, shifts and same_code.
Toolchains that are missing are skipped. The helpers here are shared with test_pin."""
from __future__ import annotations

import contextlib
import functools
import io
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from placemat import binary as B

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PINME = FIXTURES / "pinme.c"
HOT = ["poly", "scan", "maxv", "round_", "sum", "dot", "count_eq", "fill"]   # not source order
LOOPY = ["sum", "dot", "count_eq", "maxv", "scan", "poly", "fill"]


class Toolchain:
    """A way to compile and link the fixtures, with an order file in this toolchain's style."""

    def __init__(self, name, cc, style, flags=(), link=(), runs=True):
        self.name, self.cc, self.style = name, list(cc), style
        self.flags, self.link, self.runs = list(flags), list(link), runs

    def order_flags(self, order_file):
        if self.style == "ld64":
            return [f"-Wl,-order_file,{order_file}"]
        if self.style == "lld":
            return [f"-Wl,--symbol-ordering-file,{order_file}"]
        return [f"-Wl,--section-ordering-file,{order_file}"]

    def build(self, out, sources, order_file=None, align=16, defines=()):
        cmd = self.cc + ["-O2", f"-falign-functions={align}", "-fno-omit-frame-pointer"] + self.flags
        cmd += [f"-D{d}" for d in defines] + [str(s) for s in sources] + self.link
        if order_file:
            cmd += self.order_flags(order_file)
        cmd += ["-o", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr}")
        return Path(out)

    def __repr__(self):
        return self.name


def _works(tc: Toolchain) -> bool:
    if not shutil.which(tc.cc[0]):
        return False
    with tempfile.TemporaryDirectory() as d:
        try:
            b = tc.build(Path(d) / "t", [PINME])
            B.loops(b)            # the disassembler must handle it too
            return True
        except Exception:
            return False


@functools.lru_cache(maxsize=1)
def toolchains() -> list[Toolchain]:
    cands = []
    if platform.system() == "Darwin":
        host = B._host_arch()
        cands.append(Toolchain(f"ld64-{host}", ["cc"], "ld64"))
        other = "x86_64" if host == "arm64" else "arm64"
        cands.append(Toolchain(f"ld64-{other}", ["cc", "-arch", other], "ld64", runs=False))
    else:
        # -z separate-code: .text starts on a fresh page, so read-only sections placed before it
        # (.eh_frame grows with the pad functions' unwind info) cannot move the ordered region
        ff = ["-ffunction-sections", "-Wl,-z,separate-code"]
        cands.append(Toolchain("clang-lld", ["clang"], "lld", ff, ["-fuse-ld=lld"]))
        # gcc aligns loops to 32 on arm64 (raising each function section's alignment): keep
        # every alignment inside a function within the function alignment the pads assume
        gff = ff + ["-falign-loops=16"]
        cands.append(Toolchain("gcc-lld", ["gcc"], "lld", gff, ["-fuse-ld=lld"]))
        gnu = Toolchain("gcc-gnu-ld", ["gcc"], "gnu", gff, ["-fuse-ld=bfd"])
        r = subprocess.run(["ld.bfd", "--help"], capture_output=True, text=True) if shutil.which("ld.bfd") else None
        if r is not None and "--section-ordering-file" in r.stdout:
            cands.append(gnu)
        if B._host_arch() != "x86_64":
            cands.append(Toolchain("clang-lld-x86_64", ["clang", "--target=x86_64-linux-gnu"], "lld", ff,
                                   ["-fuse-ld=lld", "-nostdlib", "-static", "-Wl,-e,main"], runs=False))
    return [tc for tc in cands if _works(tc)]


class ToolchainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def each(self) -> list[Toolchain]:
        """The toolchains to run the test with (each inside its own subTest)."""
        tcs = toolchains()
        if not tcs:
            self.skipTest("no working C toolchain and disassembler")
        return tcs


def write_order(path, names, style):
    from placemat.pin.order import order_text
    Path(path).write_text(order_text(names, style))
    return Path(path)


class PureTests(unittest.TestCase):
    def test_crosses(self):
        self.assertFalse(B.crosses(0, 4096))
        self.assertTrue(B.crosses(4095, 4097))
        self.assertFalse(B.crosses(4096, 4160))
        self.assertTrue(B.crosses(4090, 4100))
        self.assertTrue(B.crosses(60, 70, 64))
        self.assertFalse(B.crosses(64, 128, 64))

    def test_crossing_shifts(self):
        # a 64-byte loop at address 0 crosses at shifts 4..4092 that put a boundary inside it
        bad = B.crossing_shifts([(0, 64)], 4096, 4)
        self.assertEqual(len(bad), 15)          # d = 4036 .. 4092
        self.assertEqual(min(bad), 4096 - 60)
        self.assertEqual(B.crossing_shifts([(0, 4)], 4096, 4), set())

    def test_link_name(self):
        self.assertEqual(B.link_name("macho", "f"), "_f")
        self.assertEqual(B.link_name("ld64", "o8.1005"), "_o8.1005")
        self.assertEqual(B.link_name("elf", "f"), "f")
        self.assertEqual(B.link_name("lld", "f"), "f")
        self.assertEqual(B.link_name("gnu", "f"), ".text.f")

    def test_parse_insn(self):
        i = B._parse_insn(0x100, "b.ne\t0x100000738", "arm64")
        self.assertEqual((i.mnem, i.target), ("b.ne", 0x100000738))
        i = B._parse_insn(0x100, "b.ne\t400588 <f+0x8>  // b.any", "arm64")
        self.assertEqual(i.target, 0x400588)
        i = B._parse_insn(0x100, "tbz\tw8, #0x3, 0x1000", "arm64")
        self.assertEqual(i.target, 0x1000)
        i = B._parse_insn(0x100, "bl\t_foo", "arm64")
        self.assertIsNone(i.target)
        i = B._parse_insn(0x100, "add\tx0, x0, #0x10", "arm64")
        self.assertIsNone(i.target)
        i = B._parse_insn(0x100, "bnd jne    401010 <f+0x10>", "x86_64")
        self.assertEqual((i.mnem, i.target), ("jne", 0x401010))
        i = B._parse_insn(0x100, "jmpq\t*0x10(%rax)", "x86_64")
        self.assertIsNone(i.target)
        i = B._parse_insn(0x100, "jne,pt\t0x100000f58", "x86_64")
        self.assertEqual((i.mnem, i.target), ("jne", 0x100000f58))
        i = B._parse_insn(0x100, "lea    0x2fcf(%rip),%rdi        # 404000 <x>", "x86_64")
        self.assertEqual(i.ops, "0x2fcf(%rip),%rdi")

    def test_read_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "o"
            p.write_text("_a\n_b.12\n")
            self.assertEqual(B.read_order(p), ["a", "b.12"])
            p.write_text("a\nb\n")
            self.assertEqual(B.read_order(p), ["a", "b"])
            write_order(p, ["a", "b"], "gnu")
            self.assertEqual(B.read_order(p), ["a", "b"])

    def test_not_a_binary(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x"
            p.write_bytes(b"hello world, not a binary")
            with self.assertRaises(ValueError):
                B.info(p)


class BinaryTests(ToolchainCase):
    def test_functions(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                b = tc.build(self.dir / f"{tc.name}-plain", [PINME])
                bi = B.info(b)
                self.assertIn(bi.fmt, ("macho", "elf"))
                self.assertIn(bi.arch, ("arm64", "x86_64"))
                t0, tsz = B.text_section(b)
                fs = B.functions(b)
                names = {f.name for f in fs}
                for n in LOOPY + ["round_", "cold", "main"]:
                    self.assertIn(n, names)
                self.assertFalse(any(n.startswith("_") and n[1:] in names for n in names))
                self.assertEqual([f.start for f in fs], sorted(f.start for f in fs))
                for f in fs:
                    self.assertTrue(t0 <= f.start and f.end <= t0 + tsz, f)
                    self.assertGreater(f.size, 0, f)
                fm = B.function_map(b)
                for a, c in zip(fs, fs[1:]):
                    if a.start != c.start:
                        self.assertLessEqual(a.end, c.start)
                self.assertEqual(fm["sum"].start % 16, 0)

    def test_loops(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                b = tc.build(self.dir / f"{tc.name}-plain", [PINME])
                fm = B.function_map(b)
                ls = B.loops(b)
                got = {l.func for l in ls}
                for n in LOOPY:
                    self.assertIn(n, got)
                for l in ls:
                    f = fm[l.func]
                    self.assertTrue(f.start <= l.start < l.end <= f.end, l)
                    self.assertLessEqual(l.size, 256)
                self.assertTrue(all(l.size <= 32 for l in B.loops(b, 32)))
                # crossing_loops agrees with loops + crosses, at any boundary
                for bd in (32, 64, 4096):
                    want = [l for l in ls if B.crosses(l.start, l.end, bd)]
                    self.assertEqual(B.crossing_loops(b, bd), want)
                self.assertTrue(B.crossing_loops(b, 32))

    def test_shifts_and_same_code(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                a = tc.build(self.dir / f"{tc.name}-a", [PINME])
                pad = self.dir / "zz_first.c"
                pad.write_text('__attribute__((used)) void zz_first(void) { __asm__ volatile(".space 200"); }\n')
                moved = tc.build(self.dir / f"{tc.name}-moved", [pad, PINME])
                changed = tc.build(self.dir / f"{tc.name}-changed", [pad, PINME], defines=["PLACEMAT_CHANGE"])
                s, unmoved, n = B.shifts(a, moved)
                self.assertNotEqual(s, 0)
                self.assertLess(unmoved, 0.5)
                self.assertGreaterEqual(n, 10)
                self.assertEqual(B.shifts(a, a)[:2], (0, 1.0))
                fixture = LOOPY + ["round_", "cold", "main"]
                r = B.same_code(a, moved, fixture)
                self.assertEqual([k for k, v in r.items() if not v.same], [], r)
                r = B.same_code(a, changed)
                diff = sorted(k for k, v in r.items() if not v.same and k in fixture)
                self.assertEqual(diff, ["poly"], {k: r[k] for k in diff})
                self.assertTrue(all(r[k].same for k in fixture if k != "poly"))
                r = B.same_code(a, changed, ["poly", "nonexistent"], mask_data=True)
                self.assertFalse(r["poly"].same)
                self.assertIsNotNone(r["poly"].first_diff)
                self.assertIsNone(r["nonexistent"].start_a)

    def test_cli(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                a = tc.build(self.dir / f"{tc.name}-a", [PINME])
                for argv, needle in ((["funcs", str(a)], "round_"), (["loops", str(a), "--max-bytes", "64"], "sum "),
                                     (["shifts", str(a), str(a)], "100.0%"),
                                     (["samefn", str(a), str(a), "sum", "dot"], "SAME")):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(B.cli(argv), 0)
                    self.assertIn(needle, out.getvalue(), argv)


if __name__ == "__main__":
    unittest.main()
