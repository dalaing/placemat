import sys,re,subprocess
sys.path.insert(0,'/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad/of/tools'); import bininfo
def bodies(b):
    out=subprocess.run(['otool','-tV',b],capture_output=True,text=True).stdout
    D={}; fn=None
    for line in out.splitlines():
        if line.endswith(':') and not re.match(r'^[0-9a-f]+\s',line) and not line.startswith('('):
            fn=line[:-1].lstrip('_'); D[fn]=[]; continue
        m=re.match(r'^([0-9a-f]+)\s+(.*)$',line)
        if m and fn: D[fn].append((int(m.group(1),16),m.group(2)))
    return D
def norm(ins,a0):
    # make branch targets / adr relative to the function start
    return [re.sub(r'0x([0-9a-f]+)',lambda m:('+%d'%(int(m.group(1),16)-a0)) if abs(int(m.group(1),16)-a0)<1<<20 else 'X',t) for a,t in ins]
