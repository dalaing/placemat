#!/usr/bin/env python3
"""twospeed.py RAW.json [CASE-SUBSTRING...]: run-to-run spread per case and version from a stage run's raw
timings. Per variant, each round's value over the variant's median; pooled over variants: the spread
(p90/p10), the share of rounds more than 15% above their variant's median (the slow speed), and the
largest within-variant max/min."""
import json, sys, statistics
r = json.load(open(sys.argv[1])); pats = sys.argv[2:]
print(f"| case | base p90/p10 | base slow rounds | base max/min | branch p90/p10 | branch slow rounds | branch max/min | branch/base (median) |")
print("|---|---|---|---|---|---|---|---|")
for key, t in r["times"].items():
    if pats and not any(p in key for p in pats):
        continue
    row = [key]
    med = {}
    for who in ("base", "branch"):
        rel, mm, allv = [], [], []
        for i, vs in t[who].items():
            v = [x[0] for x in vs if x[0]]
            if len(v) < 3: continue
            m = statistics.median(v); rel += [x / m for x in v]; mm.append(max(v) / min(v)); allv += v
        rel.sort(); n = len(rel)
        p = lambda q: rel[min(n - 1, int(q * n))]
        med[who] = statistics.median(allv)
        row += [f"{p(0.9)/p(0.1):.3f}", f"{sum(x > 1.15 for x in rel)}/{n}", f"{max(mm):.2f}"]
    row.append(f"{med['branch']/med['base']:.3f}")
    print("| " + " | ".join(row) + " |")
