"""A continuous noise record (P012): one probe reading every --every seconds, appended as a JSON line.

    placemat noiselog [--every 60] [--out PATH] [--for SECONDS] [--target "limactl shell placemat --"]

Each line: {"t": epoch seconds, "host", "median_ms", "spread", "spread_old", "load"} (lock.probe_detail;
with --target, the reading is taken there and `target` names it). The default file is
~/.cache/placemat/noise.jsonl, or $PLACEMAT_NOISELOG. It does not take the machine lock: it is meant to run
while timing does, so the report can say what the machine was like during each batch and round.

Cost: one reading is lock.K (21) runs of the probe's ~14 ms loop, about 0.3 s of one core (0.33 s CPU
measured on an M2 at load ~3, probe median 15.5 ms), so 0.55% of one core at one a minute; between
readings it sleeps. With --target the loop runs in the target (the same ~0.3 s there) and costs ~0.05 s
here (the prefix's process). It is a small,
regular, timestamped disturbance whose own effect can be checked against the record. It cannot be
pinned to a core on macOS, so it measures the machine rather than exactly what the benchmark saw.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import sys
import time

from . import lock


def default_path() -> str:
    return os.environ.get("PLACEMAT_NOISELOG") or os.path.join(os.path.expanduser("~"), ".cache", "placemat",
                                                              "noise.jsonl")


def sample(target_prefix: list[str] | None = None) -> dict:
    d = lock.probe_detail(target_prefix=target_prefix)
    r = {"t": round(time.time(), 3), "host": socket.gethostname(), "median_ms": round(d["median_ms"], 3),
         "spread": round(d["spread"], 4), "spread_old": round(d["spread_old"], 4),
         "load": round(os.getloadavg()[0], 2)}
    if target_prefix:
        r.update(target=" ".join(target_prefix), where=d.get("where"))
        for k in ("target_load", "error"):
            if k in d:
                r[k] = d[k]
    return r


def append(path: str, rec: dict) -> bool:
    """Append one line (one write, so concurrent writers do not interleave within a line)."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return True
    except OSError as e:
        print(f"placemat noiselog: cannot write {path}: {e}", file=sys.stderr, flush=True)
        return False


def read(path=None, start=None, end=None) -> list[dict]:
    """The samples with start <= t <= end (either bound may be None), in time order; unreadable lines are skipped."""
    try:
        with open(path or default_path()) as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
            t = float(r["t"])
        except (ValueError, KeyError, TypeError):
            continue
        if (start is None or t >= start) and (end is None or t <= end):
            out.append(r)
    return sorted(out, key=lambda r: r["t"])


class _Stop(Exception):
    pass


def run(path: str, every: float = 60.0, duration: float | None = None, target_prefix: list[str] | None = None) -> int:
    """Sample until `duration` seconds have passed, or SIGTERM / SIGINT (finishing the sample in hand)."""
    st = {"sleeping": False, "stop": False}

    def stop(signum, frame):
        st["stop"] = True
        if st["sleeping"]:
            raise _Stop

    old = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    t0 = time.time()
    i = 0
    try:
        while not st["stop"]:
            append(path, sample(target_prefix))
            i += 1
            nxt = t0 + i * every
            if duration is not None and nxt - t0 > duration:
                break
            st["sleeping"] = True
            try:
                if not st["stop"]:
                    time.sleep(max(0.0, nxt - time.time()))
            finally:
                st["sleeping"] = False
    except _Stop:
        pass
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    return 0


def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="placemat noiselog", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--every", type=float, default=60.0, help="seconds between readings (default 60)")
    p.add_argument("--out", help="the record (default $PLACEMAT_NOISELOG or ~/.cache/placemat/noise.jsonl)")
    p.add_argument("--for", dest="duration", type=float, help="stop after this many seconds (default: run until stopped)")
    p.add_argument("--target", help="take the readings on a target through this prefix (as [target] prefix)")
    a = p.parse_args(argv)
    return run(a.out or default_path(), a.every, a.duration, shlex.split(a.target) if a.target else None)
