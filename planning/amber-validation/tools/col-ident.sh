#!/bin/bash
# ident.sh DIR...: shasums of the equality scripts and the stress script under each build
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
for k in $S/hot/k/win_eq.k $S/hot/k/grp_eq.k $S/hot/k/fnd_eq.k $S/col/k/stress.k; do for b in "$@"; do cd $b; echo "$(basename $k) $(basename $b) $(NO_COLOR=1 AMBER_THREADS=1 ./amber $k </dev/null 2>&1 | shasum | cut -c1-12)"; done; done
