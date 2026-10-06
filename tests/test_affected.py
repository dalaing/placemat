"""placemat.affected: a change to constant data with identical code selects every case (DESIGN §3.3); a code
change selects only the cases that reach it; constant data that differs only because code moved warns."""
from __future__ import annotations

import unittest

from placemat import affected as A

from .test_binary import ToolchainCase

SRC = r"""
static const int TABLE[16] = {FIRST, 17, 3, 91, 44, 8, 260, 5, 77, 1, 13, 2048, 6, 39, 400, 11};
__attribute__((noinline)) int lookup(int i) { return TABLE[i & 15]; }
__attribute__((noinline)) int other(int x) { return x * MUL + 1; }
EXTRA
int main(int c, char **v) { (void)v; return lookup(c) + other(c); }
"""

PROFILE = {"self": {"look": {"lookup": 1.0}, "calc": {"other": 1.0}}, "edges": {}}


class Affected(ToolchainCase):
    def build(self, tc, name, first=1, mul=7, extra=""):
        src = self.dir / f"{name}.c"
        src.write_text(SRC.replace("FIRST", str(first)).replace("MUL", str(mul)).replace("EXTRA", extra))
        return tc.build(self.dir / f"{tc.name}-{name}", [src])

    def test_rodata_change_with_identical_code(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                a, b = self.build(tc, "a"), self.build(tc, "b", first=2)
                r = A.affected(a, b, PROFILE, spot=0)
                self.assertEqual(r["changed"], [])                       # the code is the same ...
                self.assertTrue(r["data_changed"], r)                    # ... the table is not
                self.assertEqual(r["selected"], ["calc", "look"])

    def test_code_change_selects_its_cases(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                a, b = self.build(tc, "a"), self.build(tc, "c", mul=9)
                r = A.affected(a, b, PROFILE, spot=0)
                self.assertIn("other", r["changed"])                     # (gcc changes main too)
                self.assertNotIn("lookup", r["changed"])
                self.assertEqual(r["data_changed"], [])
                self.assertEqual(r["selected"], ["calc"])

    def test_identical_builds_select_nothing(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                a, b = self.build(tc, "a"), self.build(tc, "a2")
                r = A.affected(a, b, PROFILE, spot=0)
                self.assertEqual((r["changed"], r["data_changed"], r["selected"]), ([], [], []))
                self.assertFalse(A.code_moved(a, b))


if __name__ == "__main__":
    unittest.main()
