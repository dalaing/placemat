"""placemat.analysis: per-case numbers, flags and verdicts on synthetic timings with planted effects (DESIGN §5.7).

The timings are built in the documented raw shape: times[arm][slot] = [(value, covariate) per round], slots
'<pad>.<s|c|t>', 'stock' (first batch) and 'a<batch>' (later batches' anchors). Within a round and slot the
arms share one drift factor, as when they run back to back.
"""
from __future__ import annotations

import math
import random
import statistics
import unittest

from placemat.analysis import Thresholds, aligned, analyse, flags, ok, one_pad, verdict
from placemat.design import COLOUR, STEP, STOCK, Design

BASE_US = 10000.0           # over fast_us: the 3% threshold applies


def synth(D: Design, m: int, arms: list[str], rnd: random.Random, factor=None, cov=None, rounds: int = 7,
          noise: float = 0.004, drift: float = 0.02, batch_drift: float = 0.0, base_us: float = BASE_US) -> dict:
    """Timings of one case over m pads. factor(arm, pad, kind) -> multiplier (pad 'stock' for the stock
    builds; anchors are pad 0 at the stock setting); cov(arm, rnd) -> (covariate, multiplier) per execution."""
    factor = factor or (lambda a, j, k: 1.0)
    times = {a: {} for a in arms}
    nb = D.batch(m - 1) + 1
    for b in range(nb):
        pads = [j for j in D.batch_pads(b) if j < m]
        slots = [(v, *D.parse(v)) for j in pads for v in D.variants(j)]
        slots += [("stock", "stock", STOCK)] if b == 0 else [(f"a{b}", 0, STOCK)]
        for s, _, _ in slots:
            for a in arms:
                times[a][s] = []
        bd = math.exp(b * batch_drift)
        for _ in range(rounds):
            for s, j, k in slots:
                d = math.exp(rnd.gauss(0, drift)) * bd
                for a in arms:
                    c, f = cov(a, rnd) if cov else (None, 1.0)
                    v = base_us * d * factor(a, j, k) * f * math.exp(rnd.gauss(0, noise))
                    times[a][s].append((v, c))
    return times


def run_all(cases: dict, arms, D, m, th=None, seed=0, stock_placement=True, **kw):
    th = th or Thresholds()
    rnd = random.Random(seed)
    res = {k: analyse(t, arms, D, m, th, rnd, **kw) for k, t in cases.items()}
    F = flags(res, arms, D, th)
    V = {k: {a: verdict(k, r, a, F, D, stock_placement) for a in arms[1:]} for k, r in res.items() if r}
    return res, F, V


def nulls(D, m, arms, rnd, n, **kw):
    return {f"null{i}": synth(D, m, arms, rnd, **kw) for i in range(n)}


AB = ["base", "branch"]


class TestHelpers(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(ok([(1.0, 3), (None, 3), (0, 1), (-2.0, 1), (2.5, None)]), [(1.0, 3), (2.5, None)])

    def test_aligned(self):
        a = [(1.0, 0), (math.e, 0), (None, 0), (1.0, 0)]
        b = [(math.e, 0), (1.0, 0), (1.0, 0)]
        self.assertEqual(aligned([a, b]), [[0.0, 1.0], [1.0, 0.0]])

    def test_thresholds(self):
        th = Thresholds()
        self.assertEqual(th.thr(5000.0), 0.03)
        self.assertEqual(th.thr(4999.0), 0.10)

    def test_missing_timings(self):
        D = Design(data="step")
        t = synth(D, 4, AB, random.Random(0))
        self.assertIsNotNone(analyse(t, AB, D, 4, Thresholds(), random.Random(0), full=False))
        t["branch"]["2.t"] = [(None, None)] * 6 + [(10000.0, None)]
        self.assertIsNone(analyse(t, AB, D, 4, Thresholds(), random.Random(0), full=False))
        del t["branch"]["2.t"]
        self.assertIsNone(analyse(t, AB, D, 4, Thresholds(), random.Random(0), full=False))
        # more pads than were timed
        self.assertIsNone(analyse(synth(D, 4, AB, random.Random(0)), AB, D, 6, Thresholds(), random.Random(0)))
        self.assertIsNone(analyse(synth(D, 4, AB, random.Random(0)), AB, D, 0, Thresholds(), random.Random(0)))

    def test_deterministic(self):
        D = Design(data="step")
        t = synth(D, 4, AB, random.Random(1), factor=lambda a, j, k: 1.02 if a == "branch" else 1.0)
        r1 = analyse(t, AB, D, 4, Thresholds(), random.Random(5))
        r2 = analyse(t, AB, D, 4, Thresholds(), random.Random(5))
        self.assertEqual(r1, r2)


class TestPureChange(unittest.TestCase):
    """Every variant of the branch (and its stock build) is 10% slower: a change, nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="both")
        rnd = random.Random(10)
        f = lambda a, j, k: 1.10 if a == "branch" else 1.0
        cases = {"changed": synth(D, 4, AB, rnd, factor=f)} | nulls(D, 4, AB, rnd, 3)
        cls.res, cls.F, cls.V = run_all(cases, AB, D, 4)

    def test_estimate(self):
        c = self.res["changed"]["cmp"]["branch"]
        self.assertAlmostEqual(c["est"], 1.10, delta=0.01)
        self.assertGreater(c["lo"], 1.05)
        self.assertLess(c["hi"], 1.15)
        for k in (STOCK, COLOUR, STEP):
            self.assertAlmostEqual(c[k][0], 1.10, delta=0.015)
        self.assertEqual(set(c["y"]), {f"{j}.{k}" for j in range(4) for k in "sct"})

    def test_verdict(self):
        v = self.V["changed"]["branch"]
        self.assertEqual(v["verdict"], "change")
        self.assertEqual(v["qualifiers"], [])
        self.assertEqual(v["attributed"], [])
        self.assertFalse(v["covers_1"])
        self.assertTrue(v["stock_measurable"])          # the stock builds see the change too

    def test_no_flags(self):
        for fam, ks in self.F["combined"].items():
            self.assertEqual(ks, set(), fam)
        self.assertFalse(self.res["changed"]["cmp"]["branch"]["dep_c"]["sig"])
        self.assertFalse(self.res["changed"]["cmp"]["branch"]["dep_t"]["sig"])

    def test_nulls_are_noise(self):
        for k in ("null0", "null1", "null2"):
            self.assertEqual(self.V[k]["branch"]["verdict"], "noise", k)

    def test_base_us_and_threshold(self):
        r = self.res["changed"]
        self.assertAlmostEqual(r["base_us"], BASE_US, delta=0.03 * BASE_US)
        self.assertEqual(r["thr"], 0.03)
        self.assertEqual(r["m"], 4)


class TestCodeEffect(unittest.TestCase):
    """Pad 2's build is 20% slower in base and branch alike (a code-layout effect, not a change)."""

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="step")
        rnd = random.Random(20)
        f = lambda a, j, k: 1.20 if j == 2 else 1.0
        cases = {"code": synth(D, 4, AB, rnd, factor=f)} | nulls(D, 4, AB, rnd, 5)
        cls.res, cls.F, cls.V = run_all(cases, AB, D, 4)

    def test_code_flag(self):
        self.assertEqual(self.F["combined"]["code"], {"code"})
        for a in AB:
            self.assertEqual(self.F["per_arm"][a]["code"], {"code"})
            A = self.res["code"]["arms"][a]
            self.assertLessEqual(A["code_p"], 0.001)
            self.assertAlmostEqual(A["code_max"], 0.20, delta=0.03)
            self.assertAlmostEqual(A["worst"], 0.20, delta=0.04)
        self.assertLessEqual(self.F["p"]["code"]["code"], 0.002)

    def test_not_a_change(self):
        v = self.V["code"]["branch"]
        self.assertEqual(v["verdict"], "placement")
        self.assertEqual(v["attributed"], ["code"])
        self.assertTrue(v["covers_1"])
        self.assertAlmostEqual(self.res["code"]["cmp"]["branch"]["est"], 1.0, delta=0.01)

    def test_not_a_data_or_run_effect(self):
        for fam in (STEP, "run"):
            self.assertEqual(self.F["combined"][fam], set(), fam)
        self.assertEqual(self.res["code"]["cmp"]["branch"]["dep_t"]["sig"], False)

    def test_nulls_unflagged(self):
        for k in self.res:
            if k.startswith("null"):
                self.assertEqual(self.V[k]["branch"]["verdict"], "noise", k)


class TestStepEffect(unittest.TestCase):
    """Step-only effects: one alike in both arms (step sensitivity), one in the branch only (step-dependent)."""

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="both")
        rnd = random.Random(30)
        alike = lambda a, j, k: 1.15 if k == STEP else 1.0
        branch = lambda a, j, k: 1.12 if (k == STEP and a == "branch") else 1.0
        cases = {"alike": synth(D, 4, AB, rnd, factor=alike), "branch": synth(D, 4, AB, rnd, factor=branch)}
        cases |= nulls(D, 4, AB, rnd, 3)
        cls.res, cls.F, cls.V = run_all(cases, AB, D, 4)

    def test_step_flags(self):
        self.assertEqual(self.F["combined"][STEP], {"alike", "branch"})
        self.assertEqual(self.F["combined"][COLOUR], set())
        self.assertEqual(self.F["combined"]["code"], set())
        A = self.res["alike"]["arms"]
        for a in AB:
            self.assertLessEqual(A[a]["t_p"], 0.001)
            self.assertAlmostEqual(A[a]["t_typ"], 0.15, delta=0.02)
            self.assertAlmostEqual(A[a]["t_max"], 0.15, delta=0.03)
            self.assertGreater(A[a]["c_p"], 0.01)
        # per arm: only the branch is step-sensitive in the second case
        self.assertIn("branch", self.F["per_arm"]["branch"][STEP])
        self.assertNotIn("branch", self.F["per_arm"]["base"][STEP])

    def test_alike_is_placement_not_dependent(self):
        v = self.V["alike"]["branch"]
        self.assertEqual(v["verdict"], "placement")
        self.assertEqual(v["attributed"], ["step"])
        self.assertEqual(v["qualifiers"], [])
        c = self.res["alike"]["cmp"]["branch"]
        self.assertFalse(c["dep_t"]["sig"])
        self.assertAlmostEqual(c["est"], 1.0, delta=0.01)

    def test_branch_only_is_step_dependent(self):
        c = self.res["branch"]["cmp"]["branch"]
        self.assertTrue(c["dep_t"]["sig"])
        self.assertLess(c["dep_t"]["p"], 0.005)
        self.assertAlmostEqual(c["dep_t"]["mean"], -math.log(1.12), delta=0.02)
        self.assertFalse(c["dep_c"]["sig"])
        self.assertAlmostEqual(c[STOCK][0], 1.0, delta=0.01)
        self.assertAlmostEqual(c[STEP][0], 1.12, delta=0.015)
        self.assertAlmostEqual(c[COLOUR][0], 1.0, delta=0.01)
        v = self.V["branch"]["branch"]
        self.assertEqual(v["qualifiers"], ["step-dependent", "data-dependent"])
        self.assertIn("step", v["attributed"])


class TestColourEffect(unittest.TestCase):
    def test_colour_only(self):
        D = Design(data="colour")
        rnd = random.Random(35)
        f = lambda a, j, k: 1.10 if k == COLOUR else 1.0
        cases = {"col": synth(D, 4, AB, rnd, factor=f)} | nulls(D, 4, AB, rnd, 2)
        res, F, V = run_all(cases, AB, D, 4)
        self.assertEqual(F["combined"][COLOUR], {"col"})
        self.assertNotIn(STEP, F["combined"])
        self.assertEqual(V["col"]["branch"]["verdict"], "placement")
        self.assertEqual(V["col"]["branch"]["attributed"], ["colour"])
        self.assertNotIn("t_p", res["col"]["arms"]["base"])
        self.assertEqual(V["col"]["branch"]["qualifiers"], [])
        self.assertLess(res["col"]["arms"]["base"]["c_lead"], 0.5)

    # The zstd Linux survey: a colour flag resting on one bad pad, the typical effect under 1%.
    def test_flag_on_one_pad(self):
        D = Design(data="colour", start=8, cap=8)
        rnd = random.Random(36)
        f = lambda a, j, k: 1.25 if (k == COLOUR and j == 3) else 1.0
        cases = {"one": synth(D, 8, AB, rnd, factor=f)} | nulls(D, 8, AB, rnd, 2)
        res, F, V = run_all(cases, AB, D, 8)
        self.assertEqual(F["combined"][COLOUR], {"one"})              # the flag rule is unchanged
        for a in AB:
            self.assertGreater(res["one"]["arms"][a]["c_lead"], 0.9)
        self.assertTrue(one_pad(res["one"], COLOUR))
        self.assertEqual(V["one"]["branch"]["qualifiers"], ["colour flag on one pad"])


class TestRunCovariate(unittest.TestCase):
    """Executions whose covariate (the region base) is the most common one run 10% slower, in both arms.
    The branch lands on that base more often than the base arm does; there is no real change."""

    SLOW, OTHER = 0x300000000, 0x108000000

    @classmethod
    def cov(cls, a, rnd):
        p = 0.9 if a == "branch" else 0.4
        return (cls.SLOW, 1.10) if rnd.random() < p else (cls.OTHER + 0x4000 * rnd.randrange(3), 1.0)

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="none", start=8, cap=8)
        rnd = random.Random(40)
        cases = {"run": synth(D, 8, AB, rnd, cov=cls.cov, drift=0.01)}
        cases |= nulls(D, 8, AB, rnd, 2, cov=lambda a, r: (cls.SLOW if r.random() < 0.6 else cls.OTHER, 1.0))
        cls.cases = cases
        cls.res, cls.F, cls.V = run_all(cases, AB, D, 8)
        # the same timings with the adjustment switched off
        cls.raw = analyse(cases["run"], AB, D, 8, Thresholds(run_alpha=0.0), random.Random(0), full=False)

    def test_detected(self):
        r = self.res["run"]
        self.assertLess(r["run_p"], 0.01)
        self.assertTrue(r["adjusted"])
        for a in AB:
            R = r["arms"][a]["run"]
            self.assertEqual(R["mode"], self.SLOW)
            self.assertAlmostEqual(R["est"]["stock"], math.log(1.10), delta=0.02)
        self.assertAlmostEqual(r["run_eff"][0], math.log(1.10), delta=0.02)
        self.assertEqual(r["run_eff"][1], "stock")
        self.assertEqual(self.F["combined"]["run"], {"run"})

    def test_mode_and_share(self):
        R = self.res["run"]["arms"]["branch"]["run"]
        self.assertGreater(R["share"], 0.8)
        self.assertLess(self.res["run"]["arms"]["base"]["run"]["share"], 0.55)

    def test_adjusted_for(self):
        unadj = self.raw["cmp"]["branch"]["est"]
        adj = self.res["run"]["cmp"]["branch"]["est"]
        self.assertFalse(self.raw["adjusted"])
        self.assertGreater(unadj, 1.02)                 # the branch's extra slow runs bias the plain estimate
        self.assertAlmostEqual(adj, 1.0, delta=0.01)
        self.assertLess(abs(adj - 1), abs(unadj - 1) / 2)
        self.assertNotEqual(self.V["run"]["branch"]["verdict"], "change")
        self.assertIn("run", self.V["run"]["branch"]["attributed"])

    def test_adjusted_code_test_not_fooled(self):
        # adjusted runs: the covariate does not show up as code spread
        self.assertEqual(self.F["combined"]["code"], set())

    def test_nulls_not_adjusted(self):
        for k in ("null0", "null1"):
            self.assertFalse(self.res[k]["adjusted"], k)
            self.assertGreater(self.res[k]["run_p"], 0.05, k)

    def test_per_arm_categories(self):
        # covariate_pooled=False (a khash covariate): each arm's runs against its own mode
        rnd = random.Random(0)
        r = analyse(self.cases["run"], AB, self.D, 8, Thresholds(), rnd, full=False, covariate_pooled=False)
        self.assertTrue(r["adjusted"])
        self.assertAlmostEqual(r["cmp"]["branch"]["est"], 1.0, delta=0.015)


class TestStockBuilds(unittest.TestCase):
    """The stock builds differ by 10%; every padded variant is equal: placement, attributed to the stock layout."""

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="step")
        rnd = random.Random(50)
        f = lambda a, j, k: 1.10 if (a == "branch" and j == "stock") else 1.0
        cls.cases = {"stock": synth(D, 4, AB, rnd, factor=f)} | nulls(D, 4, AB, rnd, 2)
        cls.res, cls.F, cls.V = run_all(cls.cases, AB, D, 4)

    def test_stock_result(self):
        est, lo, hi = self.res["stock"]["stock"]["branch"]
        self.assertAlmostEqual(est, 1.10, delta=0.02)
        self.assertGreater(lo, 1.05)

    def test_placement_attributed_to_stock_code_layout(self):
        v = self.V["stock"]["branch"]
        self.assertEqual(v["verdict"], "placement")
        self.assertTrue(v["stock_measurable"])
        self.assertEqual(v["attributed"], ["stock code layout"])
        self.assertTrue(v["covers_1"])
        for fam, ks in self.F["combined"].items():
            self.assertEqual(ks, set(), fam)

    def test_stock_build_when_not_stock_placement(self):
        _, _, V = run_all(self.cases, AB, self.D, 4, stock_placement=False)
        self.assertEqual(V["stock"]["branch"]["attributed"], ["stock build"])
        self.assertEqual(V["stock"]["branch"]["verdict"], "placement")

    def test_missing_stock_slot(self):
        t = {a: {s: v for s, v in w.items() if s != "stock"} for a, w in self.cases["stock"].items()}
        res, F, V = run_all({"x": t}, AB, self.D, 4)
        self.assertEqual(res["x"]["stock"], {})
        self.assertEqual(V["x"]["branch"]["verdict"], "noise")


class TestMultiArm(unittest.TestCase):
    """Three arms: one changed by +10%, one unchanged, both against the base."""

    ARMS = ["base", "fast", "same"]

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="both")
        rnd = random.Random(60)
        f = lambda a, j, k: 0.90 if a == "fast" else 1.0
        code = lambda a, j, k: 1.25 if (j == 1 and a == "same") else 1.0
        cases = {"multi": synth(D, 4, cls.ARMS, rnd, factor=f), "code": synth(D, 4, cls.ARMS, rnd, factor=code)}
        cases |= nulls(D, 4, cls.ARMS, rnd, 2)
        cls.res, cls.F, cls.V = run_all(cases, cls.ARMS, D, 4)

    def test_comparisons(self):
        r = self.res["multi"]
        self.assertEqual(set(r["cmp"]), {"fast", "same"})
        self.assertEqual(set(r["arms"]), set(self.ARMS))
        self.assertAlmostEqual(r["cmp"]["fast"]["est"], 0.90, delta=0.01)
        self.assertAlmostEqual(r["cmp"]["same"]["est"], 1.0, delta=0.01)
        self.assertEqual(set(r["stock"]), {"fast", "same"})
        self.assertEqual(self.V["multi"]["fast"]["verdict"], "change")
        self.assertEqual(self.V["multi"]["same"]["verdict"], "noise")

    def test_p_combined_over_arms(self):
        r = self.res["multi"]
        self.assertEqual(r["run_p"], min(1.0, 3 * min(r["arms"][a]["run"]["p"] for a in self.ARMS)))
        self.assertEqual(self.F["p"]["code"]["multi"], min(1.0, 3 * min(r["arms"][a]["code_p"] for a in self.ARMS)))

    def test_code_effect_in_one_arm(self):
        self.assertEqual(self.F["combined"]["code"], {"code"})
        self.assertEqual(self.F["per_arm"]["same"]["code"], {"code"})
        self.assertEqual(self.F["per_arm"]["base"]["code"], set())
        self.assertEqual(self.F["per_arm"]["fast"]["code"], set())
        # the slow pad moves the 'same' arm's ratio on one pad only: not a change
        self.assertEqual(self.V["code"]["same"]["verdict"], "placement")
        self.assertIn("code", self.V["code"]["fast"]["attributed"])


class TestAnchors(unittest.TestCase):
    """A later batch runs 6% slower in every slot (drift between batches): the anchors put it back on the
    first batch's scale, so the drift is not code spread."""

    @classmethod
    def setUpClass(cls):
        cls.D = D = Design(data="step")
        cls.t = synth(D, 6, AB, random.Random(70), batch_drift=math.log(1.06))
        cls.r = analyse(cls.t, AB, D, 6, Thresholds(), random.Random(0))

    def test_slots(self):
        self.assertEqual(set(self.t["base"]), {"0.s", "0.t", "1.s", "1.t", "2.s", "2.t", "3.s", "3.t", "4.s", "4.t",
                                               "5.s", "5.t", "stock", "a1"})

    def test_drift_rescaled(self):
        for a in AB:
            A = self.r["arms"][a]
            self.assertLess(A["code_max"], 0.03, a)
            self.assertGreater(A["code_p"], 0.01, a)
        self.assertAlmostEqual(self.r["cmp"]["branch"]["est"], 1.0, delta=0.01)

    def test_without_anchors_within_batches(self):
        t = {a: {s: v for s, v in w.items() if not s.startswith("a")} for a, w in self.t.items()}
        r = analyse(t, AB, self.D, 6, Thresholds(), random.Random(0))
        for a in AB:
            self.assertLess(r["arms"][a]["code_max"], 0.03, a)


if __name__ == "__main__":
    unittest.main()
