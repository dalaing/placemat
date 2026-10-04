# Amber's three biggest time sinks in the maintainer's benchmarks: what bounds them, what to do

(Written by the analysis agent, 2026-10-04; saved by the main session from its report.)

Source and binary: upstream `main` (d596e57a; its only change since the profiled d2a5b3ea is docs, and the two builds have the same addresses). Machine: Apple M2, built with the portable `./build.sh` (clang, -O3 -flto). Profile figures come from prof/REPORT.md. Timings are paired runs (layout/pair.sh, 15 rounds, B/A median with quartiles), all taken under pbt/qbuild.sh. "Identical" means the two builds' printed results hash the same (hot/k/*_eq.k).

## Summary

| bucket | what bounds it | gap to floor (before → after prototype) | best change (measured) |
|---|---|---|---|
| grouping/find/distinct (32.5%) | gaggC: instruction count and taken branches (~35 instructions, 5-6 taken branches and 6 stack reloads of spilled invariants per row; it re-dispatches on mask, key width, direct/hash, code and value type every row). unq/fnd/membC: a key-range pass that isn't vectorised. fnd/in with few probes: they build a table over the whole long side. | group, 5M rows: 2.8× → 1.5× the hand-written C loop at 10 groups, 3.4× → 2.5× at 100k groups. ?x at 5M: ~10× → ~1×. bench.k find at 5M: 69 ms → 9 ms | two-phase specialised gaggC loops (−21..−48% on every group case); vectorised range pass (−79..−96% on distinct); probe-side table (−67..−87% on bench.k find and in) |
| reductions (20.6%) | memory bandwidth at ≥5M items; at 1M, the fixed 8-lane float-sum order that issue #16 settled | ≤0-20% at every size | none worth taking under #16. One small item: fredC shift-compare does two passes, and its hot loop crosses a 4 KB boundary |
| moving windows (16.4%) | L1 set conflicts: the 4 resync lanes are 32 KB apart (a multiple of the 16 KB set stride) and the output is at the same offset as the input. Also the NaN test sits in the add chain (fcsel), and van Herk runs one block at a time | msum, 1M: 2.4 → 0.70 ms against a ~0.43 ms chain bound (0.12 ms copy) | skewed lanes + NaN-free blocks + 8-way van Herk: −39..−66% on msum/mavg/mdev, −20..−43% on mmin/mmax (w≥64) |

Combined prototype hot/all (windows + gaggC) against main. Every case not listed moved by less than ±1% (suite.k, final.k), or had a quartile range spanning 0 (qbench, bench-std):
- suite.k: msum100 −56, mavg100 −39, mdev100 −49, mmin100 −33, mmax100 −32, mavg10 −39, mavg1000 −58, mmin1000 −43, grp10 −48, grp1k −38, grp100k −22, sc_mavg_100k/1m/10m −27/−39/−37, sc_grp_100k/1m/10m −48/−48/−47; mmin10 −0.4 (w<64 keeps the old kernel); geomean −22% over all 37.
- scout (5M): group_10/100/10k/100k −46/−46/−36/−26, msum_16 −15, mavg_256 −37, mmax_64 −20, qsql_select −29; amberq group_10/100k −33/−19, qsql −25; sum_f, max_f ±1.
- qbench: groupby_sum −26, filter_groupby −33, groupby_vwap −0.4 (it doesn't reach gaggC).
- bench-std: msum −66, mavg −60, mdev −45; mmin/mmax (w=20) 0.
- bench/bench.k: rolling −63; the rest ±1. final.k: all within ±2.
- our microbench / microbench-q: geomean +0.1 / −2.3%. qselby −40. Untouched kernels: take +2.8 [−5.9..+6.4], iscan +3.0 [0..+4.5] (possibly placement).

hot/fnd against main: bench.k lin_500k/2m/5m −67/−82/−81, lin_100k −16, in_lin/in_bin −83/−82, bin_500k/2m/5m −27/−38/−18 (see flags), bench/bench.k distinct −79; scout distinct −96, distinct_100k −84, find/member +2/0. Our microbench geomean −11 (ifind −99, takekeys −22..−87, idistinct −34, setdictbigkeys −39), microbench-q −2 (qdistinct −37). maxprior and minprior read +6.5 [−5.7..+10.9] and +5.4 [−6.8..+12.1]: noise or placement.

## 1. Hashing, find, grouping (32.5%)

### 1.1 gaggC (15%; every benchmark takes the direct-index path)

**Per row** (src/o.c; hot/dis/gaggC.s; hot offsets from the sample stacks +2704..+3960). One pass over the rows, after a vectorised key-range pre-pass (cmgt/bit, ~10% of the samples at +1756..1772). For each row: tbnz on the mask → compare chain on the key width → load the key → reload `direct` from the stack → reload the slot pointer → slot[key-lo] → branch on a new group → gc[g]++ (only `count reads gc) → reload `code` and compare it 3-4 times → reload vf → reload vp → load the value → NaN branch → af[g]+=d. About 35 instructions, ~10 branches (5-6 taken) and 6 stack reloads; the inlined realloc growth paths spill the invariants. Nothing is unswitched.

**Layout:** struct-of-arrays, one array per field: gk 8 B, gf 4 B, af 8 B, al_ 8 B, gc 8 B, indexed by group id in first-appearance order, plus slot[key-lo] (int32). A sum row touches 3 random lines (slot, gc, af). At 10-1000 groups that is all L1. At 100k groups it is 400 KB of slot plus 2×800 KB, i.e. L2.

**Multi-aggregation:** one gagg call per aggregate (qaggf maps gag1 over the items; each call recomputes the range and the ids). wavg/wsum take 2 passes plus a materialised w*c.

**Bound:** throughput, not memory: U 56-64, P 19-38, D up to 22 (taken branches). The cost is flat from 10 to 100 groups (2.5 ns/row).

**Floor** (hot/c/grp.c, 5M scout rows, ms per call):

| groups | range pass | C, gaggC semantics | c_ref hash | Amber main | prototype |
|---|---|---|---|---|---|
| 10 | 0.93 | 4.08 | 6.59 | 11.6 | ≈6.2 (−46%) |
| 100 | 1.02 | 3.93 | 5.62 | 11.8 | ≈6.4 (−46%) |
| 10k | 0.93 | 4.43 | 13.8 | 14.9 | ≈9.6 (−36%) |
| 100k | 1.01 | 5.61 | 25.2 | 19.2 | ≈14.2 (−26%) |

Amber's figures also include building the dict and the scout checksum.

**Changes:**
1. Two-phase specialised loops (prototyped: hot/gagg, patches/gagg-o.c.diff). Rows go in blocks of 2048. Phase 1 maps rows to group ids in one loop per key width (direct or hash; groups are still created in row order). Phase 2 runs the update in one loop per code and value type. gc is updated only for `count. Gains as above. Risk: identical output on grp_eq.k (all 7 aggregates; keys tG..tL and tS; direct and hash; NaN, −0.0, nulls; masked qSQL; wavg). The code is shared only with gagg's callers (amber.k gsum..glast, qsql qaggf). gaggC grows by ~9 KB; text +27 KB in hot/all together with the window change. Placement: the only new 4 KB-crossing loop is a cold uzp1 narrowing loop.
2. Reuse the group ids across aggregates in qaggf (not prototyped): one range pass and one id pass, then N phase-2 loops. Saves ~1-1.5 ms per extra aggregate at 5M. No maintainer case has more than one fused aggregate.
3. Fold the range pass into phase 1 (not prototyped): assume direct indexing on the first block's range, fall back to hash if a later block leaves it. Estimate −10-15%.

### 1.2 unq/fnd/membC: the range pass is not vectorised

`F(m,L v=RD(w,a,i);min/max)` re-tests the width on every element. Hot offsets unq+2036..2064 disassemble to `ldrsw; cmp; csel; cmp; csel; ...; cmp w26,#5; b.eq; cmp w26,#4`. unqLUT then saturates almost at once, so `?A` at 5M (4.1 ms) is almost all range pass. Change (prototyped: patch_rng.py, rngW = one loop per width, which clang vectorises): scout distinct −96%, distinct_100k −84%, suite/bench distinct −79%, find_lin_100k −16%, idistinct −34%, qdistinct −37%. Results can't change (min and max are exact).

### 1.3 Few probes against a long vector

fndL indexes the haystack: 16M slots (~200 MB) for bench.k's 5,000 probes into 5M, 71 ms, P 56. membC indexes the 2M set and ignores `s. Change (prototyped: patch_probe.py): when probes×32 ≤ the long side, index the distinct probes, with a 4-bits-per-slot bitmap prefilter in L1. Scan the long side once, forward, keeping the first hit per value, and stop when all are found. Measured: lin_500k/2m/5m −67/−82/−81% (69 → 9 ms), in −83%, ifind −99%, takekeys −22..−87%. Identical output on fnd_eq.k (long, int32, nulls, symbols; misses, duplicates, empty). Without the prefilter the scan costs 4.7 ns per item (the 192 KB table spills L1). Still open: the `s attribute in `in`, and batched lower-bound search for `?` on `s vectors (bIL, 350 ns per probe).

### 1.4 grp/grpI

b_bench:groupby (grp 63%) and qbench groupby_vwap (grpI 52%) use the generic `=` path. `wavg[sz;px]` (bracket form) is not recognised by qsagg. The infix form is fused (13.4 → 8.1 ms) but changes the last bits (max |Δ| 2.6e-11), so it needs the maintainer.

## 2. Reductions (20.6%)

admf → simd_sum_f64: on arm64, 4 vector accumulators = 8 lanes, the order issue #16 fixed. addfL/maxfL/minfL are already vectorised (16 cmgt.2d+bsl accumulators). mulfL: 16 scalar mul chains. Floors (hot/c/red.c):

| case | main | floor | note |
|---|---|---|---|
| +/ 1M floats | 0.114-0.12 ms | 0.119 (8-lane order) / 0.097 (16-lane or read) | at the floor of the #16 order (4 fadd chains × 3 cycles); 16 lanes −18% but changes results, not proposed |
| +/ 5M floats | 0.65 | 0.71 read | DRAM |
| +/ */ &/ \|/ 20M longs | 2.55/3.44/3.1/3.1 | 3.06 read | ≤15% |
| same, 100k | 8-16 µs | 9 µs | L2 |
| \|/ 5M floats | 0.79 | 0.71 | ≤12% |
| &/ 20M bytes | 0.28-0.5 | ~0.3 | DRAM |

Changes: (1) fredC shift-compare (sort_presorted 1.9 ms) does two passes, a NaN/−0.0 check then the count. Fuse them and redo on the old path only if one is seen; estimate 1.9 → ~1.1 ms. Placement flag: main's float shift-count loop at 0x10001affc crosses a 4 KB boundary, as do 7 other fredC loops; measure on the order-file build first. (2) Nothing else is worth doing without changing the float order.

## 3. Moving windows (16.4%)

Already O(n): running sums with an exact recompute every 4096 items (part of the semantics), 4 interleaved resync blocks; van Herk for min/max (3 compare-selects per item); a deque only for float NaN. That is better than c_ref.c (a single-chain sum with no resync; a deque for max).

**Bound** (hot/c/mw.c, verbatim copy, identity-checked):
- L1 set conflicts: the lanes are 32 KB apart and M2's L1D (128 KB, 8-way) has a 16 KB set stride, so all lanes share one set. The output comes from the same bucket class at the same offset, giving 8+ lines in an 8-way set. Same code, output moved by 64 B: msum 1M w=100 2.46 → 0.82 ms, mavg 3.34 → 1.19, mdev 5.20 → 2.63. This is the P 50-80% stall. It depends on where the buffers land (data placement, not code placement): the maintainer's suite run got the cheaper placement (1.2 ms), ours the costly one (2.4 ms).
- The NaN select is in the chain: fadd→fcsel→fsub→fcsel ≈ 10 cycles per lane step.
- min/max: serial fcmp+fcsel chains with one block in flight; at w≥~100 no overlap (mmin10 1.25 ms against mmin1000 2.66 ms).
- mdev: 2 fdiv + fsqrt per item.

**Floor:** copy 0.12 ms per 1M; 4-lane chain bound ≈0.43 ms; prototype sum 0.70, avg 0.77.

**Changes:**
1. Skew the lanes by 16 items, plus a NaN-free fast step per 4-block group (prototyped: hot/mw, patches/windows-v.c.diff). Same additions per lane, so identical output on win_eq.k (5 inputs incl. NaN, −0.0, inf, int nulls, int32; 12 widths incl. 4095-4097; 6 functions). Standalone at either buffer offset: sum 0.70-0.94, avg 0.77-0.98, dev 1.75-2.0 ms. In Amber: −39..−66%. 8 lanes did not help.
2. 8 interleaved van Herk blocks for 64≤w≤4096 (same diff, identical incl. ties; suffix buffer 9×(w+1)): w=100 −32%, w=1000 −43%, mmax_64 −20%.
3. Vectorise EMIT, 2 lanes of div/sqrt per block (not prototyped): estimate avg 0.77 → 0.55, dev 1.75 → 1.0.
4. w<64 min/max by log-doubling, combine(older,newer) keeping newer on ties = van Herk (not prototyped): estimate 1.4 → 0.8 ms at w=20.
5. Colour large bucket allocations by k×64 B (not prototyped; global allocator change, lower priority).

## 4. Code placement

Main build (prof/main = hot/base layout); "crossing" = loops4k.py reports a loop of ≤256 B straddling 4 KB:

| kernel | address | crossing in main? |
|---|---|---|
| gaggC | 0x10006cb20-0x10006e4d0 | no |
| fnd | 0x100048004-0x10004b608 | yes: 0x100049fe4-0x10004a034 (48-80 B), fndL hash-probe loop for 16-bit probes |
| unq | 0x10006baa4-0x10006cb20 | no |
| membC | 0x1000472cc-0x100047b8c | no |
| grp | 0x10006a678-0x10006b2f0 | yes: 0x10006afe0 (36 B), byte-key counting loop |
| grpI | 0x10006b2f0-0x10006b9ac | no |
| admf | 0x1000112a0-0x10001155c | no |
| addfL/minfL/maxfL/mulfL/minfG/addfG | 0x1000102a8/0x10001071c/0x100010d74/0x10001258c/0x100010444/0x10000fc68 | no |
| mmmf | 0x1000117bc-0x100011e30 | no |
| fredC | 0x100016cb4-0x100020038 | yes, 8 loops incl. float shift-count 0x10001affc (64 B) |
| mwsum/mwavg | 0x100083450/0x1000838cc | no |
| mwdev | 0x1000847dc-0x1000850a8 | yes: 0x100084f3c (240 B), block recompute/tail loop only (~1% of items) |
| mvmaxF/mvminF | 0x1000858f8/0x100085a1c | no |

hot/all: text +27 KB; the only new crossing is gaggC's cold uzp1 loop, and mwdev's no longer crosses. hot/fnd: text +3.7 KB; new crossings in grp (16-bit), unq (byte path) and gaggC (8-bit range pass), all cold here (bench/bench.k groupby +0.7 [−0.2..+5.5]).

**Flags:**
- Window gains are algorithmic: the standalone C shows the same effect with identical code, and the main loops cross no boundary in any build.
- gaggC: algorithmic in direction (35 → ~10 instructions per row); its exact size could shift a few % with placement.
- bench.k bin_* −18..−38% in hot/fnd: bIL is unchanged; likely the preceding lin case no longer builds and frees a 200 MB table (memory state).
- Untouched kernels: take +2.8, iscan +3.0 (all); maxprior +6.5, minprior +5.4, scout find +2.1 (fnd). Placement or noise; re-check on the order-file build.
- fredC's gap and main's fnd/grp crossing loops: possibly placement.

## 5. Re-running

`hot/rerun.sh A_DIR B_DIR [ROUNDS] [OUT]` runs everything as paired runs (B against A) under the shared lock:
- hot/k/{win,grp,fnd,red}.k;
- the maintainer's scripts via hot/wrap/run.sh: suite, qbench, bench-std ×3, scout amber.k/amberq.k at 5M, final, bench.k, bench/bench.k;
- microbench.k and microbench-q.k;
- the identity checks (k/*_eq.k must print "same") and loops4k on both binaries.

Both directories need a built ./amber and the same .k libraries. Builds: hot/base (main), hot/all, hot/mw, hot/gagg, hot/fnd (patch_rng.py + patch_probe.py). Results: hot/pairs/. Floors: hot/c/{grp,red,mw}.c via hot/c/cc.sh. Disassemblies: hot/dis/.
