"""Taking the machine for timing (DESIGN §11 item 3).

placemat takes the project's lock command from its configuration rather than shipping one: the
command must take the lock, print a line on stdout once it holds it, and release it when its stdin
closes. Optionally placemat also waits for the 1-minute load average to fall below a limit, and for
a noise probe to come out quiet: a fixed CPU workload timed 21 times, whose spread (below) must be at
most max_noise in two readings in a row, a few seconds apart. The load average lags and misses work
that ignores the lock; the probe sees what a benchmark would see (on Apple Silicon, for example, being
moved to an efficiency core while the performance cores are busy). Probe results are recorded with
the run, so a run that had to go ahead on a noisy machine says so.

The spread is judged on the middle half: SCALE * (q75 - q25) / median. The probe used to take 9
timings and judge (second-largest - second-smallest) / median ("spread_old", still recorded): two
stalled timings out of nine failed it, and on an otherwise idle M2 it jumped between 2% and 46% from
one reading to the next while the median held at 13.5-13.8 ms (P012). SCALE puts the new measure on
the old one's scale on a quiet machine, so a limit means what it meant there. Calibration (M2, 2026-10-07,
readings back to back): at load ~3, 60 readings, old median 3.8% (p90 5.6%, max 14.7%), new 3.5% (p90
4.8%, max 8.1%), passing 5% 49 and 55 times; at load 9-16, 30 readings, the new measure passed 5% once,
10% four times. In the Lima VM (host load ~3) old 4.6% (max 8.8%), new 3.6% (max 5.5%).

With a target (a VM, say), the probe runs there, through the target's prefix, as a small Python
program on stdin; its readings keep their own baseline.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time

K = 21              # timings per reading (~0.3 s on an M2)
OLD_K = 9           # the old reading: the first 9 timings, judged on second-largest - second-smallest
SCALE = 1.2         # the interquartile spread on the old measure's scale: old / IQR on a quiet M2 (P012)
GAP = 5.0           # seconds between the readings that must pass in a row
NEED = 2            # passing readings in a row before timing


def _work(n: int = 200000) -> int:
    s = 0
    for i in range(n):
        s += i * i ^ (i >> 3)
    return s


def _timings(k: int) -> list[float]:
    ts = []
    for _ in range(k):
        t = time.perf_counter_ns()
        _work()
        ts.append((time.perf_counter_ns() - t) / 1e6)
    return ts


# The same workload and timing, run on a target through its prefix (stdin, so no quoting is involved):
# prints the timings and the target's load as JSON. Must stay valid on old Pythons (3.6+).
REMOTE = """
import json, os, sys, time
def work(n=200000):
    s = 0
    for i in range(n):
        s += i * i ^ (i >> 3)
    return s
ts = []
for _ in range(int(sys.argv[1])):
    t = time.perf_counter_ns()
    work()
    ts.append((time.perf_counter_ns() - t) / 1e6)
print(json.dumps({"ts": ts, "load": os.getloadavg()[0]}))
"""


def _remote(prefix: list[str], k: int) -> tuple[list[float], float]:
    r = subprocess.run(list(prefix) + ["python3", "-", str(k)], input=REMOTE, capture_output=True, text=True,
                       timeout=60)
    d = json.loads(r.stdout.strip().splitlines()[-1])
    return [float(x) for x in d["ts"]], float(d["load"])


def _q(xs: list[float], p: float) -> float:
    """Quantile p of sorted xs, interpolated linearly (inclusive)."""
    h = (len(xs) - 1) * p
    i = int(h)
    return xs[i] if i + 1 >= len(xs) else xs[i] + (h - i) * (xs[i + 1] - xs[i])


def summarise(ts: list[float]) -> dict:
    """A reading's numbers from its timings (ms): median, the interquartile spread and the old one."""
    s = sorted(ts)
    med = _q(s, 0.5)
    old = sorted(ts[:OLD_K])
    return {"median_ms": med, "spread": SCALE * (_q(s, 0.75) - _q(s, 0.25)) / med,
            "spread_old": (old[-2] - old[1]) / _q(old, 0.5), "n": len(ts)}


def probe_detail(k: int = K, target_prefix: list[str] | None = None) -> dict:
    """One reading: {median_ms, spread, spread_old, n}; with target_prefix, taken on the target (plus
    `where` and the target's `load`), or on this machine with `where` = "host" and the error if it fails."""
    if not target_prefix:
        return summarise(_timings(k))
    try:
        ts, load = _remote(target_prefix, k)
        return {**summarise(ts), "where": "target", "target_load": load}
    except Exception as e:
        return {**summarise(_timings(k)), "where": "host", "error": f"target probe failed: {e!s:.200}"}


def probe(k: int = K, target_prefix: list[str] | None = None) -> tuple[float, float]:
    """(median ms, spread) of a fixed CPU workload timed k times."""
    d = probe_detail(k, target_prefix)
    return d["median_ms"], d["spread"]


# Runs the lock command and ties it to placemat: it reads its own stdin (placemat's pipe), and when that
# closes (placemat let go, or died by any signal) closes the lock command's stdin, the protocol's release;
# a lock command still waiting for the lock does not read its stdin, so after a grace period its whole
# process group (its own session) is ended. Its stdout goes to /dev/null: only the lock command's
# "held" line may reach placemat, and its exit must end placemat's read.
GUARD = """
import os, signal, subprocess, sys
c = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE)
n = os.open(os.devnull, os.O_WRONLY)
os.dup2(n, 1)
try:
    while os.read(0, 4096):
        pass
except OSError:
    pass
try:
    c.stdin.close()
except OSError:
    pass
try:
    sys.exit(c.wait(5))
except subprocess.TimeoutExpired:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.killpg(os.getpgrp(), signal.SIGTERM)
    try:
        c.wait(5)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    sys.exit(1)
"""


def _take(cmd):
    p = subprocess.Popen([sys.executable, "-c", GUARD, *cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        got = p.stdout.readline()
    except BaseException:
        _release(p)
        raise
    if not got:
        _release(p)
        raise RuntimeError(f"lock command {' '.join(cmd)} exited ({p.returncode}) without taking the lock")
    return p


def _release(p):
    if p:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(p.pid, 9)         # the guard leads its own session: its group is the lock command's
            p.wait()
        p.stdout.close()


BASELINE = os.path.join(os.path.expanduser("~"), ".cache", "placemat", "probe.json")


def baseline_path(target_prefix: list[str] | None = None) -> str:
    """The baseline file: this machine's, or one per target (a VM's probe has its own usual time)."""
    if not target_prefix:
        return BASELINE
    h = hashlib.sha1(" ".join(target_prefix).encode()).hexdigest()[:10]
    return BASELINE[:-len(".json")] + f"-target-{h}.json"


def baseline(record: float | None = None, path: str | None = None) -> float | None:
    """The probe's usual time on this machine: the 10th percentile of its last 200 readings (kept in
    ~/.cache/placemat/probe.json). A spread alone misses a machine that is uniformly slow (a whole batch
    ran ~35% slow while the probe's spread passed, in the Lua survey); the level catches it."""
    path = path or BASELINE
    try:
        xs = json.loads(open(path).read())
    except Exception:
        xs = []
    if record is not None:
        xs = (xs + [round(record, 3)])[-200:]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + f".{os.getpid()}"
            with open(tmp, "w") as f:
                f.write(json.dumps(xs))
            os.replace(tmp, path)
        except OSError:
            pass
    return sorted(xs)[len(xs) // 10] if len(xs) >= 10 else None


def _quiet(max_load: float, max_noise: float, target_prefix: list[str] | None = None) -> tuple[bool, dict]:
    """(quiet now?, the probe's reading): the load under its limit, and the probe's spread at most max_noise
    and its median within (1 + 2 * max_noise) of this machine's usual probe time."""
    load_ok = max_load <= 0 or os.getloadavg()[0] < max_load
    if max_noise <= 0:
        return load_ok, {"median_ms": 0.0, "spread": 0.0, "spread_old": 0.0, "n": 0}
    d = probe_detail(target_prefix=target_prefix)
    base = baseline(d["median_ms"], baseline_path(target_prefix if d.get("where") == "target" else None))
    level_ok = base is None or d["median_ms"] <= base * (1 + 2 * max_noise)
    return load_ok and d["spread"] <= max_noise and level_ok, d


@contextlib.contextmanager
def hold(cmd: list[str], max_load: float = 0.0, wait: float = 1800.0, say=None, max_noise: float = 0.0,
         record: list | None = None, target_prefix: list[str] | None = None):
    """Take the machine for timing. The waits for a quiet machine happen *before* the lock is taken,
    so a waiting timing run does not block other people's work; it waits for NEED passing readings in a
    row, GAP seconds apart, so one lucky reading in a noisy spell does not count. Once the lock is held
    the machine is checked again (the streak goes on if the lock came at once, else starts afresh), and if
    it is noisy the lock is released and taken again later. After `wait` seconds placemat goes ahead
    anyway, and says so (the probe is recorded either way). With target_prefix the probe runs there."""
    say = say or (lambda *a: print("placemat:", *a, file=sys.stderr, flush=True))
    gated = max_load > 0 or max_noise > 0
    nested = bool(os.environ.get("PLACEMAT_LOCKED"))
    t0 = time.time()
    p = None
    st = {"passes": 0, "last": 0.0, "d": None}

    def reading() -> bool:
        ok, st["d"] = _quiet(max_load, max_noise, target_prefix)
        st["passes"] = st["passes"] + 1 if ok else 0
        st["last"] = time.time()
        return ok

    try:
        ok = True
        while True:
            while gated and time.time() - t0 < wait:          # waiting without the lock
                if reading() and st["passes"] >= NEED:
                    break
                time.sleep(GAP if st["passes"] else 15)
            if cmd and not nested:
                p = _take(cmd)
            if not gated:
                break
            if time.time() - st["last"] > 2 * GAP:              # the lock took a while: earlier readings are stale
                st["passes"] = 0
            ok = reading()
            while ok and st["passes"] < NEED:
                time.sleep(GAP)
                ok = reading()
            if ok or time.time() - t0 >= wait:
                break
            _release(p)                             # noisy after all: let others work, try again later
            p = None
            time.sleep(30)
        d = st["d"]
        if gated and not ok:
            say(f"machine still noisy after {wait:.0f} s (load {os.getloadavg()[0]:.2f}, probe spread "
                f"{d['spread']:.1%}); going ahead")
        if record is not None:
            if not gated or max_noise <= 0:
                d = probe_detail(target_prefix=target_prefix)
            r = {"start": time.strftime("%H:%M:%S"), "t": round(time.time(), 3), "probe_ms": round(d["median_ms"], 2),
                 "spread": round(d["spread"], 4), "spread_old": round(d["spread_old"], 4),
                 "waited_s": round(time.time() - t0), "load": os.getloadavg()[0],
                 "went_ahead": bool(gated and not ok), "passes": st["passes"] if gated else 0}
            for k in ("where", "target_load", "error"):
                if k in d:
                    r[k] = d[k]
            record.append(r)
        old = os.environ.get("PLACEMAT_LOCKED")
        os.environ["PLACEMAT_LOCKED"] = "1"
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("PLACEMAT_LOCKED", None)
            if record is not None and record:
                e = probe_detail(target_prefix=target_prefix)
                record[-1]["end_spread"] = round(e["spread"], 4)
                record[-1]["end_spread_old"] = round(e["spread_old"], 4)
    finally:
        _release(p)
