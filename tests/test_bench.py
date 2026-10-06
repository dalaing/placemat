"""placemat.bench: the benchmark protocol and its adapters (DESIGN §8.1)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from placemat import bench
from placemat.bench import Sample, Suite

K_SCRIPT = """\\l pbt/lib.k
/ out["commented";1;{x}] stays a comment
n:100000
out["grade";100;{<x}[n?1000]]
out["fsum";200;{+/x}[n?1.0]]
  out["indented";5;{x}[1]]
x:1;out["inline";5;{x}[1]]
out["setdictall";100000;{d[k]:v}]
"""


class TestParseLines(unittest.TestCase):
    def test_space_separated(self):
        got = bench.parse_lines("work 123.5 20\nsum 7 50\n")
        self.assertEqual(got, [Sample("work", 123.5, 20), Sample("sum", 7.0, 50)])

    def test_units(self):
        got = bench.parse_lines("a 1.5 10 ms\nb 2 10 s\nc 250 10 ns\nd 3 10 us\ne 4 10 µs\n")
        self.assertEqual([(s.name, s.iterations) for s in got], [("a", 10), ("b", 10), ("c", 10), ("d", 10), ("e", 10)])
        for s, v in zip(got, (1500.0, 2e6, 0.25, 3.0, 4.0)):
            self.assertAlmostEqual(s.value, v)

    def test_unit_without_iterations(self):
        got = bench.parse_lines("a 2 ms\nb 7\n")
        self.assertEqual(got[0].iterations, None)
        self.assertAlmostEqual(got[0].value, 2000.0)
        self.assertEqual(got[1], Sample("b", 7.0, None))

    def test_tab_separated_names_with_spaces(self):
        got = bench.parse_lines("dict amend, nested\t12.5\t100\tms\nplain case\t3e2\t7\n")
        self.assertEqual(got[0].name, "dict amend, nested")
        self.assertAlmostEqual(got[0].value, 12500.0)
        self.assertEqual(got[0].iterations, 100)
        self.assertEqual(got[1], Sample("plain case", 300.0, 7))

    def test_number_forms(self):
        got = bench.parse_lines("a 1e3 5\nb 2.5E-1 5\nc -1 5\n")
        self.assertEqual([s.value for s in got], [1000.0, 0.25, -1.0])

    def test_skips_noise(self):
        text = "# comment 1 2\n\nwarming up...\nwork 12 20\nname notanumber 4\nlonely\nsum 3 1\n"
        self.assertEqual([s.name for s in bench.parse_lines(text)], ["work", "sum"])


class TestK(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "mb.k"
        self.script.write_text(K_SCRIPT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_k_cases(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = bench.k_cases(self.script)
        self.assertEqual(got, {"grade": 100, "fsum": 200, "setdictall": 100000})
        # cases not at the start of their line are said, not skipped silently; comments are not
        msg = err.getvalue()
        self.assertIn("indented", msg)
        self.assertIn("inline", msg)
        self.assertNotIn("commented", msg)

    def test_k_subset_plain_and_series(self):
        dest = bench.k_subset(self.script, {"grade": None, "fsum": [25, 50, 100, 200]}, self.dir / "sub.k")
        lines = dest.read_text().splitlines()
        self.assertIn('out["grade";100;{<x}[n?1000]]', lines)
        self.assertNotIn('out["setdictall";100000;{d[k]:v}]', lines)
        fs = [l for l in lines if l.startswith('out["fsum')]
        self.assertEqual(fs, ['out["fsum@w";25;{+/x}[n?1.0]]', 'out["fsum@25";25;{+/x}[n?1.0]]',
                              'out["fsum@50";50;{+/x}[n?1.0]]', 'out["fsum@100";100;{+/x}[n?1.0]]',
                              'out["fsum@200";200;{+/x}[n?1.0]]'])
        # every other line kept, in order
        self.assertEqual(lines[:3], K_SCRIPT.splitlines()[:3])
        self.assertIn("x:1;out[\"inline\";5;{x}[1]]", lines)
        with contextlib.redirect_stderr(io.StringIO()):
            sub = bench.k_cases(dest)
        self.assertEqual(sub, {"grade": 100, "fsum@w": 25, "fsum@25": 25, "fsum@50": 50,
                                               "fsum@100": 100, "fsum@200": 200})

    def test_parse_k(self):
        text = "grade 123.4\nfsum@w 9\nfsum@25 10.5\nfsum@50 20\nsome other output\nsetdictall 7 extra\n"
        got = bench.parse_k(text, {"grade": 100})
        self.assertEqual(got, [Sample("grade", 123.4, 100), Sample("fsum", 10.5, 25), Sample("fsum", 20.0, 50)])

    def test_parse_k_unknown_reps(self):
        self.assertEqual(bench.parse_k("x 5\n", {}), [Sample("x", 5.0, None)])

    def test_suite_lists_k_cases(self):
        su = Suite("mb", format="k-out", script=self.script)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(su.list_cases(), {"grade": 100, "fsum": 200, "setdictall": 100000})
        self.assertEqual(Suite("t", cases=["a", "b"]).list_cases(), {"a": None, "b": None})

    def test_parse_dispatch(self):
        su = Suite("mb", format="k-out", script=self.script)
        self.assertEqual(bench.parse(su, "grade 5\n", {"grade": 100}), [Sample("grade", 5.0, 100)])
        self.assertEqual(bench.parse(Suite("l"), "grade 5 100\n"), [Sample("grade", 5.0, 100)])


GBENCH = """2026-10-05T10:00:00+00:00
Running ./bench
{
  "context": {"date": "2026-10-05T10:00:00+00:00", "num_cpus": 8, "library_build_type": "release"},
  "benchmarks": [
    {"name": "BM_sum/1024", "family_index": 0, "run_name": "BM_sum/1024", "run_type": "iteration",
     "repetitions": 1, "iterations": 1000, "real_time": 1250.0, "cpu_time": 1240.0, "time_unit": "ns"},
    {"name": "BM_sort", "run_type": "iteration", "iterations": 50, "real_time": 2.5, "cpu_time": 2.4,
     "time_unit": "ms"},
    {"name": "BM_sort_mean", "run_type": "aggregate", "aggregate_name": "mean", "iterations": 3,
     "real_time": 2.5, "cpu_time": 2.4, "time_unit": "ms"},
    {"name": "BM_us", "iterations": 4, "real_time": 10.0, "time_unit": "us"}
  ]
}
"""

HYPERFINE = json.dumps({"results": [
    {"command": "./prog --fast", "mean": 0.0125, "stddev": 0.0001, "median": 0.0124, "user": 0.01, "system": 0.001,
     "min": 0.012, "max": 0.013, "times": [0.0125, 0.0124, 0.0126]},
    {"mean": 1.5, "stddev": 0.1, "times": [1.5]}]})


class TestAdapters(unittest.TestCase):
    def test_gbench(self):
        got = bench.parse_gbench(GBENCH)
        self.assertEqual([s.name for s in got], ["BM_sum/1024", "BM_sort", "BM_us"])
        self.assertAlmostEqual(got[0].value, 1250.0)          # 1.25 us per iteration, 1000 iterations
        self.assertEqual(got[0].iterations, 1000)
        self.assertAlmostEqual(got[1].value, 2500.0 * 50)
        self.assertAlmostEqual(got[2].value, 40.0)
        self.assertEqual(bench.parse(Suite("g", format="gbench"), GBENCH), got)

    def test_hyperfine(self):
        got = bench.parse_hyperfine(HYPERFINE)
        self.assertEqual(got[0].name, "./prog --fast")
        self.assertAlmostEqual(got[0].value, 12500.0)
        self.assertEqual(got[0].iterations, 1)
        self.assertEqual(got[1].name, "cmd1")
        self.assertAlmostEqual(got[1].value, 1.5e6)
        self.assertEqual(bench.parse(Suite("h", format="hyperfine"), "hyperfine says\n" + HYPERFINE), got)


class TestSuite(unittest.TestCase):
    def test_does_series(self):
        self.assertTrue(Suite("k", format="k-out").does_series)
        self.assertFalse(Suite("l").does_series)
        self.assertTrue(Suite("l", series=True).does_series)
        self.assertFalse(Suite("k", format="k-out", series=False).does_series)

    def test_series_env(self):
        self.assertEqual(bench.series_env({"msum100": [0.125, 0.25, 0.5, 1.0], "b": [0.5, 1]}),
                         "msum100=0.125,0.25,0.5,1;b=0.5,1")
        self.assertEqual(bench.series_env({}), "")


if __name__ == "__main__":
    unittest.main()
