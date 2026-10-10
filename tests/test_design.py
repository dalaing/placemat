"""placemat.design: pads, colours and steps from one Kronecker sequence; variants and batches (DESIGN §5.2, §5.3, §5.7)."""
from __future__ import annotations

import json
import random
import unittest

from placemat.design import COLOUR, DATA_MODES, KIND_NAMES, M64, STEP, STOCK, Design, Setting

AMBER_PADS = [3508, 1872, 364, 2824, 1316, 3776, 2268, 696, 3220, 1712, 76, 2664]
AMBER_STEPS = [12416, 2816, 9600, 64, 6784, 13568, 3968, 10752, 1152, 7936, 14720, 5120]


class TestAmberSeed(unittest.TestCase):
    def test_seed0_pads(self):
        for data in DATA_MODES:
            for mode in ("hashed", "linear"):
                D = Design(seed=0, data=data, step_mode=mode)
                self.assertEqual([D.pad(j) for j in range(12)], AMBER_PADS)

    def test_seed0_linear_steps(self):
        D = Design(seed=0, data="step", step_mode="linear")
        self.assertEqual([D.step(j) for j in range(12)], AMBER_STEPS)
        self.assertEqual([D.setting(j, STEP).step for j in range(12)], AMBER_STEPS)

    def test_seed0_draws(self):
        # DESIGN §5.2: u ~ 0.844 and v = 13 for random.Random(0)
        D = Design(seed=0)
        self.assertAlmostEqual(D.u, 0.844422, places=6)
        self.assertEqual(D.v, 13)
        r = random.Random(0)
        u, w2 = r.random(), r.random()
        v = r.randrange(16)
        w1 = r.random()
        self.assertEqual((D.u, D.w2, D.v, D.w1), (u, w2, v, w1))

    def test_seed0_describe(self):
        # the coverage DESIGN §5.2 quotes for 12 pads at seed 0
        text = "\n".join(Design(seed=0, data="step", step_mode="linear").describe(12))
        self.assertIn("12 pads: " + ", ".join(map(str, AMBER_PADS)), text)
        self.assertIn("largest gap mod 4096 620 B (even: 341)", text)
        self.assertIn("12 of 16 phases mod 64, 4 of 4 mod 16", text)
        self.assertIn("linear steps (span 16384, unit 64): " + ", ".join(map(str, AMBER_STEPS)), text)


class TestSequence(unittest.TestCase):
    def test_pad_formula(self):
        D = Design(seed=5)
        for j in range(40):
            p = D.pad(j)
            self.assertEqual(p % 4, 0)
            self.assertTrue(0 <= p < 4096 + 64)
            self.assertEqual(p % 64, 4 * ((7 * j + D.v) % 16))

    def test_pads_spread_over_period(self):
        for seed in range(10):
            D = Design(seed=seed)
            pads = sorted(D.pad(j) % 4096 for j in range(12))
            gaps = [b - a for a, b in zip(pads, pads[1:])] + [pads[0] + 4096 - pads[-1]]
            self.assertLess(max(gaps), 3 * 4096 / 12, seed)      # golden ratio: no big holes
            self.assertEqual(len({p % 16 for p in pads}), 4, seed)

    def test_colours_and_linear_steps_whole_units_never_zero(self):
        for seed in range(30):
            for unit, span in ((64, 16384), (64, 128), (128, 4096), (16, 64)):
                D = Design(seed=seed, data="both", step_mode="linear", unit=unit, colour_span=span, step_span=span)
                for j in range(64):
                    c, s = D.colour(j), D.step(j)
                    self.assertTrue(c > 0 and c % unit == 0 and c < span, (seed, unit, span, j, c))
                    self.assertTrue(s > 0 and s % unit == 0 and s < span, (seed, unit, span, j, s))

    def test_small_span_hits_zero_and_is_replaced(self):
        # with a span of two units, floor(2 * frac) is 0 about half the time: those become one unit
        D = Design(seed=1, step_mode="linear", unit=64, colour_span=128, step_span=128)
        self.assertEqual({D.colour(j) for j in range(50)}, {64})
        self.assertEqual({D.step(j) for j in range(50)}, {64})

    def test_hashed_seeds_64_bit(self):
        for seed in range(20):
            D = Design(seed=seed)
            ss = [D.step(j) for j in range(32)]
            self.assertTrue(all(0 <= s <= M64 for s in ss))
            self.assertEqual(len(set(ss)), len(ss))
            self.assertTrue(any(s >= 1 << 63 for s in ss))        # uses the top bit
            self.assertTrue(all(isinstance(s, int) for s in ss))

    def test_hashed_seed_formula(self):
        D = Design(seed=3)
        for j in range(10):
            f = (D.w2 + j * 2 ** .5) % 1
            self.assertEqual(D.step(j), int(f * 2.0 ** 64))

    def test_seeds_differ(self):
        self.assertNotEqual([Design(seed=0).pad(j) for j in range(4)], [Design(seed=1).pad(j) for j in range(4)])
        self.assertEqual([Design(seed=7).colour(j) for j in range(9)], [Design(seed=7).colour(j) for j in range(9)])

    def test_explicit_pads_first(self):
        D = Design(seed=0, data="none", pads=[100, "200"])
        self.assertEqual([D.pad(j) for j in range(4)], [100, 200, 3508, 1872])
        self.assertEqual(D.pads, [100, 200])
        # colours and steps keep their own index
        D2 = Design(seed=0, data="both")
        self.assertEqual(Design(seed=0, data="both", pads=[8]).colour(0), D2.colour(0))


class TestSettings(unittest.TestCase):
    def test_setting_kinds(self):
        D = Design(seed=0, data="both", step_mode="hashed")
        self.assertEqual(D.setting(2, STOCK), Setting(STOCK))
        self.assertEqual(D.setting(2, COLOUR), Setting(COLOUR, colour=D.colour(2)))
        self.assertEqual(D.setting(2, STEP), Setting(STEP, step=D.step(2)))

    def test_env(self):
        self.assertEqual(Setting(STOCK).env("hashed"), {})
        self.assertEqual(Setting(COLOUR, colour=640).env("hashed"), {"PLACEMAT_COLOUR": "640"})
        self.assertEqual(Setting(STEP, step=12345).env("hashed"),
                         {"PLACEMAT_STEP_MODE": "hashed", "PLACEMAT_STEP_SEED": "12345"})
        self.assertEqual(Setting(STEP, step=64).env("linear"), {"PLACEMAT_STEP_MODE": "linear", "PLACEMAT_STEP": "64"})
        # seed 0 is a valid hashed seed, not "off"
        self.assertEqual(Setting(STEP, step=0).env("hashed")["PLACEMAT_STEP_SEED"], "0")

    def test_labels(self):
        self.assertEqual(Setting(STOCK).label(), "0, off")
        self.assertEqual(Setting(COLOUR, colour=640).label(), "c 640, off")
        self.assertEqual(Setting(STEP, step=2816).label(), "0, step 2816")
        self.assertEqual(Setting(STEP, step=0xc2094cbc7ce0d000).label(), "0, step 0xc2094cbc7ce0d000")


class TestVariantsAndBatches(unittest.TestCase):
    def test_variants_per_data_mode(self):
        want = {"none": ["3.s"], "colour": ["3.s", "3.c"], "step": ["3.s", "3.t"], "both": ["3.s", "3.c", "3.t"]}
        for data, v in want.items():
            self.assertEqual(Design(data=data).variants(3), v)
            self.assertEqual(Design(data=data).kinds, DATA_MODES[data])
        self.assertEqual(Design.parse("11.t"), (11, STEP))
        self.assertEqual(set(KIND_NAMES), {STOCK, COLOUR, STEP})

    def test_bad_data_mode(self):
        with self.assertRaises(ValueError):
            Design(data="all")

    def test_defaults_with_data(self):
        for data in ("colour", "step", "both"):
            D = Design(data=data)
            self.assertEqual((D.start, D.batch_step, D.cap), (4, 2, 12))
            self.assertEqual([D.batch(j) for j in range(12)], [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4])
            self.assertEqual([list(D.batch_pads(b)) for b in range(6)],
                             [[0, 1, 2, 3], [4, 5], [6, 7], [8, 9], [10, 11], []])

    def test_defaults_without_data(self):
        D = Design(data="none")
        self.assertEqual((D.start, D.batch_step, D.cap), (8, 4, 24))
        self.assertEqual([D.batch(j) for j in (0, 7, 8, 11, 12, 23)], [0, 0, 1, 1, 2, 4])
        self.assertEqual([list(D.batch_pads(b)) for b in range(6)],
                         [list(range(8)), [8, 9, 10, 11], [12, 13, 14, 15], [16, 17, 18, 19], [20, 21, 22, 23], []])

    def test_first_batch_variant_counts(self):
        # DESIGN §5.7: a first batch is 8, 8 or 12 variants
        for data, n in (("none", 8), ("step", 8), ("colour", 8), ("both", 12)):
            D = Design(data=data)
            self.assertEqual(sum(len(D.variants(j)) for j in D.batch_pads(0)), n)

    def test_batches_cover_pads_once(self):
        for start, step, cap in ((4, 2, 12), (4, 3, 12), (2, 2, 2), (5, 4, 7), (3, 1, 6)):
            D = Design(data="both", start=start, batch_step=step, cap=cap)
            seen = []
            b = 0
            while list(D.batch_pads(b)):
                for j in D.batch_pads(b):
                    self.assertEqual(D.batch(j), b)
                seen += list(D.batch_pads(b))
                b += 1
            self.assertEqual(seen, list(range(cap)), (start, step, cap))

    def test_cap_at_least_start(self):
        D = Design(data="both", start=6, cap=4)
        self.assertEqual(D.cap, 6)
        self.assertEqual(list(D.batch_pads(1)), [])

    def test_explicit_pads_counted_in_batches(self):
        D = Design(data="both", pads=[10, 20, 30])
        self.assertEqual([D.pad(j) for j in D.batch_pads(0)][:3], [10, 20, 30])
        self.assertEqual(D.batch(3), 0)
        self.assertEqual(D.batch(4), 1)

    def test_json_round_trip(self):
        D = Design(seed=42, data="colour", step_mode="linear", unit=128, colour_span=8192, pads=[4, 8], start=3,
                   batch_step=1, cap=5)
        d = json.loads(json.dumps(D.to_json()))
        E = Design.from_json(d)
        self.assertEqual(E.to_json(), D.to_json())
        self.assertEqual([E.pad(j) for j in range(6)], [D.pad(j) for j in range(6)])
        self.assertEqual([E.colour(j) for j in range(6)], [D.colour(j) for j in range(6)])

    def test_describe_lines_per_mode(self):
        self.assertEqual(len(Design(data="none").describe(4)), 2)
        both = Design(data="both").describe(4)
        self.assertEqual(len(both), 4)
        self.assertIn("hashed step seeds", both[2])
        self.assertTrue(both[1].startswith("colours (span 16384, unit 64): "))


if __name__ == "__main__":
    unittest.main()
