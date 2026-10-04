"""Re-attribute the profile samples (prof/raw/case/*.sample.txt) to binary symbols by address
(sample strips LTO suffixes such as o8.1005), and collect call edges. Writes of/prof2.json:
{'self': {set: {sym: equal-weight share}}, 'edges': {set: {'caller>callee': share}}, 'n': {...}}"""
import re,glob,json,bisect,collections,os
S='/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad'
ok=json.load(open(S+'/prof/raw/analysis.json'))
syms=[]
for l in open(S+'/prof/amber.nm'):
    p=l.split()
    if len(p)==3 and p[1] in 'tT': syms.append((int(p[0],16),p[2][1:]))
syms.sort(); addrs=[a for a,_ in syms]
TEXTEND=0x100000730+0x96c4c
def sym(a):
    if a>=TEXTEND: return 'STUBS@stubs'
    i=bisect.bisect_right(addrs,a)-1
    return syms[i][1] if i>=0 else '?'
LN=re.compile(r'^(?P<pre>[ +!:|]*)(?P<n>\d+) (?P<name>.+?)  \(in (?P<lib>[^)]+)\) .*?\[(?P<a>0x[0-9a-f]+)')
out={'self':{'m':collections.Counter(),'o':collections.Counter()},'edges':{'m':collections.Counter(),'o':collections.Counter()},'n':{'m':0,'o':0},'offs':collections.defaultdict(collections.Counter)}
for f in sorted(glob.glob(S+'/prof/raw/case/*.sample.txt')):
    base=os.path.basename(f)[:-len('.sample.txt')]
    key=base.replace('__',':',1)
    if key not in ok or str(ok[key].get('ok'))!='True': continue
    s='o' if key.startswith('f_') else 'm'
    t=open(f).read()
    load=int(re.search(r'Load Address:\s+(0x[0-9a-f]+)',t).group(1),16)
    cg=t.split('Call graph:')[1].split('Total number in stack')[0]
    stack=[]   # (depth, name, count, childsum)
    selfc=collections.Counter(); edges=collections.Counter(); tot=0
    nodes=[]
    for l in cg.splitlines():
        m=LN.match(l)
        if not m: continue
        d=len(m.group('pre')); n=int(m.group('n'))
        if m.group('lib')=='amber': nm=sym(int(m.group('a'),16)-load+0x100000000)
        else: nm=m.group('name')+'@'+m.group('lib')
        while stack and stack[-1][0]>=d: stack.pop()
        if stack:
            nodes[stack[-1][3]][2]+=n
            if not nm.endswith('@'+m.group('lib')) or True: edges[(stack[-1][1],nm)]+=n
        nodes.append([nm,n,0]); stack.append((d,nm,n,len(nodes)-1))
        if not stack[:-1]: tot+=n
        # offsets for amber leaf-ish nodes: all offsets listed
        if m.group('lib')=='amber':
            for o in re.findall(r'\+ ([\d,\.]+)\s+\[',l)[:1]:
                for x in o.split(','):
                    if x.isdigit(): out['offs'][nm][int(x)]+=n
    for nm,n,c in nodes: selfc[nm]+=n-c
    tot=sum(selfc.values())
    out['n'][s]+=1
    for k,v in selfc.items(): out['self'][s][k]+=v/tot
    for (a,b),v in edges.items(): out['edges'][s][a+'>'+b]+=v/tot
for s in 'mo':
    for k in out['self'][s]: out['self'][s][k]/=out['n'][s]
    for k in out['edges'][s]: out['edges'][s][k]/=out['n'][s]
json.dump(out,open(S+'/of/prof2.json','w'),indent=0)
for s in 'mo':
    a=sum(v for k,v in out['self'][s].items() if '@' not in k)
    print(s,out['n'][s],'amber share',round(a,4), out['self'][s].most_common(12))
