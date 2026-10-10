"""Which runs were timed on a quiet machine (DESIGN §11 item 3)?

    placemat inventory DIR... [--max-noise 0.05]

Lists every raw file (*.raw.json) under the directories: when it ran, its arms and cases, and how
its timing was gated. Classes:
  ungated-old   made before placemat had a noise probe (no probe readings): re-run
  ungated       probe readings recorded, but no max_noise was set: re-run if any reading is noisy
  gated         max_noise set; every batch started quiet
  noisy         max_noise set, but some batch went ahead after the wait (or ended noisy): judge, maybe re-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def classify(raw: dict, limit: float) -> tuple[str, str]:
    lg = raw.get("log", {})
    probes = lg.get("probes")
    mn = raw.get("machine", {}).get("max_noise")
    if not probes:
        return "ungated-old", "no probe readings"
    worst = max(max(p.get("spread", 0), p.get("end_spread", 0)) for p in probes)
    desc = f"worst probe spread {worst:.1%} over {len(probes)} batches"
    if not mn:
        return ("ungated" if worst > limit else "ungated (quiet)"), desc
    return ("gated" if worst <= max(mn, limit) else "noisy"), desc


def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="placemat inventory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dirs", nargs="+")
    p.add_argument("--max-noise", type=float, default=0.05)
    a = p.parse_args(argv)
    rows = []
    for d in a.dirs:
        for f in sorted(Path(d).rglob("*.raw.json")):
            try:
                raw = json.loads(f.read_text())
            except Exception as e:
                rows.append((f, "unreadable", str(e), "", ""))
                continue
            cls, desc = classify(raw, a.max_noise)
            arms = " vs ".join(x["name"] for x in raw.get("arms", []))
            rows.append((f, cls, desc, raw.get("log", {}).get("date", "?"), f"{arms}; {len(raw.get('plan', {}))} cases"))
    print("| run | class | probe | ended | arms, cases |\n|---|---|---|---|---|")
    for f, cls, desc, date, what in rows:
        print(f"| {f} | {cls} | {desc} | {date} | {what} |")
    rerun = [str(f) for f, cls, *_ in rows if cls in ("ungated-old", "ungated", "noisy")]
    print(f"\n{len(rerun)} of {len(rows)} runs to re-run or judge" + (":\n  " + "\n  ".join(rerun) if rerun else "."))
    return 0
