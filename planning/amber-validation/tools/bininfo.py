"""Function table and small loops of an arm64 Mach-O binary."""
import re,subprocess
def textsec(b):
    o=subprocess.run(['otool','-l',b],capture_output=True,text=True).stdout
    m=re.search(r'sectname __text\n\s+segname __TEXT\n\s+addr (0x[0-9a-f]+)\n\s+size (0x[0-9a-f]+)',o)
    return int(m.group(1),16),int(m.group(2),16)
def funcs(b):
    """name -> (start, size), in address order"""
    a0,sz=textsec(b)
    L=[]
    for l in subprocess.run(['nm','-n',b],capture_output=True,text=True).stdout.splitlines():
        p=l.split()
        if len(p)==3 and p[1] in 'tT' and a0<=int(p[0],16)<a0+sz: L.append((int(p[0],16),p[2][1:]))
    out={}
    for i,(a,n) in enumerate(L):
        e=L[i+1][0] if i+1<len(L) else a0+sz
        out[n]=(a,e-a)
    return out
BR=re.compile(r'^([0-9a-f]+)\s+(b|b\.\w+|cbz|cbnz|tbz|tbnz)\s+.*?0x([0-9a-f]+)\s*$')
def loops(b,mx=256):
    """(function, start, end) of every backward-branch loop of at most mx bytes"""
    out=subprocess.run(['otool','-tV',b],capture_output=True,text=True).stdout
    fn=None; got=[]
    for line in out.splitlines():
        if line.endswith(':') and not line.startswith('(') and not re.match(r'^[0-9a-f]+\s',line):
            fn=line[:-1].lstrip('_'); continue
        m=BR.match(line)
        if m:
            a,t=int(m.group(1),16),int(m.group(3),16)
            if t<=a and a+4-t<=mx: got.append((fn,t,a+4))
    return got
def crosses(s,e,P=4096): return (s//P)!=((e-1)//P)
def stats(b,hot):
    a0,sz=textsec(b); L=loops(b)
    allx=[l for l in L if crosses(l[1],l[2])]
    hotx=[l for l in allx if l[0] in hot]
    return dict(text=sz, loops=len(L), cross_all=len(allx), cross_hot=len(hotx), hot_loops=sum(1 for l in L if l[0] in hot), hotx=hotx)
if __name__=='__main__':
    import sys,json
    S='/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad'
    hot=set(l.strip()[1:] for l in open(sys.argv[2])) if len(sys.argv)>2 else set(json.load(open(S+'/of/sel.json'))['sel'])
    for b in sys.argv[1].split(','):
        s=stats(b,hot); print(b.split('/of/')[-1], {k:v for k,v in s.items() if k!='hotx'}); 
        for l in s['hotx']: print('   ',l[0],hex(l[1]),l[2]-l[1])
