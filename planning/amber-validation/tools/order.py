"""C3-style ordering of the selected hot functions. Usage: order.py BINARY OUT.order [LIMIT]
Prints the cluster list; writes one symbol per line (with leading underscore, binary names)."""
import json,sys,subprocess
S='/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad'
P=json.load(open(S+'/of/prof2.json')); sel=json.load(open(S+'/of/sel.json'))['sel']
b=sys.argv[1]; LIMIT=int(sys.argv[3]) if len(sys.argv)>3 else 16384
nm=[l.split() for l in subprocess.run(['nm','-n',b],capture_output=True,text=True).stdout.splitlines()]
nm=[(int(a,16),t,n[1:]) for a,t,n in (x for x in nm if len(x)==3)]
txt=[x for x in nm if x[1] in 'tT']
size={}
import re
_o=subprocess.run(['otool','-l',b],capture_output=True,text=True).stdout
_m=re.search(r'sectname __text\n\s+segname __TEXT\n\s+addr (0x[0-9a-f]+)\n\s+size (0x[0-9a-f]+)',_o)
TEXTEND=int(_m.group(1),16)+int(_m.group(2),16)
allsec=sorted(nm)
for i,(a,t,n) in enumerate(txt):
    nxt=txt[i+1][0] if i+1<len(txt) else TEXTEND
    size[n]=nxt-a
m={k:v for k,v in P['self']['m'].items() if '@' not in k}; o={k:v for k,v in P['self']['o'].items() if '@' not in k}
tm,to=sum(m.values()),sum(o.values())
h={f:m.get(f,0)/tm+o.get(f,0)/to for f in sel}
E={}
for s in 'mo':
    for k,v in P['edges'][s].items():
        p=k.split('>')
        if len(p)==2 and p[0] in h and p[1] in h and p[0]!=p[1]: E[tuple(p)]=E.get(tuple(p),0)+v
missing=[f for f in sel if f not in size]
if missing: print('MISSING in binary:',missing)
fs=[f for f in sel if f in size]
cl={f:[f] for f in fs}; of={f:f for f in fs}
dens=lambda c:sum(h[x] for x in c)/max(1,sum(size[x] for x in c))
for f in sorted(fs,key=lambda f:-h[f]):
    callers=sorted(((v,a) for (a,c),v in E.items() if c==f),reverse=True)
    if not callers: continue
    v,a=callers[0]
    ca,cf=of[a],of[f]
    if ca==cf: continue
    A,B=cl[ca],cl[cf]
    if sum(size[x] for x in A+B)>LIMIT: continue
    if dens(A)<dens(B)/8: continue     # C3: don't drag a hot callee behind a much colder caller
    cl[ca]=A+B; del cl[cf]
    for x in B: of[x]=ca
order=sorted(cl.values(),key=lambda c:-dens(c))
with open(sys.argv[2],'w') as w:
    for c in order:
        for x in c: w.write('_'+x+'\n')
for c in order: print(round(dens(c)*1e4,2), sum(size[x] for x in c), ' '.join(c))
print('functions',sum(map(len,order)),'bytes',sum(size[x] for c in order for x in c))
