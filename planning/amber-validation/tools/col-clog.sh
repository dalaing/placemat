#!/bin/bash
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
cd $S/col/palog
for k in $S/hot/k/win.k /Users/dave/work/ngn-k/amber/pbt/microbench-q.k bench/suite.k; do
 AMBER_COLLOG=1 NO_COLOR=1 AMBER_THREADS=1 ./amber $k </dev/null 2>&1 >/dev/null | grep '^C ' > $S/col/clog-$(basename $k).txt
 echo "$(basename $k): $(wc -l < $S/col/clog-$(basename $k).txt) large allocations; colour 0: $(grep -c ' c0 ' $S/col/clog-$(basename $k).txt)"
done
