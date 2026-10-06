"""placemat.report and placemat.legacy: the regression anchor against the Amber prototype's runs (DESIGN §10),
reports recomputed from raw timings (DESIGN §8.3), and the two-speed table."""
from __future__ import annotations

import json
import math
import os
import random
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from placemat import legacy, report
from placemat.design import Design
from tests.test_analysis import synth

ROOT = Path(__file__).resolve().parent.parent
TARBALL = ROOT / "planning" / "amber-validation" / "raw.tar.xz"
MEMBER = "ld/lv/raw/20261004-163933.json"


def table_rows(md: str) -> dict[str, list[str]]:
    """The per-case table: case name -> cells."""
    rows = {}
    lines = md.splitlines()
    hdr = next(i for i, l in enumerate(lines) if l.startswith("| Case |"))
    for l in lines[hdr + 2:]:
        if not l.startswith("|"):
            break
        cells = [c.strip() for c in l.strip().strip("|").split(" | ")]
        rows[cells[0].split("`")[1]] = cells
    return rows


@unittest.skipUnless(TARBALL.exists(), "planning/amber-validation/raw.tar.xz not present")
class TestAmberRegression(unittest.TestCase):
    """The Amber stage's run 20261004-163933, re-analysed: its reference report's numbers and verdicts."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with tarfile.open(TARBALL) as tf:
            m = tf.getmember(MEMBER)
            f = tf.extractfile(m)
            path = Path(cls.tmp.name) / "run.json"
            path.write_bytes(f.read())
        cls.proto = json.loads(path.read_text())
        cls.raw = legacy.convert(path)
        cls.C = report.compute(cls.raw)
        cls.md = report.markdown(cls.raw, cls.C)
        cls.rows = table_rows(cls.md)
        cls.J = report.to_json(cls.raw, cls.C)
        hdr = next(l for l in cls.md.splitlines() if l.startswith("| Case |"))
        cls.col = {h.strip(): i for i, h in enumerate(hdr.strip().strip("|").split(" | "))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cell(self, case, col):
        return self.rows[f"microbench: {case}"][self.col[col]]

    # ---- the conversion ----

    def test_convert_design(self):
        d = self.raw["design"]
        self.assertEqual((d["seed"], d["data"], d["step_mode"]), (0, "step", "linear"))
        self.assertEqual((d["start"], d["batch_step"], d["cap"]), (4, 2, 12))      # 8/4/24 variants, two per pad
        D = Design.from_json(d)
        self.assertEqual([D.pad(j) for j in range(12)][:3], [3508, 1872, 364])

    def test_convert_slots(self):
        t = self.raw["times"]["microbench: grade"]["base"]
        self.assertEqual(set(t), {f"{j}.{k}" for j in range(12) for k in "st"} | {"stock"})
        p = self.proto["times"]["microbench: grade"]["base"]
        self.assertEqual(t["0.s"], p["0"])
        self.assertEqual(t["0.t"], p["1"])
        self.assertEqual(t["11.t"], p["23"])
        self.assertEqual(t["stock"], p["stock"])

    def test_convert_counts(self):
        self.assertEqual(self.raw["K"], {k: v // 2 for k, v in self.proto["K"].items()})
        self.assertEqual(self.raw["K"]["microbench: igradedown"], 6)
        self.assertEqual(self.raw["series"]["microbench: fsum"], [25, 50, 100, 200])
        self.assertNotIn("microbench: grade", self.raw["series"])
        self.assertEqual([a["name"] for a in self.raw["arms"]], ["base", "branch"])

    # ---- the reference report's numbers ----

    def test_grade(self):
        self.assertEqual(self.cell("grade", "Stock builds"), "+8.3% (+6.6% to +11.8%) **")
        self.assertEqual(self.cell("grade", "Change"), "+0.2%")
        self.assertEqual(self.cell("grade", "95% interval"), "-0.8% to +1.2%")
        self.assertEqual(self.cell("grade", "Pads"), "12")
        self.assertEqual(self.cell("grade", "Attributed"), "stock code layout")
        self.assertEqual(self.cell("grade", "Verdict"), "placement")

    def test_igradedown(self):
        self.assertTrue(self.cell("igradedown", "Stock builds").startswith("+10.3% ("))
        self.assertTrue(self.cell("igradedown", "Stock builds").endswith("**"))
        self.assertEqual(self.cell("igradedown", "Change"), "-0.7%")
        self.assertEqual(self.cell("igradedown", "Pads"), "6")
        self.assertEqual(self.cell("igradedown", "Attributed"), "stock code layout")
        self.assertEqual(self.cell("igradedown", "Verdict"), "placement")

    def test_setdictnest(self):
        self.assertEqual(self.cell("setdictnest", "Change"), "+5.3%")
        self.assertEqual(self.cell("setdictnest", "95% interval"), "+3.9% to +6.7%")
        self.assertEqual(self.cell("setdictnest", "Verdict"), "**change**")
        self.assertIn("(series)", self.rows["microbench: setdictnest"][0])

    def test_setdictall(self):
        self.assertEqual(self.cell("setdictall", "Change"), "+3.0%")
        self.assertEqual(self.cell("setdictall", "Verdict"), "noise")

    def test_setlistdictall(self):
        self.assertEqual(self.cell("setlistdictall", "Change"), "+2.2%")
        self.assertEqual(self.cell("setlistdictall", "Verdict"), "placement")
        self.assertEqual(self.cell("setlistdictall", "Attributed"), "code")
        self.assertTrue(self.cell("setlistdictall", "Code (spread, p: base / arm)").endswith("**code**"))

    def test_fsum(self):
        self.assertEqual(self.cell("fsum", "Change"), "+0.0%")
        self.assertEqual(self.cell("fsum", "Verdict"), "noise")

    def test_verdicts_and_flags(self):
        V = {k.split(": ")[1]: v["branch"]["verdict"] for k, v in self.C["verdicts"].items()}
        self.assertEqual(V, {"grade": "placement", "igradedown": "placement", "setdictnest": "change",
                             "setdictall": "noise", "setlistdictall": "placement", "fsum": "noise"})
        self.assertEqual(self.C["flags"]["combined"]["code"], {"microbench: setlistdictall"})
        self.assertEqual(self.C["flags"]["combined"]["t"], set())
        self.assertEqual(self.C["flags"]["combined"]["run"], set())
        self.assertIn("verdicts: change 1, placement 3, noise 2", self.md)
        self.assertIn("flags: code 1, step 0, run 0", self.md)

    def test_numbers(self):
        c = self.C["res"]["microbench: grade"]
        s = c["stock"]["branch"]
        self.assertAlmostEqual(s[0], 1.083, delta=0.0005)
        self.assertAlmostEqual(c["cmp"]["branch"]["est"], 1.002, delta=0.0005)
        n = self.C["res"]["microbench: setdictnest"]["cmp"]["branch"]
        self.assertAlmostEqual(n["est"], 1.053, delta=0.0005)
        self.assertAlmostEqual(n["lo"], 1.039, delta=0.0005)
        self.assertAlmostEqual(n["hi"], 1.067, delta=0.0005)

    def test_design_details(self):
        self.assertIn("12 pads: 3508, 1872, 364, 2824, 1316, 3776, 2268, 696, 3220, 1712, 76, 2664", self.md)
        self.assertIn("linear steps (span 16384, unit 64): 12416, 2816, 9600, 64, 6784, 13568, 3968, 10752, 1152, "
                      "7936, 14720, 5120", self.md)
        self.assertIn("data axis step (linear steps) via hook", self.md)

    def test_json(self):
        J = json.loads(json.dumps(self.J, default=str))
        g = J["cases"]["microbench: grade"]
        self.assertEqual(g["pads"], 12)
        self.assertEqual(g["compare"]["branch"]["verdict"], "placement")
        self.assertEqual(g["compare"]["branch"]["attributed"], ["stock code layout"])
        self.assertEqual(set(g["compare"]["branch"]["settings"]), {"stock", "stepped"})
        self.assertEqual(J["cases"]["microbench: setlistdictall"]["flags"], ["code"])
        self.assertEqual(J["cases"]["microbench: setdictnest"]["compare"]["branch"]["verdict"], "change")

    def test_twospeed(self):
        L = report.twospeed(self.raw, ["grade"])
        self.assertEqual(len(L), 2 + 2)                  # header, rule, grade, igradedown
        self.assertTrue(L[2].startswith("| microbench: grade |"))


def small_raw(D: Design, m: int, factor=None, seed=0) -> dict:
    arms = ["base", "branch"]
    rnd = random.Random(seed)
    times = {k: synth(D, m, arms, rnd, factor=factor) for k in ("s: one", "s: two")}
    return {"version": 1, "project": "toy", "config": None, "design": D.to_json(),
            "arms": [{"name": "base", "source": "/a", "tree": "x"}, {"name": "branch", "source": "/b", "tree": "y"}],
            "rounds": 7, "plan": {"s: one": ("s", "one"), "s: two": ("s", "two")}, "series": {},
            "K": {"s: one": m, "s: two": m}, "times": times, "thresholds": {}, "target": 0.01,
            "stock_placement": True, "covariate": "base", "data_method": "none", "log": {}, "geometry": {}}


class TestReportFiles(unittest.TestCase):
    def test_write_and_reload(self):
        D = Design(data="none", start=4, cap=4)
        raw = small_raw(D, 4, factor=lambda a, j, k: 1.2 if a == "branch" else 1.0)
        with tempfile.TemporaryDirectory() as d:
            rawp = Path(d) / "r.raw.json"
            rawp.write_text(json.dumps(raw))
            back = report.load_raw(rawp)
            self.assertEqual(back["plan"], raw["plan"])
            self.assertIsInstance(back["times"]["s: one"]["base"]["0.s"][0], tuple)
            md = Path(d) / "r.md"
            j = report.write(back, md, "My title")
            text = md.read_text()
            self.assertTrue(text.startswith("# My title\n"))
            on_disk = json.loads(md.with_suffix(".json").read_text())
        self.assertEqual(on_disk["cases"]["s: one"]["compare"]["branch"]["verdict"], "change")
        self.assertEqual(j["cases"]["s: two"]["compare"]["branch"]["verdict"], "change")
        rows = table_rows(text)
        self.assertEqual(set(rows), {"s: one", "s: two"})
        self.assertEqual(rows["s: one"][-1], "**change**")

    def test_missing_timings_row(self):
        D = Design(data="none", start=4, cap=4)
        raw = small_raw(D, 4)
        raw["K"]["s: two"] = 6                            # more pads than were timed
        md = report.markdown(raw)
        self.assertIn("| `s: two` | (missing timings) |", md)
        self.assertIn("Cases 1 of 2", md)
        self.assertIsNone(report.to_json(raw)["cases"]["s: two"])


class TestIncomplete(unittest.TestCase):
    def test_no_batch_finished(self):
        D = Design(data="none", start=4, cap=4)
        raw = small_raw(D, 4)
        raw.update(complete=False, partial={"batch": 0, "pads": [0, 3], "cases": 2, "rounds": 3, "start": 100.0},
                   K={"s: one": 0, "s: two": 0})
        md = report.markdown(raw)
        self.assertIn("batch 0 (pads 0-3, 2 cases) stopped after 3 of 7 rounds", md)
        self.assertIn("No batch finished", md)
        self.assertIn("Cases 0 of 2", md)
        J = report.to_json(raw)
        self.assertFalse(J["complete"])
        self.assertEqual(J["partial"]["rounds"], 3)

    def test_old_raw_is_complete(self):
        D = Design(data="none", start=4, cap=4)
        md = report.markdown(small_raw(D, 4))
        self.assertNotIn("Incomplete", md)


def gated(probes, lim=0.05, **log):
    D = Design(data="none", start=4, batch_step=2, cap=8)
    raw = small_raw(D, 4)
    raw["machine"] = {"max_noise": lim}
    raw["log"] = {"probes": probes, "batches": [[4, 2], [6, 2], [8, 1]][:len(probes)], **log}
    return raw


class TestNoisyBatches(unittest.TestCase):
    """P012 items 3 and 6: batches that went ahead at the gate's limit, ended over it, or had a noisy record."""

    def test_old_probes(self):
        # older raw files: no went_ahead key, so a start probe over max_noise means the gate gave up
        raw = gated([{"spread": 0.02, "end_spread": 0.03}, {"spread": 0.128, "end_spread": 0.04},
                     {"spread": 0.03, "end_spread": 0.101}])
        B = report.batches(raw, [])
        self.assertEqual([(x["went_ahead"], x["end_over"], x["marked"]) for x in B],
                         [(False, False, False), (True, False, True), (False, True, True)])
        self.assertEqual(B[1]["pads"], [4, 5])
        L = report.noisy_lines(raw, B)
        self.assertIn("**Noisy batches** (2 of 3", L[1])
        self.assertEqual(L[3:], ["- batch 1 (pads 4-5, 2 cases): went ahead after the wait (start probe 12.8%)",
                                 "- batch 2 (pads 6-7, 1 case): ended at 10.1%"])

    def test_went_ahead_recorded(self):
        raw = gated([{"spread": 0.07, "end_spread": 0.01, "went_ahead": False}])
        self.assertFalse(report.batches(raw, [])[0]["marked"])
        L = report.noisy_lines(raw, report.batches(raw, []))
        self.assertEqual(L, ["", "**Noisy batches:** none (no batch went ahead at the gate's limit or ended with its "
                                 "probe over 5%; no noise record for this run)."])

    def test_noise_record(self):
        raw = gated([{"spread": 0.02, "end_spread": 0.02}, {"spread": 0.02, "end_spread": 0.02}],
                    batch_t=[[1000.0, 1200.0], [2000.0, 2300.0]])
        raw["host"] = "mac.local"
        smp = [{"t": 1060.0, "spread": 0.03, "host": "mac"}, {"t": 1120.0, "spread": 0.20, "host": "mac"},
               {"t": 2050.0, "spread": 0.12, "host": "mac"}, {"t": 2110.0, "spread": 0.09, "host": "mac"},
               {"t": 2170.0, "spread": 0.02, "host": "mac"}, {"t": 2100.0, "spread": 0.5, "host": "other"},
               {"t": 1500.0, "spread": 0.9, "host": "mac"}]
        raw["log"]["noise"] = smp
        B = report.batches(raw)
        self.assertEqual(B[0]["noise"], {"n": 2, "over": 1, "median": 0.115, "max": 0.20})
        self.assertTrue(B[0]["noisy_record"])                   # median 11.5% over 5%
        self.assertEqual(B[1]["noise"]["n"], 3)                   # another host's sample left out
        self.assertAlmostEqual(B[1]["noise"]["median"], 0.09)
        self.assertTrue(B[1]["marked"])
        L = report.noisy_lines(raw, B)
        self.assertIn("the noise record has 5 samples during the timing, the largest spread 20.0%", L[1])
        self.assertEqual(L[3], "- batch 0 (pads 0-3, 2 cases): noise record 2 sample(s), 1 over, median 11.5%, "
                               "max 20.0% (noisy)")
        J = report.to_json(raw)
        self.assertEqual([x["marked"] for x in J["batches"]], [True, True])

    def test_noise_log_read_when_not_kept(self):
        raw = gated([{"spread": 0.02, "end_spread": 0.02}], batch_t=[[1000.0, 1200.0]])
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "noise.jsonl"
            f.write_text("".join(json.dumps({"t": t, "host": "h", "spread": sp}) + "\n"
                                 for t, sp in ((900.0, 0.5), (1100.0, 0.08), (1150.0, 0.07))))
            with mock.patch.dict(os.environ, {"PLACEMAT_NOISELOG": str(f)}):
                B = report.batches(raw)
            with mock.patch.dict(os.environ, {"PLACEMAT_NOISELOG": str(Path(d) / "none.jsonl")}):
                self.assertIsNone(report.batches(raw)[0]["noise"])
        self.assertEqual(B[0]["noise"]["n"], 2)
        self.assertTrue(B[0]["noisy_record"])

    def test_ungated(self):
        raw = gated([{"spread": 0.3, "end_spread": 0.3}], lim=0.0)
        self.assertEqual(report.noisy_lines(raw), [])
        self.assertFalse(report.batches(raw, [])[0]["marked"])


def pad_effects(D, m, sd, seed):
    rnd = random.Random(seed)
    eff = {j: math.exp(rnd.gauss(0, sd)) for j in range(m)}
    return small_raw(D, m, factor=lambda a, j, k: eff.get(j, 1.0), seed=seed)


class TestDimensioning(unittest.TestCase):
    LOG = {"time_s": 100.0, "runs": 100, "build_s": 40.0, "build_wait_s": 500.0}

    def rows(self, raw):
        L = report.dimensioning(raw)
        return {l.split("`")[1]: l.split(" | ")[-1].rstrip(" |") for l in L if l.startswith("| `")}, L

    def test_advice_without_lock_waits(self):
        D = Design(data="none", start=8, cap=8)
        raw = pad_effects(D, 8, 0.05, 1)
        raw["log"] = dict(self.LOG)
        rows, L = self.rows(raw)
        # 40 s over 2 arms x 9 binaries: 2.2 s a build, c1 1 s: c2 3.2 s, not counting the 500 s of lock waits
        self.assertIn("c2 = 3.2 s per extra pad", L[1])
        self.assertTrue(all(r.isdigit() for r in rows.values()), rows)

    def test_no_pad_variance(self):
        D = Design(data="none", start=8, cap=8)
        raw = pad_effects(D, 8, 0.0, 2)
        raw["log"] = dict(self.LOG)
        rows, _ = self.rows(raw)
        self.assertEqual(set(rows.values()), {"cannot advise (no measurable pad variance)"})

    def test_old_raw_build_time(self):
        D = Design(data="none", start=8, cap=8)
        raw = pad_effects(D, 8, 0.05, 1)
        raw["log"] = {k: v for k, v in self.LOG.items() if k != "build_wait_s"}
        rows, L = self.rows(raw)
        self.assertIn("no r*: this raw file's build time includes lock waits", L[1])
        self.assertEqual(set(rows.values()), {"-"})


class TestTwospeed(unittest.TestCase):
    def raw(self):
        t = {"c1": {"base": {"0.s": [(100.0, 0), (100.0, 0), (100.0, 0), (200.0, 0)], "1.s": [(5.0, 0), (6.0, 0)]},
                    "branch": {"0.s": [(100.0, 0), (101.0, 0), (99.0, 0), (None, 0)]}},
             "c2": {"base": {"0.s": [(1.0, 0)]}, "branch": {}}}
        return {"arms": [{"name": "base"}, {"name": "branch"}], "times": t}

    def test_table(self):
        L = report.twospeed(self.raw())
        self.assertEqual(L[0], "| case | base p90/p10 | base slow rounds | base max/min | branch p90/p10 | "
                               "branch slow rounds | branch max/min |")
        self.assertEqual(L[1], "|---|---|---|---|---|---|---|")
        # base: one variant, values over its median 1, 1, 1, 2 (the two-round slot is skipped)
        self.assertEqual(L[2], "| c1 | 2.000 | 1/4 | 2.00 | 1.020 | 0/3 | 1.02 |")
        self.assertEqual(L[3], "| c2 | - | - | - | - | - | - |")

    def test_filter(self):
        L = report.twospeed(self.raw(), ["c2"])
        self.assertEqual(len(L), 3)
        self.assertTrue(L[2].startswith("| c2 |"))


class TestFormat(unittest.TestCase):
    def test_pct_ci(self):
        self.assertEqual(report.pct(0.083), "+8.3%")
        self.assertEqual(report.pct(-0.0071), "-0.7%")
        self.assertEqual(report.ci((1.083, 1.066, 1.118)), "+8.3% (+6.6% to +11.8%)")


if __name__ == "__main__":
    unittest.main()
