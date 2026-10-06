"""Tests for placemat.pin.compare (P009): strategy specs, profile weights, page footprints, and the
whole comparison built for real through the build protocol on the first native toolchain."""
from __future__ import annotations

import contextlib
import io
import json
import shlex
import unittest
from pathlib import Path
from unittest import mock

from placemat import binary as B
from placemat import pin
from placemat.pin import compare as C
from placemat.pin import profile as PR

from .test_binary import PINME, ToolchainCase
from .test_pin import _toy_profile


class PureTests(unittest.TestCase):
    def test_parse_strategy(self):
        self.assertEqual(C.parse_strategy("c3"), ("c3", "c3", {}))
        self.assertEqual(C.parse_strategy("random:7"), ("random-7", "random", {"seed": 7}))
        self.assertEqual(C.parse_strategy("c3:limit=8192"), ("c3-8192", "c3", {"cluster_limit": 8192}))
        with self.assertRaises(ValueError):
            C.parse_strategy("nonesuch")

    def test_hot_weights(self):
        p = _toy_profile()
        w = C.hot_weights(p, ["a", "b", "c", "d"])
        self.assertAlmostEqual(sum(sum(v.values()) for v in w.values()), 1.0)
        self.assertEqual(set(w["d"]), {0})                  # no sampled offsets: all at the entry
        self.assertAlmostEqual(w["a"][2000] / w["a"][16], 2.0)

    def test_footprint(self):
        funcs = [B.Func("a", 0x0, 64), B.Func("b", 0x1000 - 8, 64), B.Func("c", 0x5000, 32)]
        with mock.patch.object(B, "function_map", lambda _b: {f.name: f for f in funcs}):
            w = {"a": {0: 0.5}, "b": {0: 0.1, 16: 0.3}, "c": {0: 0.1}}
            f4 = C.footprint("bin", w, 4096)
            self.assertEqual((f4["touched"], f4["p90"], f4["p99"]), (3, 2, 3))   # pages 0 (0.6), 1 (0.3), 5 (0.1)
            f16 = C.footprint("bin", w, 16384)
            self.assertEqual((f16["touched"], f16["p90"]), (2, 1))              # 0..16K holds 0.9

    def test_markdown(self):
        m = {"text": 100, "region": 50, "crossings": {"loops": 2, "spans": 1},
             "pages4k": {"touched": 3, "p90": 2, "p99": 3}, "pages16k": {"touched": 1, "p90": 1, "p99": 1}}
        res = {"source": "src", "selection": 4, "spans": 2, "boundary": 4096, "align": 16, "stock_measures": m,
               "strategies": {"c3": {"after": {**m, "text": 120, "crossings": {"loops": 0, "spans": 0}},
                                     "before": m, "pads": 2, "pad_bytes": 32, "verify_ok": True,
                                     "pinned_order": "x/c3/pinned.order", "pin_source": "x/c3/pads.c"}}}
        md = C.markdown(res)
        self.assertIn("| c3 | 120 | +20 | 2 | 32 | 50 | 3 / 2 / 3 | 1 / 1 / 1 | 2 / 1 | 0 / 0 | OK |", md)
        cmd = shlex.split(C.run_command(res, "p.toml"))
        self.assertIn("--pad-first", cmd)
        self.assertIn("c3=x/c3/pinned.order", cmd)


class CompareBuildTests(ToolchainCase):
    """compare() end to end: a toy project whose build script honours PLACEMAT_ORDER_FILE and
    PLACEMAT_PIN_SOURCE (as a project adapter does), every strategy ordered, padded and verified."""

    BOUNDARY, MAX_LOOP = 128, 64

    def test_compare(self):
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
            "set -e\nF=\nX=\nO=\n"
            '[ -n "$PLACEMAT_ORDER_FILE$PLACEMAT_PIN_SOURCE" ] && F=-falign-functions=16\n'
            '[ -n "$PLACEMAT_PIN_SOURCE" ] && X="$PLACEMAT_PIN_SOURCE"\n'
            f'[ -n "$PLACEMAT_ORDER_FILE" ] && O={of}\n'
            f'{cc} $F "$PLACEMAT_SRC/pinme.c" $X {link} $O -o "$PLACEMAT_OUT/pinme"\n')
        (proj / "placemat.toml").write_text('[build]\ncommand = ["sh", "{config_dir}/build.sh"]\nbinary = "pinme"\n'
                                            'inputs = ["*.c"]\n')
        p = _toy_profile()
        p["self"] = {"m": {"dot": 0.5, "sum": 0.2, "fill": 0.2, "x@lib": 0.1},
                     "o": {"poly": 0.4, "scan": 0.3, "maxv": 0.3}}
        p["edges"] = {"m": {"round_>dot": 0.5, "round_>sum": 0.2}, "o": {"round_>poly": 0.4}}
        p["offs"] = {"dot": {8: 10, 12: 10}, "poly": {4: 5}}
        sel = ["dot", "poly", "scan", "maxv", "sum", "fill", "round_"]
        out = self.dir / "cmp"
        with contextlib.redirect_stderr(io.StringIO()):
            res = C.compare(proj / "placemat.toml", str(proj), p, sel, ["c3", "hotness", "random:1"], out,
                            work=self.dir / "work", boundary=self.BOUNDARY, max_loop=self.MAX_LOOP,
                            spans={"dot": 40, "poly": 30}, linker=tc.style)
        self.assertEqual(list(res["strategies"]), ["c3", "hotness", "random-1"])
        for lab, r in res["strategies"].items():
            self.assertTrue(r["verify_ok"], (lab, r["verify"]))
            self.assertEqual(r["after"]["crossings"]["loops"], 0, lab)
            self.assertEqual(r["after"]["crossings"]["spans"], 0, lab)
            names = B.read_order(r["order"], r["binary"])
            fm = B.function_map(r["binary"])
            self.assertEqual(sorted(names, key=lambda n: fm[n].start), names, lab)
        self.assertTrue((out / "compare.md").exists())
        self.assertEqual(json.loads((out / "compare.json").read_text())["selection"], len(sel))
        # the CLI front end (cached builds)
        PR.save(p, self.dir / "prof.json")
        (self.dir / "sel.json").write_text(json.dumps({"sel": sel}))
        (self.dir / "spans.json").write_text(json.dumps({"dot": 40, "poly": 30}))
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            rc = pin.cli(["compare", str(self.dir / "prof.json"), str(self.dir / "sel.json"),
                          "--config", str(proj / "placemat.toml"), "--source", str(proj),
                          "--work", str(self.dir / "work"), "--strategy", "density", "--boundary", "128",
                          "--max-loop", "64", "--spans", str(self.dir / "spans.json"), "--linker", tc.style,
                          "-o", str(self.dir / "cmp2")])
        self.assertEqual(rc, 0, o.getvalue())
        self.assertIn("| density |", o.getvalue())


if __name__ == "__main__":
    unittest.main()
