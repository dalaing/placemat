#!/usr/bin/env python3
"""samefn.py BIN_A BIN_B FN...: for each function, say whether its machine code is the same in
both binaries once addresses are made relative (branch targets inside the function as offsets,
calls and data references by symbol name where otool gives one, else masked). Prints the
function's start address, address mod 64 and size in each binary."""
import re,subprocess,sys,os
MASK=os.environ.get('MASK')  # MASK=1: also mask load/store/add immediates (data offsets of globals)
def dis(b,fn):
    o=subprocess.run(['otool','-tV','-p','_'+fn,b],capture_output=True,text=True).stdout.splitlines()
    out=[];start=None
    for l in o[3:]:
        if re.match(r'^_\S+:$',l) and out: break
        m=re.match(r'^([0-9a-f]+)\s+(.*)$',l)
        if not m: continue
        a=int(m.group(1),16); start=start if start is not None else a; ins=m.group(2)
        ins=re.sub(r'0x([0-9a-f]{6,})',lambda x:'+%#x'%(int(x.group(1),16)-start) if 0<=int(x.group(1),16)-start<0x10000 else 'ADDR',ins)
        ins=re.sub(r'\badrp\s+(\w+),.*',r'adrp \1,PAGE',ins)
        ins=re.sub(r'(add\s+\w+, \w+, )#0x[0-9a-f]+(\s*;.*)?$',r'\1PAGEOFF',ins)
        ins=re.sub(r';.*','',ins).strip()
        if MASK: ins=re.sub(r'#0x[0-9a-f]+\]','#IMM]',ins); ins=re.sub(r'^(add|ldr|str)(\S*\s+\w+, \w+, )#0x[0-9a-f]+$',r'\1\2#IMM',ins)
        out.append(ins)
    return start,out
a,b=sys.argv[1:3]
for fn in sys.argv[3:]:
    sa,da=dis(a,fn); sb,db=dis(b,fn)
    if sa is None or sb is None: print(f'{fn}: missing in {"A" if sa is None else "B"}'); continue
    same=da==db
    nd=sum(1 for x,y in zip(da,db) if x!=y)+abs(len(da)-len(db))
    print(f'{fn:14} {"SAME" if same else "DIFF(%d)"%nd:9} A {sa:#x} %64={sa%64:2} n={len(da)*4:5}  B {sb:#x} %64={sb%64:2} n={len(db)*4:5}')
