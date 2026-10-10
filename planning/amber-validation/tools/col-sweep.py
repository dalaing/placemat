#!/usr/bin/env python3
"""sweep.py BIN SCRIPT PREFIX REPS d1,d2,... [A]: time SCRIPT under AMBER_COLSEQ=PREFIX,A,A+d (the
large allocations before the case at colour 0 except the input at A, the output at A+d mod 16 KB);
prints per d the min and median (us) over REPS processes, interleaved in random order."""
import os, random, subprocess, sys, statistics, json
b, script, prefix, reps, ds = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), [int(x) for x in sys.argv[5].split(",")]
A = int(sys.argv[6]) if len(sys.argv) > 6 else 0
res = {d: {} for d in ds}
env0 = {k: v for k, v in os.environ.items() if not k.startswith("AMBER_COL")}
env0.update(AMBER_THREADS="1", AMBER_NO_EDIT="1", AMBER_DIAG="0")
print("load", os.getloadavg(), file=sys.stderr)
for r in range(reps):
    order = ds[:]; random.shuffle(order)
    for d in order:
        seq = (prefix + "," if prefix else "") + f"{A},{(A + d) % 16384}"
        out = subprocess.run([b, script], env=dict(env0, AMBER_COLSEQ=seq), capture_output=True, text=True).stdout
        for l in out.splitlines():
            p = l.split()
            if len(p) == 2 and p[1].isdigit():
                res[d].setdefault(p[0], []).append(int(p[1]))
print("load", os.getloadavg(), file=sys.stderr)
names = sorted({k for d in ds for k in res[d]})
print("d\td%4096\t" + "\t".join(f"{n}_min\t{n}_med" for n in names))
for d in ds:
    print(f"{d}\t{d % 4096}\t" + "\t".join(f"{min(res[d][n])}\t{statistics.median(res[d][n]):.0f}" for n in names))
json.dump({str(d): res[d] for d in ds}, open(os.environ.get("SWEEP_JSON", "/dev/null"), "w"))
