"""Rounds, arms, anchors, adaptive stopping and the raw-data store (DESIGN §5.1, §5.7).

A round runs every variant of the current batch, for every arm, in a fresh random order; within a
variant the arms run back to back in random order. The first batch also times the true stock builds
(no pad, no hook, no interposer) as one more slot; each later batch re-times pad 0 at the stock
setting as an anchor. Every binary and data setting gets one discarded execution first. Cases whose
interval is wide get more pads, a batch at a time, up to the cap; stopping is on width only.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import bench, lock
from .analysis import analyse
from .bench import Suite
from .build import Arm, Builder, header_dir
from .config import Config
from .design import STOCK, Design

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class Plan:
    cases: dict[str, tuple[str, str]]               # key 'suite: name' -> (suite, name)
    reps: dict[str, dict[str, int | None]] = field(default_factory=dict)   # suite -> name -> iterations


def plan_cases(cfg: Config, cases: list[str]) -> Plan:
    """Resolve case names: 'suite: name', or a bare name looked up in the suites that can list theirs."""
    listed = {s: su.list_cases() for s, su in cfg.suites.items()}
    plan = Plan({}, listed)
    for c in cases:
        c = c.strip()
        if not c:
            continue
        pre, sep, nm = c.partition(": ")
        if sep and pre in cfg.suites:
            plan.cases[c] = (pre, nm)
            continue
        hits = [s for s in cfg.suites if c in listed[s]]
        if not hits:
            print(f"placemat: no case {c!r} in any suite; skipped", file=sys.stderr)
            continue
        plan.cases[f"{hits[0]}: {c}"] = (hits[0], c)
    return plan


def read_log(path: Path) -> dict:
    """The hook's or interposer's exit summary (the first process's block)."""
    out: dict = {}
    if not path.exists():
        return out
    for l in path.read_text(errors="replace").splitlines():
        p = l.split()
        if not p:
            continue
        if p[0] == "end" and out:
            break
        if p[0] == "base" and "base" not in out:
            out["base"] = int(p[1], 16)
        elif p[0] == "region" and "region" not in out:
            out["region"] = int(p[1], 16)
        elif p[0] == "khash":
            out["khash"] = int(p[1], 16)
        elif p[0] in ("coloured", "wrapped"):
            out[p[0]] = int(p[1])
    return out


def noise_record(start: float, end: float) -> list[dict]:
    """The continuous noise log's samples over the run, kept with the timings (the log may be rotated or
    on another machine when the run is reanalysed). Empty when there is no noise logger."""
    try:
        from . import noiselog
        return noiselog.read(start=start, end=end)
    except Exception:                                    # no logger yet, or an unreadable log: the run goes on
        return []


class Runner:
    def __init__(self, cfg: Config, work: Path, arms: list[tuple[str, str]], design: Design,
                 rounds: int | None = None, say=None):
        self.cfg, self.work, self.D = cfg, work, design
        self.rounds = rounds or cfg.rounds
        self.say = say or (lambda *a: print("placemat:", *a, file=sys.stderr, flush=True))
        self.B = Builder(cfg, work)
        self.arms: list[Arm] = [self.B.arm(*x) if isinstance(x, tuple) else self.B.arm(**x) for x in arms]
        self.hooked = design.data != "none" and cfg.data_method in ("hook", "allocator")
        self.preload = None
        if design.data != "none" and cfg.data_method == "interposer":
            from .cbuild import build_interposer, preload_env
            self.preload = preload_env(build_interposer(work / "interposer"))
        self.nruns = 0
        self.log: dict = {"loads": [], "batches": [], "build_s": 0.0, "time_s": 0.0, "wrapped": 0, "coloured": 0,
                          "batch_t": [], "round_t": []}
        self.memory_mb = cfg.memory_mb
        if self.memory_mb and cfg.target_prefix:         # ps would watch the local client (limactl, ssh), not the benchmark
            self.say(f"memory_mb = {cfg.memory_mb} is ignored with a [target] prefix: it would watch the local "
                     f"{cfg.target_prefix[0]} process, not the benchmark on the target")
            self.memory_mb = 0

    # ---- executions ------------------------------------------------------------------------------

    def _env(self, slot_env: dict) -> dict:
        if self.cfg.inherit_env:
            e = dict(os.environ)
        else:
            e = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
        e.update(self.cfg.run_env)
        e.update(slot_env)
        return e

    def execute(self, binary: Path, arm: Arm, suite: Suite, script: Path | None, slot_env: dict,
                cases: list[str], series: dict) -> tuple[list[bench.Sample], dict]:
        """One execution of one suite: (samples, covariates from the data log)."""
        self.nruns += 1
        logf = Path(tempfile.mkstemp(prefix="placemat-log-", dir=self.tmp)[1])
        logf.unlink()
        env = self._env({**slot_env, **{k: self.cfg.expand(v, arm_dir=arm.dir) for k, v in suite.env.items()}})
        if slot_env:
            env["PLACEMAT_LOG"] = str(logf)
        env["PLACEMAT_CASES"] = ",".join(cases)
        if series and suite.format != "k-out":
            env["PLACEMAT_SERIES"] = bench.series_env(series)
        kw = dict(binary=binary, script=script or "", arm_dir=arm.dir)
        cmd = [self.cfg.expand(x, **kw) for x in suite.command] or [str(binary)]
        cwd = self.cfg.expand(suite.cwd, **kw)
        with tempfile.TemporaryFile() as out:
            from .config import on_target
            tcmd, tenv, tcwd = on_target(self.cfg, cmd, env, cwd)
            p = subprocess.Popen(tcmd, cwd=tcwd, stdout=out, stderr=subprocess.STDOUT, env=tenv, stdin=subprocess.DEVNULL)
            t0 = time.time()
            while True:
                try:
                    p.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if self.memory_mb:
                    rss = subprocess.run(["ps", "-o", "rss=", "-p", str(p.pid)], capture_output=True, text=True).stdout.strip()
                    if rss and int(rss) > self.memory_mb * 1024:
                        p.kill()
                        p.wait()
                        self.say(f"{suite.name} on {arm.name}: killed past {self.memory_mb} MB")
                        break
                if time.time() - t0 > self.cfg.timeout:
                    p.kill()
                    p.wait()
                    self.say(f"{suite.name} on {arm.name}: timed out")
                    break
            out.seek(0)
            text = ANSI.sub("", out.read().decode("utf-8", "replace"))
        cov = read_log(logf)
        logf.unlink(missing_ok=True)
        self.log["wrapped"] += cov.get("wrapped", 0)
        self.log["coloured"] += cov.get("coloured", 0)
        if slot_env:                                     # a data setting: did the hook or interposer see anything?
            self.log["data_execs"] = self.log.get("data_execs", 0) + 1
            if not cov:
                self.log["no_log"] = self.log.get("no_log", 0) + 1
        return bench.parse(suite, text, {k: v for k, v in self.plan.reps.get(suite.name, {}).items() if v}), cov

    def covariate(self, cov: dict):
        if self.cfg.covariate == "base":
            return cov.get("region", cov.get("base"))
        if self.cfg.covariate == "khash":
            return cov.get("khash")
        return None

    # ---- the stage -------------------------------------------------------------------------------

    def run(self, cases: list[str], out: Path) -> dict:
        D, cfg = self.D, self.cfg
        self.plan = plan = plan_cases(cfg, cases)
        if not plan.cases:
            raise SystemExit("placemat: no cases to time")
        rnd = random.Random(D.seed)
        ttmp = cfg.expand(cfg.target_tmp) if cfg.target_tmp else None    # {config_dir} etc., as for the lock
        if ttmp:
            Path(ttmp).mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="placemat-", dir=ttmp))
        names = [a.name for a in self.arms]
        times = {key: {a: {} for a in names} for key in plan.cases}
        exec_t = {s: {a: {} for a in names} for s in sorted({s for s, _ in plan.cases.values()})}   # timed executions' starts
        partial: dict = {}                          # the batch being timed, while it runs
        t_run = time.time()
        K = {key: 0 for key in plan.cases}
        series: dict[str, list[int]] = {}           # key -> iteration counts of its regression series
        active = set(plan.cases)
        bins: dict = {}
        first = True
        b = 0

        def binary(arm: Arm, j):
            k = (arm.name, j)
            if k not in bins:
                bins[k] = self.B.binary(arm, None if j == "stock" else D.pad(j), self.hooked and j != "stock")
            return bins[k]

        def slot_env(slot: str) -> dict:
            if slot == "stock" or D.data == "none":
                return {}
            j, kind = (0, STOCK) if slot.startswith("a") else D.parse(slot)   # an anchor: pad 0, stock setting
            e = {"PLACEMAT_UNIT": str(D.unit), "PLACEMAT_COLOUR_SPAN": str(D.colour_span),
                 "PLACEMAT_STEP_SPAN": str(D.step_span), "PLACEMAT_MIN": str(cfg.min_size), "PLACEMAT_ADDRLOG": "1",
                 **D.setting(j, kind).env(D.step_mode)}
            if self.preload:
                e.update(self.preload)
            return e

        def pad_of(slot: str):
            if slot == "stock":
                return "stock"
            if slot.startswith("a"):
                return 0
            return D.parse(slot)[0]

        def scripts_for(keys) -> dict[str, Path | None]:
            sc = {}
            for s in sorted({plan.cases[k][0] for k in keys}):
                su = cfg.suites[s]
                if su.format == "k-out":
                    want = {nm: series.get(k) for k, (ss, nm) in plan.cases.items() if ss == s and k in keys}
                    sc[s] = bench.k_subset(su.script, want, self.tmp / f"{s}.k")
                else:
                    sc[s] = su.script
            return sc

        def value(key, samples):
            s, nm = plan.cases[key]
            got = [x for x in samples if x.name == nm]
            if not got:
                return None
            if key in series:
                pts = [(x.iterations, x.value) for x in got if x.iterations]
                if len(pts) < 2:
                    return got[-1].value
                full = max(series[key])
                from .stats import slope
                v = slope(pts) * full
                return v if v > 0 else max(pts)[1]          # too fast for a fit: the full-count time
            return got[-1].value

        def one(arm, slot, suite_name, keys, sc, when=None):
            su = cfg.suites[suite_name]
            if when is not None:
                when.append(round(time.time(), 2))
            nms = [plan.cases[k][1] for k in keys if plan.cases[k][0] == suite_name]
            ser = {plan.cases[k][1]: [r / max(series[k]) for r in series[k]] for k in keys
                   if k in series and plan.cases[k][0] == suite_name}
            return self.execute(binary(arm, pad_of(slot)), arm, su, sc[suite_name], slot_env(slot), nms, ser)

        def save(complete: bool, noise: bool = True) -> dict:
            """The raw file: after every round (so a crash keeps the timings so far; an unfinished batch's
            rounds are kept but not analysed, since K counts finished batches only), and at the end."""
            if noise:
                self.log["noise"] = noise_record(t_run, time.time())
            raw = {"version": 1, "complete": complete, "partial": dict(partial) if partial else None,
                   "project": cfg.name, "config": str(cfg.path), "design": D.to_json(),
                   "arms": [{"name": a.name, "source": a.source, "tree": a.tree, "order": str(a.order) if a.order else None,
                             "pad_first": a.pad_first,
                             "pin_order": str(a.pin_order) if a.pin_order else None} for a in self.arms],
                   "rounds": self.rounds, "plan": plan.cases, "series": series, "K": K, "times": times,
                   "exec_t": exec_t, "thresholds": vars(cfg.thresholds), "target": cfg.target,
                   "target_prefix": list(cfg.target_prefix), "host": socket.gethostname(),
                   "stock_placement": cfg.stock_placement if D.data != "none" else True,
                   "covariate": cfg.covariate, "machine": {"max_noise": cfg.max_noise, "max_load": cfg.max_load,
                                                           "wait": cfg.lock_wait, "lock": bool(cfg.lock)},
                   "data_method": cfg.data_method if D.data != "none" else "none",
                   "log": {**self.log, "runs": self.nruns, "date": time.strftime("%Y-%m-%d %H:%M:%S")},
                   "geometry": self.geometry(bins) if complete else {},
                   "build_warnings": list(getattr(self.B, "warnings", [])),
                   "build_notes": list(getattr(self.B, "notes", []))}
            out.parent.mkdir(parents=True, exist_ok=True)
            tmpf = out.with_suffix(".tmp")
            tmpf.write_text(json.dumps(raw))
            tmpf.replace(out)
            return raw

        held = dict(max_noise=cfg.max_noise, wait=cfg.lock_wait, record=self.log.setdefault("probes", []),
                    target_prefix=list(cfg.target_prefix) or None)   # probe the target, where the benchmark runs

        try:
            while active:
                k0 = min(K[k] for k in active)
                keys = sorted(k for k in active if K[k] == k0)
                new = list(D.batch_pads(0 if first else D.batch(k0)))
                if not new:
                    break
                k1 = new[-1] + 1
                suites = sorted({plan.cases[k][0] for k in keys})
                t0 = time.time()
                with lock.hold([cfg.expand(x) for x in cfg.shared_lock], say=self.say):
                    tb = time.time()
                    self.log["build_wait_s"] = self.log.get("build_wait_s", 0.0) + tb - t0
                    for j in new + (["stock"] if first else []):
                        for a in self.arms:
                            binary(a, j)
                    self.log["build_s"] += time.time() - tb      # building only, not waiting for the lock
                tw = time.time()
                with lock.hold([cfg.expand(x) for x in cfg.lock], cfg.max_load, say=self.say, **held):
                    t1 = time.time()
                    self.log["lock_wait_s"] = self.log.get("lock_wait_s", 0.0) + t1 - tw
                    self.log["loads"].append(os.getloadavg()[0])
                    if first:                           # warm-up: which cases are fast enough for a series
                        sc = scripts_for(keys)
                        for s in suites:
                            smp, _ = one(self.arms[0], f"0.{STOCK}", s, keys, sc)
                            for k in keys:
                                if plan.cases[k][0] != s:
                                    continue
                                got = [x for x in smp if x.name == plan.cases[k][1]]
                                if not got:
                                    continue
                                reps = got[-1].iterations or plan.reps.get(s, {}).get(plan.cases[k][1])
                                if (cfg.suites[s].does_series and reps and reps >= 8
                                        and got[-1].value / reps < cfg.series_us):
                                    series[k] = sorted({max(1, reps * 2 ** i // 8) for i in range(4)})
                    sc = scripts_for(keys)
                    slots = [v for j in new for v in D.variants(j)] + (["stock"] if first else [f"a{D.batch(k0)}"])
                    for slot in slots:                  # every binary and data setting: a discarded execution
                        for s in suites:
                            for a in self.arms:
                                one(a, slot, s, keys, sc)
                    for k in keys:
                        for a in names:
                            for slot in slots:
                                times[k][a][slot] = []
                    for s in suites:
                        for a in names:
                            for slot in slots:
                                exec_t[s][a][slot] = []
                    rts = []
                    self.log["round_t"].append(rts)
                    partial.update(batch=b, pads=[new[0], new[-1]], cases=len(keys), rounds=0, start=round(t1, 2))
                    for r in range(self.rounds):
                        order = list(slots)
                        rnd.shuffle(order)
                        tr = time.time()
                        for slot in order:
                            for s in suites:
                                who = list(self.arms)
                                rnd.shuffle(who)
                                for a in who:
                                    smp, cov = one(a, slot, s, keys, sc, exec_t[s][a.name][slot])
                                    cv = self.covariate(cov)
                                    for k in keys:
                                        if plan.cases[k][0] == s:
                                            times[k][a.name][slot].append((value(k, smp), cv))
                        rts.append([round(tr, 2), round(time.time(), 2)])
                        partial["rounds"] = r + 1
                        if r + 1 < self.rounds:
                            save(complete=False, noise=False)
                    self.log["loads"].append(os.getloadavg()[0])
                    te = time.time()
                self.log["batch_t"].append([round(t1, 2), round(te, 2)])
                partial.clear()
                self.log["time_s"] += te - t1
                self.log["batches"].append((k1, len(keys)))
                self.say(f"batch {b}: pads {new[0]}-{new[-1]}, {len(keys)} cases, "
                         f"{time.time() - t0:.0f} s; load {self.log['loads'][-1]:.2f}")
                for k in keys:
                    K[k] = k1
                    res = analyse(times[k], names, D, k1, cfg.thresholds, rnd, full=False,
                                  covariate_pooled=cfg.covariate != "khash")
                    hw = max((c["hw"] for c in res["cmp"].values()), default=0.0) if res else 0.0
                    if res is None or hw <= math.log1p(cfg.target) or k1 >= D.cap:
                        active.discard(k)
                first = False
                b += 1
                save(complete=False)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
        return save(complete=True)


    def geometry(self, bins) -> dict:
        """Per arm and pad: the most common function shift against the arm's pad-0 build, the share of
        functions unmoved (a pinned region), and small loops crossing the code boundary."""
        from .build import shifts
        try:
            from . import binary as BI
        except ImportError:
            BI = None
        geo = {}
        for (a, j), b in bins.items():
            ref = bins.get((a, 0))
            row = {"pad": None if j == "stock" else self.D.pad(j), "path": str(b)}
            if ref:
                s, f, n = shifts(ref, b)
                row.update(shift=s, unmoved=f)
            if BI:
                try:
                    row["crossing_loops"] = len(BI.crossing_loops(b, self.cfg.boundary))
                except Exception as e:                   # the analysis must not lose a run's timings
                    row["crossing_loops_error"] = str(e)
            geo.setdefault(a, {})[str(j)] = row
        return geo
