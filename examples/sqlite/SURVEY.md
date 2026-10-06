# SQLite survey with placemat (P010)

**Question (DESIGN §3.1):** does SQLite have a placement problem on this machine (Apple M2, macOS 26,
Apple clang 17, ld64), and how does placemat's layout view compare with SQLite's own method,
Cachegrind instruction counts?

## Set-up

- **Versions:** amalgamations of 3.51.0, 3.52.0 and 3.53.0 (sqlite.org), and five amalgamations built
  from the GitHub mirror (`make sqlite3.c`) for the check-in study below. All outside this repository,
  in `/Users/dave/work/ngn-k/placemat-work/sqlite/`.
- **Harness:** `sqlbench.c`, 25 cases of 10-200 ms on in-memory databases (README.md).
- **Build:** Apple clang `-O2 -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DQS=0
  -DSQLITE_OMIT_LOAD_EXTENSION`, no LTO; the pad is its own object linked first, so pad P moves all
  of SQLite's ~975 KB of code by exactly P + 4 bytes (verified with `nm` on every variant).
- **Data axis:** placemat's colouring allocator via `sqlite3_config(SQLITE_CONFIG_MALLOC)`;
  blocks ≥ 32 KB coloured. Address log of one execution (all 25 cases): 242 blocks ≥ 64 KB per
  execution: the page cache's initial bulk block (87,360 B = 20 pages of 4,368 B; one per database),
  the page-cache hash table as it doubles (64 KB to 1 MB, i.e. 8 K to 128 K slots), and 2,048,000-byte
  blocks (the sorter's in-memory buffer: cache_size −2000 KiB). With `min = 32768` the 48,000-byte
  lookaside slab of each connection is coloured too. Individual pages (4.4 KB each) are not.
  The run covariate (`khash`, the hash of the coloured blocks' k values in allocation order) took
  its most common value in only 19-24% of a case's runs, and the run test never came near
  significance (p ≥ 0.18).
- **Design:** placemat defaults: three data settings per pad (stock, coloured, hashed step),
  4 pads then 2 at a time up to 12 while the interval's half-width exceeds 1%, 7 rounds per batch.
- **Machine:** shared with five other placemat runs (Amber, zstd, Lua, P009) behind Amber's
  `pbt/quiet.py` lock; see "Runs" for what that did to them.

## Verdict

**SQLite shows no meaningful code-layout spread on this machine at placemat's 3% threshold, and its
stock build's data placement is safe; but the relative placement of its B-tree pages matters, by up to
17.5%, if pages land a power of two apart ("Page placement", below).** Across three
runs (100 case-and-arm comparisons, 25 cases, up to 12 code pads × 3 data settings each), placemat
raised one code flag (a 2-4% alignment-phase effect in one build, below) and no data (colour or step)
or run flags. In DESIGN §3.1's terms: *no meaningful spread; placemat's cheap mode is enough*;
neither pinning nor colouring is called for. Two caveats: the machine was loaded (other sessions'
work outside the lock; noise probe 2-14%), so code spreads of 5-12% per case could not be told apart
from noise and effects below about 3% are not ruled out; and, the larger one, the data axis reached
only SQLite's larger blocks (page-cache bulk and hash table, sorter buffers, lookaside), not the B-tree
pages themselves. For a B-tree engine the pages are the hot data (page headers, cell-pointer arrays,
the cells a search visits), and their relative placement, which decides how page headers share L1D
sets, was left to malloc in every run. So "no data flags" says the slabs and tables do not matter; the
stride sweep below measured the pages: malloc's placement is as fast as the best, and the cost is
set aliasing at power-of-two strides (16,384: mean +2.8%, del_rows +17.5%), which a stock build on
this machine does not hit.

What placemat does add for SQLite is the **layout-averaged change with an interval**. A single timing
of the two stock builds was wrong in sign or size several times (it is what a stock-vs-stock benchmark
gives), and instruction counts miss cycle-only changes:
- 3.52.0 → 3.53.0, `idx_between`: stock builds −5.7%; layout-averaged +2.4% (+1.4 to +3.3);
  Cachegrind Ir +3.5%. The layout-averaged estimate agrees with the instruction count; the stock
  pair does not. Over all 25 cases the layout-averaged change correlates with the Ir change at
  r = 0.56; the stock pair's at r = −0.20.
- The page-cache check-in bf66606d4 (`iKey % nHash` → `iKey & (nHash-1)`): **zero** instruction
  change in every case and +18 instructions in SQLite's standard benchmark, yet placemat measures
  real speed-ups on the page-lookup cases: ipk_lookup −3.1% (−4.0 to −2.2), a *change*; upd_rows
  −2.4%, textpk_lookup −2.1%, del_rows −2.0%, integrity −1.9%, idx_text_between −1.6% (all intervals
  excluding no change), and nothing on sorts, scans or computation. The claim ("small performance
  improvement") is right and Cachegrind cannot see it: one `udiv` became one `and`.
- 3.52 → 3.53 disagreements between time and Ir: the inserts run +0.5..+1.0% more instructions but
  −1.0..−1.7% less time (intervals excluding 0); printf_round −0.1% Ir but −2.0% (−2.7 to −1.2) time
  (3.53's reimplemented float-to-text conversion: cheaper instructions, not fewer); subquery +3.5% Ir
  but +0.4% (−0.4 to +1.2) time.

## Page placement

The runs above leave SQLite's pages where its allocator puts them: pcache1 allocates each page
(4,368 bytes with its header) separately, below the colouring threshold, so no data setting moved
them. `sqlbench.c -DSQLBENCH_PAGECACHE` instead gives the page cache one slab of fixed-size slots
(`sqlite3_config(SQLITE_CONFIG_PAGECACHE)`, 4,096 slots against a peak of 2,072 pages, no overflow),
so the stride between pages is set by the harness. With M2's 16 KB L1D set stride and 64-byte lines,
a stride of 64·m puts the page headers in 256/gcd(m, 256) sets: all 256 at 4,416 (m = 69), 32 at
4,608 (malloc's small-zone quantum rounds 4,368 up to this), 2 at 8,192 and 1 at 16,384. Checksums
match the stock build at every stride. Three runs (`placemat-work/sqlite/pc.sh`):

1. **Stride sweep** (`sweep-3.53`, `placemat-sweep.toml`, `--data none`; done): 3.53.0 at seven page
   placements as arms, against `malloc` (the stock allocator): strides 4,416, 4,608, 8,192 and 16,384,
   plus 8,256 and 16,448, which have the same memory and TLB footprint as 8,192 and 16,384 but spread
   the headers over all 256 sets. A slowdown at 8,192 or 16,384 that is absent at 8,256 or 16,448 is
   set aliasing; one shared with them is footprint.
2. **Null run with pages on the data axis** (`null-3.53-pc`, `placemat-pc.toml`): hooked builds put the
   pages in the slab; the coloured setting moves the slab's base (every page together) and the
   stepped setting draws the stride (4,416 plus 64 × a hashed value below 4 KB, so 8,192 is one
   draw in 64). The stock build is unhooked, malloc's pages as before.
3. **3.52.0 → 3.53.0 with pages on the data axis** (`3.52-vs-3.53-pc`).

**Run 2** (`null-3.53-pc`; 12 pads × 3 data settings, 25 cases, probe 2.0-2.8%):
no data flags (colour 0, step 0) and no change verdicts, mean +0.0% (−0.1 to +0.1); one code flag
(upd_rows, *placement*). The stepped setting's strides (4,416 to 8,448) almost never alias, which is
what the sweep predicts: no spread from them. So with pages on the data axis, SQLite still shows no
data-placement spread at the strides a real allocator would choose. **Run 3** (`3.52-vs-3.53-pc`; 4-8 pads; probe 1-3%, last batch 12-13%):
no flags of any kind, no change verdicts, mean +0.5% (+0.0 to +1.0), against +0.4% (−0.0 to +0.8)
for the gated run with malloc's pages; per-case changes agree between the two at r = 0.92 (mean
difference 0.35 points; idx_between +2.5% here, +2.2% there). Moving the pages does not change what
the release comparison says.

**The sweep: set aliasing, not footprint.** 25 cases, 8 to 20 code pads each (adaptive); each case's
change against malloc's pages, layout-averaged; the mean over cases with its 95% t interval over pads:

| stride (sets) | mean over cases | change verdicts | del_rows | ins_3idx | upd_rows | upd_between | integrity |
|---|---|---|---|---|---|---|---|
| 4,416 (256) | −0.5% (−0.7 to −0.3) | 0 | −0.5% | −0.9% | −0.6% | −0.8% | −0.5% |
| 4,608 (32) | −0.4% (−0.4 to −0.3) | 0 | −0.5% | −1.1% | −0.9% | −0.1% | −0.2% |
| 8,192 (2) | +0.7% (+0.5 to +0.8) | 1 | **+4.4%** | +2.3% | +1.9% | +1.6% | +2.9% |
| 8,256 (256) | −0.3% (−0.4 to −0.2) | 0 | +0.5% | −1.2% | −0.2% | +0.0% | +0.3% |
| 16,384 (1) | **+2.8%** (+2.7 to +2.9) | 8 | **+17.5%** | **+9.7%** | **+9.7%** | **+7.0%** | **+6.0%** |
| 16,448 (256) | −0.0% (−0.2 to +0.2) | 0 | +1.0% | −0.8% | +0.5% | +0.9% | +0.3% |

The other change verdicts at 16,384: delete_refill +4.5%, textpk_lookup +4.6%, ipk_lookup +3.9% (each
interval within ±1%). The footprint controls cancel the slowdown completely (16,448 is 64 bytes from
16,384 and costs nothing), so it is L1D set conflict among page headers, and it hits the cases that
walk many pages per row (deletes, updates and inserts that touch several indexes, integrity_check),
not scans or sorts, which work through a few pages at a time. Malloc's own pages (4,608 apart: 32
sets) are as fast as a full spread (4,416: 256 sets; the non-aliasing slab arms run 0-0.5% faster than
malloc, a cost of malloc itself rather than of placement), so 32 sets are already enough here.

What this means for SQLite users: the stock build is safe here, and so is any page cache whose slot
stride is not a multiple of 8 KB on M2 (a slab slot holds a page plus its header, so even 64 KB
pages give an odd stride). The risk is an allocator that aligns each page to a power of two, such
as a custom one, or a system malloc whose size class for the page puts it on page boundaries; which
page sizes do that under macOS's or glibc's malloc was not checked. On x86 (4 KB set
stride, 64 sets) every multiple of 4 KB is one set and 4,608 is 8: the same sweep on the Linux box
(`scripts/box.sh`, stage `sweep`) is where malloc's placement might stop being safe.

Caveats: one disturbed round (batch 1, round 2, the whole machine 33% slow; paired ratios cancel it);
the last batch (pads 16-19, two cases) went ahead at a probe spread of 8-11% after waiting the gate's
limit; the code-spread test flagged one case, join4 in the 16,384 arm (spread 3.0%), and the flag marks all
six of join4's rows *placement*; join4 shows no page effect in any arm.

## Runs

| run | arms | noise-gated | pads per case (4/6/8/10/12) | executions; timing; lock wait | wall |
|---|---|---|---|---|---|
| `null-3.53` | 3.53.0 vs itself (identical binaries) | no (started before `max_noise`) | 5/6/9/2/3 | 657; 1,182 s; 14,831 s | 7.7 h |
| `3.52-vs-3.53` | 3.52.0 → 3.53.0 | no | 2/3/4/7/9 | 657; 1,515 s; 18,127 s | ~8 h |
| `checkins-0a5f27711` | 0a5f27711 → bf66606d4 (page cache), → e9f4537ea (OP_Column) | yes, `max_noise = 0.05` (batch 1 went ahead at 11.3% after 2,700 s) | 5/11/6/1/2 | 985; 1,442 s; 10,959 s | ~7 h |

Raw data, reports and logs: `/Users/dave/work/ngn-k/placemat-work/sqlite/runs/` (`*.raw.json`,
`*.md`, `*.json`, `*.log`). Two earlier attempts of the first two runs crashed after batch 0
(`*.crash1.log`; placemat bug 1 below). Timing itself was 20-25 minutes per run; the rest was the queue
for the machine lock shared with five other placemat runs.

### Null run (3.53.0 against itself)

No *change* verdicts; no code, colour, step or run flags; 24 of 25 intervals contain no change
(ins_ipk −1.2% (−1.8 to −0.5), under the threshold). Three *placement* verdicts (sort_text, group_by,
window_fn) on identical binaries: false positives from the stock-pair test (gap 4 below). Per-arm code
spreads (range of the stock-setting medians over pads) were 1-12% with permutation p 0.22-1.

### 3.52.0 → 3.53.0

No *change* verdicts and no flags; 18 of 25 intervals contain no change; every layout-averaged effect
within ±2.4%. Three *placement* verdicts, each a stock pair against a flat layout-averaged result:
idx_between (stock −5.7%, layout-averaged +2.4%), json_query (+3.5% vs +0.4%), integrity (+9.1% vs
+0.6%); given the null run's three false *placement* verdicts these are not evidence on their own.
`culprits` on them found no loops crossing only in slow variants; its "slow pads" were the first two
batches' pads in every case (3508, 1872, 364, 2824, 1316, 3776): batch drift, not layout (gap 5).
`twospeed`: p90/p10 1.1-1.8 and about 8% of rounds over 15% slow, uniformly over cases and arms:
machine interference, not SQLite switching speeds.

Per case (Ir from Cachegrind, clang -O2, Linux arm64; time from placemat, Apple clang -O2, M2):

| case | Ir | layout-averaged time (95% interval) | stock builds |
|---|---|---|---|
| ins_plain | +0.81% | −1.0% (−1.8, −0.2) | −5.0% |
| ins_ipk | +1.00% | −1.7% (−2.7, −0.8) | −3.0% |
| ins_idx_random | +0.68% | −0.4% (−1.2, +0.4) | −1.5% |
| ins_3idx | +0.47% | −1.0% (−1.8, −0.1) | −4.4% |
| scan_between | +2.00% | +0.7% (+0.1, +1.4) | −1.6% |
| scan_like | +1.52% | −0.1% (−1.1, +0.9) | +1.0% |
| sort_text | +0.10% | +0.1% (−0.7, +0.9) | −0.6% |
| sort_limit | +2.21% | +0.9% (−0.0, +1.9) | −1.6% |
| group_by | +0.70% | +0.6% (−0.1, +1.2) | +1.1% |
| window_fn | +1.01% | +0.8% (−0.2, +1.7) | +2.8% |
| create_index | +0.57% | +0.0% (−0.8, +0.8) | −1.1% |
| idx_between | +3.47% | +2.4% (+1.4, +3.3) | −5.7% (placement) |
| idx_text_between | +1.28% | +0.0% (−1.2, +1.2) | −1.1% |
| ipk_lookup | +0.62% | +0.8% (−0.5, +2.2) | +1.9% |
| textpk_lookup | +0.62% | −0.3% (−1.2, +0.7) | −0.5% |
| upd_between | +0.75% | −0.1% (−1.0, +0.7) | −0.5% |
| upd_rows | +1.03% | +0.5% (−0.5, +1.4) | −0.2% |
| del_rows | +0.79% | +0.3% (−1.1, +1.7) | +1.2% |
| delete_refill | +0.42% | −0.4% (−1.8, +1.0) | +0.5% |
| join4 | +0.84% | +0.8% (−0.2, +1.7) | −3.3% |
| subquery | +3.51% | +0.4% (−0.4, +1.2) | −2.9% |
| cte_mandel | +1.31% | +1.8% (+0.7, +2.9) | +2.0% |
| printf_round | −0.10% | −2.0% (−2.7, −1.2) | −4.2% |
| json_query | +1.58% | +0.4% (−0.5, +1.3) | +3.5% (placement) |
| integrity | +0.83% | +0.6% (−0.3, +1.6) | +9.1% (placement) |

Mean over cases: Ir +1.1%, layout-averaged time +0.16%, stock pair −0.6%.

### The two timed check-ins (against their common parent 0a5f27711)

| case | page cache bf66606d4: Ir / time | OP_Column branch e9f4537ea: Ir / time |
|---|---|---|
| ipk_lookup | 0.00% / **−3.1% (−4.0, −2.2) change** | −0.04% / −0.1% |
| upd_rows | 0.00% / −2.4% (−3.1, −1.7) | −0.01% / −0.2% |
| textpk_lookup | 0.00% / −2.1% (−3.0, −1.2) | −0.04% / −0.1% |
| del_rows | 0.00% / −2.0% (−2.7, −1.4) | +0.03% / −0.0% |
| integrity | 0.00% / −1.9% (−2.5, −1.3) | +0.09% / +0.9% (+0.2, +1.6) |
| idx_text_between | 0.00% / −1.6% (−2.5, −0.7) | 0.00% / −0.4% |
| ins_idx_random, ins_ipk, subquery, ins_plain | 0.00% / −0.8 to −1.3% (intervals exclude 0) | ≈0 / −0.2 to −0.6% |
| cte_mandel | 0.00% / +0.5% | **−6.93% / −5.0% (−5.7, −4.3) change** |
| scan_like | 0.00% / −0.0% | +0.62% / +1.1% (+0.3, +1.8) |
| window_fn | 0.00% / −0.4% | −0.08% / −0.8% (−1.3, −0.4) |
| others (scans, sorts, joins, JSON, printf) | 0.00% / within ±0.5% | within ±0.4% / within ±1% |

Against the claims: the page-cache change is a real 1-3% gain on page-lookup-heavy work that only
timing can show; the OP_Column branch's −0.62% in SQLite's own benchmark (gcc -Os) shrinks to −0.13%
in speedtest1 with clang -O2, and placemat sees it only where floating-point columns dominate
(cte_mandel −5%, agreeing with Ir −6.9%); elsewhere it is flat, with small real costs on scan_like
and integrity. Stock-pair *placement* verdicts in this run (ins_plain −4.0% and ins_3idx −3.6% vs
−0.8/−0.9% layout-averaged, json_query and integrity for the page cache; group_by +3.2% for
OP_Column) are again at the null run's false-positive rate.

**The one code flag:** scan_between in the parent build 0a5f27711 (code spread 4.2%, per-arm
p = 0.0001; the other two arms, with nearly identical code, not flagged). `culprits`: no loop
candidates, but "every slow pad shifts the code by [20, 28] mod 32, every fast pad by [0, 12, 16, 24]
mod 32 (an alignment effect)", slow pads +2.1% (worst +2.3%). A small alignment-phase sensitivity
in one build; targeted pads (`--pads`) were not run for lack of machine time.

## Cachegrind, SQLite's own method

`cachegrind.sh`, Lima VM (Linux arm64 on the same M2), clang 21 `-O2`, same SQLite options, the
timed body of each case only (Cachegrind client requests around it, `--instr-at-start=no`).

**Determinism.** Two Cachegrind runs of the same 3.53.0 binary: instruction counts (Ir) identical
in all 25 cases (0.000%); simulated I1 misses identical; simulated **D1 misses differ by up to
±7.5%** (subquery −7.5%, group_by −5.3%, window_fn −4.8%, ins_plain +3.6%). Cachegrind's cache
simulation sees the heap's placement, which moves between runs (ASLR and the C library), so its miss
counts carry a data-placement term even for one binary; only Ir is placement-free.

**3.52.0 → 3.53.0, per case (Ir):** every case but one runs more instructions in 3.53.0:
+0.4% to +3.5% (idx_between +3.5%, subquery +3.5%, sort_limit +2.2%, scan_between +2.0%,
json_query +1.6%, scan_like +1.5%, cte_mandel +1.3%, idx_text_between +1.3%, the inserts, updates
and deletes +0.4% to +1.0%), printf_round −0.1%. (3.51.0 → 3.52.0: within ±0.4% except
printf_round −12.4% and idx_between −1.7%.) The simulated I1 misses change wildly between
versions (scan_between +670%, sort_text −96%, scan_like +80%): the simulated 32 KB I1 sees the new
code layout, i.e. Cachegrind's own cache model has a code-placement effect between builds.

**Whole-benchmark figures** (Ir, millions):

| build | speedtest1 `--testset main --memdb --size 10`, clang -O2 | SQLite's standard benchmark: gcc 15 -Os, MEMSYS5, `mix1 --size 5 --heap 40000000 64` (`speedcheck.sh`) |
|---|---|---|
| 3.51.0 | 1,232.4 | 1,432.0 |
| 3.52.0 | 1,224.8 (−0.6%) | 1,380.4 (−3.6%) |
| 3.53.0 | 1,238.8 (+1.15%) | 1,372.8 (−0.55%) |

So the instruction-count verdict on 3.52 → 3.53 depends on the compiler and options: −0.55% with
the project's own configuration (gcc -Os), +1.15% (speedtest1) and +0.4..+3.5% per case (sqlbench)
with clang -O2.

## Performance check-ins that cite measurements (the extra task)

Searched the GitHub mirror's log (2024-01 to 2026-10) for performance check-ins. SQLite's
convention (test/speedtest.tcl) is Cachegrind's Ir on speedtest1's `mix1` testset at size 5,
built with gcc `-Os -DSQLITE_ENABLE_MEMSYS5` and run with `--heap 40000000 64`, i.e. on SQLite's own
buddy allocator in one 40 MB heap (note for placemat: a buddy allocator aligns blocks to their own
size, the structure that gave Amber its structural L1 set conflicts; Cachegrind cannot see them).
Check-ins often cite "cycles", which in this method are instructions.

| check-in | claim, and how measured | why it is interesting for placemat | sqlbench cases |
|---|---|---|---|
| [47774fd90](https://github.com/sqlite/sqlite/commit/47774fd90) 2026-08-20 (merge; parent 912706d94) | jsonbPayloadSize() inlining "saves a little more than 10 million cycles (0.6%) on the standard benchmark (gcc 13.3, -Os, mint linux)", +700 B of code | a sub-1% gain measured only as instructions; adds code in the JSON section, moving everything linked after it | json_query |
| [e9f4537ea](https://github.com/sqlite/sqlite/commit/e9f4537ea) 2026-08-07..08 (the OP_Column branch, fec25f94b..e9f4537ea on 0a5f27711, merged as e0725b0a4) | "Performance optimization in OP_Column": serial types decoded inline in sqlite3VdbeExec instead of calling sqlite3VdbeSerialGet(); one branch step (d3b165a85) "gives up a few CPU cycles ... according to speedtest.tcl, but not many", "the branch as a whole is still much faster than trunk" | changes the hottest function (the bytecode interpreter's big switch), so it moves the code of every opcode after OP_Column; step-by-step decisions on margins of "a few cycles" | nearly all; cte_mandel and scans most |
| [bf66606d4](https://github.com/sqlite/sqlite/commit/bf66606d4) 2026-08-05 (merge of da6fd9a04; parent 0a5f27711) | "Small performance improvement and size reduction in the page cache": the hash bucket is `iKey & (nHash-1)` instead of `iKey % nHash` on the page lookup path; no number | a cycles-only change: one `udiv` (several cycles' latency on arm64) becomes one `and`, so Ir cannot see it; it sits on the page-cache hash table, which is one of the blocks the data axis moves | every case that fetches pages: ipk_lookup, textpk_lookup, inserts, updates |
| [44980e816](https://github.com/sqlite/sqlite/commit/44980e816) 2025-01-27 (3.49.0) | hashing to match columns on INSERT: "about 1.8% faster overall according to test/speedtest.tcl" | a 1.8% instruction-count gain; not timed here | the inserts |

Also seen, not shortlisted: 8fafd53c0 "Refactor. 2M cycles faster on x64" (~0.1% of the
benchmark: a margin far inside any layout spread), c585e03a4 "fewer CPU cycles to commit a read
transaction", 92f270eb0 (a second page-cache mask change), 2227372b2 (varint decoding).

**Cachegrind on the shortlisted pairs** (`cachegrind.sh`, clang -O2; `speedcheck.sh`, SQLite's own
configuration):

| change | standard benchmark (gcc -Os, MEMSYS5, mix1) | speedtest1 main (clang -O2) | sqlbench per case (clang -O2) |
|---|---|---|---|
| 912706d94 → 47774fd90 (JSON) | −9.85 M Ir, **−0.73%**: the claim reproduces | +0.000% (no JSON in `main`) | json_query −2.84%; all others 0.00% |
| 0a5f27711 → e9f4537ea (OP_Column) | −8.42 M, **−0.62%** | −0.13% | cte_mandel −6.93%; scan_like +0.62%, group_by +0.39%, scan_between −0.37%, join4 −0.26%; others within ±0.2% |
| 0a5f27711 → bf66606d4 (page cache) | +18 instructions (0.000%) | 0.000% | 0.00% in every case |

So Cachegrind confirms the JSON claim and sees the OP_Column branch's gain mostly at `-Os`
(where gcc does not inline what clang -O2 already inlines), and it sees nothing at all for the
page-cache change, whose whole point is a cheaper instruction, not fewer. These two were timed with placemat (above).

## Placemat bugs and design gaps hit

No core module was changed. In order of cost:

1. **Runner crash at the first anchor batch, losing all timings** (the version loaded at 10:57).
   Both survey runs died entering batch 1, after ~70 minutes each:
   `placemat/runner.py, slot_env: j, kind = D.parse(slot)` → `design.py, parse: j, k = vid.split(".")`
   → `ValueError: not enough values to unpack (expected 2, got 1)`: the anchor slot `a1` has no dot.
   Another session fixed it on disk at 11:21 (`(0, STOCK) if slot.startswith("a")`), mid-run, so
   my already-running processes still crashed. That version also wrote the raw JSON only at the very
   end, so batch 0's timings were lost (P002 lesson 5); the current runner writes a partial raw file.
2. **The interposer cannot colour SQLite on macOS:** SQLite's default allocator (`mem1.c`) calls
   `malloc_zone_malloc`/`malloc_zone_free` on the default zone, which `interpose.c` does not interpose
   (it says so). Worked around with SQLite's allocator API (`SQLITE_CONFIG_MALLOC`, now
   `data.method = "allocator"`). Any project using zones (or SQLite embedded in one) needs this.
3. **Build cache ignores the adapter's own files.** A directory arm's cache key hashes `[build]
   inputs` inside the arm; `build.py` and the harness live in the config directory, so editing them
   silently reuses stale binaries. Workaround: a fresh `--work` per harness change (README).
   A `[build] inputs` entry relative to the config directory, or hashing the build command's files,
   would fix it.
4. **The stock-pair test gives false *placement* verdicts.** Null run, identical binaries: 3 of 25
   cases got *placement* because the stock pair's percentile-bootstrap interval over 7 rounds
   excluded 1 (sort_text −6.5% (−16.9 to −0.8)). That is 12% against a nominal 5%, with no
   correction across cases (the code/data/run families get Benjamini-Hochberg; this test does not),
   on a noisy machine. Every *placement* verdict in the other two runs was of this kind; none can be
   trusted alone. Suggest BH plus p ≤ 0.01 for the stock pair as for the other families, or a
   *placement* verdict only with a sensitivity flag.
5. **`culprits` is confounded by batch-to-batch drift.** In the 3.52 → 3.53 and null runs its "slow
   pads" were the same six (3508, 1872, 364, 2824, 1316, 3776 — batches 0 and 1, timed at load ~5)
   for every case, arm and run: it ranks pads by absolute time across batches. It should compare
   within batches or on the anchor's scale, as the code test does.
6. **`placemat culprits` did not exist when this work started** (`cli.py` imported `.culprits`;
   no `culprits.py` until 09:50); it does now.
7. **Dimensioning advice is distorted by the queue:** `c2` (cost of a pad) is taken from `build_s`,
   which counts waiting for the shared lock (11,651 s of "builds" in the null run, for links that take
   about a second), so c2 = 459 s and r* is inflated; and S2 is taken from absolute times across
   batches, so batch drift inflates it (S2 ≈ 0.024, i.e. a 15% between-pad s.d., where the code
   spreads were 1-12%). Cases timed in one batch get S2 = 0 and "r* = inf". The report's "Builds N s"
   label has the same problem.
8. **Every batch queues twice** (shared lock for builds, then exclusive for timing), and quiet.py's
   turnstile makes the shared request wait behind every queued exclusive one: builds of a few seconds
   waited up to 85 minutes. Building all pads of the cap up front (cheap here: a link each) would
   halve the queueing.
9. **Cases in one execution are correlated:** sqlbench runs all cases in one process, so one
   disturbed execution moves every case together (the null run's stepped setting read about −1% on
   the first six cases at once). Benjamini-Hochberg tolerates positive dependence, but per-case
   intervals are not independent evidence, and the reports do not say so.
10. **Machine noise vs the thresholds:** the two survey runs were started before `max_noise` existed
    and ran at noise-probe spreads up to 14%; the gated run once went ahead at 11.3% after 2,700 s.
    The verdicts are therefore conservative (wide code spreads, p ≥ 0.2), not layout-free.
11. Minor: `nm` lists `__mh_execute_header` as a `T` symbol, so "the first text symbol" is the Mach-O
    header, not the pad (tripped `build.py`'s own check; `placemat/build.py`'s `nm_symbols` includes it
    too, harmlessly). The Lima VM mounts the Mac's home read-only, so VM outputs must go to the VM's
    own disk (`cachegrind.sh` note).

Not placemat, but relevant to anyone timing SQLite: Cachegrind's *simulated* cache misses are not
placement-free. D1 misses moved by up to ±7.5% between two runs of one binary (heap placement), and
I1 misses by −96% to +670% between versions (code placement in the simulated 32 KB I1).
