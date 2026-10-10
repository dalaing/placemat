#!/usr/bin/env python3
"""qt.py SCRIPT REPS DIR...: quick check (not the stage): each build runs SCRIPT REPS times, interleaved; per case the
median us per build and the ratio to the first build."""
import os, random, subprocess, sys, statistics
script, reps, dirs = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
res = {d: {} for d in dirs}
env = dict(os.environ, AMBER_THREADS="1", AMBER_NO_EDIT="1", AMBER_DIAG="0", NO_COLOR="1")
print("load", os.getloadavg(), file=sys.stderr)
for r in range(reps):
    o = dirs[:]; random.shuffle(o)
    for d in o:
        out = subprocess.run([d + "/amber", script], cwd=d, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        for l in out.splitlines():
            p = l.split()
            if len(p) == 2 and p[1].isdigit(): res[d].setdefault(p[0], []).append(int(p[1]))
print("load", os.getloadavg(), file=sys.stderr)
names = list(res[dirs[0]])
print("case\t" + "\t".join(os.path.basename(d) for d in dirs))
for n in names:
    m = [statistics.median(res[d][n]) for d in dirs]
    print(n + "\t" + "\t".join(f"{x:.0f}" + ("" if i == 0 else f" ({x/m[0]-1:+.1%})") for i, x in enumerate(m)))
