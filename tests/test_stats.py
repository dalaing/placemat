"""placemat.stats: t quantiles, intervals, permutation tests, false-discovery control (DESIGN §5.7)."""
from __future__ import annotations

import math
import random
import statistics
import unittest

from placemat import stats as S

# Student's t quantiles as tabulated (3 decimals), df 1..30.
T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]
T995 = [63.657, 9.925, 5.841, 4.604, 4.032, 3.707, 3.499, 3.355, 3.250, 3.169, 3.106, 3.055, 3.012, 2.977, 2.947,
        2.921, 2.898, 2.878, 2.861, 2.845, 2.831, 2.819, 2.807, 2.797, 2.787, 2.779, 2.771, 2.763, 2.756, 2.750]
T9975 = [127.321, 14.089, 7.453, 5.598, 4.773, 4.317, 4.029, 3.833, 3.690, 3.581, 3.497, 3.428, 3.372, 3.326, 3.286,
         3.252, 3.222, 3.197, 3.174, 3.153, 3.135, 3.119, 3.104, 3.091, 3.078, 3.067, 3.057, 3.047, 3.038, 3.030]


class TestT(unittest.TestCase):
    def test_tq_against_tables(self):
        for q, table in ((0.975, T975), (0.995, T995), (0.9975, T9975)):
            for df, t in enumerate(table, 1):
                with self.subTest(q=q, df=df):
                    self.assertAlmostEqual(S.tq(df, q), t, delta=0.0006)

    def test_tq_named_values(self):
        self.assertAlmostEqual(S.tq(1, 0.975), 12.706, places=3)
        self.assertAlmostEqual(S.tq(5, 0.995), 4.032, places=3)
        self.assertAlmostEqual(S.tq(3, 0.9975), 7.453, places=3)
        self.assertAlmostEqual(S.tq(30, 0.975), 2.042, places=3)
        self.assertAlmostEqual(S.tq(30), 2.042, places=3)      # 0.975 is the default

    def test_f_sf(self):
        # tabulated 5% and 1% points of F
        self.assertAlmostEqual(S.f_sf(2.866, 4, 20), 0.05, delta=0.0005)
        self.assertAlmostEqual(S.f_sf(4.431, 4, 20), 0.01, delta=0.0002)
        self.assertAlmostEqual(S.f_sf(3.316, 2, 30), 0.05, delta=0.0005)
        self.assertAlmostEqual(S.f_sf(1.0, 3, 3), 0.5, places=6)    # equal df: the median is 1
        self.assertEqual(S.f_sf(0.0, 4, 20), 1.0)

    def test_tq_closed_forms(self):
        # df 1 (Cauchy): tan(pi (q - 1/2)); df 2: (2q - 1) / sqrt(2 q (1 - q)).
        for q in (0.6, 0.9, 0.975, 0.995, 0.9975, 0.9999):
            self.assertAlmostEqual(S.tq(1, q), math.tan(math.pi * (q - 0.5)), delta=1e-8 * S.tq(1, q))
            self.assertAlmostEqual(S.tq(2, q), (2 * q - 1) / math.sqrt(2 * q * (1 - q)), delta=1e-8 * S.tq(2, q))

    def test_tq_large_df_tends_to_normal(self):
        self.assertAlmostEqual(S.tq(100000, 0.975), 1.95996, places=3)

    def test_tq_degenerate_df(self):
        self.assertEqual(S.tq(0), math.inf)

    def test_t_cdf_cauchy(self):
        for t in (-30.0, -2.0, -0.5, 0.0, 0.3, 1.0, 7.0):
            self.assertAlmostEqual(S.t_cdf(t, 1), 0.5 + math.atan(t) / math.pi, places=10)
        self.assertEqual(S.t_cdf(math.inf, 3), 1.0)
        self.assertEqual(S.t_cdf(-math.inf, 3), 0.0)

    def test_t_sf2_is_two_sided(self):
        for df in (1, 4, 11, 29):
            self.assertAlmostEqual(S.t_sf2(S.tq(df, 0.975), df), 0.05, places=9)
            self.assertAlmostEqual(S.t_sf2(-S.tq(df, 0.995), df), 0.01, places=9)
        self.assertEqual(S.t_sf2(3.0, 0), 1.0)

    def test_betainc_special_cases(self):
        for x in (0.0, 0.1, 0.37, 0.5, 0.9, 1.0):
            self.assertAlmostEqual(S.betainc(1, 1, x), x, places=12)
            self.assertAlmostEqual(S.betainc(3, 1, x), x ** 3, places=12)
            self.assertAlmostEqual(S.betainc(1, 4, x), 1 - (1 - x) ** 4, places=12)
        self.assertAlmostEqual(S.betainc(2.5, 2.5, 0.5), 0.5, places=12)


class TestIntervals(unittest.TestCase):
    def test_tci_known(self):
        xs = [0.0, 0.2]
        e, lo, hi, h = S.tci(xs)
        self.assertAlmostEqual(h, 12.7062047 * 0.1, places=5)       # t(0.975, 1) * sd / sqrt(2)
        self.assertAlmostEqual(e, math.exp(0.1), places=12)
        self.assertAlmostEqual(lo, math.exp(0.1 - h), places=12)
        self.assertAlmostEqual(hi, math.exp(0.1 + h), places=12)

    def test_tci_quantile_and_width(self):
        xs = [math.log(x) for x in (1.01, 1.03, 0.99, 1.02, 1.00, 1.04)]
        e, lo, hi, h = S.tci(xs, 0.995)
        se = statistics.stdev(xs) / math.sqrt(len(xs))
        self.assertAlmostEqual(h, 4.032 * se, delta=0.0006 * se)
        self.assertLess(lo, e)
        self.assertLess(e, hi)

    def test_tci_single_value(self):
        e, lo, hi, h = S.tci([math.log(1.1)])
        self.assertAlmostEqual(e, 1.1)
        self.assertEqual(h, math.inf)
        self.assertEqual(lo, 0.0)
        self.assertEqual(hi, math.inf)

    def test_paired_t(self):
        mu, p, sig = S.paired_t([1, 2, 3, 4, 5], 0.05)
        self.assertEqual(mu, 3)
        t = 3 / (statistics.stdev([1, 2, 3, 4, 5]) / math.sqrt(5))
        self.assertAlmostEqual(p, S.t_sf2(t, 4))
        self.assertTrue(0.01 < p < 0.02)                       # t(0.99, 4) < t < t(0.995, 4)
        self.assertTrue(sig)
        self.assertFalse(S.paired_t([1, 2, 3, 4, 5], 0.01)[2])

    def test_paired_t_at_the_critical_value(self):
        # ds with t exactly t(0.975, 5) give p = 0.05
        base = [-1.0, -0.5, 0.0, 0.5, 1.0, 0.0]
        se = statistics.stdev(base) / math.sqrt(6)
        ds = [x + S.tq(5) * se for x in base]
        mu, p, sig = S.paired_t(ds, 0.05)
        self.assertAlmostEqual(p, 0.05, places=9)

    def test_paired_t_degenerate(self):
        self.assertEqual(S.paired_t([], 0.05), (0.0, 1.0, False))
        self.assertEqual(S.paired_t([0.3], 0.05), (0.3, 1.0, False))
        self.assertEqual(S.paired_t([0.25, 0.25, 0.25], 0.05), (0.25, 0.0, True))
        self.assertEqual(S.paired_t([0.0, 0.0], 0.05), (0.0, 1.0, False))

    def test_bootstrap_median_ratio(self):
        rr = [1.08, 1.10, 1.09, 1.12, 1.07, 1.11, 1.10]
        est, lo, hi = S.bootstrap_median_ratio(rr)
        self.assertEqual(est, statistics.median(rr))
        self.assertTrue(min(rr) <= lo <= est <= hi <= max(rr))
        self.assertEqual(S.bootstrap_median_ratio(rr), (est, lo, hi))     # seeded
        self.assertGreater(lo, 1)

    def test_geomean_ci(self):
        g, lo, hi = S.geomean_ci([(1.1, 1.0, 1.21)])        # one interval symmetric in logs: itself
        self.assertAlmostEqual(g, 1.1)
        self.assertAlmostEqual(lo, 1.0, places=3)
        self.assertAlmostEqual(hi, 1.21, places=3)
        g, lo, hi = S.geomean_ci([(1.1, 1.05, 1.15), (1 / 1.1, 0.87, 0.95)])
        self.assertAlmostEqual(g, 1.0)
        self.assertLess(lo, 1)
        self.assertGreater(hi, 1)

    def test_tmean(self):
        self.assertEqual(S.tmean([1, 2, 3, 4, 100]), 3)
        self.assertEqual(S.tmean([4, 1, 100, 2, 3]), 3)
        self.assertEqual(S.tmean([1, 2, 3, 10]), 4)

    def test_slope(self):
        self.assertAlmostEqual(S.slope([(1, 3), (2, 5), (3, 7)]), 2.0)
        self.assertAlmostEqual(S.slope([(100, 10), (200, 19), (400, 41), (800, 79)]),
                               statistics.linear_regression([100, 200, 400, 800], [10, 19, 41, 79]).slope)
        self.assertEqual(S.slope([(5, 1), (5, 2)]), 0.0)
        # a constant overhead does not change the slope
        self.assertAlmostEqual(S.slope([(r, 3 + 0.5 * r) for r in (125, 250, 500, 1000)]), 0.5)


class TestBH(unittest.TestCase):
    def test_bh_1995_example(self):
        # Benjamini & Hochberg (1995), section 4: 15 p-values, q = 0.05, four rejected.
        ps = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459, 0.3240, 0.4262, 0.5719,
              0.6528, 0.7590, 1.000]
        got = S.bh({f"h{i}": p for i, p in enumerate(ps)}, 0.05)
        self.assertEqual(got, {"h0", "h1", "h2", "h3"})

    def test_bh_step_up(self):
        # the smallest p fails its own threshold (0.02 > 0.05/3) but the second passes 2*0.05/3: both
        self.assertEqual(S.bh({"a": 0.02, "b": 0.025, "c": 0.06}, 0.05), {"a", "b"})
        self.assertEqual(S.bh({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.005, "e": 0.2}, 0.05), {"a", "b", "c", "d"})
        self.assertEqual(S.bh({"a": 0.04, "b": 0.04, "c": 0.04}, 0.05), {"a", "b", "c"})

    def test_bh_none_or_empty(self):
        self.assertEqual(S.bh({}, 0.05), set())
        self.assertEqual(S.bh({"a": 0.2, "b": 0.5}, 0.05), set())
        self.assertEqual(S.bh({"a": 0.05}, 0.05), {"a"})
        self.assertEqual(S.bh({"a": 0.0501}, 0.05), set())


def noise(rnd, n, sd=0.01, mu=0.0):
    return [rnd.gauss(mu, sd) for _ in range(n)]


class TestSignflip(unittest.TestCase):
    def test_null_gives_large_p(self):
        rnd = random.Random(1)
        ps = [S.signflip([noise(rnd, 7) for _ in range(6)], random.Random(s), 4000) for s in range(5)]
        self.assertGreater(statistics.median(ps), 0.1)
        self.assertTrue(all(p > 0.01 for p in ps), ps)

    def test_strong_effect_gives_small_p(self):
        rnd = random.Random(2)
        p = S.signflip([noise(rnd, 7, mu=0.05) for _ in range(4)], random.Random(0), 4000)
        self.assertLessEqual(p, 2 / 4001)

    def test_deterministic(self):
        rnd = random.Random(3)
        cs = [noise(rnd, 7, mu=0.003) for _ in range(5)] + [noise(rnd, 14, mu=0.003)]
        self.assertEqual(S.signflip(cs, random.Random(9), 3000), S.signflip(cs, random.Random(9), 3000))

    def test_short_contrasts_ignored(self):
        self.assertEqual(S.signflip([], random.Random(0)), 1.0)
        self.assertEqual(S.signflip([[0.5, 0.6]], random.Random(0)), 1.0)

    def test_many_rounds(self):
        rnd = random.Random(4)
        self.assertLess(S.signflip([noise(rnd, 20, mu=0.02)], random.Random(0), 2000), 0.01)
        self.assertGreater(S.signflip([noise(rnd, 20)], random.Random(0), 2000), 0.01)

    def test_p_bounds(self):
        rnd = random.Random(5)
        p = S.signflip([noise(rnd, 5) for _ in range(3)], random.Random(0), 1000)
        self.assertTrue(1 / 1001 <= p <= 1.0)


def block(rnd, rows, cols, shift=None, drift=0.0):
    """rows (rounds) of cols (variants) log times; shift: per-column offsets; drift: per-row sd."""
    shift = shift or [0.0] * cols
    out = []
    for _ in range(rows):
        d = rnd.gauss(0, drift) if drift else 0.0
        out.append([9.2 + d + shift[c] + rnd.gauss(0, 0.01) for c in range(cols)])
    return out


class TestSpreadPerm(unittest.TestCase):
    def test_null_gives_large_p(self):
        rnd = random.Random(1)
        ps = [S.spread_perm([block(rnd, 7, 4, drift=0.05), block(rnd, 7, 3, drift=0.05)], random.Random(s), 1000)
              for s in range(5)]
        self.assertGreater(statistics.median(ps), 0.1)
        self.assertTrue(all(p > 0.01 for p in ps), ps)

    def test_strong_effect_gives_small_p(self):
        rnd = random.Random(2)
        p = S.spread_perm([block(rnd, 7, 4, shift=[0.2, 0, 0, 0])], random.Random(0), 1000)
        self.assertLessEqual(p, 0.001)                         # ten times the permutations below 0.01

    def test_drift_between_batches_does_not_count(self):
        rnd = random.Random(3)
        b1 = block(rnd, 7, 4)
        b2 = [[x + 0.3 for x in r] for r in block(rnd, 7, 3)]
        self.assertGreater(S.spread_perm([b1, b2], random.Random(0), 1000), 0.05)

    def test_deterministic(self):
        rnd = random.Random(4)
        bs = [block(rnd, 7, 4, shift=[0.01, 0, 0, 0])]
        self.assertEqual(S.spread_perm(bs, random.Random(7), 500), S.spread_perm(bs, random.Random(7), 500))

    def test_input_not_modified(self):
        rnd = random.Random(5)
        b = block(rnd, 7, 4)
        copy = [list(r) for r in b]
        S.spread_perm([b], random.Random(0), 100)
        self.assertEqual(b, copy)

    def test_too_small_blocks_ignored(self):
        self.assertEqual(S.spread_perm([], random.Random(0)), 1.0)
        self.assertEqual(S.spread_perm([[[1.0, 2.0], [1.0, 2.0]]], random.Random(0)), 1.0)    # two rounds
        self.assertEqual(S.spread_perm([[[1.0], [1.0], [2.0]]], random.Random(0)), 1.0)       # one variant


def run_groups(rnd, effect, settings=("stock", "t1"), variants=4, runs=7):
    gs = {}
    for s in settings:
        g = []
        for _ in range(variants):
            lab = [int(rnd.random() < 0.6) for _ in range(runs)]
            if not 0 < sum(lab) < runs:
                lab[0], lab[1] = 1, 0
            x = [9.0 + rnd.gauss(0, 0.01) + effect * c for c in lab]
            g.append((x, lab))
        gs[s] = g
    return gs


class TestRunTest(unittest.TestCase):
    def test_null_gives_large_p(self):
        rnd = random.Random(1)
        ps = [S.run_test(run_groups(rnd, 0.0), random.Random(s), 500)[1] for s in range(4)]
        self.assertTrue(all(p > 0.01 for p in ps), ps)
        self.assertGreater(statistics.median(ps), 0.1)

    def test_strong_effect(self):
        rnd = random.Random(2)
        est, p = S.run_test(run_groups(rnd, 0.1), random.Random(0), 500)
        self.assertLessEqual(p, 0.001)
        self.assertEqual(set(est), {"stock", "t1"})
        for e, z in est.values():
            self.assertAlmostEqual(e, 0.1, delta=0.02)
            self.assertGreater(z, 3)

    def test_negative_effect_sign(self):
        rnd = random.Random(3)
        est, p = S.run_test(run_groups(rnd, -0.1, settings=("stock",)), random.Random(0), 500)
        self.assertLess(est["stock"][0], -0.08)
        self.assertLess(p, 0.01)

    def test_deterministic(self):
        gs = run_groups(random.Random(4), 0.005)
        self.assertEqual(S.run_test(gs, random.Random(5), 300), S.run_test(gs, random.Random(5), 300))

    def test_uninformative_groups(self):
        self.assertEqual(S.run_test({}, random.Random(0)), ({}, 1.0))
        same = {"stock": [([1.0, 2.0, 3.0], [1, 1, 1]), ([1.0, 2.0], [0, 0])]}
        self.assertEqual(S.run_test(same, random.Random(0)), ({}, 1.0))


class TestStratRatio(unittest.TestCase):
    def test_same_change_in_each_category(self):
        a = [(100, True), (100, True), (110, False), (110, False)]
        b = [(110, True), (110, True), (121, False), (121, False)]
        self.assertAlmostEqual(S.strat_ratio(a, b, None), math.log(1.1))

    def test_removes_composition_bias(self):
        # the category is worth +10% in both arms; b has more runs in the slow category; no change
        a = [(110, True)] * 2 + [(100, False)] * 5
        b = [(110, True)] * 5 + [(100, False)] * 2
        naive = math.log(statistics.median(v for v, _ in b) / statistics.median(v for v, _ in a))
        self.assertAlmostEqual(naive, math.log(1.1))
        self.assertAlmostEqual(S.strat_ratio(a, b, naive), 0.0)

    def test_weights_by_smaller_count(self):
        a = [(100, True)] * 3 + [(100, False)] * 1
        b = [(120, True)] * 1 + [(110, False)] * 3
        # True: log 1.2 weight 1; False: log 1.1 weight 1
        self.assertAlmostEqual(S.strat_ratio(a, b, None), (math.log(1.2) + math.log(1.1)) / 2)

    def test_fallback_when_no_shared_category(self):
        self.assertEqual(S.strat_ratio([(100, True)], [(110, False)], 0.123), 0.123)
        self.assertEqual(S.strat_ratio([], [], -1.0), -1.0)


if __name__ == "__main__":
    unittest.main()
