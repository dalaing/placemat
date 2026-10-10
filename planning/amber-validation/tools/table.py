import sys,statistics,os; sys.path.insert(0,os.path.dirname(__file__)); import summ
S='/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad/of/res/'
WIN=('win.k:','bench-std.k:')
def row(build,run,R,k,sk):
    sp=[r[k] for r in R]; nw=[r[k] for r in R if not r['case'].startswith(WIN)]
    sens=[r for r in R if r[sk]]
    big=sum(1 for x in nw if x>0.10)
    print(f"| {build} | {run} | {statistics.median(sp):.1%} | {statistics.median(nw):.1%} | {max(nw):.1%} | {big} | {len(sens)} | "+', '.join(f"{r['case'].split(': ')[-1]} {r[k]:.0%}" for r in sens)+' |')
print('| build | run | median spread (all 200) | median (184, windows excluded) | max (windows excluded) | cases >10% (windows excl.) | layout-sensitive | which |')
print('|---|---|---|---|---|---|---|---|')
for f,a,b in [('A-B','A','B'),('A-C','A','C'),('A-B16','A','B16'),('A-C2','A','C2'),('C-D','C','D'),('C-K','C','K')]:
    if not os.path.exists(S+f'dirs-{f}.layouts.md'): continue
    R=summ.parse(S+f'dirs-{f}.layouts.md')
    row(a,f,R,'bsp','bsens'); row(b,f,R,'rsp','rsens')
