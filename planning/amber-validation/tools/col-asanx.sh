#!/bin/bash
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
cd $1/src; export ASAN_OPTIONS="log_path=$1/log2/asan:detect_leaks=0" UBSAN_OPTIONS="log_path=$1/log2/ubsan:print_stacktrace=1"
for k in $S/hot/k/win_eq.k $S/hot/k/grp_eq.k $S/hot/k/fnd_eq.k $S/col/k/stress.k; do for t in 1 4; do echo "$(basename $k) t$t $(NO_COLOR=1 AMBER_THREADS=$t ./amber $k </dev/null 2>&1 | sed '1s/^.refc:[0-9a-f]*//' | shasum | cut -c1-12)"; done; done
ls $1/log2
