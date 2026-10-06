"""placemat.lock: the probe, the gate (two passing readings in a row; what it records), the lock command's guard
(no orphan when placemat dies), and the probe on a target (DESIGN §11 item 3, P012)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from placemat import lock

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


class Clock:
    """A fake time.time / time.sleep pair: sleeping advances the clock."""
    def __init__(self):
        self.now = 1e9
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


class TestProbe(unittest.TestCase):
    def test_summary_is_robust_to_two_stalls(self):
        ts = [10.0] * 21
        ts[2], ts[5] = 15.0, 16.0                   # two stalls, both among the first nine timings
        d = lock.summarise(ts)
        self.assertEqual(d["median_ms"], 10.0)
        self.assertEqual(d["spread"], 0.0)          # the middle half does not see them
        self.assertAlmostEqual(d["spread_old"], 0.5)  # the old measure did: second-largest 15 - second-smallest 10
        self.assertEqual(d["n"], 21)

    def test_spread_is_scaled_interquartile(self):
        ts = [float(x) for x in range(1, 22)]       # 1..21: q25 6, median 11, q75 16
        self.assertAlmostEqual(lock.summarise(ts)["spread"], lock.SCALE * 10 / 11)

    def test_probe_detail_and_probe(self):
        with mock.patch.object(lock, "_timings", return_value=[10.0] * 21):
            d = lock.probe_detail()
            self.assertEqual(set(d), {"median_ms", "spread", "spread_old", "n"})
            self.assertEqual(lock.probe(), (10.0, 0.0))

    def test_target_probe(self):
        # the remote program run through a prefix that runs it here: `env python3 - 21`
        d = lock.probe_detail(k=5, target_prefix=["env"])
        self.assertEqual(d["where"], "target", d)
        self.assertEqual(d["n"], 5)
        self.assertGreater(d["median_ms"], 0)
        self.assertIn("target_load", d)

    def test_target_probe_failure_falls_back_to_host(self):
        with mock.patch.object(lock, "_timings", return_value=[10.0] * 21):
            d = lock.probe_detail(target_prefix=["false"])
        self.assertEqual(d["where"], "host")
        self.assertIn("target probe failed", d["error"])

    def test_target_baseline_is_separate(self):
        self.assertEqual(lock.baseline_path(None), lock.BASELINE)
        a, b = lock.baseline_path(["limactl", "shell", "a", "--"]), lock.baseline_path(["limactl", "shell", "b", "--"])
        self.assertNotEqual(a, lock.BASELINE)
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith(".json"))


def reading(ok, spread=0.01):
    return ok, {"median_ms": 14.0, "spread": spread, "spread_old": spread * 2, "n": 21}


class TestGate(unittest.TestCase):
    def run_hold(self, seq, cmd=(), wait=1000.0, take_delay=0.0):
        clock, said, rec, calls = Clock(), [], [], []
        seq = list(seq)

        def quiet(*a):
            calls.append(clock.now)
            return seq.pop(0)

        def take(c):
            clock.now += take_delay
            return mock.Mock()

        with mock.patch.object(lock, "_quiet", side_effect=quiet), \
                mock.patch.object(lock.time, "time", clock.time), mock.patch.object(lock.time, "sleep", clock.sleep), \
                mock.patch.object(lock, "_take", side_effect=take) as tk, mock.patch.object(lock, "_release") as rl, \
                mock.patch.object(lock, "probe_detail", return_value=reading(True)[1]), \
                mock.patch.dict(os.environ, {"PLACEMAT_LOCKED": ""}):
            with lock.hold(list(cmd), max_noise=0.05, wait=wait, say=said.append, record=rec):
                pass
        self.assertEqual(seq, [], "every planned reading taken")
        return rec, said, clock, tk, rl, calls

    def test_two_passes_in_a_row_before_the_lock(self):
        rec, said, clock, tk, _, calls = self.run_hold(
            [reading(True), reading(False, 0.3), reading(True), reading(True), reading(True)], cmd=["lockcmd"])
        self.assertEqual(tk.call_count, 1)
        r = rec[0]
        self.assertEqual((r["passes"], r["went_ahead"]), (3, False))
        self.assertEqual(said, [])
        self.assertEqual(clock.slept, [lock.GAP, 15, lock.GAP])  # 5 s after a pass, 15 s after a failure
        for k in ("start", "t", "probe_ms", "spread", "spread_old", "waited_s", "load", "went_ahead", "passes",
                  "end_spread"):
            self.assertIn(k, r)
        self.assertEqual(r["t"], round(clock.now, 3))
        self.assertEqual(r["waited_s"], round(lock.GAP + 15 + lock.GAP))

    def test_noisy_under_the_lock_releases_and_retries(self):
        rec, _, _, tk, rl, _ = self.run_hold(
            [reading(True), reading(True), reading(False, 0.2), reading(True), reading(True), reading(True)],
            cmd=["lockcmd"])
        self.assertEqual(tk.call_count, 2)
        self.assertEqual(rec[0]["passes"], 3)
        self.assertFalse(rec[0]["went_ahead"])

    def test_slow_lock_needs_fresh_readings(self):
        # the lock took 60 s: the two readings before it are stale, so two more are taken under it
        rec, _, clock, _, _, calls = self.run_hold([reading(True)] * 4, cmd=["lockcmd"], take_delay=60)
        self.assertEqual(rec[0]["passes"], 2)
        self.assertEqual(calls[3] - calls[2], lock.GAP)

    def test_goes_ahead_after_the_wait_and_says_so(self):
        rec, said, _, _, _, _ = self.run_hold([reading(False, 0.2)], wait=0)
        self.assertTrue(rec[0]["went_ahead"])
        self.assertEqual(rec[0]["passes"], 0)
        self.assertEqual(rec[0]["spread"], 0.2)
        self.assertTrue(said and "going ahead" in said[0])

    def test_ungated_takes_one_reading(self):
        rec = []
        with mock.patch.object(lock, "probe_detail", return_value=reading(True)[1]) as pd:
            with lock.hold([], record=rec):
                pass
        self.assertEqual(pd.call_count, 2)          # start and end
        self.assertEqual((rec[0]["went_ahead"], rec[0]["passes"]), (False, 0))
        with mock.patch.object(lock, "probe_detail") as pd:
            with lock.hold([]):                     # nothing to record: no probe at all (a build's shared hold)
                pass
        pd.assert_not_called()


HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from placemat import lock
with lock.hold(eval(sys.argv[2])):
    print("holding", flush=True)
    time.sleep(60)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TestGuard(unittest.TestCase):
    """The lock command must not outlive placemat: a holder SIGTERMed while holding, or while still waiting
    for the lock, leaves no lock process behind."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.pids = []

    def tearDown(self):
        for pid in self.pids:                       # only the PIDs this test recorded
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        for f in self.d.iterdir():
            f.unlink()
        self.d.rmdir()

    def lock_cmd(self, held: bool) -> list[str]:
        pf = str(self.d / "lockpid")
        body = ("print('held', flush=True); sys.stdin.read()" if held else "time.sleep(60)")
        return [PY, "-c", f"import os, sys, time; open({pf!r}, 'w').write(str(os.getpid())); {body}"]

    def start(self, held: bool):
        env = {k: v for k, v in os.environ.items() if k != "PLACEMAT_LOCKED"}
        p = subprocess.Popen([PY, "-c", HOLDER, str(ROOT), repr(self.lock_cmd(held))], stdout=subprocess.PIPE, text=True,
                             env=env)
        self.pids.append(p.pid)
        pf = self.d / "lockpid"
        t = time.time()
        while not (pf.exists() and pf.read_text()) and time.time() - t < 20:
            time.sleep(0.05)
        child = int(pf.read_text())
        self.pids.append(child)
        if held:
            self.assertEqual(p.stdout.readline().strip(), "holding")
        return p, child

    def gone_within(self, pid: int, s: float) -> bool:
        t = time.time()
        while time.time() - t < s:
            if not alive(pid):
                return True
            time.sleep(0.1)
        return False

    def test_sigterm_while_holding(self):
        p, child = self.start(held=True)
        os.kill(p.pid, signal.SIGTERM)
        p.wait(10)
        p.stdout.close()
        self.assertTrue(self.gone_within(child, 10), "the lock command outlived its holder")

    def test_sigterm_while_waiting_for_the_lock(self):
        p, child = self.start(held=False)           # the lock command never says it holds the lock
        os.kill(p.pid, signal.SIGTERM)
        p.wait(10)
        p.stdout.close()
        self.assertTrue(self.gone_within(child, 20), "a waiting lock command outlived its holder")

    def test_normal_release(self):
        with mock.patch.dict(os.environ, {"PLACEMAT_LOCKED": ""}), lock.hold(self.lock_cmd(True)):
            child = int((self.d / "lockpid").read_text())
            self.pids.append(child)
            self.assertTrue(alive(child))
        self.assertTrue(self.gone_within(child, 5))

    def test_lock_command_that_fails(self):
        with self.assertRaisesRegex(RuntimeError, r"exited \(3\)"), mock.patch.dict(os.environ, {"PLACEMAT_LOCKED": ""}):
            with lock.hold([PY, "-c", "import sys; sys.exit(3)"]):
                pass


if __name__ == "__main__":
    unittest.main()
