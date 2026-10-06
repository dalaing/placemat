"""The project's configuration file, placemat.toml (DESIGN §4).

    [project]
    name = "amber"
    root = "../.."                  # the project root, relative to this file (default: its directory)

    [build]
    command = ["bash", "{config_dir}/build.sh"]   # run with the PLACEMAT_* build environment (build.py)
    binary = "amber"                 # where the build leaves the binary, relative to PLACEMAT_OUT
    inputs = ["build.sh", "src/*"]   # globs whose contents identify a source directory's build
    env = {}                         # extra environment for builds
    pin_align = 16                   # pinned arms: the function alignment their pin pads assume (PLACEMAT_ALIGN)
    mode = "env"                     # env: the command reads PLACEMAT_* itself; cc: placemat runs the command in a
                                     #   copy of the arm's tree with CC/CXX set to its compiler wrapper (placemat/cc.py),
                                     #   and `binary` is the path the build leaves in the tree

    [run]
    env = {AMBER_THREADS = "1"}      # added to a minimal environment (PATH, HOME)
    inherit_env = false              # or start from placemat's own environment
    timeout = 900                    # seconds per execution
    memory_mb = 0                    # kill an execution whose resident size passes this (0: no limit;
                                     # ignored, with a warning, under a [target] prefix)
    rounds = 7

    [machine]
    lock = []                        # a command that takes the machine lock, prints a line once held,
    shared_lock = []                 #   and releases it when its stdin closes (exclusive: timing; shared: builds)
    max_load = 0.0                   # also wait for the 1-minute load average to fall below this (0: don't)
    max_noise = 0.0                  # and for the noise probe's spread to be at most this in two readings in a
                                     #   row (0: don't; lock.py)
    wait = 1800                      # the longest wait for either, in seconds; then go ahead and say so
    boundary = 4096                  # code boundary the pads cover
    line = 64

    [target]                         # run builds and benchmarks elsewhere (a VM, say), while placemat and its lock
    prefix = []                      #   stay here: e.g. ["limactl", "shell", "placemat", "--"]; the noise probe
                                     #   runs there too (python3 through the prefix; lock.py);
    tmp = ""                         #   the tree must be at the same path there; tmp: a directory both sides see,
                                     #   for the per-execution data logs (default: the system's). The target must
                                     #   be able to write placemat's work directory (a VM: a writable mount). The
                                     #   interposer is built here, so it cannot be used on a target yet.

    [data]
    method = "hook"                  # hook (placemat.h in the project's allocator) | allocator (placemat's colouring
                                     # allocator through the project's allocator API) | interposer | none
    vary = "both"                    # both | colour | step | none
    step_mode = "hashed"             # hashed | linear
    unit = 64                        # never finer than the project's alignment guarantee
    colour_span = 16384
    step_span = 16384                # the L1D set stride (16 KB on Apple M2; 4 KB typical on x86)
    min = 65536                      # smallest coloured size, and the granule for k
    covariate = "base"               # base | khash | none: the run covariate read from the hook's log
    stock_placement = true           # (0, off) places blocks as the stock build does: default true for hook and none,
                                     # false for allocator and interposer (they add a header at every setting)

    [stats]
    threshold = 0.03
    threshold_fast = 0.10
    fast_us = 5000
    q = 0.05
    pmax = 0.01
    target = 0.01                    # adaptive stopping: the interval's half-width
    series_us = 1000                 # cases under this per iteration are timed as a regression series

    [[suite]]
    name = "microbench"
    format = "k-out"                 # lines | k-out | gbench | hyperfine (bench.py)
    script = "pbt/microbench.k"      # relative to the project root
    command = ["{binary}", "{script}"]
    cwd = "{arm_dir}"
    series = true                    # the harness honours PLACEMAT_SERIES (default: k-out suites only)

The machine overlay: PLACEMAT_MACHINE names a TOML file describing this machine rather than a project.
Its [machine] and [target] keys, and [data] colour_span and step_span (the page size and the L1D set
stride), override every project's, key by key; nothing else in it is read. So one project config serves
several machines: the Mac's lock and 16 KB spans in the project, a Linux box's flock and 4 KB spans in
its overlay (scripts/box.sh writes one).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import Thresholds
from .bench import Suite


@dataclass
class Config:
    path: Path
    name: str = "project"
    root: Path = Path(".")
    build_command: list[str] = field(default_factory=list)
    binary: str = "a.out"
    inputs: list[str] = field(default_factory=lambda: ["*"])
    build_env: dict[str, str] = field(default_factory=dict)
    run_env: dict[str, str] = field(default_factory=dict)
    inherit_env: bool = False
    timeout: float = 900.0
    memory_mb: int = 0
    rounds: int = 7
    lock: list[str] = field(default_factory=list)
    shared_lock: list[str] = field(default_factory=list)
    max_load: float = 0.0
    max_noise: float = 0.0
    lock_wait: float = 1800.0
    boundary: int = 4096
    line: int = 64
    data_method: str = "hook"
    vary: str = "both"
    step_mode: str = "hashed"
    unit: int = 64
    colour_span: int = 16384
    step_span: int = 16384
    min_size: int = 65536
    pin_align: int = 16
    build_mode: str = "env"
    target_prefix: list[str] = field(default_factory=list)
    target_tmp: str = ""
    covariate: str = "base"
    stock_placement: bool = True
    thresholds: Thresholds = field(default_factory=Thresholds)
    target: float = 0.01
    series_us: float = 1000.0
    suites: dict[str, Suite] = field(default_factory=dict)

    @property
    def config_dir(self) -> Path:
        return self.path.parent

    def expand(self, s: str, **kw) -> str:
        return s.format(config_dir=self.config_dir, root=self.root, **kw)


# The keys the machine overlay (PLACEMAT_MACHINE) may set: None for a whole section.
MACHINE_KEYS = {"machine": None, "target": None, "data": ("colour_span", "step_span")}


def machine_overlay(d: dict, overlay: str | None = None) -> dict:
    """d with the machine overlay's keys (PLACEMAT_MACHINE, or `overlay`) laid over it."""
    import os as _os
    ov = overlay if overlay is not None else _os.environ.get("PLACEMAT_MACHINE", "")
    if not ov:
        return d
    o = tomllib.loads(Path(ov).expanduser().read_text())
    d = {k: (dict(v) if isinstance(v, dict) else v) for k, v in d.items()}
    for sec, keys in MACHINE_KEYS.items():
        for k, v in o.get(sec, {}).items():
            if keys is None or k in keys:
                d.setdefault(sec, {})[k] = v
    return d


def load(path: str | Path, overlay: str | None = None) -> Config:
    path = Path(path).resolve()
    d = machine_overlay(tomllib.loads(path.read_text()), overlay)
    p, b, r, m, da, st = (d.get(k, {}) for k in ("project", "build", "run", "machine", "data", "stats"))
    c = Config(path=path)
    c.name = p.get("name", path.parent.name)
    c.root = (path.parent / p.get("root", ".")).resolve()
    c.build_command = list(b.get("command", []))
    c.binary = b.get("binary", c.binary)
    c.inputs = list(b.get("inputs", c.inputs))
    c.build_env = dict(b.get("env", {}))
    c.pin_align = int(b.get("pin_align", 16))
    c.build_mode = b.get("mode", "env")
    if c.build_mode not in ("env", "cc"):
        raise ValueError(f"{path}: [build] mode {c.build_mode!r}: env | cc")
    c.run_env = dict(r.get("env", {}))
    c.inherit_env = bool(r.get("inherit_env", False))
    c.timeout = float(r.get("timeout", c.timeout))
    c.memory_mb = int(r.get("memory_mb", 0))
    c.rounds = int(r.get("rounds", c.rounds))
    c.lock, c.shared_lock = list(m.get("lock", [])), list(m.get("shared_lock", []))
    t = d.get("target", {})
    c.target_prefix = list(t.get("prefix", []))
    c.target_tmp = t.get("tmp", "")
    c.max_load = float(m.get("max_load", 0.0))
    c.max_noise = float(m.get("max_noise", 0.0))
    c.lock_wait = float(m.get("wait", 1800.0))
    c.boundary, c.line = int(m.get("boundary", 4096)), int(m.get("line", 64))
    c.data_method = da.get("method", c.data_method)
    c.vary = da.get("vary", c.vary)
    c.step_mode = da.get("step_mode", c.step_mode)
    c.unit = int(da.get("unit", c.unit))
    c.colour_span = int(da.get("colour_span", c.colour_span))
    c.step_span = int(da.get("step_span", c.step_span))
    c.min_size = int(da.get("min", c.min_size))
    if c.data_method not in ("hook", "allocator", "interposer", "none"):
        raise ValueError(f"{path}: [data] method {c.data_method!r}: hook | allocator | interposer | none")
    c.covariate = da.get("covariate", "base" if c.data_method == "hook" else "khash")
    # (0, off) places blocks as the stock build does: a hook that only adds offsets, or no data method at all
    # (variants differ from the stock build in code only); not placemat's colouring allocator or the interposer
    c.stock_placement = bool(da.get("stock_placement", c.data_method in ("hook", "none")))
    th = Thresholds()
    for k in ("threshold", "threshold_fast", "fast_us", "q", "pmax"):
        if k in st:
            setattr(th, k, float(st[k]))
    c.thresholds = th
    c.target = float(st.get("target", c.target))
    c.series_us = float(st.get("series_us", c.series_us))
    for s in d.get("suite", []):
        script = s.get("script")
        su = Suite(name=s["name"], format=s.get("format", "lines"), command=list(s.get("command", [])),
                   script=(c.root / script) if script else None, cwd=s.get("cwd", "{arm_dir}"),
                   cases=list(s.get("cases", [])), env=dict(s.get("env", {})),
                   series=s.get("series"))
        c.suites[su.name] = su
    return c

def on_target(cfg, cmd: list[str], env: dict, cwd) -> tuple[list[str], dict, object]:
    """The command as run on the configured target: with a prefix (a VM, say), the PLACEMAT_* and project
    variables travel explicitly through `env`, and the working directory through `cd`; without one, unchanged."""
    if not cfg.target_prefix:
        return cmd, env, cwd
    import os as _os
    import shlex as _shlex
    base = set(_os.environ)
    fwd = [f"{k}={v}" for k, v in env.items() if k.startswith("PLACEMAT_") or k not in base or env[k] != _os.environ.get(k)]
    fwd = [x for x in fwd if not x.startswith(("PATH=", "HOME=", "TMPDIR=", "LANG="))]
    inner = "cd " + _shlex.quote(str(cwd)) + " && exec env " + " ".join(_shlex.quote(x) for x in fwd) + " " + \
        " ".join(_shlex.quote(x) for x in cmd)
    return list(cfg.target_prefix) + ["sh", "-c", inner], dict(_os.environ), None
