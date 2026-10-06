"""Profile import (DESIGN §6.3 step 1).

One JSON format for every source::

    {"self":  {set: {function: share}},          # self samples, equal weight per case
     "edges": {set: {"caller>callee": share}},   # call edges (samples under the callee at that edge)
     "n":     {set: number of cases},
     "offs":  {function: {offset: count}}}       # sampled offsets from the function's start, raw counts

A benchmark *set* is a group of cases weighted equally among themselves ("m" and "o" in Amber's
data; any names in general). Each case (one profile file) is normalised to sum 1 over all its
samples, in or out of the binary, and the set's shares are the mean over its cases. Functions
outside the binary are named ``name@library``; addresses in the binary but outside its text
section (stubs) are lumped as ``STUBS@stubs``. Names with ``@`` are not in-binary.

Samples are attributed to the binary's functions by address, not by the profiler's symbol name:
macOS ``sample`` strips LTO suffixes (``o8.1005`` is reported as ``o8``) and attributes stubs past
``__text`` to the last function.

Sources:
- macOS ``sample`` text reports (``sample PID SECONDS -file report.txt``): the call graph section.
- Linux ``perf script`` output, with call chains (``perf record -g``; frame pointers, so build with
  ``-fno-omit-frame-pointer``, or ``--call-graph dwarf``), one file per case; ``perf script
  --no-inline`` keeps it simple (inlined frames sharing an address with a real one are dropped).
"""
from __future__ import annotations

import bisect
import collections
import glob
import json
import os
import re
from pathlib import Path

from .. import binary as B

STUBS = "STUBS@stubs"


def _glob_all(specs) -> list[str]:
    out = []
    for s in specs if isinstance(specs, (list, tuple)) else [specs]:
        s = str(s)
        if os.path.isdir(s):
            out += sorted(glob.glob(os.path.join(s, "*")))
        else:
            g = sorted(glob.glob(s))
            out += g if g else [s]
    return out


class _Acc:
    """Accumulates per-case normalised shares into the profile format."""

    def __init__(self):
        self.self = collections.defaultdict(collections.Counter)
        self.edges = collections.defaultdict(collections.Counter)
        self.n = collections.Counter()
        self.offs = collections.defaultdict(collections.Counter)

    def add_case(self, s: str, selfc: collections.Counter, edges: collections.Counter):
        tot = sum(selfc.values())
        if tot <= 0:
            return False
        self.n[s] += 1
        for k, v in selfc.items():
            self.self[s][k] += v / tot
        for (a, b), v in edges.items():
            self.edges[s][a + ">" + b] += v / tot
        return True

    def result(self) -> dict:
        out = {"self": {}, "edges": {}, "n": dict(self.n), "offs": {}}
        for s, n in self.n.items():
            out["self"][s] = {k: v / n for k, v in self.self[s].items()}
            out["edges"][s] = {k: v / n for k, v in self.edges[s].items()}
        out["offs"] = {f: dict(c) for f, c in self.offs.items()}
        return out


class _Resolver:
    """Address in the binary's link-time address space -> function name (or STUBS)."""

    def __init__(self, binary, text=None):
        bi = B.info(binary)
        self.bi = bi
        self.text = text or bi.text
        self.loc = B._Locator(bi.funcs)
        self.funcs = B.function_map(binary)

    def __call__(self, a: int) -> tuple[str, int | None]:
        t0, tsz = self.text
        if not (t0 <= a < t0 + tsz):
            return STUBS, None
        f = self.loc(a)
        if f is None:          # in a gap between functions: the preceding symbol, as nm-based lookups do
            i = max(0, bisect.bisect_right(self.loc.starts, a) - 1)
            f = self.loc.funcs[i] if self.loc.funcs else None
        if f is None:
            return "?", None
        return f.name, f.start


# ---------------------------------------------------------------------------------------------
# macOS sample

_SAMPLE_LINE = re.compile(r"^(?P<pre>[ +!:|]*)(?P<n>\d+) (?P<name>.+?)  \(in (?P<lib>[^)]+)\) (?P<rest>.*)$")
_SAMPLE_ADDRS = re.compile(r"\[(0x[0-9a-f]+(?:,0x[0-9a-f]+)*)")
_SAMPLE_OFFS = re.compile(r"\+ ([\d,\.]+)\s+\[")


def _sample_load(text: str, image: str) -> int:
    i = text.find("Binary Images:")
    if i >= 0:
        for line in text[i:].splitlines()[1:]:
            m = re.match(r"^\s*(0x[0-9a-f]+)\s+-\s+(?:0x[0-9a-f]+|\?\?\?)\s+\+?(\S+)", line)
            if m and m.group(2) == image:
                return int(m.group(1), 16)
    m = re.search(r"Load Address:\s+(0x[0-9a-f]+)", text)
    if not m:
        raise ValueError(f"no load address for {image} in the sample report")
    return int(m.group(1), 16)


def sample_case(text: str, resolve: _Resolver, image: str, base: int, offs=None):
    """One sample report -> (self counts, edge counts); adds sampled offsets to ``offs``."""
    load = _sample_load(text, image)
    if "Call graph:" not in text:
        raise ValueError("no call graph in the sample report")
    cg = text.split("Call graph:", 1)[1]
    cg = re.split(r"\n\s*Total number in stack", cg, maxsplit=1)[0]
    stack = []          # (depth, name, node index)
    nodes = []          # [name, count, children's count]
    edges = collections.Counter()
    for line in cg.splitlines():
        m = _SAMPLE_LINE.match(line)
        if not m:
            continue
        d = len(m.group("pre"))
        n = int(m.group("n"))
        lib = m.group("lib")
        start = None
        if lib == image:
            am = _SAMPLE_ADDRS.search(m.group("rest"))
            if am:
                addrs = [int(x, 16) for x in am.group(1).split(",")]
                nm, start = resolve(addrs[0] - load + base)
            else:
                nm = m.group("name")
        else:
            nm = m.group("name") + "@" + lib
        while stack and stack[-1][0] >= d:
            stack.pop()
        if stack:
            nodes[stack[-1][2]][2] += n
            edges[(stack[-1][1], nm)] += n
        nodes.append([nm, n, 0])
        stack.append((d, nm, len(nodes) - 1))
        if offs is not None and lib == image and start is not None:
            om = _SAMPLE_OFFS.search(m.group("rest"))
            if om:
                listed = [int(x) for x in om.group(1).split(",") if x.isdigit()]
                # listed offsets are from the profiler's symbol; rebase them onto our function
                delta = 0
                if listed and am:
                    delta = (addrs[0] - load + base - start) - listed[0]
                for x in listed:
                    offs[nm][x + delta] += n
    selfc = collections.Counter()
    for nm, n, c in nodes:
        selfc[nm] += n - c
    return selfc, edges


def from_sample(sets: dict, binary, image: str | None = None, text: tuple[int, int] | None = None,
                base: int | None = None) -> dict:
    """Import macOS ``sample`` reports. ``sets``: {set name: [report paths, directories or globs]}.
    ``image``: the binary's name as the report shows it (default: the binary's file name);
    ``text``: (address, size) of the text section at link time (default: read from the binary);
    ``base``: the link-time address the report's load address corresponds to (default: the
    binary's __TEXT vmaddr)."""
    res = _Resolver(binary, text)
    image = image or os.path.basename(str(binary))
    base = res.bi.base if base is None else base
    acc = _Acc()
    for s, specs in sets.items():
        for f in _glob_all(specs):
            t = Path(f).read_text(errors="replace")
            selfc, edges = sample_case(t, res, image, base, acc.offs)
            acc.add_case(s, selfc, edges)
    return acc.result()


# ---------------------------------------------------------------------------------------------
# Linux perf script

_PERF_FRAME = re.compile(r"^\s+([0-9a-fA-F]+)\s+(.*?)\s+\((.*)\)\s*$")
_PERF_HEAD_FRAME = re.compile(r":\s+([0-9a-fA-F]+)\s+(\S.*?)\s+\(([^()]*)\)\s*$")
_PERF_PERIOD = re.compile(r"\s(\d+)\s+[\w\-./]+(?::[\w]*)*:\s")
_SYMOFF = re.compile(r"^(.*)\+0x([0-9a-fA-F]+)$")


def _perf_samples(text: str):
    """Yield (header line, [(ip, sym, dso), ...] leaf first)."""
    head, frames = None, []
    for line in text.splitlines():
        if not line.strip():
            if head is not None:
                yield head, frames
            head, frames = None, []
            continue
        if line.startswith("#"):
            continue
        if not line[0].isspace():
            if head is not None:
                yield head, frames
            head, frames = line, []
            m = _PERF_HEAD_FRAME.search(line)
            if m:      # perf script without call chains: the sampled ip on the header line
                frames.append((int(m.group(1), 16), m.group(2), m.group(3)))
            continue
        m = _PERF_FRAME.match(line)
        if m and head is not None:
            frames.append((int(m.group(1), 16), m.group(2), m.group(3)))
    if head is not None:
        yield head, frames


def perf_case(text: str, resolve: _Resolver, image: str, binpath: str, offs=None):
    """One ``perf script`` output -> (self counts, edge counts); adds sampled offsets to ``offs``."""
    selfc = collections.Counter()
    edges = collections.Counter()
    deltas: dict[str, int] = {}      # pid/comm -> runtime minus link-time address of the image
    real = os.path.realpath(binpath)
    arm = resolve.bi.arch == "arm64"

    def ours(dso: str) -> bool:
        return os.path.basename(dso) == image or os.path.realpath(dso) == real

    for head, frames in _perf_samples(text):
        if not frames:
            continue
        pm = _PERF_PERIOD.search(head + " ")
        w = int(pm.group(1)) if pm else 1
        key = head.split(":")[0].split()[1] if len(head.split()) > 1 else head
        names = []
        ips = [ip for ip, _, dso in frames if dso != "inlined"]
        frames = [fr for fr in frames if fr[2] != "inlined" or fr[0] not in ips]
        for depth, (ip, sym, dso) in enumerate(frames):
            if ours(dso):
                addr = None
                sm = _SYMOFF.match(sym)
                if sm and sm.group(1) in resolve.funcs:
                    addr = resolve.funcs[sm.group(1)].start + int(sm.group(2), 16)
                    deltas.setdefault(key, ip - addr)
                elif sym in resolve.funcs:
                    addr = resolve.funcs[sym].start
                    deltas.setdefault(key, ip - addr)
                elif key in deltas:
                    addr = ip - deltas[key]
                else:
                    t0, tsz = resolve.text
                    addr = ip if t0 <= ip < t0 + tsz else None
                if addr is None:
                    nm, start = "?", None
                else:
                    nm, start = resolve(addr)
                if offs is not None and start is not None:
                    off = addr - start
                    if depth and arm and off % 4 == 3:     # perf shows a return address less one
                        off += 1
                    offs[nm][off] += w
            else:
                base = re.sub(r"\+0x[0-9a-fA-F]+$", "", sym)
                nm = base + "@" + os.path.basename(dso)
            names.append(nm)
        selfc[names[0]] += w
        for callee, caller in zip(names, names[1:]):
            edges[(caller, callee)] += w
    return selfc, edges


def from_perf(sets: dict, binary, image: str | None = None, text: tuple[int, int] | None = None) -> dict:
    """Import ``perf script`` outputs (one file per case). ``sets``: {set name: [paths, dirs or
    globs]}. Frames in the binary (matched by file name ``image``, default the binary's, or by
    path) are attributed by address: perf's ``symbol+offset`` when the symbol is one of the
    binary's, else the raw address less the load offset learnt from a resolved frame of the same
    process (or as is, for a non-PIE binary). Periods weight samples when perf prints them."""
    res = _Resolver(binary, text)
    image = image or os.path.basename(str(binary))
    acc = _Acc()
    for s, specs in sets.items():
        for f in _glob_all(specs):
            t = Path(f).read_text(errors="replace")
            selfc, edges = perf_case(t, res, image, str(binary), acc.offs)
            acc.add_case(s, selfc, edges)
    return acc.result()


# ---------------------------------------------------------------------------------------------
# files and shares


def load(path) -> dict:
    """Read a profile JSON, with offsets as integers."""
    p = json.loads(Path(path).read_text())
    p["offs"] = {f: {int(k): v for k, v in o.items()} for f, o in p.get("offs", {}).items()}
    return p


def save(profile: dict, path) -> None:
    Path(path).write_text(json.dumps(profile, indent=0))


def in_binary(profile: dict, s: str) -> dict[str, float]:
    """Set ``s``'s self shares of functions in the binary (names without '@')."""
    return {k: v for k, v in profile["self"].get(s, {}).items() if "@" not in k}


def hotness(profile: dict, names=None, sets=None) -> dict[str, float]:
    """Combined hotness: the sum over sets of each function's share of that set's in-binary
    samples (so every set counts equally)."""
    sets = list(sets or profile["self"].keys())
    shares = {s: in_binary(profile, s) for s in sets}
    tot = {s: sum(v.values()) or 1.0 for s, v in shares.items()}
    names = names if names is not None else sorted(set().union(*[set(v) for v in shares.values()]) if shares else [])
    return {f: sum(shares[s].get(f, 0) / tot[s] for s in sets) for f in names}
