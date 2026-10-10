"""placemat.culprits: the address-phase permutation test over pads, pads compared within their batch,
and culprits from a raw file whose binaries are gone (DESIGN §6.2, §11 item 11)."""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from placemat import culprits as C
from placemat.design import Design

D12 = Design(seed=0, data="both", start=4, batch_step=2, cap=12)
SHIFT = {j: D12.pad(j) - D12.pad(0) for j in range(12)}       # seed 0: phase mod 8 alternates 0, 4, 0, 4, ...


def raw_file(d: Path, D: Design, m: int, level, drift=None, seed: int = 1, noise: float = 0.002) -> Path:
    """A raw file of one case and one arm: pad j's stock setting at level(j) us, 7 rounds, batch b's rounds all
    `drift[b]` times slower (anchors too); geometry pointing at binaries that do not exist."""
    rnd = random.Random(seed)
    drift = drift or {}
    t = {}

    def series(x, b):
        return [[x * drift.get(b, 1.0) * (1 + rnd.gauss(0, noise)), 0] for _ in range(7)]

    for j in range(m):
        b = D.batch(j)
        for k in D.kinds:
            t[f"{j}.{k}"] = series(level(j), b)
        if j == D.batch_pads(b)[0] and b:
            t[f"a{b}"] = series(level(0), b)
    raw = {"design": D.to_json(), "arms": [{"name": "base"}], "K": {"s: c": m}, "plan": {},
           "times": {"s: c": {"base": t}},
           "thresholds": {"threshold": 0.03, "threshold_fast": 0.10, "fast_us": 5000},
           "geometry": {"base": {str(j): {"pad": D.pad(j), "path": str(d / f"gone/p{D.pad(j)}/bin"),
                                          "shift": D.pad(j) - D.pad(0)} for j in range(m)}}}
    p = d / "r.raw.json"
    p.write_text(json.dumps(raw))
    return p


class PhaseTest(unittest.TestCase):
    def test_real_phase_survives(self):
        # Lua 5.4.6 fib on Linux: the six pads of one phase mod 8 slow, the other six fast
        slow = [j for j in range(12) if SHIFT[j] % 8 == 4]
        fast = [j for j in range(12) if j not in slow]
        t = C.phase_test(SHIFT, slow, fast, 4096)
        self.assertEqual(t["m"], 8)
        self.assertEqual((t["slow_phases"], t["fast_phases"]), ([4], [0]))
        self.assertTrue(t["exact"])
        self.assertAlmostEqual(t["p"], 2 / 924)                      # 2 of the 924 splits separate this well
        self.assertLessEqual(t["p_adj"], C.ALPHA)
        self.assertIn("an alignment effect", C.phase_lines(t)[0])

    def test_one_slow_pad_of_four_cannot_tell(self):
        # Amber a2 dictplus: one slow pad of four separates at mod 16, as any lone pad would
        sh = {0: 0, 1: 2588, 2: 952, 3: 3540}
        t = C.phase_test(sh, [2], [0, 1, 3], 4096)
        self.assertGreater(t["floor"], C.ALPHA)
        line = C.phase_lines(t)[0]
        self.assertIn("too few pads to tell", line)
        self.assertNotIn("alignment effect", line)

    def test_distinct_phases_mean_nothing(self):
        # every pad in its own phase mod 64: any split separates there (the old claim), so that means nothing
        sh = {j: 4 * j for j in range(12)}
        t = C.phase_test(sh, [1, 2, 3], [0, 4, 5, 6, 7, 8, 9, 10, 11], 64)
        self.assertEqual([m for m, _ in t["separates"]], [64])
        self.assertEqual(t["separates"][0][1], 1.0)                   # every split separates at mod 64
        self.assertIn("no modulus separates", C.phase_lines(t)[0])
        # six pads in six phases: not even a perfect split at mod 8 would be significant
        sh = {j: 4 * j for j in range(6)}
        t = C.phase_test(sh, [1, 3, 5], [0, 2, 4], 64)
        self.assertGreater(t["floor"], C.ALPHA)
        self.assertIn("too few pads to tell", C.phase_lines(t)[0])

    def test_imperfect_split_not_an_effect(self):
        slow = [0, 1, 2]                                               # phases 0, 4, 0: no phase holds them
        t = C.phase_test(SHIFT, slow, [3, 4, 5, 6, 7, 8, 9, 10, 11], 4096)
        self.assertGreater(t["p_adj"], C.ALPHA)
        self.assertIn("no modulus separates", C.phase_lines(t)[0])

    def test_null_calibration(self):
        # random slow/fast labels: the corrected p is at most alpha in at most about alpha of them
        rnd = random.Random(3)
        hits = 0
        for _ in range(60):
            js = list(range(12))
            rnd.shuffle(js)
            t = C.phase_test(SHIFT, js[:4], js[4:], 4096)
            hits += t["p_adj"] <= 0.05
        self.assertLessEqual(hits, 6)

    def test_sampled_when_many_splits(self):
        D = Design(seed=0, data="none", cap=24)
        sh = {j: D.pad(j) - D.pad(0) for j in range(24)}
        slow = [j for j in range(24) if sh[j] % 8 == 4]
        t = C.phase_test(sh, slow, [j for j in range(24) if j not in slow], 4096)
        self.assertFalse(t["exact"])
        self.assertLessEqual(t["p_adj"], 0.01)


class CulpritsFromRaw(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_batch_drift_removed(self):
        # batch 2 (pads 6, 7) ran 10% slow, anchor included: its pads must not look slow
        lv = lambda j: 10000 * (1.05 if SHIFT[j] % 8 == 4 else 1.0)
        p = raw_file(self.dir, D12, 12, lv, drift={2: 1.10})
        meds, notes = C.pad_levels(json.loads(p.read_text())["times"]["s: c"]["base"], D12, 12)
        self.assertEqual(notes, [])
        for j in range(12):
            self.assertAlmostEqual(meds[j] / lv(j), meds[0] / lv(0), delta=0.01)
        out = C.culprits(p, "c")
        self.assertIn("compared within their batch", out[0])
        self.assertIn("an alignment effect", out[1])
        self.assertIn("slow pads " + ", ".join(str(D12.pad(j)) for j in range(12) if SHIFT[j] % 8 == 4), out[2])
        self.assertIn("loop candidates skipped", out[2])          # the binaries are gone: phase from the raw file alone

    def test_no_anchor_said(self):
        lv = lambda j: 10000.0
        p = raw_file(self.dir, D12, 6, lv)
        raw = json.loads(p.read_text())
        del raw["times"]["s: c"]["base"]["a1"]
        meds, notes = C.pad_levels(raw["times"]["s: c"]["base"], D12, 6)
        self.assertEqual(len(meds), 6)
        self.assertIn("no anchor", notes[0])

    def test_one_slow_pad_of_four(self):
        D = Design(seed=1, data="both", start=4, batch_step=2, cap=12)
        lv = lambda j: 16400 * (1.05 if j == 2 else 1.0)
        p = raw_file(self.dir, D, 4, lv)
        out = C.culprits(p, "c")
        self.assertIn("one batch", out[0])
        self.assertIn("too few pads to tell", out[1])
        self.assertIn(f"slow pads {D.pad(2)} of 4", out[2])


if __name__ == "__main__":
    unittest.main()
