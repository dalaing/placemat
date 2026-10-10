#!/bin/bash
# run.sh NAME BASEDIR BRANCHDIR CASESFILE [extra evidence.py options]: one designed-stage run (--no-data),
# started only when no other evidence.py is running; load logged at start and end.
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
export AMBER_LAYOUT_DIR=$S/col/lv
n=$1; a=$2; b=$3; c=$4; shift 4
cd /Users/dave/work/ngn-k/amber || exit 1
busy() { ps -axo pid=,args= | awk '$0 ~ /[Pp]ython/ && $0 ~ /pbt\/evidence\.py/' | grep -q .; }
while busy; do sleep 60; done
mkdir -p $S/col/res/$n
echo "== $n start $(date) $(uptime)" | tee -a $S/col/res/runs.log
/usr/bin/time python3 pbt/evidence.py --dirs $a $b --script $S/hot/k/win.k --script $S/hot/k/grp.k --script $S/hot/k/fnd.k --script $S/hot/k/red.k --cases "$(cat $c)" --design --no-data --out $S/col/res/$n "$@" > $S/col/res/$n/log 2>&1
echo "== $n end $(date) $(uptime)" | tee -a $S/col/res/runs.log; tail -3 $S/col/res/$n/log
