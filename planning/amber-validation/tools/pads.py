"""pads.py BINARY ORDER OUTDIR [MARGIN]: exact DP over the start address mod 4096 (16-byte steps; every
function 16-aligned) choosing pad functions between the ordered functions so that no loop of at most
256 bytes in an ordered function straddles a 4 KB boundary (fewest crossings first, then fewest pad
bytes). BINARY: a build with the same order file and no pads. Writes OUTDIR/amber.order (pads
interleaved) and OUTDIR/src/zz_ofpad.c."""
import sys,os
sys.path.insert(0,os.path.dirname(__file__)); import bininfo
b,order_f,out=sys.argv[1:4]; MARGIN=0
import json
H64=json.load(open(sys.argv[4])) if len(sys.argv)>4 and sys.argv[4]!='-' else {}
SPAN=json.load(open(sys.argv[5])) if len(sys.argv)>5 else {}   # C2: hot entry span (bytes from the start) kept inside one 4 KB page
def costspan(n,x):
    return int(n in SPAN and x//P!=(x+SPAN[n]-1)//P)   # D: hottest loop (offset, length) per function: keep it in one 64-byte line
order=[l.strip()[1:] for l in open(order_f) if l.strip() and not l.startswith('_am_of_pad')]
F=bininfo.funcs(b); L=bininfo.loops(b)
assert all(F[n][0]%16==0 for n in order)
S0=F[order[0]][0]
# check the binary really has them in order, contiguous
P=4096; U=16; NS=P//U; BIG=10**6
lo={n:[] for n in order}
for fn,s,e in L:
    if fn in lo: lo[fn].append((s-F[fn][0],e-F[fn][0]))
def cost_at(n,x):   # crossings if function n starts at x (mod 4096); MARGIN: keep loops that far from a boundary
    c=0
    for s,e in lo[n]:
        a=(x+s-MARGIN); z=(x+e-1+MARGIN)
        if a//P!=z//P: c+=1
    return c
def cost64(n,x):
    if n not in H64: return 0
    s,e,_=H64[n]; return int((x+s)//64!=(x+e-1)//64)
size={n:-(-F[n][1]//U)*U for n in order}
INF=float('inf')
# best[x]: min cost with the next function starting at x (units of 16), before choosing a pad
cur=[INF]*NS; cur[(S0%P)//U]=0; back=[]
for n in order:
    # pad: from y to x costs (x-y)*16 bytes, but a pad is at least 16 bytes (or zero)
    nb=cur[:]; arg=list(range(NS))
    # circular sweep: nb[x]=min(cur[x], nb[x-1]+16) going round twice
    for it in range(2*NS):
        x=it%NS; y=(x-1)%NS
        if nb[y]+U<nb[x]: nb[x]=nb[y]+U; arg[x]=arg[y]
    nxt=[INF]*NS; choice=[None]*NS
    for x in range(NS):
        if nb[x]==INF: continue
        c=nb[x]+BIG*cost_at(n,x*U)+1000*cost64(n,x*U)+10000*costspan(n,x*U)
        e=(x+size[n]//U)%NS
        if c<nxt[e]: nxt[e]=c; choice[e]=(x,arg[x])
    back.append(choice); cur=nxt
# backtrack
e=min(range(NS),key=lambda i:cur[i]); total=cur[e]
plan=[]
for n,choice in zip(reversed(order),reversed(back)):
    x,y=choice[e]; plan.append((n,(x-y)%NS*U,x*U)); e=y
plan.reverse()
cross=sum(cost_at(n,x) for n,p,x in plan); padb=sum(p for n,p,x in plan)
print('64-byte-line crossings of hottest loops',sum(cost64(n,x) for n,p,x in plan),'of',len(H64)); print('hot spans straddling 4 KB',sum(costspan(n,x) for n,p,x in plan),'of',len(SPAN)); print('crossing hot loops',cross,'pad bytes',padb,'pads',sum(1 for n,p,x in plan if p))
for n,p,x in plan:
    c=cost_at(n,x)
    if c: print('  still crossing:',n,c)
os.makedirs(out+'/src',exist_ok=True)
with open(out+'/amber.order','w') as w, open(out+'/src/zz_ofpad.c','w') as c:
    c.write('/* prototype: pad functions placed between the hot functions by the order file (of/tools/pads.py) */\n')
    k=0
    for n,p,x in plan:
        if p:
            k+=1; c.write('__attribute__((used)) void am_of_pad_%d(void){__asm__ volatile(".space %d");}\n'%(k,p-16))
            w.write('_am_of_pad_%d\n'%k)
        w.write('_'+n+'\n')
