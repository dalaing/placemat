#!/bin/bash
# rss.sh DIR...: peak RSS (MB) of the maintainer's benchmarks (no file I/O) under each build; outputs kept
S=/private/tmp/claude-501/-Users-dave-work-ngn-k-k/eb2f95d5-4068-4d95-8225-d12e510d750c/scratchpad
mkdir -p $S/col/rss
for f in bench.k bench-std.k bench/suite.k bench/qbench/amber.k bench/queries/amber_groupby.k bench/queries/amber_vecarith.k bench/queries/amber_vecsum.k; do
  for b in "$@"; do cd $b; n=$(basename $b)-$(echo $f | tr / _)
    /usr/bin/time -l env NO_COLOR=1 AMBER_THREADS=1 ./amber $f </dev/null > $S/col/rss/$n.out 2> $S/col/rss/$n.err
    echo "$f $(basename $b) $(awk '/maximum resident set size/{printf "%.1f", $1/1048576}' $S/col/rss/$n.err) MB $(awk '/real/{print $1}' $S/col/rss/$n.err)s"
  done; done
