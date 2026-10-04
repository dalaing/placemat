# placemat: issues and plans

Working notes for placemat, a layout-aware benchmarking tool. Each entry has a number (P001, …), a one-line title, a kind (tooling, design, platform, research), a status, and the evidence behind it.

placemat started as tooling inside a fork of the Amber K interpreter, where it was built to tell real speed changes from code- and data-placement effects (Apple M2, macOS, Apple clang 17, ld64). These entries were moved here from that fork's issue list on 2026-10-05; each notes the number it had there ("was Amber A266"), which is internal to that fork. Amber stays placemat's first user and its regression test: the validations below were all run on Amber.

Supporting reports from those runs are copied into [amber-validation/](amber-validation/) (written in the Amber work's scratch area; paths inside them refer to that area, which is not preserved).

| # | kind | title | status |
|---|---|---|---|
| [P001](#p001) | design | placemat itself: shape, licence, steps | in progress: repo set up; prior-art survey running |
| [P002](#p002) | tooling | the joint code-and-data layout stage, and what building it taught | built (in the Amber fork's `pbt/`); to extract |
| [P003](#p003) | tooling | pinning hot code: order file, alignment, pads, and its pitfalls | prototyped (Amber, Mach-O only); to extract and generalise |
| [P004](#p004) | design | data-layout adapters: a malloc interposer and a hook header for custom allocators | designed; the Amber hook exists as a fork-only patch |
| [P005](#p005) | tooling | make the stage faster | to do |
| [P006](#p006) | platform | Linux support: ELF analysis, linker options, the stage on shared CI runners, Valgrind | to do |
| [P007](#p007) | platform | macOS measurement tools beyond sampling | to explore |
| [P008](#p008) | platform | a Linux arm64 VM on the Mac, for Valgrind on the same CPU | to explore |
| [P009](#p009) | tooling | compare hot-code ordering strategies on the system under test | idea (user, 2026-10-05) |
| [P010](#p010) | validation | validate placemat on one or two small open-source projects that could benefit and can be coloured | idea (user, 2026-10-05) |

---

### P001
**placemat itself: shape, licence, steps** · design · in progress (user, 2026-10-05) · was Amber A277

Name and licence: `placemat`, MIT, © Dave Laing (repository dalaing/placemat). Only generic code comes here; anything that embeds a project's own source (such as the Amber allocator patch, which contains Amber's `src/m.c`) stays with that project under its licence and calls placemat. **Amber is AGPL-3.0:** nothing derived from Amber's source may enter placemat. That rules out the colouring patches and their generator (kept in the Amber fork's `pbt/patches/`) and the data-hook text in `pbt/layouts.py` (`HOOK_DECL`, `HOOK_FUNS`, `HOOK_EDITS`, which quote Amber's allocator); placemat's hook is written fresh against `placemat.h`. The rest of `pbt/` is our own code and can be MIT-licensed here.

Shape:
1. **Core, language-agnostic:** a runner (alternating base and branch, rounds, anchor variants re-timed per batch, adaptive stopping on interval width), the design (code pads and data colours from one low-discrepancy sequence), statistics (t intervals over pads, permutation tests attributing spread to code, data or run, Benjamini-Hochberg across cases), reports (Markdown, JSON). Benchmark protocol: a command printing name/value lines, plus a repetition-series convention; adapters for Google Benchmark JSON and for Amber/K `out[name;reps;f]` scripts.
2. **Code-layout adapters:** source padding (a generated, sized, unused function linked first; needs only a flags hook in the build); linker shuffling where the linker has it (lld/mold `--shuffle-sections` with a seed); pinning (P003); binary analysis for Mach-O and ELF (the loop-crossing scanner, "culprits": which loops cross a boundary only in the slow variants).
3. **Data-layout adapters:** P004.

Steps: (1) prior-art survey → `planning/PRIOR-ART.md` (running); (2) a short design document in `planning/`, with the hook header and the benchmark protocol, Amber as first user; (3) extract from the Amber fork's `pbt/` (`layouts.py`, `ldesign.py`, the stage in `evidence.py`, the order-file tools `pads.py`/`pick.py`/`order.py`/`prof2.py`, `loops4k.py`, `samefn.py`), keeping Amber's validations (P002) as the regression test: the same verdicts. Linux support (P006) can make its GitHub Actions run placemat's CI. Fold in P005's speed-ups where natural. Possibly the pinning half as a separate subcommand or tool.

### P002
**The joint code-and-data layout stage, and what building it taught** · tooling · built in the Amber fork (`pbt/evidence.py --design`, `pbt/ldesign.py`, `pbt/layouts.py`), validated 2026-10-04; to extract · was Amber A266 + A267

**What it does.** Re-times the cases a normal paired run flagged, across designed layout variants of both builds, and says whether a difference is a change, code placement, data placement or run-to-run noise.
- *Code axis:* an unused padding function linked first, sizes from a golden-ratio sequence covering the 4 KB period and the 64-byte phase (12 pads: largest gap mod 4096 620 B against 341 for even spacing; 12 of 16 phases mod 64, all 4 mod 16). The plastic-number R2 sequence was rejected: its first coordinate is close to 3/4, so the first dozen pads fell in four clusters.
- *Data axis:* an allocator hook (P004) offsetting large allocations by a designed colour; each pad is timed twice, at the stock data placement and at its colour, so code is tested at the placement users get and each pair isolates data. K variants cost K/2 builds per version.
- *Statistics:* per-variant median of paired ratios, geometric mean per pad, t interval over pads (honest at small K; a cluster bootstrap over six variants understated the variance). Code test: permutation of variant labels within a batch's rounds on trimmed means (medians' range saturates under permutation). Data test: sign-flip permutation on the data contrasts. Run covariate: the ASLR region base. Flags need Benjamini-Hochberg (q = 0.05) **and** p ≤ 0.01 **and** an effect over the threshold: BH alone turned one p ≈ 0.03 event into five flags in a six-case null run.
- *Adaptive stopping:* start at 8 variants, add 4 while the 95% interval half-width is over 1%, cap 24; stop on width only, never on significance. Each later batch re-times variant 0 as an anchor: drift between batches reached 5% and produced false code flags before anchors.

**Validation on Amber (2026-10-04).** (a) main against itself, 52 cases × 2: no changes, no code flags; the data flags were real. (b) a branch with a known placement slowdown: `grade` +8..+13% in the stock layout, about 0% averaged, attributed to code; its real costs (+3..+6%) still shown. (c) moving-window kernels: their two-speed run-to-run switching attributed to data, and a prototype's gain confirmed with intervals 3-4× tighter than a code-only stage. (d) a pinned build against main: code spread collapses (once pads aimed at main's rare bad windows were added), data spread does not. Cost: ~8 s per variant build; 12-50 s per case (2-4× the code-only stage); median 12 variants under adaptive stopping. Reports: [amber-validation/joint-stage-summary.md](amber-validation/joint-stage-summary.md).

**Terminology (from PRIOR-ART.md):** use Kalibera & Jones's levels (iteration < execution < compilation; a layout variant is the compilation level) and Mytkowicz et al.'s *measurement bias* and *setup randomisation*; call the M2 code effect a "4 KB boundary crossing" (not a page crossing: macOS arm64 pages are 16 KB, and the mechanism is unexplained) and the buffer effect "L1 set conflicts" ("4K aliasing" is Intel's store-to-load false dependence).

**Lessons to keep:**
1. *Colours must be whole cache lines.* Amber assumes 32-byte-aligned payloads (`__builtin_assume_aligned(x,32)`; its allocator guarantees 64). 16-byte colour steps put half the variants at 16 mod 32, which made two kernels 25-35% slower: a placement the allocator can never produce, i.e. a false data effect. A general tool must keep each project's alignment guarantee; default to 64-byte steps and let a project declare more.
2. *Evenly spaced pads alias at fine scales* (six pads spread over 4 KB covered only three positions mod 64), and *few variants miss rare bad windows*: a 64-byte loop crosses 4 KB in ~1.5% of layouts; adaptive stopping on the change interval can miss windows of ≤2%. Targeted pads (`--layout-pads`, chosen with "culprits") find them; choosing them is still manual.
3. *Uncorrected sensitivity flags* give 2-4 chance flags per 200-case run; also flagged: two-speed run-to-run switching (data placement) and noisy runs. Comparing a build's spread across two runs separates real placement from these.
4. *Benchmark parsing:* a case not at the start of its line was silently skipped (three cases missed in two long runs); warn, or parse properly.
5. *A fast case can defeat the repetition-series fit:* a negative slope made a ratio ≤ 0 and `log()` failed, losing a whole run; fall back to the full-repetition time, and never let the second stage lose the first stage's results.
6. *The data hook moves pinned code:* it grows the allocator, so in an order-file build it shifts the pinned region; time pinned builds with `--no-data` (or colour them natively, P004).
7. *main against main is not a perfect null* when base and branch run from different worktree paths.

### P003
**Pinning hot code: order file, alignment, pads, and its pitfalls** · tooling · prototyped on Amber (Mach-O/arm64 only), 2026-10-04; to extract and generalise · was the tooling part of Amber A268

**Procedure.** (1) Profile the benchmark set and re-attribute samples by address: macOS `sample` strips LTO suffixes (`_o8.1005` → `o8`) and lumps stubs past `__text` into the last function. (2) Pick hot functions until each benchmark set reaches ~95% of in-binary samples (Amber: 101 functions, 99.3% / 95.2%, 211 KB of 618 KB). (3) Group them C3-style (each behind its heaviest caller; clusters capped at 16 KB; skip a merge when the caller's cluster is under 1/8 as dense; sort clusters by density). (4) Link with `-Wl,-order_file` (ld64) and `-falign-functions=16`. (5) Insert pad functions, listed in the order file, sized by an exact search over the start address mod 4096 so that no loop ≤256 B in a listed function and no hot entry span (entry to the last hot sampled offset under 1 KB) crosses 4 KB. (6) Verify after linking (`nm`, the loop scanner), then time.

**Results on Amber.** Layout-sensitive cases 9-21 per run → 0 with the order file alone, at +0.05% (−0.29, +0.39); with alignment and the entry-span pads (build "C2"): 0 sensitive, −0.59% (−0.92, −0.26), nothing over 3% slower. A branch's false +13.5% became +0.9% when both builds were pinned the same way, while its real costs still showed. Report: [amber-validation/order-file-report.md](amber-validation/order-file-report.md).

**Pitfalls found:**
1. *ld64 keeps each function's address mod 16 from the LTO object* when it reorders, so the order file alone pins only to within ~64 bytes; `-falign-functions=16` makes it exact (needed for pads).
2. *Loops are not the only 4 KB hazard:* a page boundary between a hot function's entry and its hot loop cost 4-9%; alignment without pads left two loops of one function crossing in every layout (+10%). Pinning freezes a layout's luck; the pad rule and a timing pass are what make it good.
3. *Linker warnings get hidden:* the project's build sent stderr to /dev/null, hiding "can't find … order_file entry"; always verify placement with `nm`.
4. *Renamed statics* (`.NNN` suffixes) change numbers from build to build; list plain names and verify.
5. *Edits inside the pinned region shift it until the next pad:* regenerate pads per build (the pads belong to the build, not the source).
6. *Flags that seem to align loops may not exist:* Apple clang 17's LLVM has no `-align-loops`, ld64 silently ignores unknown `-Wl,-mllvm` options, and `-align-all-nofallthru-blocks=6` / `-align-all-blocks=4` cost 35-40% more text and still left most crossings: always check that the binary changed.

To generalise: ELF and lld `--symbol-ordering-file` / GNU ld section ordering (P006); profile import from perf as well as `sample`/xctrace; boundaries per architecture (4 KB on M2; 32/64-byte windows on x86, to be measured).

### P004
**Data-layout adapters: a malloc interposer and a hook header for custom allocators** · design · designed 2026-10-05; the Amber hook exists as a fork-only patch · from Amber A277 (point 3), with lessons from Amber A269 and A271

**Why two adapters.** Projects on the system allocator can be coloured from outside: a `malloc` interposer (`DYLD_INSERT_LIBRARIES` / `LD_PRELOAD`) that offsets large allocations by a colour. Custom allocators cannot: Amber's buddy allocator takes memory with `mmap` and aligns each block to its own size, so every payload ≥64 KB lands at the same offset mod 16 KB in every run (structural L1 set aliasing); shifting the `mmap` region moves every block equally and changes nothing. They need an in-allocator hook.

**Colours and steps** (user, 2026-10-05: a general tool needs both). A *colour* is one constant offset for every coloured allocation (moves data relative to page boundaries, uncoloured allocations, statics, the stack); a *step* offsets the k-th allocation by s·(k+1) (moves coloured buffers relative to each other: their set conflicts). Offset of the k-th block: (c + s·(k+1)) mod span. Amber needed steps only (its buddy allocator gives every large block the same offset, so a colour moves them all equally); other allocators and page-boundary-sensitive workloads need colours. DESIGN §5.3.

**Hook header (sketch).** A single header a project includes under a compile flag (e.g. `-DPLACEMAT_HOOK`), identical binary without it:
- `placemat_colour(size, spare_room)` → offset to apply to the next large block, from the variant's colour and step (environment variables `PLACEMAT_COLOUR`, `PLACEMAT_STEP`, `PLACEMAT_UNIT`, `PLACEMAT_SPAN`, `PLACEMAT_MIN`); 0 in stock runs;
- `placemat_log_alloc(ptr, size)` → address logging for diagnostics (which offsets alias; is placement fixed per binary or per run?).
Valgrind client requests (`VALGRIND_MALLOCLIKE_BLOCK` / `VALGRIND_FREELIKE_BLOCK`) under the same flag would let DHAT/Massif/Memcheck see custom-allocator blocks (P006).

**Lessons from Amber's colouring allocator** (a real fix there: window kernels 2-2.8× and a grouping case 2.5× faster, nothing slower, run-to-run switching gone; [amber-validation/colouring-report.md](amber-validation/colouring-report.md)):
1. Colours in whole 64-byte lines (P002 lesson 1).
2. Take the colour from the block's existing spare room so block classes and virtual memory don't change; move the header back on free so free lists and splitting never see coloured blocks; keep the per-allocation fast path for small blocks untouched (an extra check on every allocation cost 3-10% on allocation-bound cases until it moved out of line).
3. Policies: a counter (deterministic within a run; relative offsets depend only on allocation distance; sweeps colours in a timing loop), a hash of block index (reintroduces an ASLR lottery across classes), random (heavier tail). Equal speed on Amber; counter chosen. For measurement variants, placemat sets the colour; for a project's own fix, the project chooses.
4. A growth rule: when colouring reduces a block's capacity, a vector growing one item at a time must still move up a class at the same point, or it re-copies within the class.
5. Check the project's alignment guarantee with its own tooling (Amber: a `-DAMBER_ALIGNCHECK` build reporting any unaligned payload).

### P005
**Make the stage faster** · tooling · to do (user, 2026-10-05: "they take hours") · was Amber A276

Builds are cheap (~8 s per variant: one file recompiled and a relink); timing dominates (~200 cases × up to 24 variants × rounds; 12-50 s per case; 1-3 h per comparison). Shortcuts, by payoff:
1. **Time only affected cases** (needs pinned builds, P003): unchanged hot functions don't move, so with a "which functions' machine code changed" tool (Amber's `samefn.py`) and the profile's case → function map, skip or spot-check cases whose hot functions are identical and unmoved. Typical change: 200 → 10-30 cases. Also an argument for pinning in any project: it makes change benchmarking cheap, not just stable.
2. **Drop axes that cannot matter:** colouring built in (P004) removes the data axis (halves variants); changes that leave the binary unchanged (scripts in the project's own language, docs, tests) skip the code axis (detect from the diff; caveat: script changes can alter the allocation sequence, which colouring largely neutralises).
3. **Multi-arm runs:** base, P1, P2, P3 in one rotation, timing the base once per round (n+1 instead of 2n executions).
4. **Adaptive variants and rounds:** on pinned + coloured builds start at 4 variants; stop rounds early when the interval is tight; calibrate repetitions to ~20-50 ms per sample instead of fixed counts.
5. **Instruction-count pre-filter** (P007's counters, or Cachegrind on Linux): identical instructions plus unmoved code → skip.
6. **Representative subsets while iterating** (~40 cases clustered by profile), always finishing with the full set: no verdicts from a subset.
7. **Repetition counts per level from Kalibera & Jones (ISMM 2013):** their formula chooses how many repetitions to run at each lower level (iteration, execution) from each level's variance and cost; placemat's layout variant is their compilation level (PRIOR-ART.md).
Not helpful: timing cases in parallel on different cores (shared L2 and memory bandwidth). Expected: a typical change check from hours to ~10-20 min; research comparisons about halved.

### P006
**Linux support** · platform · to do · from Amber A272 (points 1, 2, 5) and A274

1. **The stage on Linux:** does source padding survive GCC/clang LTO and the GNU/lld links; ELF binary analysis (objdump/nm) for the loop scanner, culprits and pads; lld `--symbol-ordering-file` or GNU ld section ordering (`-ffunction-sections`). lld has a ready-made code axis: `--randomize-section-padding` (lld 20, December 2024; its release note says it is "to control measurement bias in A/B experiments") and `--shuffle-sections` (2020): use them on Linux in place of source padding where available, and credit them (PRIOR-ART.md).
2. **Which code boundaries matter on x86** (32/64-byte fetch windows, 4 KB) and on Linux arm64, so the pad rule is per-architecture.
3. **Shared CI runners** (e.g. GitHub Actions): do the statistics cope with their noise; can they become placemat's CI.
4. **Valgrind:** Cachegrind's exact instruction counts as a noise-free metric beside timings (blind to placement and cache effects, so a complement, not a replacement); its cache simulation (`--cache-sim=yes`; simplified, no prefetcher: relative comparisons only); Callgrind for where instructions go; DHAT for bytes allocated/read/written per allocation site; Massif for heap size (colouring's overhead). Custom allocators need P004's client requests to be visible.

### P007
**macOS measurement tools beyond sampling** · platform · to explore · was Amber A273

No Valgrind on macOS arm64 (and, as far as we know, no DynamoRIO or Pin). Options:
- **kperf/kpc private framework** (counting mode, the `perf stat` equivalent): cycles, instructions retired, branch misses, L1D misses; needs root. Instructions retired is nearly deterministic: a candidate pre-filter (P005) and noise-free metric beside the stage.
- **Instruments CPU Counters, GUI modes:** sampling L1D misses and mispredictions to instructions (the CLI gives only the default top-down "CPU Bottlenecks" mode).
- **llvm-mca** (Homebrew LLVM): static cycles-per-iteration and the limiting resource for a loop, with Apple core models; no memory model.
- **lldb stepping:** exact instruction counts per operation; too slow for anything long.
- **Processor Trace** (Instruments): believed to need M4-generation hardware.
- **Heap tools** (`leaks`, `heap`, `vmmap`, `malloc_history`, Allocations) track malloc only; custom allocators are one mapped region to them, so P004's `placemat_log_alloc` is the substitute.

### P008
**A Linux arm64 VM on the Mac** · platform · to explore · from Amber A274 (point 1)

Apple's Virtualization framework (via UTM, Lima, OrbStack or Docker) runs Linux arm64 lightly: Valgrind on the same CPU (P006 point 4), and a cheap first step toward P006's Linux questions (arm64 rather than x86, but the same linkers). Caveats: a different toolchain and ABI (e.g. Linux's initial-exec thread-local storage is nearly free where macOS's is not); timing inside a VM is less trustworthy than bare metal, but instruction counts and allocation traffic are fine.

### P009
**Compare hot-code ordering strategies on the system under test** · tooling · idea (user, 2026-10-05)

Different orderings of the hot set optimise different things; placemat could build each and present how they compare on the project being benchmarked. Candidates (most are options of BOLT's function reordering; names to be checked against BOLT's documentation): by hotness (execution count), by density (samples per byte), Pettis-Hansen (greedy merging along the heaviest call edges, 1990), C3/hfsort (each function behind its heaviest caller, Ottoni & Maher 2017), hfsort+ and CDSort (refinements), balanced partitioning (lld; aimed at startup and compression), per-benchmark clusters, and **random order as a control**. The pad rule (P003) applies after any of them.

**The trap:** each pinned layout freezes its own luck (on Amber, each pinned build had a few cases ±5-10% off from where that layout happened to put them), so one build per strategy compares lucky draws. Measure each strategy averaged over layout: shift the whole ordered region through designed offsets (a pad in front of it, over the 4 KB period and the 64-byte phase) and, optionally, several equally good pad solutions; random order measured the same way is the baseline.

**Report per strategy:** static (text and pad bytes; profile-weighted count of 4 KB and 16 KB pages spanned by hot code; crossings before padding) and measured (layout-averaged speed against stock; spread and layout-sensitive cases; per-case winners and losers).

**Expectation:** small differences on Amber/M2 (instruction-fetch stalls 6% of the maintainer's set; packing the hot code changed none of the front-end-heavy cases measurably; large L1 instruction cache), possibly larger on x86 (32 KB L1I), so the comparison is most interesting where P006 lands.

### P010
**Validate placemat on one or two small open-source projects** · validation · idea (user, 2026-10-05)

Amber proved the methods, but it shaped them too. A second and third project, chosen to (a) plausibly benefit from a placemat analysis and (b) be colourable, would test whether the design is general. Colourable without patching the project is best: a pluggable allocator API lets the harness install placemat's colouring allocator from outside; a project on the system allocator can use the interposer (P004).

Criteria: C or C++, small and quick to build (so variants are cheap), a built-in or easily scripted benchmark suite, large buffers or a hot interpreter loop, and either a pluggable allocator or plain `malloc`. Licence matters only for adapters: anything derived from a project's source stays with that project (as for Amber, AGPL-3.0).

Candidates (allocator APIs to be confirmed before relying on them):
- **zstd**: large window and table buffers used together (a natural data-axis case); a built-in benchmark (`zstd -b`); custom allocators through `ZSTD_customMem`. BSD/GPLv2.
- **SQLite**: a single amalgamation file; `speedtest1`; a pluggable allocator (`sqlite3_config(SQLITE_CONFIG_MALLOC, ...)`); and the project tracks performance with Cachegrind instruction counts, so placemat's layout view can be set beside an established method. Public domain.
- **ngn/k** (the K interpreter Amber derives from; checked out next to Amber): likely the same buddy-allocator structure and so the same structural L1 set conflicts; small and fast to build; a direct comparison with the Amber results. Needs a hook (no allocator API), kept with ngn/k (AGPL-3.0).
- **Lua or QuickJS**: small interpreters with allocator callbacks (`lua_newstate(lua_Alloc)`; `JS_NewRuntime2` with `JSMallocFunctions`): mainly a code-axis and ordering-strategy (P009) test, most interesting on x86 (P006), where interpreter loops are more front-end bound.

Suggested order: zstd (data axis through an allocator API, no patching), then SQLite (both axes, an established performance culture to compare with), with ngn/k as the closest relative to Amber. Each gets the lifecycle in DESIGN.md §3: a survey first, and only then fixes.
