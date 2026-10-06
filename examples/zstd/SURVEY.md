# zstd survey (P010, DESIGN §3.1)

Question: does zstd have a placement problem on this machine (Apple M2, macOS 26.6, Apple clang 17, ld64), in code placement, data placement, or from run to run?

## Verdict

**No placement problem worth fixing on this machine.** Across 16 cases (one-shot and streaming compression at levels −5 to 19, one-shot and streaming decompression; text, binary records and random data) and four runs of the joint design (code pads, colours and hashed steps; three of them noise-gated), placemat flagged no case for code, colour, step or run sensitivity in the gated runs, and one case for code in the noisy, ungated null run (in one arm of two identical binaries), which `culprits` could not tie to any loop and which the gated null rerun did not repeat (no flags in any of its 16 cases). Real changes came through cleanly, with intervals of about ±1%. The quietest run (copy8, below) shows what is there: statistically clear code spreads of 1-2.5% in the compression cases, under the 3% threshold, and no data effect. Two cases are noisy rather than layout-sensitive: `d3-rand` (an 8 MB copy, 26-33% spread in every run, never significant) and `c3-rand` (5-15%). By DESIGN §3.1's verdicts this is "no meaningful spread": placemat's cheap mode is enough for zstd here, and neither pinning nor colouring is called for. The qualification: on this shared machine the run-to-run scatter (p90/p10 of 1.2-1.4 per execution in the gated survey run) limited the survey runs' spread estimates to about 10%; the copy8 run, on a quieter machine, brought that to 1-2%.

## What was run

Harness and build: [README.md](README.md). Arms are git revisions of a zstd clone; builds are static, `-O3`, no LTO, single-threaded. Data method: placemat's colouring allocator through `ZSTD_customMem` (`stock_placement = false`, `covariate = "khash"`); both data kinds (colours over 16 KB, hashed steps over 16 KB, 64-byte units); default adaptive design (4 pads, +2 per batch while the change interval's half-width exceeds 1%, cap 12; 7 rounds per batch).

| run | arms | gate | result files (in `placemat-work/zstd/runs/`) |
|---|---|---|---|
| smoke | v1.5.6 → v1.5.7, 3 cases, 2 pads, 2 rounds | none | `smoke.md` |
| null | v1.5.7 → v1.5.7 (identical binaries), 16 cases | **not gated** (started before the noise probe existed; load 2.3-10.1 during timing, noise probe 0.9-11.6%) | `null.md`, `null.raw.json` |
| real | v1.5.6 → v1.5.7, 16 cases | gated: `max_noise = 0.05` (probe 2.5-4.8% at every batch) | `v156-v157.md`, `v156-v157.raw.json` |
| copy8 | `7eefc221` → `1e9d2006`, 16 cases | gated (last batch went ahead after 2,700 s at 13%) | `copy8.md`, `copy8.raw.json` |
| decseq | `3c3b8274` → `a28e8182`, 6 decompression cases | gated (3 of 5 batches went ahead after 2,700 s at 5.8-14.5%) | `decseq.md`, `decseq.raw.json` |
| null, gated rerun | v1.5.7 → v1.5.7, 16 cases | gated | `null-gated.md`: all 16 cases noise, no flags of any kind, mean +0.1% (−0.3 to +0.4); `d3-text` unflagged (−0.1%, −1.0 to +0.9), so the ungated null run's flag was the load. 4-12 pads; probe 2-14% (gate waited its limit twice); 2.0 h wall |
| Linux smoke | v1.5.6 → v1.5.7, 3 cases, 2 pads, 2 rounds, Lima VM (4 vCPU, GCC 15) | none (VM) | VM `/tmp/zstd-runs/smoke-linux.md` (not kept) |

Costs: each full run did 657 executions (about 1.2-1.6 s each) and about 10 minutes of actual timing, plus 2-4 minutes of builds (each pad build: one pad object, the harness and a relink; libzstd compiled once per arm). Wall time was 7.3 h (null) and 8.7 h (real), almost all of it queued for the machine lock behind five other placemat runs (the reports' "waiting for the machine lock" is 10,940 s and 24,404 s; their "Builds" figures, 14,794 s and 6,381 s, also include waiting for the shared lock). Coloured blocks: about 15,300 per run, none wrapped.

## Results

### null (v1.5.7 against itself; noisy machine, not gated)

0 change, 15 noise, 1 placement: `d3-text`, code-flagged in the base arm (spread 19.7%, p 0.0011) but not in the other arm (13.4%, p 0.63), although both arms ran the **same binaries**. Its slow pads (1872, 2268, 696) are mostly the batch timed at load 10; `placemat culprits` finds no loop that crosses 4 KB only in them ("0 candidate loops"). A false positive from machine drift between batches, not placement. All 16 change intervals but one contain 0 (`d3-rand`, +2.1%, +0.3% to +4.0%, 12 pads: its run-to-run scatter is the largest of any case). `placemat twospeed`: p90/p10 1.5-2.4 for every case, against 1-2% between repeats in a quiet calibration run; `c1-text` ran at about 78 ms per sample against 54 ms quiet. The raw data are kept but this run should not be used for spreads.

### real (v1.5.6 → v1.5.7; gated)

Mean over cases −1.1% (−2.1% to −0.1%). Flags: none in any family.

| case | change (95% over pads) | stock builds | verdict |
|---|---|---|---|
| `c3-rand` | **−23.5%** (−24.2% to −22.8%), every setting −22.8% to −24.1% | −22.8% | change |
| `c3-text` | **+3.3%** (+2.4% to +4.1%), every setting +2.8% to +3.9% | +3.6% | change |
| `c3-rec` | +1.8% (+0.9% to +2.7%) | −2.0% | noise (under the 3% threshold) |
| `c12-text`, `c19-text` | +1.0%, +0.8% (intervals exclude 0) | −0.8%, +0.4% | noise (under threshold) |
| the other 11 | −0.3% to +0.9%, intervals contain 0 | | noise |

So v1.5.7 takes about a quarter less time on incompressible data and level-3 text about 3% slower on the M2 (consistent across all three data settings and with the stock builds' own +3.6%, so not placement); the level-3 slowdown is plausibly the price of v1.5.7's level 3-4 ratio work (block pre-splitting, #4136, and `63267751`, whose message measured "between 0 and −1%" on its author's machine). Decompression is unchanged within ±1%.

**Spreads.** Code spread per arm (range of the stock-setting medians over pads, anchors applied): 10-17% for v1.5.6 in every case and 3-15% for v1.5.7, `d3-rand` 26-33% in both; none significant after Benjamini-Hochberg (smallest per-arm p: `ds3-text` 0.0054 for v1.5.6, `c1-rec` 0.02). Colour and step: largest per-pad effects 5-25%, typical effects within ±5%, all p > 0.05. Run covariate: unavailable (see design findings). `twospeed`: p90/p10 1.17-1.39, 7-14% of rounds more than 15% slow, max/min within a variant up to 1.5-3.75. With every execution scattered like that, a range over 4-12 pad medians is 10-15% even without any placement effect, which is what the spreads look like: the floor of this survey, not a property of zstd.

### Where the noise comes from (not placement)

The scatter is two-speed (a cluster of rounds about 1.3-1.6× slow), uniform across cases and both arms, present with the noise probe passing, and it was 1-2% in a calibration run on a quiet machine. The most likely mechanism is the M2's scheduler moving a 1.5-second single-threaded process from a performance core to an efficiency core when other work (outside the lock: the load stayed at 2.2-4 during exclusive holds) takes the performance cores. Nothing placemat records today identifies core type, and macOS offers no way to pin a process to a performance core. `d3-rand` (a 8 MB memory copy 100 times) is the most affected, as a bandwidth-bound case would be.

### Data placement under the stock allocator (address log)

Under macOS's allocator every large buffer zstd and the harness use (8 MB inputs and outputs, 0.1-1.3 MB workspaces, the DCtx) starts at an address that is a multiple of at least 16 KB (logged: `0xd16000000`, `0xd16c00000`, `0x8eb400000`, ...; placemat_alloc's payload is 64 bytes in). So in the stock build all large buffers share one offset modulo the L1 set stride, exactly the structural aliasing Amber's buddy allocator had. On zstd it costs nothing measurable at this floor: the hashed steps, which give every buffer its own offset, changed no case by more than noise. The likely reason is that zstd's hot tables live inside one workspace allocation, laid out by zstd itself, so the allocator never decides their relative placement (README).

### Linux arm64 (VM)

The adapter builds and runs unchanged under GCC 15 and GNU ld (builds 15 s; `main` lands before the pad in `.text.startup`, libzstd after it). The VM smoke run's +60%/+120% "colour" effect on `d3-text` (2 rounds) did not reproduce in a direct probe (five colours and the stock build, 5 repeats each: all 67-74 ms). VM timings are less trustworthy than bare metal, and the VM shares the host's cores with the timing runs.

## What this says about placemat's generality

- **The adapter shape carried over unchanged.** The build protocol (pad object first, verify with `nm`), the benchmark protocol (`name value iterations us`, `PLACEMAT_CASES`) and the allocator route needed about 360 lines of C and 120 of Python, and no change to zstd or to placemat's core. A project with an allocator API is colourable from outside, as P010 hoped.
- **Real changes are robust to noise; spreads are not.** The paired design held the change intervals to about ±1% even in the null run at load 10. The sensitivity tests, which compare medians across pads and batches, are only as good as the machine's run-to-run stability; on a shared M2 that floor is about 10%. The noise probe helps but did not catch the remaining two-speed scatter here.
- **zstd's real data structure limits what an allocator-level data axis can vary** (one workspace for all compressor tables), so "window against tables" (DESIGN §5.3's zstd example) is only partly reachable from outside; a hook inside `ZSTD_cwksp` would be needed to vary table offsets within the workspace.
- **k from addresses is not run-stable on macOS**, so the khash run covariate never repeats and hashed steps are re-drawn every execution (below).

## Design findings and gaps hit

1. **A crash lost a whole batch (fixed by another session during this work; the loss-of-results half is still open).** The first null run died at the start of batch 1: `runner.py` `slot_env()` passed the anchor slot `a1` to `Design.parse()` → `ValueError: not enough values to unpack (expected 2, got 1)` (fixed at 11:21 on 2026-10-05, line 187). Because raw timings are written only at the end of `Runner.run()`, the 18 minutes of batch 0 were lost (ISSUES P002 lesson 5: "never let the second stage lose the first stage's results"). A run that starts seconds before such a fix uses the old module (mine did, and had to be restarted).
2. **khash covariate never repeats on macOS.** macOS places medium (≲1.3 MB) and large blocks in separate regions at an ASLR-dependent distance, so k for the medium-region blocks (about −273,000 granules from the base) differs in every process. Consequences: the khash run covariate is unavailable (now reported as such; at the time of the first runs the report printed "most common in 2-8% of runs" and ran the run test anyway), and under hashed steps each execution of a stepped variant gets new relative offsets, so a "step variant" is an average over random placements, not one designed placement (DESIGN §5.3 anticipates this for allocators that randomise mappings). A per-size-class or per-region base would make k stable but brings back the problems §5.3 lists.
3. **Run-to-run two-speed scatter on Apple Silicon is probably core-type scheduling, not placement**, and passes the noise probe. placemat could record the core type per execution (e.g. a per-process `proc_pid_rusage` P/E-core cycle split, where available) and use it as a run covariate.
4. **Killing placemat orphans its lock process.** `lock.hold()` starts the lock command with `Popen` and nothing kills it if placemat dies by a signal; my killed run left a `quiet.py --exclusive` waiter (no parent) that would have held the machine through its load wait. It had to be found and killed by PID.
5. **The build cache was not keyed by the adapter's own files** (fixed by another session: the key now hashes the config directory's `.py`/`.c` files and placemat's data sources) and is still not keyed by platform: a work directory shared by macOS and the Linux VM would hand one platform's binaries to the other.
6. **"Builds" time in the report includes waiting for the shared lock** (14,794 s for builds that took a few minutes).
7. **`placemat culprits` was missing** (`ModuleNotFoundError: placemat.culprits`) when this work started; another session added it.
8. **ELF ordering:** GCC puts `main` in `.text.startup` and cold splits in `.text.unlikely`, which GNU ld places before `.text`, so a pad linked first does not move them (P006). The build checks that the pad precedes all of libzstd instead.
9. **The noise gate goes ahead when the machine never quietens.** On this shared machine five of ten gated batches across copy8 and decseq waited the full 2,700 s and then timed anyway at probe spreads of 5.8-14.5%. That is the right default (a survey must finish), but the report should then mark those batches' spreads as unreliable rather than only list the probe values; the change estimates were unaffected.
10. **The Kalibera-Jones dimensioning table** reports S1 of 0.04-0.14 (a within-pad sd of 20-37% in log time) and S2 = 0 for most cases, so r* = ∞: on a noisy machine it advises nothing useful; it should probably say so rather than print infinities.

## Recommendations for zstd (DESIGN §3.1)

- Verdict: no fix needed (no pinning, no colouring) at this sensitivity; use placemat's cheap mode for zstd changes on this machine.
- To lower the floor: rerun the null and the real comparison on a quiet machine (or a dedicated one), where the calibration scatter was 1-2%; that would bring the spread estimates down to the 2-4% where Amber's code effects live.
- If a project wants the data question answered properly, a hook in `ZSTD_cwksp` (offsets between tables within the workspace) is the next step; the allocator route has shown that the buffers' relative placement outside the workspace does not matter here.

## Performance claims in zstd's history that could be placement effects

Searched: `git log` of the `dev` branch since mid-2023 (commit messages and the PR titles in merge commits) and the CHANGELOG, for performance commits that cite numbers. A claim is suspicious when the margin is small (under ~5%) or measured on one machine and one build; when the change moves a lot of code (an inline helper, a rewritten hot function) and the claim is about speed; or when the path is buffer-heavy (windows, tables) where data placement can matter.

| commit (PR) | claim, benchmark, machine | why it might be placement | cases that exercise it |
|---|---|---|---|
| `1e9d2006` (#4414, 2025-06-20), "AArch64: Use better block copy8"; parent `7eefc221` | decompression +0.03% to +1.8% on silesia.tar levels 1-7, one Neoverse V2 machine, one build per compiler (Clang 19/20, GCC 14/15); gains differ fourfold between compilers | a one-line change to an inline helper (`ZSTD_copy8` in `zstd_internal.h`, NEON load/store → `memcpy` on AArch64) recompiles every function that copies literals or matches (both decoders, and compression's literal copies), so the whole library's code moves; the claimed margins are well inside the code spreads seen on Amber (and below placemat's 3% threshold) | `d1-text`, `d3-text`, `d12-text`, `d3-rec`, `ds3-text`; compression cases through literal copies |
| `bd38fc2c` (#4413, 2025-06-20), "AArch64: Enhance struct access in Huffman decode 2X"; parent `7eefc221` | decompression +0.1% to +2.5%, same machine and method | small margins from one build per compiler; rewrites the 4-stream Huffman decoder's hot loop (code moves within `huf_decompress.c` and everything after it) | `d1-text`, `d3-text`, `d12-text` (Huffman-coded literals); `d3-rec` |
| `a28e8182` (#4418, 2025-06-24), "AArch64: Improve ZSTD_decodeSequence performance"; parent `3c3b8274` | decompression +11% to +24% (Clang 19/20), +2% to +11% (GCC), about 0% for "Clang-*" (a Clang 21 build with an LLVM fix), one Neoverse V2 machine; partly reverted later for a data-corruption case (`33618c89`) | the margin is large, so most likely real where it was measured; but it is compiler-specific (it targets an LLVM alias-analysis weakness) and grows `ZSTD_decodeSequence` (+119 lines, inlined into the sequence-decoding loops), so on Apple clang 17 (LLVM 19-based) with an M2 the gain is an open question, and a large rewrite of the hottest decoder loop is where a 4 KB crossing could add or hide a few percent | all decompression cases (`d*`, `ds3-text`) |
| `e8fce389` + #4165 (2024-09/10), "avoid unpredictable branches" / "Improve compression speed on small blocks" | fleetbench `BM_ZSTD_COMPRESS_Fleet` ~8-15% faster (machine not named; x86 by the context), and the follow-up notes "performance is not good on clang (yet)", choosing cmov or branch by window log | real algorithmic change, but in the hash-table probe of the fast and dfast match finders: buffer-heavy (hash table against the window), so data placement (L1 set conflicts between table and window) can add to or hide it; part of v1.5.6 → v1.5.7, so the real comparison above covers it | `cneg5-text`, `c1-text`, `c1-rec`, `c3-text`, `c3-rec`, `cs3-text` |

Also seen but not shortlisted: `63267751` (levels 3-4 ratio; "speed cost below noise level, between 0 and -1%": a no-change claim, which placemat could check but which is not suspicious), `b6a4d5a8` and `8e440046` (`ZSTD_get1BlockSummary`, +10% and ~2-3×: large, in a function only the block splitter uses), `b89e8c2a` (CPUID probe per context: x86 only).

### Timing the shortlist

**`1e9d2006` (copy8) against its parent `7eefc221`, all 16 cases, default design, noise-gated** (`copy8.md`; the gate held at every batch but the last, which went ahead after 2,700 s at a probe spread of 13%): **no change, no placement: noise in all 16 cases.** Mean over cases +0.1% (−0.4% to +0.5%). The decompression cases the commit targets: `d1-text` +0.6% (+0.3% to +0.9%), `ds3-text` +0.3% (+0.0% to +0.7%), `d3-text` +0.4%, `d12-text` +0.2%, `d3-rec` +0.3%, all under the 3% threshold, and the two whose intervals exclude 0 are slightly *slower*, not faster. The commit does change code on Apple clang 17 (`placemat binary samefn`, data references masked: 8 of 500 functions differ; `ZSTD_decompressSequencesLong` +1,052 B, `ZSTD_decompressSequencesSplitLitBuffer` +596 B, `ZSTD_decompressSequences` +212 B, `ZSTD_safecopy` +232 B, so everything after them moves by about 2.3 KB). So on an M2 with Apple clang the claimed +0.3% to +1.8% does not appear; whether it was real on Neoverse V2 cannot be told from here, but a +1% claim from one build per compiler is within what one layout can show: in this very run, `placemat`'s "if placement were ignored" table shows that one layout of the same pair could have reported `d3-rand` anywhere from −5.9% to +15.7% and `c3-rand` from −2.2% to +6.5%, against layout-averaged results of +0.1% and −0.1%.

This run was quieter than the survey runs (noise probe 1.7-4.9% for four of five batches), and it shows what the survey runs could not: small, statistically clear code-placement spreads in the compression cases, below the 3% threshold. Per-arm code spreads 0.8-2.5% with permutation p down to 0.0013 (`cneg5-text` 1.2%, p 0.0013; `c1-rec` 2.4% and 2.1%, p 0.0014 and 0.004; `c1-text` 1.9% and 1.7%, p 0.017 and 0.011), and `c3-rand` 5-9%, `d3-rand` 26-31% (neither significant). zstd's code placement effects on the M2 are real but around 1-2.5%: under placemat's flag threshold, and far smaller than Amber's.

**`a28e8182` (decodeSequence) against its parent `3c3b8274`, the six decompression cases, default design, noise-gated** (`decseq.md`; three of five batches went ahead after 2,700 s with probe spreads of 5.8-14.5%, and placemat marked two disturbed rounds): **a real change, 5 of 6 cases.** `d1-text` −10.8% (−11.6% to −10.1%), `d3-text` −11.8% (−12.5% to −11.1%), `d12-text` −12.5% (−13.1% to −12.0%), `d3-rec` −14.9% (−15.6% to −14.2%), `ds3-text` −10.6% (−11.3% to −9.8%); the same at every data setting (stock, coloured, stepped within a point of each other), matching the stock builds' own −9.9% to −14.5%, no code, colour, step or run flags (code spreads 1.5-4.2%). `d3-rand` is unchanged (−0.4%): its frames are raw blocks, which have no sequences to decode. So the claimed +11% to +24% (Clang 19/20, Neoverse V2) carries over to Apple clang 17 on an M2 as a 12-18% speed-up (time −10.6% to −14.9%), and it is not a placement effect. The later partial revert (`33618c89`) was not timed.

Summary of the shortlist: the large claim (`a28e8182`) is real on this machine; the small one (`1e9d2006`, +0.3% to +1.8%) does not appear (within ±0.6%, if anything slightly slower), and its size is within what a single layout shows for some cases here. `bd38fc2c` (Huffman 2X, +0.1% to +2.5%) was not timed: below placemat's 3% threshold on this machine, a timing could only say "noise" for it, as for copy8.
