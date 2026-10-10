#!/bin/bash
# box.sh: placemat's P010 surveys (and optionally the Amber runs) on a dedicated Linux machine, x86-64
# or arm64, from a clone of this repository. See scripts/README.md.
#
#   git clone https://github.com/dalaing/placemat ~/placemat && cd ~/placemat
#   scripts/box.sh setup        # packages, sources, the machine overlay, a probe baseline, the tests
#   scripts/box.sh tune         # optional, as root: performance governor, turbo off, apt timers stopped
#   scripts/box.sh start        # the run queue in the background (nohup); safe to log out
#   scripts/box.sh status       # what has finished, what is running
#
# Layout (the same as on the Mac, so relative paths in the example configs hold): this repository at
# $P, the work directory at $W (default: placemat-work next to it), Amber (optional) at $P/../amber.
# Environment: W, CC_KIND (clang | gcc; default clang), STAGES (default: all, in the order below),
# AMBER_GIT and AMBER_REF (a branch of the Amber fork with pbt/placemat committed), AMBER_RUNS;
# DRY=1 prints each run's placemat command instead of running it ('DRY=1 scripts/box.sh queue').
set -u
P=$(cd "$(dirname "$0")/.." && pwd)
W=${W:-$(cd "$P/.." && pwd)/placemat-work}
PY=${PY:-python3}
CC_KIND=${CC_KIND:-clang}
STAGES=${STAGES:-surveys sweep releases history pagecache gcc amber}
AMBER_RUNS=${AMBER_RUNS:-a1 b1 c11 c21 e1 a2 b2 c12 c22 e2}
SUDO=$([ "$(id -u)" = 0 ] && echo "" || echo sudo)

ZC=c1-text,c3-text,c6-text,c12-text,c19-text,cneg5-text,c1-rec,c3-rec,c3-rand,cs3-text,d1-text,d3-text,d12-text,d3-rec,d3-rand,ds3-text
LC=fib,loop_int,loop_float,str_concat,str_format,str_pattern,tbl_array,tbl_sort,tbl_hash,closures,methods,json,coroutines,array_stream,nbody,spectral,sieve,vararg
SQLITE_CHECKINS="0a5f27711 bf66606d4 e9f4537ea"
LUA_HISTORY="2ff34717 b34a97a4"
SWEEP="malloc: s4416:4416 s4608:4608 s8192:8192 s8256:8256 s16384:16384 s16448:16448"

say() { echo "box.sh: $*"; }
die() { echo "box.sh: $*" >&2; exit 1; }

# --- setup -------------------------------------------------------------------------------------------

packages() {
    command -v apt-get > /dev/null || { say "no apt-get: install a C toolchain (gcc, clang, lld), binutils, git, curl, unzip, tcl, make and python3 >= 3.11 yourself"; return; }
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -q
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q build-essential clang lld llvm git curl unzip tcl make python3 valgrind
}

overlay() {  # this machine's settings for every project config (placemat/config.py, PLACEMAT_MACHINE)
    local idx size="" ways="" line="" page stride note=""
    page=$(getconf PAGESIZE)
    for idx in /sys/devices/system/cpu/cpu0/cache/index*; do      # the L1 data cache, from sysfs ...
        [ "$(cat $idx/level 2> /dev/null)" = 1 ] && [ "$(cat $idx/type 2> /dev/null)" = Data ] || continue
        size=$(sed 's/K$//' $idx/size 2> /dev/null); [ -n "$size" ] && size=$(( size * 1024 ))
        ways=$(cat $idx/ways_of_associativity 2> /dev/null); line=$(cat $idx/coherency_line_size 2> /dev/null)
    done
    [ -n "$size" ] && [ "$size" != 0 ] || size=$(getconf LEVEL1_DCACHE_SIZE 2> /dev/null)    # ... or the C library
    [ -n "$ways" ] && [ "$ways" != 0 ] || ways=$(getconf LEVEL1_DCACHE_ASSOC 2> /dev/null)
    [ -n "$line" ] && [ "$line" != 0 ] || line=$(getconf LEVEL1_DCACHE_LINESIZE 2> /dev/null)
    case "$line" in ''|0|undefined) line=64 ;; esac
    if [ -n "${L1D_STRIDE:-}" ]; then stride=$L1D_STRIDE
    elif [ -n "$size" ] && [ "$size" -gt 0 ] 2> /dev/null && [ -n "$ways" ] && [ "$ways" -gt 0 ] 2> /dev/null; then stride=$(( size / ways ))
    else stride=4096; note=" (not detected: 4096 assumed; set L1D_STRIDE and rerun 'box.sh overlay')"; say "L1D geometry not detected: step_span 4096 assumed; set L1D_STRIDE to override"
    fi
    cat > "$W/machine.toml" <<EOF
# This machine, for every project config (PLACEMAT_MACHINE; written by scripts/box.sh setup).
# $(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/.*: //') ; L1D set stride $stride B$note, line $line B, page $page B
[machine]
lock = ["flock", "$W/.lock", "$PY", "-c", "import sys; print('held', flush=True); sys.stdin.read()"]
shared_lock = ["flock", "-s", "$W/.lock", "$PY", "-c", "import sys; print('held', flush=True); sys.stdin.read()"]
max_noise = 0.10
wait = 2700
line = $line

[target]
prefix = []
tmp = ""

[data]
colour_span = $page
step_span = $stride
EOF
    say "machine overlay: $W/machine.toml (L1D set stride $stride, line $line, page $page)"
}

fetch_sqlite() {
    local S=$W/sqlite c d
    sh "$P/examples/sqlite/fetch.sh" "$S" 3.52.0:2026 3.53.0:2026
    [ -d "$S/repo/.git" ] || git clone -q https://github.com/sqlite/sqlite "$S/repo"
    for c in $SQLITE_CHECKINS; do               # amalgamations of single check-ins
        d=$S/$c; [ -f "$d/sqlite3.c" ] && continue
        rm -rf "$S/build-$c"; git -C "$S/repo" worktree add -q --detach "$S/build-$c" "$c"
        (cd "$S/build-$c" && ./configure > /dev/null && make sqlite3.c > /dev/null) || die "sqlite amalgamation $c failed"
        mkdir -p "$d"; cp "$S/build-$c/sqlite3.c" "$S/build-$c/sqlite3.h" "$S/build-$c/test/speedtest1.c" "$d/"
        git -C "$S/repo" worktree remove --force "$S/build-$c"
        say "sqlite: amalgamation of $c"
    done
    for a in $SWEEP; do                        # the stride sweep's arms (examples/sqlite/README.md)
        local n=${a%%:*} v=${a#*:}
        mkdir -p "$S/sweep/$n"; ln -sf ../../3.53.0/sqlite3.c "$S/sweep/$n/sqlite3.c"; ln -sf ../../3.53.0/sqlite3.h "$S/sweep/$n/sqlite3.h"
        if [ -n "$v" ]; then echo "-DSQLBENCH_PAGECACHE -DSQLBENCH_PC_STRIDE=$v"; else echo "# malloc's own page placement (no slab)"; fi > "$S/sweep/$n/sqlbench.cfg"
    done
}

fetch_lua() {
    local L=$W/lua v c
    mkdir -p "$L"
    for v in 5.4.6:7d5ea1b9cb6aa0b59ca3dde1c6adcb57ef83a1ba8e5432c0ecd06bf439b3ad88 \
             5.4.7:9fbf5e28ef86c69858f6d3d34eccc32e911c1a28b4120ff3e84aaa70cfbf1e30; do
        local n=${v%%:*} h=${v#*:}
        [ -d "$L/lua-$n" ] && continue
        curl -sSf -o "$L/lua-$n.tar.gz" "https://www.lua.org/ftp/lua-$n.tar.gz"
        echo "$h  $L/lua-$n.tar.gz" | sha256sum -c --quiet - || die "lua-$n.tar.gz: checksum mismatch"
        tar -xzf "$L/lua-$n.tar.gz" -C "$L"
    done
    [ -d "$L/lua-git/.git" ] || git clone -q https://github.com/lua/lua "$L/lua-git"
    for c in $LUA_HISTORY; do                  # each commit and its parent, as release-like trees (src/)
        for side in "$c-parent:$c^" "$c:$c"; do
            local d=$L/git-${side%%:*} rev=${side#*:}
            [ -d "$d/src" ] && continue
            mkdir -p "$d/src"; git -C "$L/lua-git" archive "$rev" | tar -x -C "$d/src"
            git -C "$L/lua-git" rev-parse "$rev" > "$d/REV"
        done
    done
}

fetch_zstd() {
    [ -d "$W/zstd/src/.git" ] || git clone -q https://github.com/facebook/zstd "$W/zstd/src"
}

fetch_amber() {
    [ -n "${AMBER_GIT:-}" ] || { say "amber: AMBER_GIT not set; skipped"; return; }
    local A=$P/../amber
    [ -d "$A/.git" ] || git clone -q "$AMBER_GIT" "$A"
    git -C "$A" fetch -q origin && git -C "$A" checkout -q "${AMBER_REF:-main}"
    git -C "$A" branch -f main "$(git -C "$A" rev-parse HEAD)" 2> /dev/null || true   # validate.sh uses main
    [ -f "$A/pbt/placemat/validate.sh" ] || die "amber: no pbt/placemat/validate.sh at ${AMBER_REF:-main}: commit the adapter to that branch"
}

baseline() {  # seed the noise probe's level baseline (~/.cache/placemat/probe.json) on an idle machine
    PYTHONPATH=$P $PY -c "
from placemat import lock
for _ in range(20):
    m, s = lock.probe(); lock.baseline(m)
print('probe baseline', round(lock.baseline(), 3), 'ms')"
}

setup() {
    packages
    $PY -c 'import sys; assert sys.version_info >= (3, 11), sys.version' || die "python >= 3.11 needed (PY=...)"
    mkdir -p "$W/runs"
    overlay
    fetch_sqlite; fetch_lua; fetch_zstd; fetch_amber
    say "tests"; (cd "$P" && PYTHONPATH=$P $PY -m unittest discover -s tests -t . -q) || die "tests failed"
    baseline
    say "setup done; optionally 'sudo scripts/box.sh tune', then 'scripts/box.sh start'"
}

# --- tune --------------------------------------------------------------------------------------------

tune() {  # until the next reboot
    local f
    for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do [ -w "$f" ] && echo performance > "$f"; done
    [ -w /sys/devices/system/cpu/intel_pstate/no_turbo ] && echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo
    [ -w /sys/devices/system/cpu/cpufreq/boost ] && echo 0 > /sys/devices/system/cpu/cpufreq/boost
    systemctl stop apt-daily.timer apt-daily-upgrade.timer unattended-upgrades 2> /dev/null || true
    say "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2> /dev/null || echo 'n/a (a VM?)'); turbo: $(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2> /dev/null | sed 's/1/off/;s/0/on/' || cat /sys/devices/system/cpu/cpufreq/boost 2> /dev/null | sed 's/0/off/;s/1/on/' || echo 'n/a')"
}

# --- the queue ---------------------------------------------------------------------------------------

pm() {  # PROJECT NAME CONFIG COMMAND ARGS...: one placemat run, skipped if its report exists
    local proj=$1 name=$2 cfg=$3; shift 3
    local out=$W/$proj/runs
    mkdir -p "$out"
    [ -e "$out/$name.md" ] && { say "$proj/$name: done already"; return; }
    [ -n "${DRY:-}" ] && { echo "$proj/$name: WORK=$W/$proj/${WORK:-work} LUA_CC=${LUA_CC:-$CC_KIND} placemat $* --config $cfg"; return; }
    echo "$(date '+%F %T') start $proj/$name" >> "$W/queue.log"
    (cd "$P" && timeout 86400 env PYTHONPATH=$P PLACEMAT_MACHINE=$W/machine.toml SQLBENCH_CC=$CC_KIND LUA_CC=${LUA_CC:-$CC_KIND} ZB_CC=$CC_KIND \
        $PY -m placemat "$@" --config "$cfg" --work "$W/$proj/${WORK:-work}" --out "$out" --name "$name") > "$out/$name.log" 2>&1
    echo "$(date '+%F %T') $proj/$name exit $?" >> "$W/queue.log"
}

stage() {
    local S=$W/sqlite L=$W/lua E=$P/examples
    case $1 in
    surveys)    # the null runs: each project against itself (DESIGN §3.1)
        pm lua null-547 $E/lua/placemat.toml survey --arm base=$L/lua-5.4.7 --arm new=$L/lua-5.4.7 --cases $LC
        pm sqlite null-3.53 $E/sqlite/placemat.toml survey --arm base=$S/3.53.0 --arm new=$S/3.53.0 --cases-file $E/sqlite/cases.txt
        pm zstd null $E/zstd/placemat.toml survey --arm base=git:v1.5.7 --arm new=git:v1.5.7 --cases $ZC ;;
    sweep)      # SQLite's page placement: the slot stride as arms (examples/sqlite/README.md)
        local arms=() a
        for a in $SWEEP; do arms+=(--arm "${a%%:*}=$S/sweep/${a%%:*}"); done
        WORK=work-sweep pm sqlite sweep-3.53 $E/sqlite/placemat-sweep.toml run --data none "${arms[@]}" --cases-file $E/sqlite/cases.txt ;;
    releases)   # adjacent releases
        pm lua rel-546-547 $E/lua/placemat.toml run --arm base=$L/lua-5.4.6 --arm new=$L/lua-5.4.7 --data both --cases $LC
        pm sqlite 3.52-vs-3.53 $E/sqlite/placemat.toml run --arm base=$S/3.52.0 --arm new=$S/3.53.0 --cases-file $E/sqlite/cases.txt
        pm zstd v156-v157 $E/zstd/placemat.toml run --arm base=git:v1.5.6 --arm new=git:v1.5.7 --cases $ZC ;;
    history)    # performance commits revisited (the SURVEYs' history sections)
        local c
        set -- $SQLITE_CHECKINS
        pm sqlite checkins-$1 $E/sqlite/placemat.toml run --arm base=$S/$1 --arm pcache=$S/$2 --arm opcolumn=$S/$3 --cases-file $E/sqlite/cases.txt
        pm zstd copy8 $E/zstd/placemat.toml run --arm base=git:7eefc221 --arm new=git:1e9d2006 --cases $ZC
        pm zstd decseq $E/zstd/placemat.toml run --arm base=git:3c3b8274 --arm new=git:a28e8182 --cases d1-text,d3-text,d12-text,d3-rec,d3-rand,ds3-text
        for c in $LUA_HISTORY; do
            pm lua hist-$c $E/lua/placemat.toml run --arm base=$L/git-$c-parent --arm new=$L/git-$c --data both --cases $LC
        done ;;
    pagecache)  # SQLite with its pages on the data axis
        WORK=work-pc pm sqlite null-3.53-pc $E/sqlite/placemat-pc.toml survey --arm base=$S/3.53.0 --arm new=$S/3.53.0 --cases-file $E/sqlite/cases.txt
        WORK=work-pc pm sqlite 3.52-vs-3.53-pc $E/sqlite/placemat-pc.toml run --arm base=$S/3.52.0 --arm new=$S/3.53.0 --cases-file $E/sqlite/cases.txt ;;
    gcc)        # the other compiler: Lua's interpreter loop is where code alignment showed (its own work dir)
        local other=$([ "$CC_KIND" = clang ] && echo gcc || echo clang)
        LUA_CC=$other WORK=work-$other pm lua null-547-$other $E/lua/placemat.toml survey --arm base=$L/lua-5.4.7 --arm new=$L/lua-5.4.7 --cases $LC ;;
    amber)      # Amber's regression runs (pbt/placemat/validate.sh in the fork); (d)'s pins are M2's, so not run
        local A=$P/../amber
        [ -f "$A/pbt/placemat/validate.sh" ] || { say "amber: no fork at $A; skipped"; return; }
        [ -n "${DRY:-}" ] && { echo "amber: (cd $A && PLACEMAT_WORK=$W/amber bash pbt/placemat/validate.sh $AMBER_RUNS)"; return; }
        echo "$(date '+%F %T') start amber $AMBER_RUNS" >> "$W/queue.log"
        (cd "$A" && PLACEMAT_MACHINE=$W/machine.toml PLACEMAT_PYTHON=$PY PLACEMAT_WORK=$W/amber PLACEMAT_SRC=$P \
            timeout 172800 bash pbt/placemat/validate.sh $AMBER_RUNS) > "$W/amber-validate.log" 2>&1
        echo "$(date '+%F %T') amber exit $?" >> "$W/queue.log" ;;
    *) say "unknown stage $1" ;;
    esac
}

queue() {
    local s
    [ -n "${DRY:-}" ] || echo "$(date '+%F %T') queue start: $STAGES (CC $CC_KIND)" >> "$W/queue.log"
    for s in $STAGES; do stage $s; done
    [ -n "${DRY:-}" ] || echo "$(date '+%F %T') queue done" >> "$W/queue.log"
}

start() {
    [ -f "$W/machine.toml" ] || die "run 'scripts/box.sh setup' first"
    if [ -f "$W/queue.pid" ] && kill -0 "$(cat "$W/queue.pid")" 2> /dev/null; then die "a queue is running (pid $(cat "$W/queue.pid"))"; fi
    nohup "$P/scripts/box.sh" queue > "$W/queue.out" 2>&1 &
    echo $! > "$W/queue.pid"
    say "queue started (pid $!): tail -f $W/queue.log"
}

status() {
    [ -f "$W/queue.pid" ] && { kill -0 "$(cat "$W/queue.pid")" 2> /dev/null && say "queue running (pid $(cat "$W/queue.pid"))" || say "queue not running"; }
    tail -n 15 "$W/queue.log" 2> /dev/null
    local f; f=$(ls -t "$W"/*/runs/*.log 2> /dev/null | head -1)
    [ -n "$f" ] && { echo "== latest: $f"; tail -n 3 "$f"; }
}

[ "${BASH_SOURCE[0]}" = "$0" ] || return 0     # sourced: the functions only
case ${1:-} in
    setup) setup ;;
    tune) [ "$(id -u)" = 0 ] || die "tune needs root (sudo)"; tune ;;
    start) start ;;
    queue) shift; [ $# -gt 0 ] && STAGES="$*"; queue ;;
    status) status ;;
    overlay) mkdir -p "$W"; overlay ;;
    fetch) mkdir -p "$W"; fetch_sqlite; fetch_lua; fetch_zstd; fetch_amber ;;
    *) sed -n '2,17p' "$0"; exit 1 ;;
esac
