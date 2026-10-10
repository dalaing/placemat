"""Comparing hot-code ordering strategies on the project (DESIGN §6.4, P009): the static half.

For each strategy (``order.strategies``: c3, hotness, density, pettis_hansen, random as a control):

1. order the hot set (on the stock binary, whose sizes and profile offsets the profile refers to)
   and write the order file;
2. build the project with it through the build protocol (``PLACEMAT_ORDER_FILE``; the project's
   build adds the function alignment for a pinned arm), no pads;
3. plan the pads on that build (``pads.pads``, with the hot entry spans from ``select.hot_spans``,
   the C2 rule: loops and spans, no lines, unless ``hot64`` is given);
4. build again with the padded order file and the pad functions (``PLACEMAT_PIN_SOURCE``);
5. verify the result against the plan (``verify.verify(plan=...)``);
6. measure it statically: text and pad bytes, the hot code's footprint in pages of each size
   (profile-weighted: pages touched by sampled hot code, and the fewest pages holding 90% and
   99% of the hot samples), small-loop and hot-span crossings before padding (step 2's build)
   and after (step 4's).

The measured half is an ordinary multi-arm ``placemat run`` with the stock build as the base and
each strategy as an arm (``--order NAME=<dir>/NAME/pinned.order --pin-source NAME=<dir>/NAME/pads.c
--pad-first``): with ``--pad-first`` the code axis moves the whole ordered region, so each arm's
interval covers layouts of its pinned arrangement, not one lucky draw. ``compare`` prints that
command line.

Output (``out``): per strategy a directory with ``order`` (no pads), ``pinned.order``, ``pads.c``,
``plan.json``, ``verify.txt``; ``spans.json``; ``compare.json`` and ``compare.md``.
"""
from __future__ import annotations

import json
import math
import shlex
import sys
from pathlib import Path

from .. import binary as B
from . import profile as _profile
from .order import order_text, strategies
from .pads import is_pad, pad_source, pads as plan_pads
from .select import Selection, hot_spans
from .verify import verify

PAGES = (4096, 16384)


def parse_strategy(spec: str) -> tuple[str, str, dict]:
    """'c3', 'random:7' (seed 7), 'c3:limit=8192' -> (label, strategy, options)."""
    name, _, rest = spec.partition(":")
    if name not in strategies:
        raise ValueError(f"unknown strategy {name!r} (known: {', '.join(sorted(strategies))})")
    opts: dict = {}
    for x in filter(None, rest.split(",")):
        k, eq, v = x.partition("=")
        if not eq:
            k, v = "seed", k
        opts[{"limit": "cluster_limit"}.get(k, k)] = int(v)
    label = name if not rest else name + "-" + "-".join(str(v) for v in opts.values())
    return label, name, opts


def hot_weights(profile: dict, selection) -> dict[str, dict[int, float]]:
    """{function: {offset: weight}}: each selected function's combined hotness (every benchmark set
    counting equally) spread over its sampled offsets in proportion to their counts (all at the
    entry when it has none); the weights sum to 1 over the selection."""
    sel = list(selection.sel if isinstance(selection, Selection) else selection)
    h = _profile.hotness(profile, names=sel)
    tot = sum(h.values()) or 1.0
    out = {}
    for f in sel:
        o = profile["offs"].get(f) or {}
        o = {int(k): v for k, v in o.items() if int(k) >= 0}
        n = sum(o.values())
        out[f] = {k: h[f] / tot * v / n for k, v in o.items()} if n else {0: h[f] / tot}
    return out


def footprint(binary, weights: dict[str, dict[int, float]], page: int) -> dict:
    """Pages of ``page`` bytes holding the sampled hot code in ``binary``: touched (any sample),
    p90 / p99 (fewest pages holding 90% / 99% of the hot weight), and eff (exp of the entropy of
    the weight over pages: the 'effective' number of pages)."""
    F = B.function_map(binary)
    w: dict[int, float] = {}
    for f, offs in weights.items():
        if f not in F:
            continue
        for off, x in offs.items():
            p = (F[f].start + off) // page
            w[p] = w.get(p, 0.0) + x
    tot = sum(w.values()) or 1.0
    ws = sorted((x / tot for x in w.values()), reverse=True)

    def cover(q):
        c = 0.0
        for i, x in enumerate(ws):
            c += x
            if c >= q - 1e-12:
                return i + 1
        return len(ws)
    ent = -sum(x * math.log(x) for x in ws if x > 0)
    return {"touched": len(ws), "p90": cover(0.90), "p99": cover(0.99), "eff": round(math.exp(ent), 2)}


def region(binary, names) -> tuple[int, int]:
    """(start, end) of the listed functions in ``binary``."""
    F = B.function_map(binary)
    fs = [F[n] for n in names if n in F]
    return (min(f.start for f in fs), max(f.end for f in fs)) if fs else (0, 0)


def crossings(binary, names, spans: dict, boundary: int = 4096, max_loop: int = 256) -> dict:
    """Small loops of the listed functions, and their hot entry spans, straddling ``boundary``."""
    listed = {n for n in names if not is_pad(n)}
    F = B.function_map(binary)
    lc = [l for l in B.loops(binary, max_loop) if l.func in listed and B.crosses(l.start, l.end, boundary)]
    sc = [n for n, ln in spans.items() if n in listed and n in F and B.crosses(F[n].start, F[n].start + ln, boundary)]
    return {"loops": len(lc), "loop_functions": sorted({l.func for l in lc}), "spans": len(sc), "span_functions": sorted(sc)}


def measures(binary, names, weights, spans, boundary: int = 4096, max_loop: int = 256) -> dict:
    t0, tz = B.text_section(binary)
    r0, r1 = region(binary, names)
    out = {"text": tz, "region": r1 - r0, "crossings": crossings(binary, names, spans, boundary, max_loop)}
    for p in PAGES:
        out[f"pages{p // 1024}k"] = footprint(binary, weights, p)
    return out


class _Builds:
    """Builds through the project's build protocol (placemat.build), under its shared lock."""

    def __init__(self, config, work, source: str):
        from .. import config as C
        from ..build import Builder
        self.cfg = C.load(config)
        self.B = Builder(self.cfg, Path(work).resolve())
        self.source = source

    def build(self, name: str, order=None, pin_source=None) -> Path:
        from .. import lock
        a = self.B.arm(name, self.source, order=str(order) if order else None,
                       pin_source=str(pin_source) if pin_source else None)
        with lock.hold([self.cfg.expand(x) for x in self.cfg.shared_lock]):
            return self.B.binary(a, None)


def compare(config, source: str, profile, selection, specs: list[str], out, work=None, binary=None,
            boundary: int = 4096, align: int = 16, max_loop: int = 256, spans=None, hot64=None,
            weights=(10**6, 10**4, 10**3, 1), linker=None, say=None) -> dict:
    """Build and measure each strategy in ``specs`` (see the module docstring). ``source``: the
    project tree (a directory or git:REV) as ``placemat run --arm`` takes it; ``binary``: the stock
    build the profile was taken on (default: built from ``source``); ``spans``: hot entry spans
    (default: ``hot_spans`` of the profile over the selection); ``linker``: the order-file style
    ('ld64', 'lld', 'gnu'; default from the binary's format)."""
    say = say or (lambda *a: print("placemat pin compare:", *a, file=sys.stderr, flush=True))
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    prof = _profile.load(profile) if isinstance(profile, (str, Path)) else profile
    if isinstance(selection, (str, Path)):
        d = json.loads(Path(selection).read_text())
        selection = list(d["sel"] if isinstance(d, dict) else d)
    sel = list(selection.sel if isinstance(selection, Selection) else selection)
    bld = _Builds(config, work or (Path(config).resolve().parent / ".placemat"), source)
    stock = Path(binary) if binary else bld.build("stock")
    say(f"stock binary {stock}")
    if spans is None:
        spans = hot_spans(prof, stock, selection=sel)
    elif isinstance(spans, (str, Path)):
        spans = json.loads(Path(spans).read_text())
    (out / "spans.json").write_text(json.dumps(spans) + "\n")
    hw = hot_weights(prof, sel)
    res = {"source": source, "stock": str(stock), "boundary": boundary, "align": align, "selection": len(sel),
           "spans": len(spans), "strategies": {}}
    res["stock_measures"] = measures(stock, sel, hw, spans, boundary, max_loop)
    for spec in specs:
        label, name, opts = parse_strategy(spec)
        d = out / label
        d.mkdir(exist_ok=True)
        names = strategies[name](str(stock), prof, sel, **opts)
        missing = [f for f in sel if f not in names]
        (d / "order").write_text(order_text(names, linker or str(stock)))
        say(f"{label}: {len(names)} functions ordered" + (f", {len(missing)} not in the binary" if missing else ""))
        b0 = bld.build(f"{label}-nopad", order=d / "order")
        plan = plan_pads(b0, names, boundary, align, max_loop, spans, hot64, weights, 64,
                         d / "pinned.order", d / "pads.c", linker)
        (d / "plan.json").write_text(json.dumps(plan.to_json()) + "\n")
        say(f"{label}: {plan.summary()}")
        b1 = bld.build(label, order=d / "pinned.order", pin_source=d / "pads.c")
        rep = verify(b1, str(d / "pinned.order"), boundary, spans, max_loop, align, hot64, 64, plan=plan)
        (d / "verify.txt").write_text(str(rep) + "\n")
        if rep.misplaced:                       # the pads were planned for addresses the code did not get
            from ..build import misplaced_message
            raise SystemExit(misplaced_message(label, rep, b1, B.fmt(b1), plan.boundary))
        say(f"{label}: verify {'OK' if rep.ok else 'FAILED'}")
        m0 = measures(b0, names, hw, spans, boundary, max_loop)
        m1 = measures(b1, plan.order(), hw, spans, boundary, max_loop)
        res["strategies"][label] = {
            "strategy": name, "options": opts, "functions": len(names), "missing": missing,
            "binary_nopad": str(b0), "binary": str(b1), "pads": len(plan.pads), "pad_bytes": plan.pad_bytes,
            "plan_residual": {"loops": len(plan.loop_crossings), "spans": len(plan.span_crossings)},
            "verify_ok": rep.ok, "verify": rep.lines()[:12],
            "before": m0, "after": m1,
            "order": str(d / "order"), "pinned_order": str(d / "pinned.order"), "pin_source": str(d / "pads.c"),
        }
    (out / "compare.json").write_text(json.dumps(res, indent=1) + "\n")
    (out / "compare.md").write_text(markdown(res) + "\n")
    return res


def markdown(res: dict) -> str:
    s0 = res["stock_measures"]
    L = [f"# Ordering strategies: static measures", "",
         f"Source {res['source']}; {res['selection']} hot functions, {res['spans']} hot entry spans; boundary "
         f"{res['boundary']}, functions aligned to {res['align']}. Pages: touched by sampled hot code / fewest "
         "holding 90% / 99% of the hot samples (profile-weighted, each benchmark set counting equally). "
         "Crossings: small loops (<=256 B) of the hot functions / hot entry spans straddling the boundary, "
         "before padding (ordered, aligned, no pads) and after (verified build).", "",
         "| strategy | text bytes | vs stock | pads | pad bytes | hot region | 4 KB pages | 16 KB pages "
         "| crossings before | after | verify |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]

    def pg(m, k):
        x = m[k]
        return f"{x['touched']} / {x['p90']} / {x['p99']}"
    c = s0["crossings"]
    L.append(f"| stock (unordered) | {s0['text']} | +0 | - | - | {s0['region']} | {pg(s0, 'pages4k')} | "
             f"{pg(s0, 'pages16k')} | {c['loops']} / {c['spans']} | - | - |")
    for lab, r in res["strategies"].items():
        a, b = r["after"], r["before"]
        L.append(f"| {lab} | {a['text']} | {a['text'] - s0['text']:+d} | {r['pads']} | {r['pad_bytes']} | {a['region']} "
                 f"| {pg(a, 'pages4k')} | {pg(a, 'pages16k')} | {b['crossings']['loops']} / {b['crossings']['spans']} "
                 f"| {a['crossings']['loops']} / {a['crossings']['spans']} | {'OK' if r['verify_ok'] else 'FAILED'} |")
    return "\n".join(L)


def run_command(res: dict, config: str, work: str | None = None) -> str:
    """The measured comparison: one multi-arm placemat run, stock as the base, every strategy an arm."""
    args = ["python3", "-m", "placemat", "run", "--config", config]
    if work:
        args += ["--work", work]
    args += ["--arm", f"stock={res['source']}"]
    for lab, r in res["strategies"].items():
        args += ["--arm", f"{lab}={res['source']}", "--order", f"{lab}={r['pinned_order']}",
                 "--pin-source", f"{lab}={r['pin_source']}"]
    args += ["--pad-first", "--data", "none", "--cases", "..."]
    return " ".join(shlex.quote(x) for x in args)
