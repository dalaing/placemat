"""Pin pads planned afresh for every build (`placemat run --pin`, DESIGN §6.3 step 7): each padded build
of a pinned arm, with the code-axis pad first (`--pad-first`), is linked once without pin pads, planned at
its own position and linked again; the result must verify against its own plan, and the plans differ as
the region moves."""
from __future__ import annotations

import shlex
from pathlib import Path

from placemat import binary as B
from placemat import config
from placemat.build import PAD_SYMBOL, Builder

from .test_binary import PINME, ToolchainCase


class ReplanTests(ToolchainCase):
    BOUNDARY = 128          # small, so loops cross often and the plans have work to do

    def test_replan_per_build(self):
        tcs = [tc for tc in self.each() if tc.runs]
        if not tcs:
            self.skipTest("no native toolchain")
        tc = tcs[0]
        proj = self.dir / "proj"
        proj.mkdir()
        (proj / "pinme.c").write_text(PINME.read_text())
        cc = " ".join(shlex.quote(x) for x in tc.cc + ["-O2", "-fno-omit-frame-pointer"] + tc.flags)
        link = " ".join(shlex.quote(x) for x in tc.link)
        of = " ".join(tc.order_flags('"$PLACEMAT_ORDER_FILE"'))
        (proj / "build.sh").write_text(
            "set -e\nF=\nX=\nO=\nP=\n"
            '[ -n "$PLACEMAT_ORDER_FILE" ] && F=-falign-functions=$PLACEMAT_ALIGN\n'
            '[ -n "$PLACEMAT_PIN_SOURCE" ] && X="$PLACEMAT_PIN_SOURCE"\n'
            '[ -n "$PLACEMAT_PAD_SOURCE" ] && P="$PLACEMAT_PAD_SOURCE"\n'
            f'[ -n "$PLACEMAT_ORDER_FILE" ] && O={of}\n'
            f'{cc} $F $P "$PLACEMAT_SRC/pinme.c" $X {link} $O -o "$PLACEMAT_OUT/pinme"\n')
        (proj / "placemat.toml").write_text(
            '[build]\ncommand = ["sh", "{config_dir}/build.sh"]\nbinary = "pinme"\ninputs = ["*.c"]\npin_align = 16\n'
            f'[machine]\nboundary = {self.BOUNDARY}\n')
        fmt = "macho" if tc.style == "ld64" else "elf"
        order = self.dir / "base.order"
        hot = ["dot", "poly", "scan", "maxv", "sum"]
        order.write_text("\n".join(B.link_name(fmt, n) for n in hot) + "\n")
        cfg = config.load(proj / "placemat.toml")
        Bd = Builder(cfg, self.dir / "work")
        arm = Bd.arm("pinned", str(proj), pin_order=str(order), pad_first=True)
        bins = {p: Bd.binary(arm, p) for p in (4, 36, 68, 100)}
        stock = Bd.binary(arm, None)
        self.assertEqual(Bd.warnings, [], Bd.warnings)
        starts = {}
        for p, b in bins.items():
            fm = B.function_map(b)
            got = sorted([n for n in hot], key=lambda n: fm[n].start)
            self.assertEqual(got, hot, p)                              # the order holds
            self.assertLess(fm[PAD_SYMBOL].start, fm[hot[0]].start)    # the pad comes first
            for lp in B.loops(b, max_bytes=64):                        # no small loop of a hot function crosses
                if lp.func in hot:
                    self.assertFalse(B.crosses(lp.start, lp.end, self.BOUNDARY), (p, lp))
            starts[p] = fm[hot[0]].start
        self.assertGreater(len(set(starts.values())), 1)               # the region really moved
        self.assertTrue(stock.exists())
        # the unpadded build is shared between pad-first and plain pinned arms (one cache entry)
        arm2 = Bd.arm("pinned", str(proj), pin_order=str(order), pad_first=False)
        self.assertEqual(Bd.binary(arm2, None), stock)


if __name__ == "__main__":
    import unittest
    unittest.main()
