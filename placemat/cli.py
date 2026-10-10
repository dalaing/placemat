"""placemat's command line.

    placemat run      --config F --arm NAME=SRC ... --cases C,...   time arms across designed layout variants
    placemat survey   (as run, with the survey's defaults: both data kinds, a fixed 12 pads (16 without data))
    placemat check    (as run, with the steady state's defaults: --data from the config, first batch only)
    placemat reanalyse RAW.json [--out FILE.md]                    the report again from raw timings
    placemat design   [--seed N] [--pads M] [--data D] [--step-mode M]   print a design's variants and coverage
    placemat culprits RAW.json CASE [--arm A] [--hot FILE] [--alpha A]  crossing loops; address phase with a permutation p
    placemat twospeed RAW.json [CASE-SUBSTRING...]                 run-to-run two-speed switching per case
    placemat inventory DIR...                                      which runs were timed on a quiet machine
    placemat noiselog [--every 60] [--out PATH] [--for S]          a continuous noise record (no lock; run while timing)
    placemat affected BASE_BIN CHANGE_BIN PROFILE.json             steady state: cases whose reachable code changed
    placemat binary   {funcs,loops,shifts,samefn} ...              binary analysis (Mach-O, ELF)
    placemat pin      {profile,pick,order,spans,pads,verify,compare} ...   pinning hot code

SRC is a directory or git:<rev> (a revision of the configured project). Cases are 'suite: name' or a
bare name that one suite lists; --cases takes a comma-separated list (';' when names hold commas),
--cases-file one case per line (or the same separators).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _cases(a) -> list[str]:
    out = []
    for s in ([a.cases] if a.cases else []) + ([Path(a.cases_file).read_text()] if a.cases_file else []):
        s = s.strip()
        sep = ";" if ";" in s else ("\n" if "\n" in s else ",")
        out += [x.strip() for x in s.split(sep) if x.strip() and not x.strip().startswith("#")]
    return out


def cmd_run(a, mode: str = "run") -> int:
    from . import config, report
    from .design import Design
    from .runner import Runner
    cfg = config.load(a.config)
    data = a.data or ("none" if cfg.data_method == "none" else "both" if mode == "survey" else cfg.vary)
    start, cap = a.start, a.cap
    if mode == "check" and cap is None:             # the steady state: the first batch only
        cap = start or (8 if data == "none" else 4)
    if mode == "survey":                            # a fixed, generous pad count: adaptive stopping on the change
        start = start or (16 if data == "none" else 12)   # interval stops a null run at its first batch, where the
        cap = cap or start                          # code test has little power (Lua survey, P010)
    D = Design(seed=a.seed, data=data, step_mode=a.step_mode or cfg.step_mode, unit=cfg.unit,
               colour_span=cfg.colour_span, step_span=cfg.step_span, period=cfg.boundary, line=cfg.line,
               pads=[int(x) for x in a.pads.split(",")] if a.pads else [], start=start, batch_step=a.step, cap=cap)
    def per_arm(opts):
        return dict(x.split("=", 1) for x in opts or [])
    orders, pins, replans, spans = per_arm(a.order), per_arm(a.pin_source), per_arm(a.pin), per_arm(a.pin_spans)
    arms = []
    for x in a.arm:
        n, _, s = x.partition("=")
        if not s:
            n, s = (f"arm{len(arms)}" if arms else "base"), n
        arms.append({"name": n, "source": s, "order": orders.get(n), "pin_source": pins.get(n),
                     "pin_order": replans.get(n), "pin_spans": spans.get(n),
                     "pad_first": a.pad_first and (n in orders or n in replans)})
    if len(arms) < 2:
        sys.exit("placemat: give at least two --arm NAME=SRC (the first is the base)")
    cases = _cases(a)
    if not cases:
        sys.exit("placemat: no cases (--cases or --cases-file)")
    work = Path(a.work or (cfg.config_dir / ".placemat")).resolve()
    out = Path(a.out or (work / "runs")).resolve()
    name = a.name or time.strftime("%Y%m%d-%H%M%S")
    R = Runner(cfg, work, arms, D, rounds=a.rounds)
    if getattr(a, "affected", None):                # the steady state: only the cases a change can reach
        import json as _json
        from .affected import affected as _affected
        prof = _json.loads(Path(a.affected).read_text())
        base = R.B.binary(R.arms[0], None)
        keep, notes = set(), []
        for arm in R.arms[1:]:
            sel = _affected(base, R.B.binary(arm, None), prof, spot=a.spot, seed=a.seed)
            keep |= set(sel["selected"])
            notes.append(f"{arm.name}: {len(sel['changed'])} changed functions, "
                         f"{sum(1 for x in sel['cases'].values() if x['affected'])} affected cases, "
                         f"spot-check {len(sel['spot'])}" + (f"; data changed ({', '.join(sel['data_changed'])}): all cases"
                                                             if sel["data_changed"] else ""))
        before = list(cases)
        from .runner import plan_cases as _plan
        def _key(c):                                  # a bare name resolved to its 'suite: name' key
            pl = _plan(cfg, [c]).cases
            return next(iter(pl), c)
        cases = [c for c in cases if c in keep or _key(c) in keep or _key(c).split(": ", 1)[-1] in keep]
        R.log["selection"] = {"profile": str(a.affected), "notes": notes, "timed": cases,
                              "skipped": [c for c in before if c not in cases]}
        print("placemat: " + "; ".join(notes) + f"; timing {len(cases)} of {len(before)} cases", file=sys.stderr)
        if not cases:
            print("placemat: no case is affected; nothing to time", file=sys.stderr)
            return 0
    raw = R.run(cases, out / f"{name}.raw.json")
    report.write(report.load_raw(out / f"{name}.raw.json"), out / f"{name}.md", a.title)
    print(out / f"{name}.md")
    return 0


def cmd_reanalyse(a) -> int:
    from . import report
    raw = report.load_raw(Path(a.raw))
    if a.base:
        raw = report.rebase(raw, a.base)
    if a.out:
        report.write(raw, Path(a.out), a.title)
        print(a.out)
    else:
        print(report.markdown(raw, title=a.title))
    return 0


def cmd_design(a) -> int:
    from .design import Design
    D = Design(seed=a.seed, data=a.data or "both", step_mode=a.step_mode or "hashed")
    for j in range(a.pads_n):
        print(j, D.pad(j), *(D.setting(j, k).label() for k in D.kinds), sep="\t")
    print("\n".join(D.describe(a.pads_n)))
    return 0


def cmd_twospeed(a) -> int:
    from . import report
    print("\n".join(report.twospeed(report.load_raw(Path(a.raw)), a.pats)))
    return 0


def cmd_culprits(a) -> int:
    from .culprits import culprits
    print("\n".join(culprits(Path(a.raw), a.case, a.arm, Path(a.hot) if a.hot else None, alpha=a.alpha)))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["binary"]:
        from . import binary
        return binary.cli(argv[1:])
    if argv[:1] == ["inventory"]:
        from . import inventory
        return inventory.cli(argv[1:])
    if argv[:1] == ["noiselog"]:
        from . import noiselog
        return noiselog.cli(argv[1:])
    if argv[:1] == ["affected"]:
        from . import affected
        return affected.cli(argv[1:])
    if argv[:1] == ["pin"]:
        from . import pin
        return pin.cli(argv[1:])
    p = argparse.ArgumentParser(prog="placemat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    for nm in ("run", "survey", "check"):
        r = sub.add_parser(nm)
        r.add_argument("--config", required=True)
        r.add_argument("--arm", action="append", default=[], help="NAME=SRC (directory or git:REV); the first is the base")
        r.add_argument("--cases")
        r.add_argument("--cases-file")
        r.add_argument("--seed", type=int, default=0)
        r.add_argument("--data", choices=["none", "colour", "step", "both"])
        r.add_argument("--step-mode", choices=["hashed", "linear"])
        r.add_argument("--pads", help="explicit pads first (bytes, comma-separated): targeted variants")
        r.add_argument("--order", action="append", help="NAME=FILE: link arm NAME with this order file (PLACEMAT_ORDER_FILE)")
        r.add_argument("--pin-source", action="append", help="NAME=FILE: arm NAME's pad functions (PLACEMAT_PIN_SOURCE)")
        r.add_argument("--affected", metavar="PROFILE", help="time only the cases whose reachable code changed "
                       "(a profile with one set per case; `placemat affected`), plus a random spot-check of the rest")
        r.add_argument("--spot", type=float, default=0.1, help="with --affected: the share of skipped cases spot-checked")
        r.add_argument("--pin", action="append", help="NAME=ORDER: pin arm NAME with this order (no pin pads in it), "
                       "planning its pin pads afresh for every build at that build's position")
        r.add_argument("--pin-spans", action="append", help="NAME=FILE: hot entry spans (JSON) for --pin's planning")
        r.add_argument("--pad-first", action="store_true",
                       help="list the code-axis pad first in pinned arms' order files, so the ordered region moves (P009)")
        r.add_argument("--start", type=int)
        r.add_argument("--step", type=int)
        r.add_argument("--cap", type=int)
        r.add_argument("--rounds", type=int)
        r.add_argument("--work")
        r.add_argument("--out")
        r.add_argument("--name")
        r.add_argument("--title")
    r = sub.add_parser("reanalyse")
    r.add_argument("raw")
    r.add_argument("--base", help="compare every arm with this one instead of the run's first arm")
    r.add_argument("--out")
    r.add_argument("--title")
    r = sub.add_parser("design")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--pads", dest="pads_n", type=int, default=12)
    r.add_argument("--data", choices=["none", "colour", "step", "both"])
    r.add_argument("--step-mode", choices=["hashed", "linear"])
    r = sub.add_parser("twospeed")
    r.add_argument("raw")
    r.add_argument("pats", nargs="*")
    r = sub.add_parser("culprits")
    r.add_argument("raw")
    r.add_argument("case")
    r.add_argument("--arm")
    r.add_argument("--hot")
    r.add_argument("--alpha", type=float, default=0.05)
    a = p.parse_args(argv)
    if a.cmd in ("run", "survey", "check"):
        return cmd_run(a, a.cmd)
    if a.cmd == "reanalyse":
        return cmd_reanalyse(a)
    if a.cmd == "design":
        return cmd_design(a)
    if a.cmd == "twospeed":
        return cmd_twospeed(a)
    if a.cmd == "culprits":
        return cmd_culprits(a)
    p.print_help()
    return 2
