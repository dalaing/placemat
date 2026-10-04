"""summ.py FILE.layouts.md...: per-file spread and change summaries."""
import sys,re,math,statistics
def pct(s): return float(s.strip().rstrip('%'))/100
def parse(f):
    rows=[]
    for l in open(f):
        if not l.startswith('| `'): continue
        c=[x.strip() for x in l.strip().strip('|').split('|')]
        if len(c)<8 or 'missing' in l: continue
        name=c[0].strip('`').replace(' (series)','').replace('` (series)','').strip('`')
        stock=c[1]; ch=pct(c[2]); lo,hi=[pct(x) for x in c[3].split(' to ')]
        bs=c[4]; br=c[5]
        rows.append(dict(case=name,stock=stock,change=ch,lo=lo,hi=hi,
            bsp=pct(bs.split('/')[0]),bsens='sens' in bs,rsp=pct(br.split('/')[0]),rsens='sens' in br,verdict=c[7].strip('* '),stockm='**' in stock))
    return rows
def geo(rows):
    lg=[math.log(1+r['change']) for r in rows]
    se=[(math.log(1+r['hi'])-math.log(1+r['lo']))/3.92 for r in rows]
    g=sum(lg)/len(lg); s=math.sqrt(sum(x*x for x in se))/len(se)
    return math.exp(g)-1, math.exp(g-1.96*s)-1, math.exp(g+1.96*s)-1
if __name__=='__main__':
    for f in sys.argv[1:]:
        R=parse(f); print('==',f.split('/')[-1],len(R),'cases')
        for side,k,sk in (('base','bsp','bsens'),('branch','rsp','rsens')):
            sp=[r[k] for r in R]
            print(f'  {side}: spread median {statistics.median(sp):.1%} max {max(sp):.1%} ({R[sp.index(max(sp))]["case"]}); sensitive {sum(r[sk] for r in R)}: '+', '.join(f'{r["case"]} {r[k]:.0%}' for r in R if r[sk]))
        g=geo(R); print(f'  geomean change {g[0]:+.2%} ({g[1]:+.2%} to {g[2]:+.2%})')
        for grp in ('microbench:','microbench-q:','bench.k:','bench-std.k:','win.k:','grp.k:','fnd.k:','red.k:'):
            S=[r for r in R if r['case'].startswith(grp)]
            if S: g=geo(S); print(f'    {grp} {len(S)} geomean {g[0]:+.2%} ({g[1]:+.2%} to {g[2]:+.2%})')
        print('  verdict change:',', '.join(f'{r["case"]} {r["change"]:+.1%} [{r["lo"]:+.1%},{r["hi"]:+.1%}]' for r in R if r['verdict']=='change'))
        print('  slower (interval above 0):',', '.join(f'{r["case"]} {r["change"]:+.1%} [{r["lo"]:+.1%},{r["hi"]:+.1%}]' for r in R if r['lo']>0))
        print('  stock measurable:',', '.join(f'{r["case"]} {r["stock"]}' for r in R if r['stockm']))
