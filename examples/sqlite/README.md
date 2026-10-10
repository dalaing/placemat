# placemat on SQLite

An adapter for running placemat on SQLite (planning/ISSUES.md P010), written for placemat's survey
(DESIGN §3.1). It is MIT-licensed, like placemat, and holds no SQLite source: SQLite is public
domain, and the adapter uses only its public API. Results are in [SURVEY.md](SURVEY.md).

| file | what it is |
|---|---|
| `placemat.toml` | the configuration: build, the one benchmark suite, the machine lock, data colouring |
| `placemat-sweep.toml`, `placemat-pc.toml` | the same with B-tree pages in a page-cache slab: the stride as an arm, or on the data axis ("Page placement") |
| `build.py` | SQLite's side of the build protocol (placemat/build.py) |
| `sqlbench.c` | the benchmark harness: 25 cases, each of 10-200 ms, each on its own in-memory database |
| `cases.txt` | the case list for `--cases-file` |
| `fetch.sh` | downloads amalgamations (and speedtest1.c) into a directory outside this repository |
| `cachegrind.sh` | Cachegrind instruction counts per case on Linux (SQLite's own performance method) |
| `speedcheck.sh` | SQLite's standard benchmark as its `test/speedtest.tcl` runs it (gcc -Os, MEMSYS5, speedtest1 mix1) under Cachegrind |
| `SURVEY.md` | the survey's results |

## Arms

An arm is a directory holding one amalgamation (`sqlite3.c`, `sqlite3.h`), not a git revision:
SQLite's releases ship as amalgamations, and the amalgamation is what users build.

    sh examples/sqlite/fetch.sh $W/sqlite 3.52.0:2026 3.53.0:2026
    PYTHONPATH=. python3.12 -m placemat run --config examples/sqlite/placemat.toml \
        --arm base=$W/sqlite/3.52.0 --arm new=$W/sqlite/3.53.0 --cases-file examples/sqlite/cases.txt \
        --work $W/sqlite/work --out $W/sqlite/runs --name 3.52-vs-3.53

A null run gives the same directory to both arms (identical binaries, run from the same directory).

placemat keys a directory arm's builds on the amalgamation only (`[build] inputs`): `build.py` and
`sqlbench.c` live here, outside the arm, so after editing either, use a fresh `--work`.

## Build and code placement

`build.py` compiles the amalgamation once per arm (cached in `PLACEMAT_WORK`) with Apple clang
`-O2 -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DQS=0 -DSQLITE_OMIT_LOAD_EXTENSION`,
and then links each variant:

    cc -o sqlbench [pad.o] sqlite3.o sqlbench.o [placemat_alloc.o]

The pad goes in its own object, linked first, rather than into the amalgamation: without LTO, ld64
lays `__text` out in command-line order, so the pad moves every SQLite function by the pad's size,
and a variant costs a link, not a 10-second compile of a 9 MB file. `sqlite3.o`'s `__text` is only
4-byte aligned, so the shift is exact: a pad of P bytes moves SQLite by P + 4 (the pad function's
`ret`). `build.py` verifies every padded link with `nm` (the pad must be the first text symbol and
the next must come from `sqlite3.o`) and prints the shift. The hook's objects are linked last, so a
hooked build has SQLite's code at the same addresses as the unhooked build of the same pad.

## Data colouring: the hook, not the interposer

`data.method = "allocator"` (the first runs used `"hook"` with `stock_placement = false` and
`covariate = "khash"`, which behaves the same): in hooked builds (`PLACEMAT_HOOK=1`), `sqlbench.c`, compiled with
`$PLACEMAT_CFLAGS`, installs placemat's colouring allocator (`placemat_alloc.c`, with the
`placemat_sqlite_*` adapter in `placemat_alloc.h`) through `sqlite3_config(SQLITE_CONFIG_MALLOC)`
before `sqlite3_initialize()`. Unhooked builds use SQLite's own allocator.

Why not the interposer (DESIGN §7): on macOS SQLite's default allocator (`mem1.c`) calls
`malloc_zone_malloc` on the default zone, not `malloc`, and the interposer does not interpose the
`malloc_zone_*` functions (interpose.c says so), so it would see none of SQLite's heap. Building SQLite
with `-DSQLITE_WITHOUT_ZONEMALLOC` would route it through `malloc`, but that changes the stock
build. SQLite's pluggable allocator is the documented way in, and it reaches every SQLite heap
allocation and nothing else.

`stock_placement = false`: `placemat_alloc` over-allocates every large block by a header, its
alignment and colour_span + step_span at every data setting, (0, off) included, so the hooked
build's no-offset placement is not the one SQLite's own allocator gives; the stock build differs
from (0, off) in code and data, and placemat reports any stock-build difference as "stock build"
rather than "stock code layout".

What gets coloured (`min = 32768`; from the allocator's address log, PLACEMAT_ADDRLOG=2): SQLite's
own sub-allocators sit on top of the allocator. *Lookaside* (per connection; default 1200 × 40 =
48,000 bytes, carved into 1200-byte and 128-byte slots) is one block, coloured at this threshold (it
would not be at the default 64 KB). The *page cache* (pcache1) takes each page (4,368 bytes with its
header) as its own allocation, except an initial bulk block of SQLITE_DEFAULT_PCACHE_INITSZ = 20
pages (87,360 bytes); its hash table is coloured from 64 KB (8,192 slots) as it doubles. The sorter's
in-memory buffer (2,048,000 bytes, from cache_size −2000 KiB) and any large strings, blobs and arrays
are coloured. Individual B-tree pages are not: the data axis moves the slabs and tables SQLite uses
to find and sort pages, not the pages themselves (see "Page placement" below for the configuration
that does move them).

Unit 64 bytes (SQLite needs 8-byte alignment); spans 16 KB (the macOS arm64 page; M2's L1D set
stride). The run covariate is `khash` (the k values in allocation order): `placemat_alloc` reports no
region.

## Page placement: the page-cache slab

`placemat.toml` leaves the B-tree pages to SQLite's allocator: pcache1 allocates each page (4,368 bytes
with its header) separately, below any sensible colouring threshold (colouring each 4 KB page with
16 KB spans would quadruple its memory). Two other configurations put the pages in one slab of
fixed-size slots through `sqlite3_config(SQLITE_CONFIG_PAGECACHE)` (`sqlbench.c`, `-DSQLBENCH_PAGECACHE`;
4,096 slots, against a peak of 2,072 pages in `delete_refill`; `SQLBENCH_PCSTAT=1` prints each case's
peak and overflow). A page's 4,096 data bytes start its slot, so the slot stride decides how page
headers map onto L1D sets (16 KB set stride on M2; a stride of 64·m uses 256/gcd(m, 256) sets).

- `placemat-sweep.toml`: the stride as an arm. An arm directory holds the amalgamation and
  `sqlbench.cfg` (`-DSQLBENCH_PAGECACHE -DSQLBENCH_PC_STRIDE=8192`, say; without them, malloc's pages);
  run with `--data none`. The sweep's arms live in `placemat-work/sqlite/sweep/`.
- `placemat-pc.toml`: the pages on the data axis. `build.py --pagecache` puts hooked builds' pages in
  the slab, which the colouring allocator places like any large block (the coloured setting moves all
  pages together); at the stepped setting the stride is 4,416 + 64 × (splitmix64(PLACEMAT_STEP_SEED)
  mod 64), so every variant draws its own stride below 4,416 + 4 KB. The stock build stays unhooked.

On M2 the sweep found set aliasing among page headers at power-of-two strides (16,384: +2.8% over all
cases, del_rows +17.5%) and none at malloc's own placement: SURVEY.md, "Page placement".

## Harness

`sqlbench.c` prints `name<TAB>microseconds<TAB>1<TAB>us` per case and honours `PLACEMAT_CASES`.
Each case opens `:memory:`, builds its fixture untimed (seeded, deterministic data), times one
body with `clock_gettime_nsec_np(CLOCK_UPTIME_RAW)`, and closes the database. The cases follow
speedtest1's categories (inserts without and with one or three indexes; unindexed range scans,
LIKE scans, sorts, GROUP BY, window functions; index builds; indexed range and point lookups on
rowid and on a TEXT primary key; updates and deletes; joins and correlated subqueries; a recursive
CTE; printf/round; JSON), written afresh. A `# check name value` line gives a checksum per case
(placemat skips `#` lines).

Why not speedtest1 itself: its tests depend on each other's databases (no way to run one), and it
reports times in whole milliseconds, too coarse for 3% thresholds on 10-100 ms tests. It is still
used, under Cachegrind, as SQLite's own reference figure (`cachegrind.sh`).
