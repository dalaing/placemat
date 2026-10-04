#!/usr/bin/env python3
"""csum.py FILE.layouts.md [--exclude PREFIX,...]: the designed stage's table (--no-data): geomean of the
layout-averaged changes with an interval (as of/tools/summ.py: normal approximation from each case's
interval), every case whose interval excludes 0, and the measurable ones."""
import sys, math, re
f = sys.argv[1]; ex = sys.argv[sys.argv.index("--exclude") + 1].split(",") if "--exclude" in sys.argv else []
pct = lambda s: float(s.strip().rstrip('%')) / 100
rows = []
for l in open(f):
    if not l.startswith('| `'): continue
    c = [x.strip() for x in l.strip().strip('|').split('|')]
    name = c[0].strip('`').replace('` (series)', '').strip('`')
    if any(name.startswith(e) for e in ex): continue
    lo, hi = [pct(x) for x in c[3].split(' to ')]
    rows.append(dict(case=name, stock=c[1], ch=pct(c[2]), lo=lo, hi=hi, K=c[4], code=c[5], verdict=c[-1].strip('* '), att=c[-2]))
lg = [math.log(1 + r['ch']) for r in rows]
se = [(math.log(1 + r['hi']) - math.log(1 + r['lo'])) / 3.92 for r in rows]
g = sum(lg) / len(lg); s = math.sqrt(sum(x * x for x in se)) / len(se)
print(f"{len(rows)} cases: geomean {math.exp(g)-1:+.2%} ({math.exp(g-1.96*s)-1:+.2%}, {math.exp(g+1.96*s)-1:+.2%})")
for nm, sel in (("slower, interval above 0", lambda r: r['lo'] > 0), ("faster, interval below 0", lambda r: r['hi'] < 0)):
    R = sorted([r for r in rows if sel(r)], key=lambda r: r['ch'])
    print(f"{nm}: {len(R)}")
    for r in R:
        print(f"  {r['case']}: {r['ch']:+.1%} ({r['lo']:+.1%}, {r['hi']:+.1%}) K{r['K']} {r['verdict']} {r['att']} | stock {r['stock']}")
fl = [r for r in rows if '**code**' in r['code']]
print(f"code-flagged: {len(fl)}: " + ", ".join(f"{r['case']} ({r['code']})" for r in fl))
