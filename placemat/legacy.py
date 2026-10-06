"""Import raw timings from the Amber prototype stage (pbt/ldesign.py) into placemat's raw format, so
the reference runs can be re-analysed with placemat's statistics (the regression test's baseline).

The prototype numbered variants: with data, variant 2j was pad j at colour 0 and 2j+1 pad j at its
colour, a linear step over per-class slot indices (placemat's 'linear' step mode); without data,
variant i was pad i. Its batches counted variants (start 8, step 4, cap 24).
"""
from __future__ import annotations

import json
from pathlib import Path

from .design import STEP, STOCK


def convert(path: Path) -> dict:
    r = json.loads(Path(path).read_text())
    data = r["data"]
    per = 2 if data else 1
    start, step, cap = r.get("start", 8), r.get("step", 4), r.get("cap", 24)
    design = {"seed": r["seed"], "data": "step" if data else "none", "step_mode": "linear", "unit": 64,
              "colour_span": 16384, "step_span": 16384, "period": 4096, "line": 64, "pads": r.get("pads") or [],
              "start": start // per, "batch_step": step // per, "cap": cap // per}

    def slot(i: str) -> str:
        if not i.isdigit():
            return i                      # 'stock', 'a1', ...
        i = int(i)
        if not data:
            return f"{i}.{STOCK}"
        return f"{i // 2}.{STOCK if i % 2 == 0 else STEP}"
    times = {k: {who: {slot(i): v for i, v in d.items()} for who, d in w.items()} for k, w in r["times"].items()}
    return {"version": 1, "project": "amber (prototype raw)", "config": None, "design": design,
            "arms": [{"name": "base", "source": "prototype base", "tree": ""},
                     {"name": "branch", "source": "prototype branch", "tree": ""}],
            "rounds": r["rounds"], "plan": r["plan"], "series": {k: v for k, (_, nm) in r["plan"].items() for n, v in r.get("series", {}).items() if n == nm},
            "K": {k: v // per for k, v in r["K"].items()}, "times": times,
            "thresholds": {}, "target": r.get("target", 0.01), "stock_placement": True,
            "covariate": "base", "data_method": "hook" if data else "none", "log": {}, "geometry": {}}
