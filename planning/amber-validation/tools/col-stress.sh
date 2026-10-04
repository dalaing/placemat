#!/bin/bash
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
for b in "$@"; do cd $b; for t in 1 4; do NO_COLOR=1 AMBER_THREADS=$t ./amber $S/col/k/stress.k </dev/null > $S/col/stress-$(basename $b)-$t.txt 2>&1; done; done
