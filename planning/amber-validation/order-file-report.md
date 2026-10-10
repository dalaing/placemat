# A linker order file for Amber's hot code: prototype and measurements

(Written by the order-file agent, 2026-10-04; saved by the main session from its report, with its B/B16 clarification folded in.)

A measurement exercise; nothing goes upstream. Source: upstream main d596e57a (clean `git archive` in `of/A`). Apple M2, Apple clang 17, ld-1230.1, the portable `./build.sh` (-O3 -flto). Builds through `pbt/qbuild.sh`; timings only through `pbt/evidence.py --dirs X Y --layouts 6` (6 padded variants plus the stock build, 7 rounds, paired), one evidence.py at a time. Load at start and end of every run 2.8-3.4 (each report's Variants footer).

## Summary

**The order file alone gives most of the stability.** In the stage, the order-file build (B) had no layout-sensitive cases; main (A) had 9-21 per run. But B pins the hot functions only to within about 64 bytes: ld64 keeps each function's address mod 16 when it reorders (the LTO object's `__text` is 16-aligned because clang gives a few functions, gaggC and asc, `.p2align 4`; the rest are 4-aligned at arbitrary offsets), so in B's padded variants the ordered functions drifted by −64 to +16 bytes. Adding `-falign-functions=16` (it survives LTO) makes them exact: 0 shift for all 101 functions. Exact placement is what padding needs.

**Stability.** Main's sensitive cases recur in every run: the `setdictbig*` family (one variant 2× slower), chargrade +33%, fnd.k sc_member +35%, takekeys10x10 +17%, s_grp10/s_grp100k +12-18%, sc_qsql_select +10%. All pinned builds flatten them; their remaining sensitive counts (0-6) are about what the uncorrected p<0.01 test flags by chance over 200 cases. The recommended build, C2: 0 sensitive cases, median spread 3.2% (main 3.5%), max 31% (a memory-bound 20M reduction, noisy in every build).

**Speed.** Pinning fixes one layout for every case, and that layout has its own ±5-10% luck on a few cases. B16 (aligned, no pads) leaves `cntgrd`'s loops across 4 KB in every build: dategrade +10%, stampgrade +8.5%. C (loop pads) fixes that but puts a 4 KB boundary 80 bytes into `psh`, between its entry and its hot loop: JSON and datelist cases +4-9%. C2 also keeps each function's hot entry span inside one page: no measurable slowdown, geomean over 200 cases −0.6% [−0.9, −0.3].

**#69.** Same order file, `-falign-functions=16`, pads regenerated for the branch: grade/igradedown no longer swing (stock vs stock +13.5%/+12.0% before; now +0.9%/+1.0% with the C procedure, +0.9%/+2.6% with C2). The real amend effects remain: setdictnest +4-5.5%, setdictverb +4.5%, settableverb +3-3.6%, setlistdictall +2-3%.

## How the order file was built

- **Profile** (`tools/prof2.py`): the per-case samples in prof/ re-attributed by address (126 maintainer cases, 130 ours, each weighted equally), because `sample` strips LTO suffixes (`_o8.1005` → `o8`) and attributes addresses past `__text` (stubs such as bzero) to the last function. Call edges from the sample call trees (cut above `evs`/`run`).
- **Selection** (`tools/pick.py`): ranked by combined share, taken until both sets reached 95% of samples inside amber: 101 symbols, covering 99.3% (maintainer) and 95.2% (ours) of amber samples, 97.2% and 84.3% of all samples (ours spend 10% in libsystem memmove and TLV). 211 KB of the 618 KB text; fredC alone 38 KB.
- **Grouping** (`tools/order.py`): C3-style, each function merged behind its heaviest caller; clusters capped at 16 KB; a merge skipped when the caller's cluster is under 1/8 as dense; clusters sorted by density (samples/byte). E.g. `_2 gaggC amrdx8 mvminF membC mvmaxF mwsum i1`, `_1 fnd mxms fFL an`, `run ajc wjbC ejxC`. File: `of/hot.order`.
- **Naming under LTO:** none of the 101 needed a suffix; ld64 accepts local suffixed names (`_o8.1005` placed correctly in a test). ld64 warns about missing entries ("can't find function/data for order_file entry", "only N out of M order_file symbols were applicable"; `-Wl,-order_file_statistics` works), but build.sh's link line sends stderr to /dev/null, so every build was checked directly: the first 101 text symbols in `nm -n` match the file exactly, in all builds including #69's.

## Builds

| build | what |
|---|---|
| A | main d596e57a as is |
| B | A + `-Wl,-order_file,amber.order` |
| B16 | B + `-falign-functions=16` (every function 16-aligned: the ordered region exactly pinned) |
| C | B16 + pad functions between hot functions (`src/zz_ofpad.c`, listed in the order file); sizes from an exact search over the start address mod 4096 in 16-byte steps: no loop ≤256 B in a listed function crosses 4 KB, fewest pad bytes |
| C2 | C + each hot function's entry span inside one 4 KB page (span: offset 0 to its last sampled offset under 1 KB holding ≥10% of its samples; 75 functions have one) |
| D | C + each hot function's hottest loop ≤64 B inside one 64-byte line (pad equivalent of `aligned(64)`; a source-level `aligned(64)` would raise section alignment to 64 and, by the mod-16 rule, ld64 would then keep addresses mod 64 — inferred, not measured) |

| build | __text bytes | vs A | pad bytes | small loops crossing 4 KB, all code | in the 101 hot | hot entry spans crossing 4 KB (of 75) | hottest small loops crossing a 64 B line (of 56) |
|---|---|---|---|---|---|---|---|
| A | 617548 | +0 | 0 | 79 | 27 | 11 | 29 |
| B | 618476 | +928 | 0 | 71 | 26 | 6 | 33 |
| B16 | 621280 | +3732 | 0 | 92 | 26 | 5 | 30 |
| C | 622192 | +4644 | 912 (12 pads) | 55 | 0 | 5 | 26 |
| C2 | 624784 | +7236 | 3504 (18 pads) | 57 | 0 | 0 | 30 |
| D | 624784 | +7236 | 3504 (36 pads) | 57 | 0 | 7 | 3 |
| #69 stock | 620084 | +2536 | 0 | 81 | 36 | | |
| K0 = #69 + order + align16 | 623840 | +6292 | 0 | 84 | 30 | 5 | 28 |
| K = #69, C procedure | 624672 | +7124 | 832 (13 pads) | 52 | 0 | 5 | 28 |
| K2 = #69, C2 procedure | 627264 | +9716 | 3424 (17 pads) | 49 | 0 | 0 | 30 |

- B's extra 928 bytes: the mod-16 gaps (zero `udf` words) ld64 leaves between reordered functions.
- What moves in B (padded variants vs B's stock build, 3 of 6 pads): 576-byte pad −48..+4 bytes (mostly −12, −16, −28, −32, −44); 1256 −64..+16 (mostly −4, −52, −48, +12); 1940 −48..+8 (mostly −8, −24, −16). Hot crossing loops 26 stock → 25 in each of those 3. The stage's shift column agrees: B 0.3-1.3% "unmoved"; B16 and C 100% within the ordered region. Why B still showed 0 sensitive cases: ≤64 bytes of drift against a 4 KB period flips only loops that close to a boundary, and no timed hot loop flipped.
- In B16 (variants 576, 1256) and C the ordered functions moved by 0; functions outside the order file still move, as intended.
- win_eq, grp_eq, fnd_eq: identical outputs under A, B, B16, C, C2, D.
- Pinned builds relink in seconds once the objects exist; the pad search (`tools/pads.py`) takes under a second.

## Stability: layout spread per case across the 6 padded variants

Cases (200): bench.k (10) and bench-std.k (6, whole); all of pbt/microbench.k (109) and microbench-q.k (29); the maintainer's kernels from suite.k, scout amber.k at 5M, final.k and qbench through hot/k/{win,grp,fnd,red}.k (46). suite.k, final.k and scout were not run whole (evidence.py takes out[]-style scripts only; hot/k holds those kernels in that form).

Spread = range of variant medians relative to their median. Sensitive = the stage's test (range over threshold and permutation p < 0.01), uncorrected across cases: ~2-4 chance flags per run expected over 200 cases × 2 builds.

| build | run | median spread (200) | median, windows excl. (184) | max, windows excl. | cases >10%, windows excl. | sensitive | which |
|---|---|---|---|---|---|---|---|
| A | A-B | 3.5% | 3.5% | 100.6% | 15 | 21 | setdictbig/-rows/-all/-name 98-100%, chargrade 34%, sc_member 33%, setdictbigkeys 30%, takekeys10x10 16%, s_grp10 14%, s_grp100k 10%, sc_qsql_select 10%, setdictflat 9%, setdictverb 8%, setdictrows 7%, tillists 6%, takecols 5%, fdistinctshort, genericdistinct, setlistnest, takekeys10x1, takekeys1kx10, qhhtime 3-4% |
| A | A-C | 3.5% | 3.5% | 101.7% | 15 | 9 | setdictbig family 96-102%, sc_member 36%, chargrade 33%, takekeys10x10 17%, s_grp100k 12%, sc_qsql_select 10% |
| A | A-B16 | 3.5% | 3.4% | 100.7% | 13 | 14 | setdictbig family 98-101%, sc_member 35%, setdictbigkeys 28%, takekeys10x10 18%, find 5M binary 13%, settablecol 9%, setdictnest 8%, sc_qsql_select 8%, ffind 7%, sc_mmax64 5%, qlj 4% |
| A | A-C2 | 3.8% | 3.6% | 100.5% | 16 | 13 | setdictbig family 99-100%, sc_member 36%, chargrade 31%, setdictbigkeys 30%, takekeys10x10 19%, s_grp10 18%, setdictnest 9%, sc_max_f5m 9%, settableverb 8%, setdictflat 7% |
| **B** | A-B | 2.8% | 2.8% | 32.1% | 7 | **0** | |
| **B16** | A-B16 | 3.0% | 3.0% | 36.6% | 3 | **3** | setdictrows 5%, fdistinctlong 4%, fgroup 3% (not cntgrd: that is a constant change, not spread) |
| **C** | A-C | 3.4% | 3.2% | 69.5% | 3 | 8 | the 5 window cases, find 5M linear, isum, charmin 5% |
| **C2** | A-C2 | 3.4% | 3.2% | 30.7% | 4 | **0** | |
| C | C-D | 3.2% | 3.1% | 36.4% | 2 | 1 | sc_distinct_100k 5% |
| **D** | C-D | 3.6% | 3.4% | 39.2% | 4 | 6 | s_grp100k 7%, modlarge 6%, sc_distinct 5%, fgrade 4%, takekeys10x10 4%, takekeys100kx1k 3% |
| C | C-K | 7.0% | 7.0% | 33.8% | 28 | 0 | (noisy run) |
| K | C-K | 7.8% | 7.8% | 32.5% | 54 | 5 | flipdict 14%, genericdistinct1k 14%, tillists 13%, jsonread17 11%, fgrade 11% |

- **Main's sensitive cases are real code placement:** one variant out of line, the others agree (setdictbig 12.0 ms in variant 1 vs 6.0 ms; chargrade 45.9 vs 34.3 ms in variant 4). Flat at 2-5% in all pinned builds.
- **Largest remaining spreads are not code placement:** red.k f_min_int20m (30-70% in every build), the 20M long reductions, bench.k 500k binary find: memory-bound, never flagged, as noisy in A.
- **Window cases switch between two speeds run to run** (win.k s_msum100, s_mavg*, s_mdev100; bench-std msum/mavg/mdev), whatever the code placement: s_mavg100 ≈23 or ≈33 ms; A's variant 0 fast in A-B, slow in A-C; in C mwavg is at the same address in every variant and its variants still split. Fits ANALYSIS.md's data-placement diagnosis (L1 set conflicts). Their sensitivity flags are false positives (left out of the "windows excl." columns).
- **The C-K run was noisy:** within-run variance about double for both C and K (variants 0, 2 and stock >3% slow in about a third of cases for both; the same C variants were not slow in A-C). Spreads reflect the machine there; paired ratios still stand.

## Speed against A

Layout-averaged: geometric mean over variants of the paired ratio, 95% two-level bootstrap; the overall geomean interval combined from per-case intervals.

| build vs A | geomean (200) | microbench (109) | microbench-q (29) | bench.k | bench-std | win.k | grp.k | fnd.k | red.k |
|---|---|---|---|---|---|---|---|---|---|
| B | +0.05% (−0.29, +0.39) | −0.26 | +0.00 | +0.13 | +3.4 (−1.3, +8.4) | +1.5 | −0.8 | −0.4 | +0.2 |
| B16 | −0.23% (−0.57, +0.12) | −0.30 | −0.08 | −0.04 | −0.7 | +0.9 | −0.8 | −1.05 (−1.95, −0.15) | −0.2 |
| C | −0.11% (−0.46, +0.24) | −0.19 | −0.05 | +0.07 | +3.0 (−1.1, +7.2) | −0.9 | −0.5 | −0.7 | +0.6 |
| **C2** | **−0.59% (−0.92, −0.26)** | −0.53 (−0.92, −0.13) | −0.04 | +0.34 | −1.6 | −2.2 | −0.9 | −0.7 | −0.4 |
| D vs C | +0.00% (−0.29, +0.29) | −0.12 | −0.46 (−0.75, −0.17) | +0.17 | +1.9 | −0.2 | +0.7 | +0.25 | +0.7 |

Measurably slower cases (interval above zero; the stage's "change" verdict also needs >3%):
- **B:** no change verdicts. Small: setdictdictfan +2.4% [+1.8, +2.9], fstring +2.2% [+1.2, +3.2], setlistbigfan +2.1%, setlistdictall +1.4%, setlistdictfan +1.3%, kprint +1.3%.
- **B16:** **dategrade +10.1% [+7.7, +12.2], stampgrade +8.5% [+5.3, +9.9], flipbits +5.5% [+0.4, +10.1]**: cntgrd (70% of dategrade's samples) has two loops across 4 KB in the pinned layout, so every variant has it.
- **C:** **jsonints +9.4% [+6.0, +11.1], jsonshort +8.0% [+5.5, +10.2], jsonread +5.7% [+2.5, +6.9], datelist +4.0% [+0.6, +5.5]**: no loop crosses; psh starts at 0xfb0, so a 4 KB boundary falls 80 bytes in, between entry and hot loop (offsets 92-252); psh is 24-30% of all four. (psh also crossed in A, B, B16, but at offset 400-550, after its hot span.) C2's entry-span rule fixes it.
- **C2:** nothing over threshold; jsonshort +2.3% [+0.3, +4.1] and setlistdictall +1.3% [+0.1, +2.4] under 3%; dategrade +0.9%, stampgrade +0.8%, jsonints +0.5% not measurable.
- **D vs C:** no consistent net value (another layout's luck), not pursued: removes C's JSON loss (jsonints −4.6%, jsonread −3.1%); faster on several amends (setflatplus −7.4%, settablerow −6.3%, setdictverb −5.4%, setdictnest −5.1%, settablecol −4.8%, qhhtime −7.0%, qhhatom −5.3%); slower on eachenum +4.3% [+2.2, +5.8], settablerows +3.6% [+0.9, +5.3]; its 36 pads leave m0 crossing 4 KB 48 bytes in.

## The #69 check (fix/amend-keep-more-2 at 3216f9cb)

Procedure: export the branch with B16's build.sh and the same `amber.order` (all 101 present and in order) → K0 → `tools/pads.py K0/amber` (with `hotspans.json` for C2) → K (C procedure) / K2 (C2 procedure). 95 (K) and 96 (K2) of the 101 ordered functions land at the same address as in C/C2 (run grew 48 bytes; the pads absorbed it before rdxg).

| case, comparison | stock vs stock | layout-averaged |
|---|---|---|
| grade: #69 stock vs main stock (earlier run, lv/final) | **+13.5% (+12.2, +17.2)** | +0.3% |
| grade: K vs C | +0.9% (−0.3, +3.0) | −0.0% (−1.8, +1.8) |
| grade: K2 vs C2 | +0.9% (−1.9, +3.4) | +1.1% (−1.7, +3.4) |
| igradedown: #69 stock vs main stock | **+12.0% (+9.1, +13.0)** | +1.1% |
| igradedown: K vs C | +1.0% (+0.4, +2.2) | −1.0% (−2.3, +1.1) |
| igradedown: K2 vs C2 | +2.6% (+0.9, +5.4), under 3% | +0.5% (−0.8, +2.2) |

Stock-vs-stock now agrees with layout-averaged: the false swing is gone. Real costs remain:

| case | K vs C | K2 vs C2 |
|---|---|---|
| setdictnest | +4.4% [+1.9, +6.7] | +5.5% [+3.9, +7.4] |
| setdictverb | +4.5% | +4.6% |
| settableverb | +3.0% | +3.6% |
| setlistdictall | +3.0% | +2.0% |
| setdictall | +1.4% | +3.1% |

These match the earlier stage run (setdictnest +3.9%, setlistdictall +3.8%). Full K vs C (200 cases): geomean +0.06% (−0.23, +0.36); every other measurable case an amend case.

## Problems met

1. **The mod-16 rule:** with the order file alone, ordered functions drift by up to 64 bytes and gain 0-12 bytes of gap each — enough for stability in practice (B: 0 sensitive; 1 hot loop changed crossing state in 3 variants), not for padding, which needs exact positions. `-falign-functions=16` gives them for 3.7 KB.
2. **Hidden linker warnings:** build.sh's `2>/dev/null` hides "can't find … order_file entry". A real build change should not hide it, or should check placement with `nm` after linking.
3. **Profile names:** `sample` drops LTO suffixes and lumps stubs into the last function; resolve by address. An upstream order file should list plain names and be checked against `nm`; renamed statics (`.NNN`) get different numbers from build to build.
4. **Loops are not the only 4 KB hazard:** a page boundary between a hot function's entry and its hot loop cost 4-9% (C's psh). Any pinned layout also has its own luck on a few cases (B16's cntgrd, D's eachenum), now frozen rather than random, so pads must be checked against a profile-weighted rule and then timed, not trusted blind.
5. **The stage's sensitivity flag can be fooled:** run-to-run two-speed switching (window kernels' data placement), noisy runs (C-K), and no correction across cases (~2-4 chance flags per run). Comparing a build's spread across two runs separates real placement effects from these.
6. **Edits inside the pinned region shift it until the next pad:** in #69, 5-6 of 101 moved; regenerating pads absorbs most of it, so the pads belong to the build, not the source.

## What I'd propose upstream

An opt-in first, measured by the maintainer on his own machine:
1. **An order file:** `amber.order` (~100 hot kernels and evaluator functions, grouped by caller), passed with `-Wl,-order_file` (Apple ld) or `--symbol-ordering-file` (lld); build.sh reports how many entries applied and which were missing instead of discarding stderr. This alone removed every layout-sensitive case here, at +0.05% (−0.29, +0.39).
2. **`-falign-functions=16`** in the default flags: makes the ordered region exact rather than within 64 bytes; measured with the order file, neutral: −0.23% (−0.57, +0.12); prerequisite for item 3.
3. **A padding script** like `tools/pads.py`: pad functions so no hot loop and no hot entry span crosses 4 KB; run at release time or in CI, not by hand.

Pitch it as a measurement aid (PR timings stop moving when unrelated code moves), not a speedup: C2 measured −0.6% overall with no case >3% slower, but a different profile or pinned layout will have its own few ±5-10% cases. Items 1-2 alone make the PR-noise argument, with #69 as the example; item 3 needs the entry-span rule and a timing pass whenever the order changes.

## Files

Nothing in the amber checkout was changed. Under `$S/of/`: REPORT.md, builds.txt, spreads.md; `res/dirs-{A-B,A-C,A-B16,A-C2,C-D,C-K,C2-K2}.layouts.md` (stage output; logs in `res/*.log`); `hot.order`, `sel.json` (selection and coverage); `prof2.json` (re-attributed profile); `hotloops64.json`, `hotspans.json`; `tools/` (prof2, pick, order, pads, bininfo, summ, table, variants); `pads*/`; build trees `A B B16 C C2 D K0 K K2` (source, build.sh, amber.order, o/, amber); `runall.sh` (the run queue). The layout cache was deleted; the stage rebuilds variants in about a minute.
