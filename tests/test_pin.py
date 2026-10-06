"""Tests for placemat.pin: profile import (synthetic sample and perf texts, and live sample/perf
runs where the profiler works), hot-set choice, spans, ordering strategies, the pad search (against
brute force), and the whole pipeline linked for real with an order file and generated pads on
every available toolchain (ld64; lld; GNU ld), checked by verify.

Optional regression against the Amber prototype: set PLACEMAT_AMBER_DIR to a directory holding the
prototype's builds A/amber, B16/amber, C2/amber and C2/amber.order (planning/amber-validation)."""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from placemat import binary as B
from placemat import pin
from placemat.pin import profile as PR
from placemat.pin import select as S
from placemat.pin import order as O
from placemat.pin.pads import pads, pad_source, is_pad
from placemat.pin.verify import verify

from .test_binary import HOT, PINME, ToolchainCase, toolchains, write_order

REPO = Path(__file__).resolve().parent.parent
AMBER_INPUTS = REPO / "planning" / "amber-validation" / "inputs"


def _sample_text(binary, load, frames_by_case):
    """A minimal macOS sample report: frames_by_case is a list of (count, [(name, func, offset), ...])
    paths from the root; name is what sample prints (it may differ from the function's)."""
    bi = B.info(binary)
    fm = B.function_map(binary)
    lines = ["Analysis of sampling pinme (pid 1) every 1 millisecond", f"Load Address:    {load:#x}", "",
             "Call graph:", "    100 Thread_1   DispatchQueue_1: com.apple.main-thread  (serial)"]
    for count, path in frames_by_case:
        for depth, (name, func, off) in enumerate(path):
            if func is None:            # an address past the text section (a stub)
                a = load + (bi.text[0] + bi.text[1] + 8 - bi.base)
                lib = "pinme"
            elif func.endswith("@lib"):
                a, lib = 0x18BFA1360, "libsystem_platform.dylib"
            else:
                a = load + (fm[func].start - bi.base) + off
                lib = "pinme"
            pre = "    " + "  " * (depth + 1)
            lines.append(f"{pre}{count} {name}  (in {lib}) + {off}  [{a:#x}]")
    lines += ["", "Total number in stack (recursive counted multiple, when >=5):", ""]
    return "\n".join(lines) + "\n"


class ProfileTests(ToolchainCase):
    def test_sample_synthetic(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                b = tc.build(self.dir / f"{tc.name}-p", [PINME])
                load = 0x102924000
                t1 = _sample_text(b, load, [(60, [("main", "main", 8), ("round_", "round_", 16), ("dot", "dot", 12)]),
                                            (30, [("main", "main", 8), ("round_", "round_", 20), ("SUM_STRIPPED", "sum", 4)]),
                                            (10, [("main", "main", 8), ("memmove", "x@lib", 0)])])
                t2 = _sample_text(b, load, [(50, [("main", "main", 8), ("poly", "poly", 4)]),
                                            (50, [("main", "main", 8), ("stub", None, 0)])])
                (self.dir / "c1.txt").write_text(t1)
                (self.dir / "c2.txt").write_text(t2)
                (self.dir / "c3.txt").write_text(t1)
                p = PR.from_sample({"a": [str(self.dir / "c1.txt"), str(self.dir / "c2.txt")],
                                    "b": [str(self.dir / "c3.txt")]}, b, image="pinme")
                self.assertEqual(p["n"], {"a": 2, "b": 1})
                sa = p["self"]["a"]
                # each case equal weight: dot is 60% of case 1, poly 50% of case 2
                self.assertAlmostEqual(sa["dot"], 0.30)
                self.assertAlmostEqual(sa["sum"], 0.15)          # attributed by address, not by name
                self.assertAlmostEqual(sa["poly"], 0.25)
                self.assertAlmostEqual(sa[PR.STUBS], 0.25)
                self.assertAlmostEqual(sa["memmove@libsystem_platform.dylib"], 0.05)
                self.assertNotIn("SUM_STRIPPED", sa)
                self.assertAlmostEqual(p["edges"]["a"]["round_>dot"], 0.30)
                self.assertAlmostEqual(p["self"]["b"]["dot"], 0.60)
                self.assertEqual(p["offs"]["dot"], {12: 120})
                self.assertEqual(p["offs"]["sum"], {4: 60})
                self.assertNotIn(PR.STUBS, p["offs"])

    def test_perf_synthetic(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                b = tc.build(self.dir / f"{tc.name}-p", [PINME])
                fm = B.function_map(b)
                delta = 0xAAAA00000000 if B.info(b).pie else 0
                path = str(self.dir / "pinme")

                def fr(f, off, sym=True):
                    ip = fm[f].start + off + delta
                    return f"\t    {ip:x} {f}+{off:#x} ({path})" if sym else f"\t    {ip:x} [unknown] ({path})"
                text = "\n".join([
                    "pinme  4242 [001]  100.000001:    1000 cycles:u: ",
                    fr("dot", 8), fr("round_", 0x40), fr("main", 0x10),
                    "\t    ffff8000123 __libc_start_main+0x80 (/usr/lib/libc.so.6)", "",
                    "pinme  4242 [001]  100.000002:    3000 cycles:u: ",
                    fr("sum", 4, sym=False), fr("round_", 0x20), fr("main", 0x10), "",
                    "pinme  4242 [001]  100.000003:    1000 cycles:u: ",
                    "\t    ffff8000456 memcpy+0x10 (/usr/lib/libc.so.6)", fr("round_", 0x24), fr("main", 0x10), "",
                ]) + "\n"
                (self.dir / "perf1.txt").write_text(text)
                p = PR.from_perf({"s": [str(self.dir / "perf1.txt")]}, b, image="pinme")
                s = p["self"]["s"]
                self.assertAlmostEqual(s["dot"], 0.2)
                self.assertAlmostEqual(s["sum"], 0.6)            # raw ip mapped with the learnt load offset
                self.assertAlmostEqual(s["memcpy@libc.so.6"], 0.2)
                self.assertAlmostEqual(p["edges"]["s"]["round_>sum"], 0.6)
                self.assertAlmostEqual(p["edges"]["s"]["main>round_"], 1.0)
                self.assertEqual(p["offs"]["sum"], {4: 3000})

    @unittest.skipUnless(platform.system() == "Darwin" and shutil.which("sample"), "macOS sample only")
    def test_sample_live(self):
        tc = next((t for t in toolchains() if t.runs), None)
        if tc is None:
            self.skipTest("no native toolchain")
        b = tc.build(self.dir / "pinme", [PINME])
        proc = subprocess.Popen([str(b), "spin"])
        try:
            time.sleep(0.2)
            out = self.dir / "s.txt"
            r = subprocess.run(["sample", str(proc.pid), "1", "-file", str(out)], capture_output=True, text=True)
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
        if r.returncode or not out.exists():
            self.skipTest(f"sample failed: {r.stderr[:200]}")
        p = PR.from_sample({"live": [str(out)]}, b)
        ib = PR.in_binary(p, "live")
        self.assertGreater(sum(ib.values()), 0.5)
        top = sorted(ib, key=lambda k: -ib[k])[:4]
        self.assertTrue({"dot", "fill"} & set(top), top)
        self.assertGreater(p["edges"]["live"].get("round_>dot", 0), 0)
        sp = S.hot_spans(p, b, selection=["dot", "fill"])
        self.assertTrue(sp)

    @unittest.skipUnless(platform.system() == "Linux" and shutil.which("perf"), "Linux perf only")
    def test_perf_live(self):
        tc = next((t for t in toolchains() if t.runs and t.cc[0] == "gcc"), None) or \
            next((t for t in toolchains() if t.runs), None)
        if tc is None:
            self.skipTest("no native toolchain")
        b = tc.build(self.dir / "pinme", [PINME])
        data = self.dir / "perf.data"
        r = subprocess.run(["perf", "record", "-q", "-g", "-F", "999", "-o", str(data), "--", str(b), "100000"],
                           capture_output=True, text=True)
        if r.returncode or not data.exists():
            self.skipTest(f"perf record failed (perf_event_paranoid?): {r.stderr[-300:]}")
        txt = subprocess.run(["perf", "script", "-i", str(data)], capture_output=True, text=True).stdout
        (self.dir / "perf.txt").write_text(txt)
        p = PR.from_perf({"live": [str(self.dir / "perf.txt")]}, b)
        ib = PR.in_binary(p, "live")
        self.assertGreater(sum(ib.values()), 0.5, sorted(p["self"]["live"].items(), key=lambda kv: -kv[1])[:6])
        top = sorted(ib, key=lambda k: -ib[k])[:4]
        self.assertTrue({"dot", "fill"} & set(top), top)
        self.assertGreater(p["edges"]["live"].get("round_>dot", 0), 0)


def _toy_profile():
    return {"self": {"m": {"a": 0.5, "b": 0.3, "c": 0.1, "x@lib": 0.1},
                     "o": {"c": 0.6, "d": 0.3, "a": 0.05, "y@lib": 0.05}},
            "edges": {"m": {"a>b": 0.3, "b>c": 0.1}, "o": {"c>d": 0.3, "a>c": 0.01}},
            "n": {"m": 1, "o": 1},
            "offs": {"a": {0: 10, 16: 50, 40: 40, 2000: 100}, "b": {8: 1, 4: 99}, "c": {12: 5}}}


class SelectOrderTests(unittest.TestCase):
    def test_pick(self):
        p = _toy_profile()
        sel = S.pick(p, 0.95)
        self.assertEqual(sel.sel[:2], ["c", "a"])
        self.assertEqual(set(sel.sel), {"a", "b", "c", "d"})
        sel = S.pick(p, 0.5)
        self.assertEqual(sel.sel, ["c", "a"])
        self.assertAlmostEqual(sel.cov["m"][0], 0.6 / 0.9)

    def test_hot_spans(self):
        p = _toy_profile()
        # a: offsets under 1024 holding >= 10% of all of a's samples (200): 16 (50) and 40 (40)
        self.assertEqual(S.hot_spans(p, None, selection=["a", "b", "c"]), {"a": 44, "b": 8, "c": 16})
        self.assertEqual(S.hot_spans(p, None, min_share=0.3, selection=["a"]), {})
        self.assertEqual(S.hot_spans(p, None, max_bytes=4096, selection=["a"]), {"a": 2004})

    def test_strategies(self):
        p = _toy_profile()
        funcs = [B.Func("a", 0x1000, 64), B.Func("b", 0x1040, 64), B.Func("c", 0x1080, 32),
                 B.Func("d", 0x10a0, 4096)]
        with mock.patch.object(B, "functions", lambda _b: funcs):
            sel = ["a", "b", "c", "d", "missing"]
            for name, f in O.strategies.items():
                got = f("bin", p, sel)
                self.assertEqual(sorted(got), ["a", "b", "c", "d"], name)
            self.assertEqual(O.strategies["hotness"]("bin", p, sel)[0], "c")
            self.assertEqual(O.strategies["density"]("bin", p, sel)[0], "c")
            # C3: b behind its caller a, d behind c (c is denser than d) unless the cluster limit forbids it
            # C3: c (hottest) behind its heaviest caller b, b behind a, then d behind c's cluster,
            # unless the cluster limit forbids it
            self.assertEqual(O.c3_clusters("bin", p, sel), [["a", "b", "c", "d"]])
            self.assertEqual(O.c3_clusters("bin", p, sel, cluster_limit=1024), [["a", "b", "c"], ["d"]])
            self.assertEqual(O.order("bin", p, sel, cluster_limit=1024), ["a", "b", "c", "d"])
            ph = O.pettis_hansen("bin", p, sel)
            self.assertEqual(abs(ph.index("a") - ph.index("b")), 1)
            self.assertNotEqual(O.random_order("bin", p, sel, seed=1), O.random_order("bin", p, sel, seed=2))

    def test_order_text(self):
        self.assertEqual(O.order_text(["a", "b"], "ld64"), "_a\n_b\n")
        self.assertEqual(O.order_text(["a", "b"], "lld"), "a\nb\n")
        t = O.order_text(["a"], "gnu")
        self.assertTrue(t.startswith(".text : {") and "*(.text.a " in t, t)


class PadSearchTests(unittest.TestCase):
    """The DP against brute force, on a made-up binary."""

    def _fake(self, funcs, loops):
        fs = [B.Func(n, s, z) for n, s, z in funcs]
        bi = B.BinInfo("bin", "macho", "arm64", (0x1000, 0x1000), (0x1000, 0x2000), 0, None, tuple(fs))
        return contextlib.ExitStack(), [
            mock.patch.object(B, "function_map", lambda _b: {f.name: f for f in fs}),
            mock.patch.object(B, "functions", lambda _b: fs),
            mock.patch.object(B, "info", lambda _b: bi),
            mock.patch.object(B, "loops", lambda _b, _m=256: loops)]

    def fake(self, funcs, loops):
        stack, patches = self._fake(funcs, loops)
        for p in patches:
            stack.enter_context(p)
        return stack

    def test_matches_brute_force(self):
        P, U = 128, 16
        funcs = [("f", 0x1000, 48), ("g", 0x1030, 80), ("h", 0x1080, 32), ("k", 0x10a0, 64)]
        loops = [B.Loop("f", 0x1000 + 20, 0x1000 + 44), B.Loop("g", 0x1030 + 8, 0x1030 + 40),
                 B.Loop("g", 0x1030 + 52, 0x1030 + 76), B.Loop("h", 0x1080 + 4, 0x1080 + 28),
                 B.Loop("k", 0x10a0 + 30, 0x10a0 + 60)]
        spans = {"g": 60, "k": 64}
        hot64 = {"f": [20, 44, 0.9]}
        W = (10**6, 10**4, 10**3, 1)
        with self.fake(funcs, loops):
            plan = pads("bin", ["f", "g", "h", "k"], boundary=P, align=U, spans=spans, hot64=hot64, line=32,
                        weights=W)
        sizes = {"f": 48, "g": 80, "h": 32, "k": 64}
        lo = {n: [] for n in sizes}
        for l in loops:
            s0 = dict((n, s) for n, s, _ in funcs)[l.func]
            lo[l.func].append((l.start - s0, l.end - s0))

        def cost(pp):
            x, c = 0x1000 % P, 0
            for n, p in zip(["f", "g", "h", "k"], pp):
                x += p
                c += W[3] * p
                c += W[0] * sum(1 for s, e in lo[n] if B.crosses(x + s, x + e, P))
                if n in spans:
                    c += W[1] * B.crosses(x, x + spans[n], P)
                if n in hot64:
                    c += W[2] * B.crosses(x + hot64[n][0], x + hot64[n][1], 32)
                x += sizes[n]
            return c
        best = min(itertools.product(range(0, P, U), repeat=4), key=cost)
        self.assertEqual(plan.cost, cost(best))
        self.assertEqual(cost([e.pad for e in plan.entries]), plan.cost)
        self.assertEqual(plan.loop_crossings, [])
        # offsets are consistent with the pads
        x = 0x1000 % P
        for e, n in zip(plan.entries, ["f", "g", "h", "k"]):
            x = (x + e.pad) % P
            self.assertEqual(e.offset, x)
            x += sizes[n]

    def test_rejects_unordered_binary(self):
        funcs = [("f", 0x1000, 48), ("cold", 0x1030, 16), ("g", 0x1040, 80)]
        with self.fake(funcs, []):
            with self.assertRaises(ValueError):
                pads("bin", ["f", "g"], boundary=128)
            with self.assertRaises(ValueError):
                pads("bin", ["g", "f"], boundary=128)
            plan = pads("bin", ["f", "g"], boundary=128, strict=False)
            self.assertEqual(len(plan.entries), 2)

    def test_pad_source(self):
        funcs = [("f", 0x1000, 48), ("g", 0x1030, 80)]
        with self.fake(funcs, [B.Loop("g", 0x1030 + 4, 0x1030 + 28)]):     # crosses 64 unless padded
            plan = pads("bin", ["f", "g"], boundary=64, align=16)
        self.assertTrue(plan.pads)
        src = pad_source(plan)
        for name, size in plan.pads:
            self.assertIn(f'void {name}(void) {{ __asm__ volatile(".space {size - 16}"); }}', src)
        self.assertTrue(all(is_pad(n) for n, _ in plan.pads))
        self.assertEqual([n for n in plan.order() if not is_pad(n)], ["f", "g"])


class PipelineTests(ToolchainCase):
    """Order file, build without pads, pad search, build with pads, verify: for real."""

    BOUNDARY, MAX_LOOP = 128, 64     # small, so the fixture's few loops need pads

    def test_pin_and_verify(self):
        for tc in self.each():
            with self.subTest(toolchain=tc.name):
                d = self.dir / tc.name
                d.mkdir()
                order0 = write_order(d / "hot.order", HOT, tc.style)
                b0 = tc.build(d / "b0", [PINME], order_file=order0)
                fm0 = B.function_map(b0)
                self.assertEqual(sorted(HOT, key=lambda n: fm0[n].start), HOT, "order file not applied")
                spans = {"dot": 40, "round_": 120, "poly": 30}
                out_order, out_c = d / "pinned.order", d / "pads.c"
                plan = pads(b0, order0, boundary=self.BOUNDARY, max_loop=self.MAX_LOOP, spans=spans,
                            out_order=out_order, out_c=out_c, linker=tc.style)
                self.assertEqual(plan.loop_crossings, [], plan.summary())
                self.assertEqual(plan.span_crossings, [], plan.summary())
                b1 = tc.build(d / "b1", [PINME, out_c], order_file=out_order)
                r = verify(b1, out_order, boundary=self.BOUNDARY, max_loop=self.MAX_LOOP, spans=spans, plan=plan)
                self.assertTrue(r.ok, str(r))
                fm1 = B.function_map(b1)
                for name, size in plan.pads:            # footprints are exactly as planned
                    nxt = min(f.start for f in B.functions(b1) if f.start > fm1[name].start)
                    self.assertEqual(nxt - fm1[name].start, size, name)
                # the unpadded build fails against the pinned order (pads missing), and the
                # crossings verify reports are the ones the loop scanner sees
                if plan.pads:
                    r0 = verify(b0, out_order, boundary=self.BOUNDARY, max_loop=self.MAX_LOOP)
                    self.assertFalse(r0.ok)
                    self.assertTrue(r0.missing)
                r0 = verify(b0, order0, boundary=self.BOUNDARY, max_loop=self.MAX_LOOP)
                want = [l for l in B.crossing_loops(b0, self.BOUNDARY, self.MAX_LOOP) if l.func in HOT]
                self.assertEqual(len(r0.loop_crossings), len(want))
                # an unordered build fails the order check
                plain = tc.build(d / "plain", [PINME])
                self.assertFalse(verify(plain, order0, boundary=self.BOUNDARY).ok)

    def test_cli_pipeline(self):
        tcs = [tc for tc in self.each() if tc.runs]
        if not tcs:
            self.skipTest("no native toolchain")
        tc = tcs[0]
        p = _toy_profile()
        p["self"]["m"] = {"dot": 0.5, "sum": 0.2, "fill": 0.2, "x@lib": 0.1}
        p["self"]["o"] = {"poly": 0.4, "scan": 0.3, "maxv": 0.3}
        p["edges"] = {"m": {"round_>dot": 0.5, "round_>sum": 0.2}, "o": {"round_>poly": 0.4}}
        p["offs"] = {"dot": {8: 10, 12: 10}, "poly": {4: 5}}
        PR.save(p, self.dir / "prof.json")
        b = tc.build(self.dir / "prof-bin", [PINME])
        quiet = contextlib.redirect_stderr(io.StringIO())
        with contextlib.redirect_stdout(io.StringIO()), quiet:
            self.assertEqual(pin.cli(["pick", str(self.dir / "prof.json"), "-o", str(self.dir / "sel.json")]), 0)
            self.assertEqual(pin.cli(["order", str(b), str(self.dir / "prof.json"), str(self.dir / "sel.json"),
                                      "--linker", tc.style, "-o", str(self.dir / "hot.order")]), 0)
            self.assertEqual(pin.cli(["spans", str(self.dir / "prof.json"), str(b), str(self.dir / "sel.json"),
                                      "-o", str(self.dir / "spans.json"), "--hot64", str(self.dir / "h64.json")]), 0)
        names = B.read_order(self.dir / "hot.order", b)
        self.assertEqual(set(names), {"dot", "sum", "fill", "poly", "scan", "maxv"})
        b0 = tc.build(self.dir / "b0", [PINME], order_file=self.dir / "hot.order")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pin.cli(["pads", str(b0), str(self.dir / "hot.order"), "--boundary", "128",
                                      "--max-loop", "64", "--spans", str(self.dir / "spans.json"),
                                      "--out-order", str(self.dir / "pinned.order"),
                                      "--out-c", str(self.dir / "pads.c"), "--plan", str(self.dir / "plan.json"),
                                      "--linker", tc.style]), 0)
        b1 = tc.build(self.dir / "b1", [PINME, self.dir / "pads.c"], order_file=self.dir / "pinned.order")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = pin.cli(["verify", str(b1), str(self.dir / "pinned.order"), "--boundary", "128",
                          "--max-loop", "64", "--spans", str(self.dir / "spans.json")])
        self.assertEqual(rc, 0, out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pin.cli(["verify", str(b0), str(self.dir / "pinned.order"), "--boundary", "128"]),
                             1 if json.loads((self.dir / "plan.json").read_text())["pads"] else 0)


@unittest.skipUnless(os.environ.get("PLACEMAT_AMBER_DIR"), "set PLACEMAT_AMBER_DIR for the Amber regression")
class AmberRegression(unittest.TestCase):
    """pads() on the prototype's B16 build with hot.order and hotspans.json reproduces padsC2;
    verify() passes C2 and fails A."""

    def setUp(self):
        self.d = Path(os.environ["PLACEMAT_AMBER_DIR"])
        for p in ("A/amber", "B16/amber", "C2/amber", "C2/amber.order"):
            if not (self.d / p).exists():
                self.skipTest(f"missing {self.d / p}")
        if B.info(self.d / "B16/amber").fmt != "macho" or not shutil.which("otool"):
            self.skipTest("needs otool")

    def test_pads_reproduce_c2(self):
        ref_order = B.read_order(AMBER_INPUTS / "pads/padsC2/amber.order", "macho")
        sizes = [int(x) for x in re.findall(r"\.space (\d+)", (AMBER_INPUTS / "pads/padsC2/src/zz_ofpad.c").read_text())]
        want, pend = [], 0
        for n in ref_order:
            if is_pad(n):
                pend = sizes[int(n.rsplit("_", 1)[1]) - 1] + 16
            else:
                want.append((n, pend))
                pend = 0
        plan = pads(self.d / "B16/amber", AMBER_INPUTS / "hot.order", spans=AMBER_INPUTS / "hotspans.json")
        self.assertEqual([(e.name, e.pad) for e in plan.entries], want)
        self.assertEqual((len(plan.pads), plan.pad_bytes), (18, 3504))
        r = verify(self.d / "C2/amber", self.d / "C2/amber.order", spans=AMBER_INPUTS / "hotspans.json", plan=plan)
        self.assertTrue(r.ok, str(r))
        r = verify(self.d / "A/amber", self.d / "C2/amber.order", spans=AMBER_INPUTS / "hotspans.json")
        self.assertFalse(r.ok)
        self.assertTrue(r.out_of_order and r.loop_crossings and r.span_crossings)


if __name__ == "__main__":
    unittest.main()
