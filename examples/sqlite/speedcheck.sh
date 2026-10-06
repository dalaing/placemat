#!/bin/bash
# SQLite's "standard benchmark" as its test/speedtest.tcl runs it (no SQLite source here; the flags
# are copied from speedtest.tcl's defaults): gcc -g -Os -DSQLITE_ENABLE_MEMSYS5 -DSQLITE_ENABLE_RTREE,
# speedtest1 --journal wal --size 5 --heap 40000000 64 --testset mix1, under Cachegrind; prints the
# total instruction count per build. Linux with Valgrind (e.g. the Lima VM); under the --shared lock.
#   speedcheck.sh SRC OUT REV...    (SRC/<rev>/{sqlite3.c,speedtest1.c}; OUT writable in the VM)
set -euo pipefail
src=$1; out=$2; shift 2
mkdir -p "$out"
for v in "$@"; do
    b="$out/$v"; mkdir -p "$b"
    [ -x "$b/speedtest1" ] || gcc -g -Os -DSQLITE_ENABLE_MEMSYS5 -DSQLITE_ENABLE_RTREE -I "$src/$v" \
        "$src/$v/speedtest1.c" "$src/$v/sqlite3.c" -o "$b/speedtest1" -lm
    [ -s "$b/cg.out" ] || (cd "$b" && valgrind --tool=cachegrind --cache-sim=no --cachegrind-out-file=cg.out \
        ./speedtest1 --journal wal --size 5 --heap 40000000 64 --testset mix1 > st.out 2> vg.err || true)
    printf '%s\t%s\n' "$v" "$(awk '/^summary:/ {print $2}' "$b/cg.out")"
done
