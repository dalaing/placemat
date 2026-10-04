import json,ast,collections,sys
S='/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad'
d=json.load(open(S+'/prof/raw/analysis.json'))
def P(x): return ast.literal_eval(x) if isinstance(x,str) else x
sets={'m':collections.Counter(),'o':collections.Counter()}; n={'m':0,'o':0}; amb={'m':0.0,'o':0.0}
for k,v in d.items():
    if str(v.get('ok'))!='True': continue
    s='o' if k.startswith('f_') else 'm'
    self_=P(v['self']); lib=P(v['lib'])
    n[s]+=1
    for f,x in self_.items():
        sets[s][(f,lib.get(f))]+=x
for s in sets:
    for f in sets[s]: sets[s][f]/=n[s]
    amb[s]=sum(x for (f,l),x in sets[s].items() if l=='amber')
print(n, {s:round(amb[s],4) for s in amb})
json.dump({s:{f:x for (f,l),x in sets[s].items() if l=='amber'} for s in sets}, open(S+'/of/shares.json','w'),indent=0)
