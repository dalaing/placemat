"""placemat.runner (with build.py and the run command in cli.py): rounds, arms, the raw-data store, builds and
their verification, and the data log's summary (DESIGN §5.1, §8.2, §8.3)."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from placemat import build, cbuild, cli, config, report
from placemat.design import Design
from placemat.runner import Plan, Runner, plan_cases, read_log

CC = cbuild.find_cc()
HAS_NM = shutil.which("nm") is not None

MAIN_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e6+t.tv_nsec/1e3;}
__attribute__((noinline)) long work(long n){long s=0;for(long i=0;i<n;i++)s+=i*i^(i>>SHIFT);return s;}
__attribute__((noinline)) double sum(double*x,long n){double s=0;for(long i=0;i<n;i++)s+=x[i];return s;}
int main(void){
 const char*c=getenv("PLACEMAT_CASES");volatile long r=0;
 if(!c||strstr(c,"work")){double t=now();for(int k=0;k<20;k++)r+=work(20000);printf("work %.3f 20\n",now()-t);}
 if(!c||strstr(c,"sum")){long n=1<<15;double*x=malloc(n*8);for(long i=0;i<n;i++)x[i]=i;
  double t=now();for(int k=0;k<20;k++)r+=(long)sum(x,n);printf("sum %.3f 20\n",now()-t);free(x);}
 return 0;}
"""

BUILD_SH = """#!/bin/sh
set -e
cd "$PLACEMAT_OUT"
srcs="$PLACEMAT_SRC/main.c"
[ -n "$PLACEMAT_PAD_SOURCE" ] && srcs="$PLACEMAT_PAD_SOURCE $srcs"
"$TOY_CC" -O2 $PLACEMAT_CFLAGS -o toy $srcs $PLACEMAT_LDFLAGS
"""

NOPAD_SH = """#!/bin/sh
set -e
cd "$PLACEMAT_OUT"
"$TOY_CC" -O2 -o toy "$PLACEMAT_SRC/main.c"
"""

FAIL_SH = """#!/bin/sh
echo "main.c:3: error: boom" >&2
exit 3
"""

TOML = """
[project]
name = "toy"
[build]
command = ["sh", "{{config_dir}}/{script}"]
binary = "toy"
inputs = ["*.c"]
env = {{TOY_CC = "{cc}"}}
[run]
rounds = 2
[data]
method = "{method}"
min = 65536
[[suite]]
name = "toy"
format = "lines"
command = ["{{binary}}"]
cases = ["work", "sum"]
"""


def make_project(d: Path, method: str = "none", script: str = "build.sh") -> config.Config:
    for arm, shift in (("a", 3), ("b", 2)):
        (d / arm).mkdir(parents=True, exist_ok=True)
        (d / arm / "main.c").write_text(MAIN_C.replace("SHIFT", str(shift)))
    (d / "build.sh").write_text(BUILD_SH)
    (d / "nopad.sh").write_text(NOPAD_SH)
    (d / "fail.sh").write_text(FAIL_SH)
    (d / "placemat.toml").write_text(TOML.format(cc=CC or "cc", method=method, script=script))
    return config.load(d / "placemat.toml")


def quiet(*a):
    pass


# ---- the data log's summary ----------------------------------------------------------------------

SUMMARY = """placemat-log 1 pid=4242
config colour=640 step_mode=hashed step=0x00000000deadbeef unit=64 colour_span=16384 step_span=16384 min=65536 addrlog=2
base 0x300000000
region 0x300000000 1048576
region 0x310000000 2097152
khash 0x0123456789abcdef
coloured 7
wrapped 2
block 0x300000040 131072 0 64
block 0x2ff000000 65536 -16 128
block 0x400000000 70000 - -
free 0x300000040
end
placemat-log 1 pid=4243
config colour=0 step_mode=none step=- unit=64 colour_span=16384 step_span=16384 min=65536 addrlog=0
base 0x500000000
region 0x500000000 65536
khash 0x0000000000000001
coloured 1
wrapped 0
end
"""


class TestReadLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_summary(self):
        self.path.write_text(SUMMARY)
        self.assertEqual(read_log(self.path), {"base": 0x300000000, "region": 0x300000000,
                                               "khash": 0x0123456789abcdef, "coloured": 7, "wrapped": 2})

    def test_first_process_only(self):
        # a first block without base or regions: nothing leaks in from the second process
        first = ("placemat-log 1 pid=1\nconfig colour=0 step_mode=linear step=64 unit=64 colour_span=16384 "
                 "step_span=16384 min=65536 addrlog=0\nkhash 0x00000000000000ff\ncoloured 0\nwrapped 0\nend\n")
        self.path.write_text(first + SUMMARY)
        self.assertEqual(read_log(self.path), {"khash": 0xff, "coloured": 0, "wrapped": 0})

    def test_stock_setting_summary(self):
        # (0, off) with only the hook's counters, no address logging
        self.path.write_text("placemat-log 1 pid=9\nconfig colour=0 step_mode=none step=- unit=64 colour_span=16384 "
                             "step_span=16384 min=65536 addrlog=0\nbase 0x104000000\nkhash 0x0000000000000000\n"
                             "coloured 3\nwrapped 0\nend\n")
        self.assertEqual(read_log(self.path), {"base": 0x104000000, "khash": 0, "coloured": 3, "wrapped": 0})

    def test_missing_or_empty(self):
        self.assertEqual(read_log(self.path), {})
        self.path.write_text("")
        self.assertEqual(read_log(self.path), {})

    def test_unknown_keywords_ignored(self):
        self.path.write_text("placemat-log 2 pid=1\nfuture 1 2 3\n\ncoloured 4\nend\n")
        self.assertEqual(read_log(self.path), {"coloured": 4})

    def test_covariate_choice(self):
        r = Runner.__new__(Runner)
        cov = read_log_text(SUMMARY)
        for name, want in (("base", 0x300000000), ("khash", 0x0123456789abcdef), ("none", None)):
            r.cfg = mock.Mock(covariate=name)
            self.assertEqual(r.covariate(cov), want, name)
        r.cfg = mock.Mock(covariate="base")
        self.assertEqual(r.covariate({"base": 5}), 5)
        self.assertIsNone(r.covariate({}))


def read_log_text(text: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log"
        p.write_text(text)
        return read_log(p)


# ---- planning cases ------------------------------------------------------------------------------

class TestPlanCases(unittest.TestCase):
    def test_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = make_project(Path(d))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                p = plan_cases(cfg, ["work", " toy: sum ", "", "nosuch", "toy: anything"])
        self.assertIsInstance(p, Plan)
        self.assertEqual(p.cases, {"toy: work": ("toy", "work"), "toy: sum": ("toy", "sum"),
                                   "toy: anything": ("toy", "anything")})
        self.assertEqual(p.reps, {"toy": {"work": None, "sum": None}})
        self.assertIn("no case 'nosuch'", err.getvalue())


# ---- builds -------------------------------------------------------------------------------------

class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pad_source(self):
        s = build.pad_source(3508)
        self.assertIn(build.PAD_SYMBOL, s)
        self.assertIn('".space 3508"', s)
        self.assertIn("__attribute__((used))", s)

    def test_build_failure_is_clear(self):
        cfg = make_project(self.dir, script="fail.sh")
        B = build.Builder(cfg, self.dir / "work")
        arm = B.arm("base", str(self.dir / "a"))
        with self.assertRaises(SystemExit) as cm:
            B.binary(arm, 3508)
        msg = str(cm.exception.code)
        self.assertIn("build of base p3508 failed", msg)
        self.assertIn("fail.sh", msg)
        self.assertIn("main.c:3: error: boom", msg)

    def test_not_a_source(self):
        cfg = make_project(self.dir)
        B = build.Builder(cfg, self.dir / "work")
        with self.assertRaises(SystemExit) as cm:
            B.arm("base", str(self.dir / "nonexistent"))
        self.assertIn("neither a directory nor a git revision", str(cm.exception.code))

    def test_arm_cache_key_follows_inputs(self):
        cfg = make_project(self.dir)
        B = build.Builder(cfg, self.dir / "work")
        a1 = B.arm("base", str(self.dir / "a"))
        self.assertEqual(B.arm("x", str(self.dir / "a")).tree, a1.tree)
        self.assertNotEqual(B.arm("y", str(self.dir / "b")).tree, a1.tree)

    @unittest.skipUnless(CC and HAS_NM, "needs a C compiler and nm")
    def test_pad_symbol_verified(self):
        cfg = make_project(self.dir, script="nopad.sh")
        B = build.Builder(cfg, self.dir / "work")
        arm = B.arm("base", str(self.dir / "a"))
        stock = B.binary(arm, None)                 # no pad wanted: fine
        self.assertTrue(stock.exists())
        with self.assertRaises(SystemExit) as cm:
            B.binary(arm, 3508)
        self.assertIn(f"no {build.PAD_SYMBOL}", str(cm.exception.code))

    @unittest.skipUnless(CC and HAS_NM, "needs a C compiler and nm")
    def test_padded_build_and_cache(self):
        cfg = make_project(self.dir)
        B = build.Builder(cfg, self.dir / "work")
        arm = B.arm("base", str(self.dir / "a"))
        p = B.binary(arm, 1872)
        self.assertTrue(build.has_symbol(p, build.PAD_SYMBOL))
        self.assertFalse(build.has_symbol(B.binary(arm, None), build.PAD_SYMBOL))
        self.assertEqual(p.parent.name, "p1872")
        mtime = p.stat().st_mtime_ns
        self.assertEqual(B.binary(arm, 1872), p)    # cached
        self.assertEqual(p.stat().st_mtime_ns, mtime)
        self.assertEqual(B.binary(arm, 1872, hooked=True).parent.name, "p1872-h")


# ---- end to end ----------------------------------------------------------------------------------

@unittest.skipUnless(CC and HAS_NM, "needs a C compiler and nm")
class TestEndToEnd(unittest.TestCase):
    """Two arms of a tiny C program, 2 pads, 2 rounds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def run_toy(self, method: str, data: str):
        cfg = make_project(self.dir, method=method)
        D = Design(seed=0, data=data, start=2, cap=2)
        R = Runner(cfg, self.dir / "work", [("base", str(self.dir / "a")), ("branch", str(self.dir / "b"))], D,
                   rounds=2, say=quiet)
        out = self.dir / "runs" / "t.raw.json"
        raw = R.run(["work", "toy: sum"], out)
        return D, raw, out

    def check_shape(self, D, raw, out, slots):
        self.assertTrue(out.exists())
        disk = json.loads(out.read_text())
        self.assertEqual(disk["version"], 1)
        self.assertEqual(disk["project"], "toy")
        self.assertEqual(disk["design"], D.to_json())
        self.assertEqual([a["name"] for a in disk["arms"]], ["base", "branch"])
        self.assertEqual(disk["rounds"], 2)
        self.assertEqual(disk["plan"], {"toy: work": ["toy", "work"], "toy: sum": ["toy", "sum"]})
        self.assertEqual(disk["K"], {"toy: work": 2, "toy: sum": 2})
        self.assertEqual(disk["series"], {})
        for key in disk["plan"]:
            for a in ("base", "branch"):
                t = disk["times"][key][a]
                self.assertEqual(set(t), slots, (key, a))
                for s, xs in t.items():
                    self.assertEqual(len(xs), 2, (key, a, s))
                    for v, _ in xs:
                        self.assertIsInstance(v, float)
                        self.assertGreater(v, 0)
        # the first case's warm-up, a discarded execution per slot and arm, then the rounds
        self.assertEqual(disk["log"]["runs"], 1 + len(slots) * 2 + 2 * len(slots) * 2)
        self.assertEqual(disk["log"]["batches"], [[2, 2]])
        # the raw file reanalyses
        md = report.markdown(report.load_raw(out))
        self.assertIn("`toy: work`", md)
        self.assertIn("`toy: sum`", md)
        return disk

    def check_geometry(self, D, disk):
        geo = disk["geometry"]
        for a in ("base", "branch"):
            self.assertEqual(set(geo[a]), {"0", "1", "stock"})
            self.assertEqual(geo[a]["0"]["pad"], D.pad(0))
            self.assertIsNone(geo[a]["stock"]["pad"])
            for j in ("0", "1"):
                b = Path(geo[a][j]["path"])
                self.assertTrue(build.has_symbol(b, build.PAD_SYMBOL), (a, j))
            self.assertFalse(build.has_symbol(Path(geo[a]["stock"]["path"]), build.PAD_SYMBOL))
            self.assertEqual(geo[a]["0"]["shift"], 0)
            # the pad sits before the code under study: it moves by the difference in pad sizes
            s0, s1 = (build.nm_symbols(Path(geo[a][j]["path"])) for j in ("0", "1"))
            # (exactly on Mach-O with clang; GCC on ELF aligns the next function, to 16 bytes on aarch64)
            self.assertLess(abs(s1["work"] - s0["work"] - (D.pad(1) - D.pad(0))), 16, a)
            if sys.platform == "darwin":
                self.assertEqual(s1["work"] - s0["work"], D.pad(1) - D.pad(0), a)
                # Mach-O: every function follows the pad. (On ELF the C runtime's functions and GCC's
                # .text.startup come before it, so the most common shift there is 0.)
                self.assertEqual(geo[a]["1"]["shift"], D.pad(1) - D.pad(0), a)

    def test_no_data(self):
        D, raw, out = self.run_toy("none", "none")
        disk = self.check_shape(D, raw, out, {"0.s", "1.s", "stock"})
        self.assertEqual(disk["data_method"], "none")
        for key in disk["plan"]:
            for a in ("base", "branch"):
                self.assertTrue(all(c is None for xs in disk["times"][key][a].values() for _, c in xs))
        self.check_geometry(D, disk)

    def test_interposer(self):
        D, raw, out = self.run_toy("interposer", "both")
        slots = {f"{j}.{k}" for j in (0, 1) for k in "sct"} | {"stock"}
        disk = self.check_shape(D, raw, out, slots)
        self.assertEqual(disk["data_method"], "interposer")
        self.assertEqual(disk["covariate"], "khash")
        self.assertFalse(disk["stock_placement"])
        self.assertGreater(disk["log"]["coloured"], 0)          # the 256 KB buffer, at every data setting
        for key in disk["plan"]:
            for a in ("base", "branch"):
                for s, xs in disk["times"][key][a].items():
                    for _, c in xs:
                        if s == "stock":
                            self.assertIsNone(c)                # the true stock build: no interposer, no log
                        else:
                            self.assertIsInstance(c, int, (key, a, s))
        self.check_geometry(D, disk)


# ---- the runner's logic, with stand-in builds ----------------------------------------------------

FAKE_BIN = """#!{python}
# a stand-in benchmark binary: reports the data setting it was given, and writes a data-log summary
import os, random, sys
e = os.environ
cases = e.get("PLACEMAT_CASES", "").split(",")
arm = {arm!r}
if "work" in cases:
    print("work", 1000.0 * (1.2 if arm == "branch" else 1.0) * random.uniform(1.0, 1.05), 10)
if "colour" in cases:
    print("colour", int(e.get("PLACEMAT_COLOUR", "0")) + 1, 1)
if "step" in cases:
    print("step", int(e.get("PLACEMAT_STEP", "0")) + 1, 1)
if "env" in cases:
    print("env", 1 + ("PLACEMAT_LOG" in e) + 2 * ("PLACEMAT_UNIT" in e) + 4 * ("PLACEMAT_STEP_MODE" in e), 1)
if e.get("PLACEMAT_LOG"):
    with open(e["PLACEMAT_LOG"], "a") as f:
        f.write("placemat-log 1 pid=1\\nconfig colour=0\\nbase 0x1000\\nregion 0x{region:x} 65536\\n"
                "khash 0x00000000000000aa\\ncoloured 2\\nwrapped 1\\nend\\n")
"""

FAKE_K = """#!{python}
# a stand-in K interpreter: each out["name";reps;...] line takes 0.5 us per repetition plus 3 us
import re, sys
for l in open(sys.argv[1]):
    m = re.match(r'out\\["([^"]+)";(\\d+);', l)
    if m:
        print(m.group(1), 0.5 * int(m.group(2)) + 3)
"""

FAKE_TOML = """
[project]
name = "fake"
[data]
method = "{method}"
[stats]
target = 0.0
[[suite]]
name = "s"
format = "lines"
command = ["{{binary}}"]
cases = ["work", "colour", "step", "env"]
[[suite]]
name = "mb"
format = "k-out"
script = "mb.k"
command = ["{{binary}}", "{{script}}"]
"""


class TestRunnerLogic(unittest.TestCase):
    """Runner.run with the builds replaced by scripts: slots, settings, logs, anchors, batches, series."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = d = Path(self.tmp.name).resolve()
        self.built = []
        for arm, region in (("base", 0x300000000), ("branch", 0x400000000)):
            (d / arm).mkdir()
            for kind, text in (("bin", FAKE_BIN), ("k", FAKE_K)):
                p = d / f"{arm}.{kind}"
                p.write_text(text.format(python=sys.executable, arm=arm, region=region))
                p.chmod(0o755)
        (d / "mb.k").write_text('/ microbench\nout["fast";100;{+/x}]\nout["tiny";4;{x}]\nout["other";9;{x}]\n')

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, D: Design, method="hook", k=False, extra="", say=quiet):
        (self.dir / "placemat.toml").write_text(FAKE_TOML.format(method=method) + extra)
        cfg = config.load(self.dir / "placemat.toml")
        built, d = self.built, self.dir

        class FakeBuilder:
            def __init__(self, cfg, work):
                pass

            def arm(self, name, source):
                return build.Arm(name, source, Path(source), name)

            def binary(self, arm, pad, hooked=False):
                built.append((arm.name, pad, hooked))
                return d / f"{arm.name}.{'k' if k else 'bin'}"

        with mock.patch("placemat.runner.Builder", FakeBuilder):
            R = Runner(cfg, d / "work", [("base", str(d / "base")), ("branch", str(d / "branch"))], D, rounds=2,
                       say=say)
        return R

    def test_settings_logs_and_covariates(self):
        D = Design(seed=0, data="both", step_mode="linear", start=2, cap=2)
        R = self.runner(D)
        raw = R.run(["s: work", "colour", "step", "env"], self.dir / "out" / "r.json")
        slots = {f"{j}.{k}" for j in (0, 1) for k in "sct"} | {"stock"}
        self.assertEqual(raw["K"], {"s: work": 2, "s: colour": 2, "s: step": 2, "s: env": 2})
        for a in ("base", "branch"):
            t = raw["times"]
            self.assertEqual(set(t["s: colour"][a]), slots)
            for j in (0, 1):
                # each slot ran at its own data setting
                self.assertEqual({v for v, _ in t["s: colour"][a][f"{j}.c"]}, {D.colour(j) + 1})
                self.assertEqual({v for v, _ in t["s: colour"][a][f"{j}.s"]}, {1})
                self.assertEqual({v for v, _ in t["s: colour"][a][f"{j}.t"]}, {1})
                self.assertEqual({v for v, _ in t["s: step"][a][f"{j}.t"]}, {D.step(j) + 1})
                self.assertEqual({v for v, _ in t["s: step"][a][f"{j}.c"]}, {1})
                # a log and the data environment at every setting, the step mode only with a step
                self.assertEqual({v for v, _ in t["s: env"][a][f"{j}.s"]}, {4})
                self.assertEqual({v for v, _ in t["s: env"][a][f"{j}.t"]}, {8})
            # the true stock build: no data environment, no log, no covariate
            self.assertEqual(t["s: env"][a]["stock"], [(1.0, None)] * 2)
            # the covariate (cfg covariate 'base' under the hook: the first region)
            region = 0x300000000 if a == "base" else 0x400000000
            for s in slots - {"stock"}:
                self.assertEqual({c for _, c in t["s: work"][a][s]}, {region}, s)
        self.assertTrue(all(1200.0 <= v <= 1260.0 for v, _ in raw["times"]["s: work"]["branch"]["0.s"]))
        # hooked pad builds; the stock builds plain
        self.assertEqual(set(self.built), {(a, p, True) for a in ("base", "branch") for p in (D.pad(0), D.pad(1))}
                         | {("base", None, False), ("branch", None, False)})
        runs = 1 + 7 * 2 + 2 * 7 * 2
        self.assertEqual(raw["log"]["runs"], runs)
        self.assertEqual(raw["log"]["coloured"], 2 * (runs - 2 - 2 * 2))    # every run but the stock builds' 6
        self.assertEqual(raw["log"]["wrapped"] * 2, raw["log"]["coloured"])
        self.assertEqual(raw["covariate"], "base")
        self.assertTrue(raw["stock_placement"])

    def test_batches_and_anchors(self):
        D = Design(seed=0, data="none", start=2, batch_step=1, cap=4)
        raw = self.runner(D, method="none").run(["work"], self.dir / "out" / "r.json")
        self.assertEqual(raw["K"], {"s: work": 4})
        self.assertEqual(set(raw["times"]["s: work"]["base"]), {"0.s", "1.s", "stock", "2.s", "a1", "3.s", "a2"})
        self.assertEqual([list(b) for b in raw["log"]["batches"]], [[2, 1], [3, 1], [4, 1]])
        self.assertEqual(raw["data_method"], "none")
        self.assertTrue(all(not h for _, _, h in self.built))       # no hook without a data axis
        # epoch timestamps: each batch's timing, each round, each timed execution (parallel to the times)
        lg = raw["log"]
        self.assertTrue(raw["complete"])
        self.assertIsNone(raw["partial"])
        self.assertEqual([len(x) for x in lg["round_t"]], [2, 2, 2])
        self.assertEqual(len(lg["batch_t"]), 3)
        for (b0, b1), rs in zip(lg["batch_t"], lg["round_t"]):
            self.assertTrue(all(b0 <= s0 <= s1 <= b1 for s0, s1 in rs))
        self.assertGreater(lg["batch_t"][0][0], 1.7e9)
        et = raw["exec_t"]["s"]
        for a in ("base", "branch"):
            self.assertEqual({sl: len(v) for sl, v in et[a].items()}, {sl: 2 for sl in raw["times"]["s: work"][a]})
        r1 = lg["round_t"][1]
        self.assertTrue(all(r1[0][0] <= t <= r1[1][1] for t in et["base"]["2.s"] + et["base"]["a1"]))

    # The zstd and Lua surveys lost runs that died mid-way: the raw file is saved after every round.
    def test_raw_kept_when_killed(self):
        D = Design(seed=0, data="none", start=2, batch_step=1, cap=4)
        R = self.runner(D, method="none")
        real, n = R.execute, [0]

        def dies(*a, **kw):
            n[0] += 1
            if n[0] > 19 + 4 + 4 + 2:       # batch 0 (19 executions), batch 1's discarded 4 and first round, half its second
                raise _Stop
            return real(*a, **kw)
        R.execute = dies
        out = self.dir / "out" / "r.raw.json"
        with self.assertRaises(_Stop):
            R.run(["work"], out)
        raw = report.load_raw(out)
        self.assertFalse(raw["complete"])
        self.assertEqual(raw["K"], {"s: work": 2})
        self.assertEqual(raw["partial"]["batch"], 1)
        self.assertEqual(raw["partial"]["rounds"], 1)
        self.assertEqual(len(raw["times"]["s: work"]["base"]["2.s"]), 1)      # kept, not analysed
        self.assertEqual([len(x) for x in raw["log"]["round_t"]], [2, 1])
        self.assertFalse(out.with_suffix(".tmp").exists())
        md = report.markdown(raw)
        self.assertIn("**Incomplete run**", md)
        self.assertIn("batch 1 (pads 2-2, 1 case) stopped after 1 of 2 rounds", md)
        self.assertEqual(report.compute(raw)["res"]["s: work"]["m"], 2)

    # Linux survey: with a [target] prefix, ps would watch the local client (limactl), not the benchmark.
    def test_memory_limit_off_on_target(self):
        D = Design(seed=0, data="none", start=2, cap=2)
        said = []
        R = self.runner(D, method="none", extra='[run]\nmemory_mb = 512\n[target]\nprefix = ["limactl", "shell", "vm"]\n',
                        say=lambda *a: said.append(" ".join(map(str, a))))
        self.assertEqual(R.memory_mb, 0)
        self.assertEqual(len(said), 1)
        self.assertIn("memory_mb = 512 is ignored with a [target] prefix", said[0])
        self.assertIn("limactl", said[0])
        self.assertEqual(self.runner(D, method="none", extra="[run]\nmemory_mb = 512\n").memory_mb, 512)

    # Regression: the last, partial batch Design.batch_pads() defines is timed (was skipped).
    def test_partial_last_batch(self):
        D = Design(seed=0, data="none", start=2, batch_step=3, cap=4)
        self.assertEqual(list(D.batch_pads(1)), [2, 3])
        raw = self.runner(D, method="none").run(["work"], self.dir / "out" / "r.json")
        self.assertEqual(raw["K"], {"s: work": 4})

    def test_series(self):
        D = Design(seed=0, data="none", start=2, cap=2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            raw = self.runner(D, method="none", k=True).run(["fast", "tiny", "mb: other"], self.dir / "out" / "r.json")
        # fast enough and reps >= 8: a regression series of 1/8 .. 1 of the count
        self.assertEqual(raw["series"], {"mb: fast": [12, 25, 50, 100], "mb: other": [1, 2, 4, 9]})
        t = raw["times"]
        # the slope removes the per-execution overhead: 0.5 us per repetition times the full count
        for v, _ in t["mb: fast"]["base"]["0.s"]:
            self.assertAlmostEqual(v, 50.0)
        for v, _ in t["mb: other"]["base"]["1.s"]:
            self.assertAlmostEqual(v, 4.5)
        # under 8 repetitions: timed once at its own count
        self.assertEqual({v for v, _ in t["mb: tiny"]["branch"]["stock"]}, {5.0})

    def test_no_cases(self):
        D = Design(seed=0, data="none", start=2, cap=2)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.runner(D, method="none").run(["nosuch"], self.dir / "out" / "r.json")


# ---- the run command -----------------------------------------------------------------------------

class TestRunCommand(unittest.TestCase):
    """cmd_run builds the Design from the command line and the configuration (the Runner is replaced)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        make_project(self.dir, method="hook")
        self.seen = {}

    def tearDown(self):
        self.tmp.cleanup()

    def designed(self, *argv) -> Design:
        seen = self.seen

        class FakeRunner:
            def __init__(self, cfg, work, arms, design, rounds=None, say=None):
                seen.update(cfg=cfg, arms=arms, D=design, rounds=rounds)

            def run(self, cases, out):
                seen["cases"] = cases
                raise _Stop

        args = [argv[0], "--config", str(self.dir / "placemat.toml"), "--arm", f"base={self.dir / 'a'}",
                "--arm", str(self.dir / "b"), "--cases", "work,sum", *argv[1:]]
        with mock.patch("placemat.runner.Runner", FakeRunner):
            with self.assertRaises(_Stop):
                cli.main(args)
        return seen["D"]

    def test_run_defaults(self):
        D = self.designed("run")
        self.assertEqual(D.data, "both")
        self.assertEqual((D.start, D.batch_step, D.cap), (4, 2, 12))
        self.assertEqual([(x["name"], x["source"]) for x in self.seen["arms"]],
                         [("base", str(self.dir / "a")), ("arm1", str(self.dir / "b"))])
        self.assertEqual(self.seen["cases"], ["work", "sum"])

    def test_run_options(self):
        D = self.designed("run", "--data", "step", "--step-mode", "linear", "--pads", "100,200", "--start", "3",
                          "--step", "1", "--cap", "5", "--seed", "4", "--rounds", "3")
        self.assertEqual((D.data, D.step_mode, D.pads, D.start, D.batch_step, D.cap, D.seed),
                         ("step", "linear", [100, 200], 3, 1, 5, 4))
        self.assertEqual(self.seen["rounds"], 3)

    def test_check_with_start(self):
        D = self.designed("check", "--start", "2")
        self.assertEqual((D.start, D.cap), (2, 2))

    # Regression: `placemat check` is the first batch only, also without --start.
    def test_check_is_first_batch_only(self):
        D = self.designed("check")
        self.assertEqual(D.cap, D.start)

    # Regression: `placemat survey` varies both data kinds whatever the config's default.
    def test_survey_varies_both(self):
        (self.dir / "placemat.toml").write_text((self.dir / "placemat.toml").read_text()
                                                .replace('min = 65536', 'min = 65536\nvary = "step"'))
        D = self.designed("survey")
        self.assertEqual(D.data, "both")

    def test_needs_two_arms(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            cli.main(["run", "--config", str(self.dir / "placemat.toml"), "--arm", str(self.dir / "a"),
                      "--cases", "work"])
        self.assertIn("at least two --arm", str(cm.exception.code))


class _Stop(Exception):
    pass


if __name__ == "__main__":
    unittest.main()
