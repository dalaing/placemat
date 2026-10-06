"""Affected-case selection for the steady state (DESIGN §3.3, P005 item 1).

A case is re-timed when any function it can reach changed machine code between the base and the
change. Changed: placemat.binary.same_code (call and branch targets, adrp pages and RIP-relative
displacements masked, so code that merely moved is the same); functions added or removed count as
changed. Reachable: from the case's profiled functions (its own set in a profile made with one set
per case, `placemat pin profile`), through the profile's call edges and the binary's static call
graph (direct calls and tail calls; indirect calls are invisible, hence the profile's edges and a
spot-check). A random sample of the skipped cases is re-timed anyway, every time.

Data: the sizes of the data sections, and the contents of string and plain data sections, are compared
(`data_changes`); a difference selects every case. Constant-data sections that can hold code addresses
or offsets (Mach-O __TEXT,__const and __DATA_CONST,__const; ELF .rodata and .data.rel.ro, where a linked
ELF binary also keeps its strings and constant tables) select every case too when no function moved
(the same text section, every function at the same address: moved code cannot explain the difference,
so it is a data or read-only data change with identical code), and only warn when code moved at the same
size, since moving code changes them. Changes to the allocation sequence are not detected (select every
case by hand when they apply).

    placemat affected BASE_BIN CHANGE_BIN PROFILE.json [--spot 0.1] [--seed N] [--min-share 0.0]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from . import binary as B

CALLS = {"bl", "b", "call", "callq", "jmp", "jmpq", "blr"}
HEX = re.compile(r"0x([0-9a-fA-F]+)")


import hashlib
import struct

ADDRESSY = {("__TEXT", "__const"), ("__DATA_CONST", "__const"), (".rodata", ""), (".data.rel.ro", "")}


def _sections(path) -> dict[tuple[str, str], tuple[int, str | None]]:
    """(segment, section) -> (size, sha1 of contents or None for zero-fill), for thin Mach-O or ELF."""
    data = Path(path).read_bytes()
    out = {}
    if data[:4] == b"\xcf\xfa\xed\xfe":                 # 64-bit little-endian Mach-O
        ncmds, = struct.unpack_from("<I", data, 16)
        p = 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", data, p)
            if cmd == 0x19:
                nsects, = struct.unpack_from("<I", data, p + 64)
                q = p + 72
                for _ in range(nsects):
                    sect = data[q:q + 16].split(b"\0")[0].decode()
                    seg = data[q + 16:q + 32].split(b"\0")[0].decode()
                    if not (seg.startswith("__DATA") or (seg, sect) in (("__TEXT", "__cstring"), ("__TEXT", "__const"))):
                        q += 80                       # code, stubs, unwind tables: not data
                        continue
                    _addr, size, offset = struct.unpack_from("<QQI", data, q + 32)
                    flags, = struct.unpack_from("<I", data, q + 64)
                    zero = (flags & 0xFF) in (1, 12, 18)  # S_ZEROFILL, S_GB_ZEROFILL, S_THREAD_LOCAL_ZEROFILL
                    out[(seg, sect)] = (size, None if zero else hashlib.sha1(data[offset:offset + size]).hexdigest())
                    q += 80
            p += cmdsize
    elif data[:4] == b"\x7fELF" and data[4] == 2:
        shoff, = struct.unpack_from("<Q", data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
        sh = [struct.unpack_from("<IIQQQQ", data, shoff + i * shentsize) for i in range(shnum)]
        st = sh[shstrndx]
        for h in sh:
            name = data[st[4] + h[0]:st[4] + h[0] + 64].split(b"\0")[0].decode()
            if not name.startswith((".data", ".rodata", ".bss", ".tdata", ".tbss")):
                continue
            key = (".rodata.str" if name.startswith(".rodata.str") else name, "")
            out[key] = (h[5], None if h[1] == 8 else hashlib.sha1(data[h[4]:h[4] + h[5]]).hexdigest())
    return out


def code_moved(base, change) -> bool:
    """Whether any code moved: the text section starts elsewhere, or a function both binaries have does."""
    if B.text_section(base)[0] != B.text_section(change)[0]:
        return True
    fa, fb = B.function_map(base), B.function_map(change)
    return any(fb[n].start != f.start for n, f in fa.items() if n in fb)


def data_changes(base, change) -> tuple[list[str], list[str]]:
    """(definite, possible): data sections whose size or (string/plain data) contents differ, and
    constant-data sections whose contents differ at the same size (they can hold code addresses): definite
    too when no code moved, since then moved code cannot be why."""
    a, b = _sections(base), _sections(change)
    definite, possible = [], []
    moved = None
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b or a[k][0] != b[k][0]:
            definite.append(f"{','.join(x for x in k if x)} size")
        elif a[k][1] != b[k][1]:
            if k in ADDRESSY:
                if moved is None:
                    moved = code_moved(base, change)
                if not moved:
                    definite.append(f"{','.join(x for x in k if x)} contents (no code moved)")
                    continue
            (possible if k in ADDRESSY else definite).append(f"{','.join(x for x in k if x)} contents")
    return definite, possible


def call_graph(path) -> dict[str, set[str]]:
    """Static direct-call graph: function -> functions it calls or tail-calls."""
    funcs = B.functions(path)
    loc = B._Locator(funcs)
    g: dict[str, set[str]] = defaultdict(set)
    for ins in B.disassemble(path):
        if ins.mnem not in CALLS and ins.target is None:
            continue
        t = ins.target
        if t is None:
            m = HEX.search(ins.ops)
            if not m:
                continue
            t = int(m.group(1), 16)
        src, dst = loc(ins.addr), loc(t)
        if src and dst and src.name != dst.name:
            g[src.name].add(dst.name)
    return g


def reachable(roots, graph, edges=()) -> set[str]:
    adj = defaultdict(set, {k: set(v) for k, v in graph.items()})
    for e in edges:
        a, _, b = e.partition(">")
        adj[a].add(b)
    seen, todo = set(), list(roots)
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        todo += [x for x in adj.get(f, ()) if x not in seen]
    return seen


PADS = r"^placemat_(pin_)?pad(_\d+)?$"


def affected(base, change, profile: dict, min_share: float = 0.0, spot: float = 0.1, seed: int = 0,
             ignore: str = PADS) -> dict:
    """ignore: functions never counted as changed (placemat's own pads, by default)."""
    same = B.same_code(base, change)
    fa, fb = B.function_map(base), B.function_map(change)
    changed = {n for n, s in same.items() if not s.same}
    changed |= {n for n in fb if n not in fa and not any(s.name_b == n for s in same.values())}
    changed |= {n for n in fa if n not in fb and n not in same}
    from .pin.pads import is_pad
    changed = {n for n in changed if not re.match(ignore, n) and not is_pad(n)}
    g = call_graph(change)
    gb = call_graph(base)
    for k, v in gb.items():
        g.setdefault(k, set()).update(v)
    definite, possible = data_changes(base, change)
    out = {"changed": sorted(changed), "cases": {}, "spot": [], "data_changed": definite, "data_maybe": possible}
    for case, shares in profile["self"].items():
        roots = {f for f, x in shares.items() if "@" not in f and x > min_share}
        edges = [e for e in profile.get("edges", {}).get(case, {}) if "@" not in e]
        roots |= {p for e in edges for p in e.split(">") if "@" not in p}
        reach = reachable(roots, g, edges)
        hit = sorted(reach & changed)
        out["cases"][case] = {"affected": bool(hit), "via": hit[:8], "reach": len(reach), "roots": len(roots)}
    skipped = sorted(c for c, r in out["cases"].items() if not r["affected"])
    rnd = random.Random(seed)
    n = min(len(skipped), max(1, round(spot * len(skipped)))) if skipped and spot > 0 else 0
    out["spot"] = sorted(rnd.sample(skipped, n))
    if definite:                                  # data changed: every case may be affected
        out["selected"] = sorted(out["cases"])
    else:
        out["selected"] = sorted(c for c, r in out["cases"].items() if r["affected"]) + out["spot"]
    return out


def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="placemat affected", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base")
    p.add_argument("change")
    p.add_argument("profile")
    p.add_argument("--spot", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--min-share", type=float, default=0.0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--ignore", default=PADS, help="regex of functions never counted as changed (default: placemat's pads)")
    a = p.parse_args(argv)
    prof = json.loads(open(a.profile).read())
    r = affected(a.base, a.change, prof, a.min_share, a.spot, a.seed, a.ignore)
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    print(f"changed functions ({len(r['changed'])}): {', '.join(r['changed'][:40])}{' ...' if len(r['changed']) > 40 else ''}")
    if r["data_changed"]:
        print(f"data changed ({', '.join(r['data_changed'])}): every case selected")
    if r["data_maybe"]:
        print(f"constant data differs at the same size ({', '.join(r['data_maybe'])}): may be moved code addresses; not selecting on it")
    na = sum(1 for x in r["cases"].values() if x["affected"])
    print(f"cases: {na} affected of {len(r['cases'])}; spot-check {len(r['spot'])}: {', '.join(r['spot'])}")
    for c, x in sorted(r["cases"].items()):
        if x["affected"]:
            print(f"  {c}: via {', '.join(x['via'])}")
    print("selected: " + ",".join(r["selected"]), file=sys.stderr)
    return 0
