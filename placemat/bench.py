"""Benchmark protocol and adapters (DESIGN §8.1).

A *suite* is one benchmark command; one run of it is one *execution*. The protocol: the command
prints one line per case and iteration count, `name value iterations [unit]` (whitespace-separated;
or tab-separated when a name holds spaces), value being the time for that many iterations (default
microseconds). placemat passes, per execution,

    PLACEMAT_CASES   the cases to run, comma-separated (others may be skipped)
    PLACEMAT_SERIES  per case, scale factors for a regression series: `name=0.125,0.25,0.5,1;name2=...`
                     (the harness runs each listed case at each scale after a discarded warm-up and
                     prints a line per scale with its own iteration count)

Adapters translate other formats:
  k-out      K scripts of `out["name";reps;{...}]` lines (Amber's): placemat writes a subset script
             with only the wanted cases, and the series as extra out[] lines; the script prints
             `name value` (microseconds for reps repetitions).
  gbench     Google Benchmark JSON (--benchmark_format=json): real_time per iteration.
  hyperfine  hyperfine --export-json: each command's mean, as one case per command name.
  lines      the protocol itself.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

OUT_RE = re.compile(r'^out\["([^"]+)";(\d+);(.*)\]\s*$')
NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
UNITS = {"ns": 1e-3, "us": 1.0, "µs": 1.0, "ms": 1e3, "s": 1e6}


@dataclass
class Sample:
    name: str
    value: float        # microseconds for `iterations` iterations
    iterations: int | None = None


@dataclass
class Suite:
    name: str
    format: str = "lines"            # lines | k-out | gbench | hyperfine
    command: list[str] = field(default_factory=list)   # {binary} {script} {arm_dir} {config_dir} expanded
    script: Path | None = None       # k-out: the script holding out[] lines
    cwd: str = "{arm_dir}"
    cases: list[str] = field(default_factory=list)     # for formats that cannot list their cases
    env: dict[str, str] = field(default_factory=dict)
    series: bool | None = None       # the harness honours PLACEMAT_SERIES (default: k-out only)

    @property
    def does_series(self) -> bool:
        return self.format == "k-out" if self.series is None else self.series

    def list_cases(self) -> dict[str, int | None]:
        """name -> iterations, where the suite can say (k-out scripts); else the configured list."""
        if self.format == "k-out" and self.script:
            return k_cases(self.script)
        return {c: None for c in self.cases}


# ---- k-out --------------------------------------------------------------------------------------

def k_cases(script: Path) -> dict[str, int]:
    """A K script's cases: name -> repetitions. A case not at the start of its line cannot be timed:
    said, not skipped silently (the Amber stage once missed three cases that way)."""
    got = {}
    for l in Path(script).read_text().splitlines():
        m = OUT_RE.match(l)
        if m:
            got[m.group(1)] = int(m.group(2))
        elif 'out["' in l and not l.lstrip().startswith("/"):
            print(f"placemat: {Path(script).name}: out[...] not at the start of its line, skipped: {l[:80]}",
                  file=sys.stderr)
    return got


def k_subset(script: Path, want: dict[str, list[int] | None], dest: Path) -> Path:
    """The script with only the wanted out[...] lines (every other line kept, in order). A case given
    a list of repetition counts is timed at each, as `name@reps`, after a discarded warm-up."""
    lines = []
    for l in Path(script).read_text().splitlines():
        m = OUT_RE.match(l)
        if not m:
            lines.append(l)
        elif m.group(1) in want:
            nm, f, series = m.group(1), m.group(3), want[m.group(1)]
            if series:
                lines.append(f'out["{nm}@w";{series[0]};{f}]')
                lines += [f'out["{nm}@{r}";{r};{f}]' for r in series]
            else:
                lines.append(l)
    dest.write_text("\n".join(lines) + "\n")
    return dest


def parse_k(text: str, reps: dict[str, int]) -> list[Sample]:
    out = []
    for l in text.splitlines():
        p = l.split()
        if len(p) == 2 and re.fullmatch(r"\d+(\.\d+)?", p[1]):
            nm = p[0]
            if "@" in nm:
                base, _, r = nm.partition("@")
                if r == "w" or not r.isdigit():
                    continue
                out.append(Sample(base, float(p[1]), int(r)))
            else:
                out.append(Sample(nm, float(p[1]), reps.get(nm)))
    return out


# ---- the protocol and other formats -------------------------------------------------------------

def parse_lines(text: str) -> list[Sample]:
    out = []
    for l in text.splitlines():
        if not l.strip() or l.startswith("#"):
            continue
        p = l.rstrip("\n").split("\t") if "\t" in l else l.split()
        if len(p) < 2 or not NUM.fullmatch(p[1].strip()):
            continue
        it = int(p[2]) if len(p) > 2 and p[2].strip().isdigit() else None
        unit = p[3].strip() if len(p) > 3 else (p[2].strip() if len(p) == 3 and it is None else "us")
        out.append(Sample(p[0].strip(), float(p[1]) * UNITS.get(unit, 1.0), it))
    return out


def parse_gbench(text: str) -> list[Sample]:
    d = json.loads(text[text.index("{"):])
    out = []
    for b in d.get("benchmarks", []):
        if b.get("run_type", "iteration") != "iteration":
            continue
        it = int(b.get("iterations", 1))
        per = float(b["real_time"]) * UNITS.get(b.get("time_unit", "ns"), 1e-3)
        out.append(Sample(b["name"], per * it, it))
    return out


def parse_hyperfine(text: str) -> list[Sample]:
    d = json.loads(text[text.index("{"):])
    return [Sample(r.get("command", f"cmd{i}"), float(r["mean"]) * 1e6, 1) for i, r in enumerate(d["results"])]


PARSERS = {"lines": parse_lines, "gbench": parse_gbench, "hyperfine": parse_hyperfine}


def parse(suite: Suite, text: str, reps: dict[str, int] | None = None) -> list[Sample]:
    if suite.format == "k-out":
        return parse_k(text, reps or {})
    return PARSERS[suite.format](text)


def series_env(series: dict[str, list[float]]) -> str:
    return ";".join(f"{n}={','.join(f'{s:g}' for s in xs)}" for n, xs in series.items())
