"""Pinning hot code (DESIGN §6.3): profile import, hot-set choice, ordering, pad search, verification.

Pipeline::

    placemat pin profile --binary BIN --sample --set m='prof/m/*.txt' --set o='prof/o/*.txt' -o prof.json
    placemat pin pick prof.json -o sel.json
    placemat pin order BIN prof.json sel.json -o hot.order          # build BIN0: order + align, no pads
    placemat pin spans prof.json BIN sel.json -o spans.json
    placemat pin pads BIN0 hot.order --spans spans.json --out-order pinned.order --out-c pads.c
    # build with pinned.order, pads.c, -falign-functions=16 (ELF: -ffunction-sections)
    placemat pin verify BIN1 pinned.order --spans spans.json

    # P009: build and measure every ordering strategy through the project's build protocol
    placemat pin compare --config placemat.toml --source DIR prof.json sel.json -o DIR \
        --strategy c3 --strategy hotness --strategy random:1 ...

Modules: profile (import), select (pick, hot_spans, hot_loops64), order (C3 and other strategies,
order files), pads (the pad search), verify (the placement check), compare (ordering strategies
built, padded, verified and measured statically: DESIGN §6.4).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pads as _pads
from . import profile as _profile
from . import verify as _verify
from .order import c3_clusters, order_text, strategies, write_order
from .pads import Plan, pad_source
from .profile import from_perf, from_sample
from .select import Selection, hot_loops64, hot_spans, pick
from .verify import Report

# The functions order(), pads() and verify() live in the like-named modules
# (placemat.pin.order.order, placemat.pin.pads.pads, placemat.pin.verify.verify); the package
# attributes order, pads and verify are those modules.
__all__ = ["from_sample", "from_perf", "pick", "Selection", "hot_spans", "hot_loops64", "c3_clusters",
           "strategies", "order_text", "write_order", "Plan", "pad_source", "Report", "cli",
           "profile", "select", "order", "pads", "verify"]


def _sets(specs: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in specs:
        name, _, paths = s.partition("=")
        if not paths:
            name, paths = "all", s
        out.setdefault(name, []).extend(p for p in paths.split(",") if p)
    return out


def _load_sel(path) -> list[str]:
    d = json.loads(Path(path).read_text())
    return list(d["sel"] if isinstance(d, dict) else d)


def _weights(s: str) -> tuple:
    w = tuple(float(x) for x in s.split(","))
    if len(w) != 4:
        raise argparse.ArgumentTypeError("weights: loop,span,line,byte")
    return w


def _out(text: str, path: str | None):
    if path and path != "-":
        Path(path).write_text(text)
    else:
        sys.stdout.write(text)


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="placemat pin", description="Pin hot code: profile, pick, order, pads, verify.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile", help="import profiles into placemat's profile JSON")
    p.add_argument("--binary", required=True, help="the profiled binary (addresses are attributed with its symbols)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample", action="store_true", help="macOS sample text reports")
    g.add_argument("--perf", action="store_true", help="Linux perf script output (with call chains)")
    p.add_argument("--set", action="append", default=[], metavar="NAME=PATHS",
                   help="a benchmark set: comma-separated files, directories or globs (repeatable)")
    p.add_argument("--image", help="the binary's name in the reports (default: its file name)")
    p.add_argument("--text", help="link-time text range START:SIZE (hex), default from the binary")
    p.add_argument("-o", "--output", required=True)

    p = sub.add_parser("pick", help="choose the hot set")
    p.add_argument("profile")
    p.add_argument("--target", type=float, default=0.95)
    p.add_argument("--sets", help="comma-separated sets (default: all)")
    p.add_argument("-o", "--output")

    p = sub.add_parser("order", help="order the hot set and write an order file")
    p.add_argument("binary")
    p.add_argument("profile")
    p.add_argument("selection")
    p.add_argument("--strategy", default="c3", choices=sorted(strategies))
    p.add_argument("--limit", type=int, default=16384, help="C3 cluster size limit in bytes")
    p.add_argument("--seed", type=int, default=0, help="random strategy seed")
    p.add_argument("--linker", choices=["ld64", "lld", "gnu"], help="order file style (default from the binary)")
    p.add_argument("-o", "--output")

    p = sub.add_parser("spans", help="hot entry spans (and hottest small loops) from a profile")
    p.add_argument("profile")
    p.add_argument("binary")
    p.add_argument("selection", nargs="?")
    p.add_argument("--min-share", type=float, default=0.10)
    p.add_argument("--max-bytes", type=int, default=1024)
    p.add_argument("--hot64", help="also write the hottest small loops (<= --line bytes) here")
    p.add_argument("--line", type=int, default=64)
    p.add_argument("-o", "--output")

    p = sub.add_parser("pads", help="compute pad functions for an order")
    p.add_argument("binary", help="built with the order file, no pads, -falign-functions=ALIGN")
    p.add_argument("order")
    p.add_argument("--boundary", type=int, default=4096)
    p.add_argument("--align", type=int, default=16)
    p.add_argument("--max-loop", type=int, default=256)
    p.add_argument("--line", type=int, default=64)
    p.add_argument("--spans")
    p.add_argument("--hot64")
    p.add_argument("--weights", type=_weights, default=(10**6, 10**4, 10**3, 1), help="loop,span,line,byte")
    p.add_argument("--linker", choices=["ld64", "lld", "gnu"])
    p.add_argument("--out-order", required=True)
    p.add_argument("--out-c", required=True)
    p.add_argument("--plan", help="write the plan as JSON here")

    p = sub.add_parser("verify", help="check a linked binary against its order file (exit 1 on failure)")
    p.add_argument("binary")
    p.add_argument("order")
    p.add_argument("--boundary", type=int, default=4096)
    p.add_argument("--align", type=int, default=16, help="0 skips the alignment check")
    p.add_argument("--max-loop", type=int, default=256)
    p.add_argument("--spans")
    p.add_argument("--hot64")
    p.add_argument("--line", type=int, default=64)
    p.add_argument("--plan", help="the pad plan (`pin pads --plan`): every planned function must sit at its "
                                  "planned offset mod the boundary")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("compare", help="build, pad, verify and measure ordering strategies (P009)")
    p.add_argument("profile")
    p.add_argument("selection")
    p.add_argument("--config", required=True, help="the project's placemat.toml (its build command builds the strategies)")
    p.add_argument("--source", required=True, help="the project tree: a directory or git:REV")
    p.add_argument("--work", help="placemat's work directory (default: .placemat next to the config)")
    p.add_argument("--binary", help="the stock build the profile was taken on (default: built from --source)")
    p.add_argument("--strategy", action="append", default=[], metavar="NAME[:SEED|:limit=N]",
                   help=f"repeatable; known: {', '.join(sorted(strategies))} (default: all, random with seeds 1 and 2)")
    p.add_argument("--boundary", type=int, default=4096)
    p.add_argument("--align", type=int, default=16)
    p.add_argument("--max-loop", type=int, default=256)
    p.add_argument("--spans", help="hot entry spans JSON (default: from the profile, as `pin spans`)")
    p.add_argument("--hot64")
    p.add_argument("--weights", type=_weights, default=(10**6, 10**4, 10**3, 1), help="loop,span,line,byte")
    p.add_argument("--linker", choices=["ld64", "lld", "gnu"], help="order file style (default from the binary)")
    p.add_argument("-o", "--output", required=True, help="directory for order files, pads, plans and compare.{json,md}")

    a = ap.parse_args(argv)
    if a.cmd == "compare":
        from . import compare as _compare
        specs = a.strategy or ["c3", "hotness", "density", "pettis_hansen", "random:1", "random:2"]
        res = _compare.compare(a.config, a.source, a.profile, a.selection, specs, a.output, a.work, a.binary,
                               a.boundary, a.align, a.max_loop, a.spans, a.hot64, a.weights, a.linker)
        print(_compare.markdown(res))
        print("\nmeasured comparison:\n  " + _compare.run_command(res, a.config, a.work))
        return 0 if all(r["verify_ok"] for r in res["strategies"].values()) else 1
    if a.cmd == "profile":
        sets = _sets(a.set)
        if not sets:
            ap.error("profile: give at least one --set")
        text = None
        if a.text:
            s, _, z = a.text.partition(":")
            text = (int(s, 16), int(z, 16))
        fn = from_sample if a.sample else from_perf
        prof = fn(sets, a.binary, image=a.image, text=text)
        _profile.save(prof, a.output)
        for s in prof["n"]:
            ib = sum(_profile.in_binary(prof, s).values())
            top = sorted(_profile.in_binary(prof, s).items(), key=lambda kv: -kv[1])[:8]
            print(f"{s}: {prof['n'][s]} cases, in-binary share {ib:.3f}; "
                  + " ".join(f"{k} {v:.3f}" for k, v in top))
    elif a.cmd == "pick":
        prof = _profile.load(a.profile)
        sel = pick(prof, a.target, a.sets.split(",") if a.sets else None)
        _out(json.dumps(sel.to_json()) + "\n", a.output)
        cov = "; ".join(f"{s} {c[0]:.3f} of in-binary, {c[1]:.3f} of all" for s, c in sel.cov.items())
        print(f"{len(sel.sel)} functions; coverage {cov}", file=sys.stderr)
    elif a.cmd == "order":
        prof = _profile.load(a.profile)
        sel = _load_sel(a.selection)
        names = strategies[a.strategy](a.binary, prof, sel, cluster_limit=a.limit, seed=a.seed)
        missing = [f for f in sel if f not in names]
        if missing:
            print(f"not in the binary ({len(missing)}): {' '.join(missing[:20])}", file=sys.stderr)
        _out(order_text(names, a.linker or a.binary), a.output)
    elif a.cmd == "spans":
        prof = _profile.load(a.profile)
        sel = _load_sel(a.selection) if a.selection else None
        sp = hot_spans(prof, a.binary, a.min_share, a.max_bytes, selection=sel)
        _out(json.dumps(sp) + "\n", a.output)
        if a.hot64:
            Path(a.hot64).write_text(json.dumps(hot_loops64(prof, a.binary, a.line, selection=sel)) + "\n")
        print(f"{len(sp)} hot entry spans", file=sys.stderr)
    elif a.cmd == "pads":
        plan = _pads.pads(a.binary, a.order, a.boundary, a.align, a.max_loop, a.spans, a.hot64, a.weights, a.line,
                    a.out_order, a.out_c, a.linker)
        if a.plan:
            Path(a.plan).write_text(json.dumps(plan.to_json()) + "\n")
        print(plan.summary())
        for n, s, e in plan.loop_crossings:
            print(f"  still crossing: loop {n}+{s}..{e}")
        for n, ln in plan.span_crossings:
            print(f"  still crossing: hot span {n} ({ln} B)")
    elif a.cmd == "verify":
        plan = Plan.from_json(json.loads(Path(a.plan).read_text())) if a.plan else None
        r = _verify.verify(a.binary, a.order, a.boundary, a.spans, a.max_loop, a.align or None, a.hot64, a.line,
                           plan=plan)
        if a.json:
            import dataclasses
            print(json.dumps(dataclasses.asdict(r)))
        else:
            print(r)
        return 0 if r.ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
