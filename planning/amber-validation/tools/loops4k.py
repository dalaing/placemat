#!/usr/bin/env python3
"""List the backward-branch loops in an arm64 Mach-O binary whose body straddles a 4 KB
boundary (on Apple M2 such a loop measured at half speed: harness/). Usage:
loops4k.py BINARY [MAXBYTES [BOUNDARY]]   -> lines: function loopstart loopend bytes"""
import re,subprocess,sys
b=sys.argv[1]; mx=int(sys.argv[2]) if len(sys.argv)>2 else 256
sh=(int(sys.argv[3]) if len(sys.argv)>3 else 4096).bit_length()-1   # boundary (default 4096); 'ALL' counts below
n_all=0
out=subprocess.run(['otool','-tV',b],capture_output=True,text=True).stdout
fn=None
br=re.compile(r'^([0-9a-f]+)\s+(b|b\.\w+|cbz|cbnz|tbz|tbnz)\s+.*?0x([0-9a-f]+)\s*$')
for line in out.splitlines():
    if line.endswith(':') and not line.startswith('(') and not re.match(r'^[0-9a-f]+\s',line):
        fn=line[:-1]; continue
    m=br.match(line)
    if not m: continue
    a=int(m.group(1),16); t=int(m.group(3),16)
    if t<=a and a+4-t<=mx: n_all+=1
    if t<=a and a+4-t<=mx and (t>>sh)!=((a+3)>>sh):
        print(f'{fn} {t:#x} {a+4:#x} {a+4-t}')
print(f'# {n_all} backward-branch loops of at most {mx} bytes in all', file=sys.stderr)
