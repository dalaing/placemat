#!/bin/bash
# Cachegrind instruction counts per sqlbench case (SQLite's own performance method), for setting beside
# placemat's verdicts. Run on Linux with Valgrind >= 3.22 (Cachegrind client requests), e.g. in the
# Lima VM from the Mac, under the machine lock:
#   python3 .../amber/pbt/quiet.py --shared -- limactl shell placemat -- bash -lc \
#       'bash examples/sqlite/cachegrind.sh $W/sqlite $W/sqlite/cg 3.52.0 3.53.0'
# Arguments: the directory holding <version>/sqlite3.c, an output directory, versions. CASES=a,b,... limits
# the cases (default: placemat.toml's); speedtest1 is skipped for a version without speedtest1.c.
# Output: <out>/cg.tsv with  version  case  Ir  D1mr  D1mw  I1mr  (the timed body only), and
#         <out>/speedtest1.tsv with  version  Ir  for speedtest1 --testset main --memdb --size 10 (whole run).
# The output directory must be writable inside the VM (Lima mounts the Mac's home read-only).
# Builds use the same SQLite options as build.py, with clang -O2.
set -euo pipefail
src=$1; out=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
CC=${CC:-clang}
CFLAGS="-O2 -g -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DQS=0 -DSQLITE_OMIT_LOAD_EXTENSION"
mkdir -p "$out"
cases=${CASES:+$(echo "$CASES" | tr ',' '\n')}
cases=${cases:-$(sed -n '/^cases = \[/,/\]/p' "$here/placemat.toml" | tr -d '[]",' | sed 's/cases =//' | tr -s ' \n' '\n' | grep -v '^$')}
for v in "$@"; do
    b="$out/build/$v"; mkdir -p "$b"
    if [ ! -x "$b/sqlbench" ]; then
        $CC $CFLAGS -I "$src/$v" -c "$src/$v/sqlite3.c" -o "$b/sqlite3.o"
        $CC $CFLAGS -DSQLBENCH_CACHEGRIND -I "$src/$v" -c "$here/sqlbench.c" -o "$b/sqlbench.o"
        $CC -o "$b/sqlbench" "$b/sqlite3.o" "$b/sqlbench.o" -lm
        [ -f "$src/$v/speedtest1.c" ] && $CC $CFLAGS -I "$src/$v" -o "$b/speedtest1" "$src/$v/speedtest1.c" "$b/sqlite3.o" -lm
    fi
    for c in $cases; do
        f="$out/$v.$c.cg"
        [ -s "$f" ] || PLACEMAT_CASES=$c valgrind --tool=cachegrind --cache-sim=yes --instr-at-start=no \
            --cachegrind-out-file="$f" "$b/sqlbench" > "$out/$v.$c.out" 2> "$out/$v.$c.err"
        awk -v v="$v" -v c="$c" '/^summary:/ {print v "\t" c "\t" $2 "\t" $6 "\t" $9 "\t" $3}' "$f"
    done
    f="$out/$v.speedtest1.cg"
    [ -x "$b/speedtest1" ] || continue
    [ -s "$f" ] || valgrind --tool=cachegrind --cache-sim=no --cachegrind-out-file="$f" \
        "$b/speedtest1" --testset main --memdb --size 10 > "$out/$v.speedtest1.out" 2>&1 || true
    awk -v v="$v" '/^summary:/ {print v "\t" $2}' "$f" >> "$out/speedtest1.tsv.tmp"
done > "$out/cg.tsv"
touch "$out/speedtest1.tsv.tmp"; sort -u "$out/speedtest1.tsv.tmp" > "$out/speedtest1.tsv"; rm -f "$out/speedtest1.tsv.tmp"
cat "$out/cg.tsv" "$out/speedtest1.tsv"
