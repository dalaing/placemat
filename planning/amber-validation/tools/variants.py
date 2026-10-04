import sys
def table(f):
    t=open(f).read().split('\n| Variant |')[1].splitlines()
    hdr=['Variant']+[c.strip() for c in t[0].split('|')]
    rows=[[x.strip() for x in l.split('|')][1:] for l in t[2:9]]
    return hdr,rows
if __name__=='__main__':
    hdr,rows=table(sys.argv[1])
    for w in sys.argv[2:]:
        i=hdr.index(w+' base / branch'); print(w, ' | '.join(f"{r[0]}:{r[i]}" for r in rows))
