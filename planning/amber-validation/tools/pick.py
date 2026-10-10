import json,sys
S='/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad'
P=json.load(open(S+'/of/prof2.json'))
tgt=float(sys.argv[1]) if len(sys.argv)>1 else 0.95
m={k:v for k,v in P['self']['m'].items() if '@' not in k}; o={k:v for k,v in P['self']['o'].items() if '@' not in k}
tm,to=sum(m.values()),sum(o.values())
fs=sorted(set(m)|set(o), key=lambda f:-(m.get(f,0)/tm+o.get(f,0)/to))
sel=[];cm=co=0
for f in fs:
    if cm/tm>=tgt and co/to>=tgt: break
    sel.append(f); cm+=m.get(f,0); co+=o.get(f,0)
print(len(sel), 'coverage of amber samples m %.3f o %.3f; of all samples m %.3f o %.3f'%(cm/tm,co/to,cm,co))
json.dump({'sel':sel,'cov':[cm/tm,co/to,cm,co]},open(S+'/of/sel.json','w'))
print(' '.join(sel))
