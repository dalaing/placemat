"""placemat.config: loading placemat.toml (DESIGN §4)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from placemat import config
from placemat.analysis import Thresholds

FULL = """
[project]
name = "amber"
root = "../.."

[build]
command = ["bash", "{config_dir}/build.sh"]
binary = "out/amber"
inputs = ["build.sh", "src/*"]
env = {CFLAGS = "-O3"}

[run]
env = {AMBER_THREADS = "1"}
inherit_env = true
timeout = 120
memory_mb = 2048
rounds = 5

[machine]
lock = ["flock", "/tmp/m.lock", "cat"]
shared_lock = ["flock", "-s", "/tmp/m.lock", "cat"]
max_load = 1.5
boundary = 32
line = 32

[data]
method = "interposer"
vary = "step"
step_mode = "linear"
unit = 128
colour_span = 2097152
step_span = 4096
min = 131072
covariate = "base"
stock_placement = true

[stats]
threshold = 0.02
threshold_fast = 0.08
fast_us = 2000
q = 0.1
pmax = 0.005
target = 0.02
series_us = 500

[[suite]]
name = "microbench"
format = "k-out"
script = "pbt/microbench.k"
command = ["{binary}", "{script}"]
cwd = "{arm_dir}"
series = true

[[suite]]
name = "gb"
format = "gbench"
command = ["{binary}", "--benchmark_format=json"]
cases = ["BM_a", "BM_b"]
env = {GB = "1"}
"""


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve() / "proj" / "tools" / "placemat"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str, name: str = "placemat.toml") -> Path:
        p = self.dir / name
        p.write_text(text)
        return p


class TestFull(ConfigCase):
    def test_every_section(self):
        c = config.load(self.write(FULL))
        root = self.dir.parent.parent
        self.assertEqual(c.path, self.dir / "placemat.toml")
        self.assertEqual(c.config_dir, self.dir)
        self.assertEqual(c.name, "amber")
        self.assertEqual(c.root, root)
        self.assertEqual(c.build_command, ["bash", "{config_dir}/build.sh"])
        self.assertEqual(c.binary, "out/amber")
        self.assertEqual(c.inputs, ["build.sh", "src/*"])
        self.assertEqual(c.build_env, {"CFLAGS": "-O3"})
        self.assertEqual(c.run_env, {"AMBER_THREADS": "1"})
        self.assertTrue(c.inherit_env)
        self.assertEqual((c.timeout, c.memory_mb, c.rounds), (120.0, 2048, 5))
        self.assertEqual(c.lock, ["flock", "/tmp/m.lock", "cat"])
        self.assertEqual(c.shared_lock, ["flock", "-s", "/tmp/m.lock", "cat"])
        self.assertEqual((c.max_load, c.boundary, c.line), (1.5, 32, 32))
        self.assertEqual((c.data_method, c.vary, c.step_mode), ("interposer", "step", "linear"))
        self.assertEqual((c.unit, c.colour_span, c.step_span, c.min_size), (128, 2097152, 4096, 131072))
        self.assertEqual(c.covariate, "base")
        self.assertTrue(c.stock_placement)
        th = c.thresholds
        self.assertEqual((th.threshold, th.threshold_fast, th.fast_us, th.q, th.pmax), (0.02, 0.08, 2000.0, 0.1, 0.005))
        self.assertEqual((c.target, c.series_us), (0.02, 500.0))
        self.assertEqual(list(c.suites), ["microbench", "gb"])
        mb = c.suites["microbench"]
        self.assertEqual((mb.format, mb.command, mb.cwd, mb.series), ("k-out", ["{binary}", "{script}"], "{arm_dir}", True))
        self.assertEqual(mb.script, root / "pbt/microbench.k")
        gb = c.suites["gb"]
        self.assertEqual((gb.format, gb.cases, gb.env, gb.script, gb.series), ("gbench", ["BM_a", "BM_b"], {"GB": "1"}, None, None))
        self.assertFalse(gb.does_series)

    def test_expand(self):
        c = config.load(self.write(FULL))
        self.assertEqual(c.expand(c.build_command[1]), f"{self.dir}/build.sh")
        self.assertEqual(c.expand("{root}/x {arm_dir}", arm_dir="/a"), f"{c.root}/x /a")

    def test_thresholds_type(self):
        c = config.load(self.write(FULL))
        self.assertIsInstance(c.thresholds, Thresholds)
        self.assertEqual(c.thresholds.thr(1000.0), 0.08)
        self.assertEqual(c.thresholds.thr(2000.0), 0.02)


class TestDefaults(ConfigCase):
    def test_empty_file(self):
        c = config.load(self.write(""))
        self.assertEqual(c.name, "placemat")            # the config file's directory
        self.assertEqual(c.root, self.dir)
        self.assertEqual((c.build_command, c.binary, c.inputs, c.build_env), ([], "a.out", ["*"], {}))
        self.assertEqual((c.run_env, c.inherit_env, c.timeout, c.memory_mb, c.rounds), ({}, False, 900.0, 0, 7))
        self.assertEqual((c.lock, c.shared_lock, c.max_load, c.boundary, c.line), ([], [], 0.0, 4096, 64))
        self.assertEqual((c.data_method, c.vary, c.step_mode, c.unit), ("hook", "both", "hashed", 64))
        self.assertEqual((c.colour_span, c.step_span, c.min_size), (16384, 16384, 65536))
        self.assertEqual(c.covariate, "base")
        self.assertTrue(c.stock_placement)
        self.assertEqual(vars(c.thresholds), vars(Thresholds()))
        self.assertEqual((c.target, c.series_us, c.suites), (0.01, 1000.0, {}))

    def test_interposer_defaults(self):
        # the interposer's run covariate is the hash of k values, and (0, off) is not the stock placement
        c = config.load(self.write('[data]\nmethod = "interposer"\n'))
        self.assertEqual(c.covariate, "khash")
        self.assertFalse(c.stock_placement)
        c = config.load(self.write('[data]\nmethod = "allocator"\n'))
        self.assertEqual(c.covariate, "khash")
        self.assertFalse(c.stock_placement)
        # with no data method the variants differ from the stock build in code only
        c = config.load(self.write('[data]\nmethod = "none"\n'))
        self.assertTrue(c.stock_placement)
        with self.assertRaises(ValueError):
            config.load(self.write('[data]\nmethod = "malloc"\n'))

    def test_suite_defaults(self):
        c = config.load(self.write('[[suite]]\nname = "s"\n'))
        s = c.suites["s"]
        self.assertEqual((s.format, s.command, s.script, s.cwd, s.cases, s.env, s.series),
                         ("lines", [], None, "{arm_dir}", [], {}, None))

    def test_relative_path(self):
        p = self.write('[project]\nname = "x"\n')
        import os
        old = os.getcwd()
        os.chdir(self.dir)
        try:
            c = config.load("placemat.toml")
        finally:
            os.chdir(old)
        self.assertEqual(c.path, p)
        self.assertTrue(c.path.is_absolute())

    def test_bad_toml(self):
        import tomllib
        with self.assertRaises(tomllib.TOMLDecodeError):
            config.load(self.write("[project\nname = 1\n"))


class TestMachineOverlay(ConfigCase):
    PROJECT = ('[machine]\nlock = ["quiet.py", "--exclusive"]\nmax_noise = 0.05\nboundary = 4096\n'
               '[data]\nmethod = "allocator"\ncolour_span = 16384\nstep_span = 16384\nmin = 32768\n'
               '[run]\nrounds = 7\n')
    OVERLAY = ('[machine]\nlock = ["flock", "/var/tmp/placemat.lock"]\nwait = 600\n'
               '[target]\nprefix = ["ssh", "box"]\n'
               '[data]\ncolour_span = 4096\nstep_span = 4096\nmin = 1\n'
               '[run]\nrounds = 99\n')

    def test_overlay_sets_machine_keys_only(self):
        c = config.load(self.write(self.PROJECT), overlay=str(self.write(self.OVERLAY, "box.toml")))
        self.assertEqual(c.lock, ["flock", "/var/tmp/placemat.lock"])
        self.assertEqual(c.lock_wait, 600)
        self.assertEqual(c.max_noise, 0.05)                # kept: the overlay does not set it
        self.assertEqual(c.target_prefix, ["ssh", "box"])
        self.assertEqual((c.colour_span, c.step_span), (4096, 4096))
        self.assertEqual(c.min_size, 32768)                 # a project key: not the overlay's
        self.assertEqual(c.rounds, 7)                       # [run] is the project's

    def test_environment(self):
        import os
        from unittest import mock
        ov = str(self.write(self.OVERLAY, "box.toml"))
        with mock.patch.dict(os.environ, {"PLACEMAT_MACHINE": ov}):
            self.assertEqual(config.load(self.write(self.PROJECT)).step_span, 4096)
        with mock.patch.dict(os.environ, {"PLACEMAT_MACHINE": ""}):
            self.assertEqual(config.load(self.write(self.PROJECT)).step_span, 16384)


if __name__ == "__main__":
    unittest.main()
