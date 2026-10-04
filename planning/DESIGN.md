# placemat: design

Draft, 2026-10-05. Companion documents: [ISSUES.md](ISSUES.md) (work items P001-P008), [PRIOR-ART.md](PRIOR-ART.md) (what exists, what to credit, what not to claim), [amber-validation/](amber-validation/) (the runs on Amber that this design rests on).

## 1. What placemat is for

placemat answers one question about a change to a compiled program: **is this timing difference caused by the change, or by where the code and data happened to land?**

A change to one function moves every function linked after it. Moving code alone can change an unrelated benchmark by 10-30%, or double it. On Amber (Apple M2), one dict amend ran 2× slower in about one layout in 60; date grading ran 10% slower in every build where two of its loops crossed a 4 KB boundary; and window kernels switched speeds between runs because every large buffer sat at the same offset mod 16 KB. A single timing of each build cannot tell these from real effects. This is *measurement bias* (Mytkowicz et al., 2009).

placemat does three things:
1. **Measure:** time both builds across designed layout variants, so the result no longer depends on one layout, and attribute each case's spread to code placement, data placement, or run-to-run noise.
2. **Fix (optional):** turn placement from an uncontrolled variable into a controlled one (Kalibera & Jones's phrase): pin hot code with an order file, function alignment and pads; colour large allocations so buffers used together stop sharing cache sets.
3. **Watch:** once fixed, check changes cheaply, and re-run the full measurement now and then to catch what the fixes no longer cover.

Non-goals: it is not a profiler, not a code optimiser (it does not try to find the *fastest* layout), and it does not claim a layout-independent "true speed". It reports effects with intervals over layouts, next to the result in the layout the user actually ships.

## 2. Vocabulary

From Kalibera & Jones (ISMM 2013) and Mytkowicz et al. (2009); see PRIOR-ART.md §(c).

| term | meaning |
|---|---|
| **case** | one named benchmark measurement (e.g. `msum100`) |
| **iteration** | one repetition of the measured operation inside a timed sample (the lowest level) |
| **execution** | one process run of one build in a round |
| **round** | one pass that runs every arm's builds once, in random order: a *block* in the design |
| **arm** | one version being compared: the base and one or more changes |
| **pad** | one code-layout build of an arm (a chosen code pad): the *build level*, the highest level of repetition, and the unit of the interval |
| **layout variant** | one (pad, colour) combination: a pad's build timed at one data colour sequence; each pad has two, colour 0 and its designed colour |
| **stock** | the build as the project ships it: no pad, no colour |
| **code axis / data axis / run** | what a variant varies: where code sits; where large buffers sit; what differs between executions (ASLR, the machine) |
| **layout spread** | how much a case's time varies across variants of one build (a variance component at the build level) |
| **layout-sensitive case** | a case whose spread over the code (or data) axis exceeds the threshold, with a permutation p ≤ 0.01 that also survives the false-discovery-rate correction across cases |
| **hot entry span** | from a hot function's entry to its last sampled offset under 1 KB that holds at least 10% of the function's samples |
| **pinned** | built so the hot code's addresses do not depend on unrelated code |
| **coloured** | built so large allocations are spread over the cache's set stride |
| **4 KB boundary crossing** | a hot loop (or a hot function's entry-to-loop span) straddling a 4 KB address boundary (not "page crossing": macOS arm64 pages are 16 KB, and the mechanism is unexplained) |
| **L1 set conflicts** | buffers used together mapping to the same L1 sets (not "4K aliasing", which is Intel's store-to-load false dependence) |

## 3. Lifecycle: initial setup, steady state, and periodic audits

The expensive measurement is not something a project should run on every change forever. It answers "do we have a problem?", then pays for the fixes, then mostly stands guard. placemat is organised around four modes; a project moves through them, and returns to the first two when something changes underneath it.

### 3.1 Survey: "do we have a problem?"

*When:* first use; after a compiler, linker, allocator or CPU change; when an audit (§3.4) finds something new.

*What:* the full design on the stock build. Main against itself (a null run: any "change" is a false positive, and the layout spread of every case is measured), and a handful of recent real changes. Both axes, all cases, generous variant counts, targeted variants for rare windows (§5.4).

*Output:* per case, its code spread, data spread and run spread, with the layout-sensitive cases listed and, for each, the "culprits" (the loops that cross a boundary only in the slow variants; the buffers that conflict only at the slow colours). A verdict for the project:
- **No meaningful spread:** placemat's cheap mode (§3.3 without fixes) is enough; stop here.
- **Code spread:** pinning (§3.2) will probably help.
- **Data spread:** colouring (§3.2) will probably help, and may be a real speed-up, not just a measurement fix (it was on Amber: window kernels 2-2.8× faster, a grouping case 2.5×).
- **Run spread** (switching between speeds from run to run): usually data placement under ASLR; colouring removed it on Amber.

*Cost:* hours (on Amber about 1-3 h per comparison of ~200 cases). Run it rarely and keep its raw data.

### 3.2 Fix: pin and colour, then verify

*Pin* (§6.3): profile the benchmark set, choose the hot set (~95% of in-binary samples), write the order file, align functions, compute pads so no hot loop or hot entry span crosses a boundary, verify after linking. *Colour* (§7): build the project's allocator with colouring of large blocks (a real change to the project), or, for measurement only, use placemat's interposer or hook.

*Verify:* re-run the survey's comparisons on the fixed builds. The code spread should collapse for the pinned cases while real changes still show (on Amber: 9-21 layout-sensitive cases → 0; a false +13.5% → +0.9%, with the change's real costs, about +3-6%, intact). The data spread and the run switching should collapse once coloured. If they don't, the survey's culprits say where to look.

A project may stop after measuring, or take only one fix. placemat records which fixes are in place, because that decides what the steady state may skip.

### 3.3 Steady state: cheap checks on each change

*When:* every change worth timing (a pull request, a candidate fix).

*What:* placemat drops what the fixes have made unnecessary:
- **Only affected cases.** In a pinned build, unchanged hot functions do not move. placemat diffs the two binaries' machine code per function (ignoring call and branch targets, which change when unpinned callees move) and selects a case if any function it can reach changed, using the call graph, not only the stored hot set; it also takes a short fresh profile of the change's build, so a function the change made hot is caught. Changes to data or read-only data with identical code, and changes to the allocation sequence, select every case that touches them, or all cases when that cannot be told. A random sample of skipped cases is spot-checked every time. On a typical change we expect 200 cases to become 10-30 (an estimate; P005).
- **Only the axes still open.** With colouring built into both arms, the data axis is skipped. A change that leaves the binary unchanged (scripts in the project's own language, docs, tests) skips the code axis.
- **Few variants.** Pinned and coloured builds have small spread, so adaptive stopping starts at 4 variants and usually stops early.
- **One base for many arms.** Several candidates are timed against one base in the same rounds.

*What the steady state's interval means:* with pinned code, the code axis moves only the unpinned (cold) code, so the interval covers that pinned layout, not all layouts. Reports say so; they never call the result layout-independent.

*Output:* the change's effect per affected case with an interval, the stock-layout result next to it, and a short list of anything that needs the full treatment (a case flagged layout-sensitive despite pinning, a hot function that moved). *Cost:* minutes (an estimate: 10-20 min for a typical change on Amber; to be measured, P005).

Without fixes, the steady state is a normal paired run of all cases at the stock layout (the *screening pass*), then the full design on the cases it flags. Placement can hide a real change in the screening pass, so audits (§3.4) re-run everything.

### 3.4 Audit: the long version, now and then

Fixes cover what the survey saw. They decay, and they can hide new problems. An audit is the survey again, on the fixed builds and on stock builds, run periodically and whenever a drift detector fires. Things it exists to catch:

- **Hot-set drift:** the program's time moves into functions that are not in the order file (new features, new benchmarks, a refactor that renamed or split a hot function). Pinning then covers less of what matters, silently.
- **Stale profile data:** pads are regenerated on every build (§6.3), but the hot spans and hot loops they protect come from the stored profile; when a hot function's code changes, its sampled offsets go stale and the pads protect the wrong spans.
- **What the steady state skipped:** cases its selection judged unaffected, and real changes the screening pass missed because placement hid them.
- **New classes of data problem:** a new allocation path that skips colouring; buffers below the colouring threshold that now matter; or a placement assumption nobody knew about. Our own example: placemat's first data axis coloured in 16-byte steps; that broke Amber's 32-byte alignment assumption (its allocator guarantees 64) and produced a false 25-35% "data effect" on two kernels. Only a wider look found it, and only knowing the project's alignment guarantee explained it. Audits should vary what the fixes hold fixed, within the project's guarantees, precisely to find such things.
- **Platform change:** a new compiler or linker version (flags that silently stop working: Apple's linker ignores unknown `-mllvm` options), a new CPU with different boundaries or cache geometry.
- **The fixes' own cost:** pads and colouring take space and a few instructions; an audit re-times stock against fixed builds so their cost and benefit stay known.

An audit on pinned builds varies data through placemat's interposer, or through the project's own colouring when it has one, not through a hook added to the allocator: an added hook grows the allocator and moves the pinned code (Amber's did). And in a hooked build, colour 0 is not the shipped code, so every audit also times the true stock build (§5.3).

*Cheap drift detectors,* run with the steady state, decide when an audit is due:
1. **Hot-set coverage:** the stored profile's share of samples inside the order file, recomputed from a short profile of the current build; below a threshold (e.g. 90%), audit and regenerate.
2. **Placement check:** after every pinned build, the ordered functions are in order and aligned, and no hot loop or entry span crosses a boundary; failure means regenerate pads (cheap) and note it.
3. **Alignment check:** the project's own alignment assertions (Amber: a `-DAMBER_ALIGNCHECK` build) on a coloured build.
4. **Instruction counts** (where available, P006/P007): a case whose instruction count changed but whose time did not, or the reverse, is worth a look.
5. **Anomalies:** a steady-state check that flags a case as layout-sensitive despite pinning.

*Cadence:* per release, after toolchain or hardware changes, and when a detector fires. Its results become the new survey baseline.

## 4. Architecture

```
placemat (Python, standard library only)
├── core
│   ├── design      layout variants: code pads and data colours (§5)
│   ├── runner      rounds, arms, anchors, adaptive stopping, raw-data store
│   ├── stats       estimators, intervals, permutation tests, FDR (§5.5)
│   └── report      Markdown and JSON; re-analysis from raw data
├── adapters
│   ├── bench       name/value lines; Google Benchmark JSON; K out[] scripts (§8.1)
│   ├── build       the build protocol: flags, pad source, order file (§8.2)
│   ├── code        source pads; lld --randomize-section-padding / --shuffle-sections
│   └── data        malloc interposer; hook header for custom allocators (§7)
├── binary          Mach-O and ELF: symbols, loops, boundary crossings, per-function diffs
├── pin             profile import, hot-set choice, ordering, pad search, verification (§6.3)
└── cli             survey · fix (pin) · check · audit · culprits · reanalyse
```

The project supplies a small configuration file (`placemat.toml`): how to build with extra flags, how to run the benchmarks, the case list, the platform's boundaries and cache geometry (with defaults), the project's alignment guarantee, the colouring threshold, and which fixes are in place.

Implementation stays standard-library Python, as the Amber tooling is: no dependencies to install on a CI runner. Python 3.11 or later, for `tomllib`. Native pieces are small C files compiled on demand: the pad function source, the interposer, the hook header.

## 5. The experiment design

### 5.1 Levels and pairing

Each round runs every arm's current variant once, in random order (randomised multiple interleaved trials, Abedi & Brecht 2017); per-round ratios against the base are paired contrasts, which block out machine drift. Within an execution a case is timed at its iteration count; for cases under ~1 ms placemat times a geometric series of iteration counts and takes the regression slope (Criterion-style), falling back to the full-count time if the fit fails (a negative slope once lost a whole run). Rounds per pad (the levels below the top) follow Kalibera & Jones's dimensioning formula once a first batch has estimated the variance components (P005); the number of pads, the top level, is set by the precision wanted (adaptive stopping, §5.5).

### 5.2 Code axis

- **Source pads** (portable): an unused function of P bytes, kept by `__attribute__((used))`, placed before the code under study. On Amber it sits at the top of the first source file in link order; under LTO, "linked first" alone does not fix where a function lands, so placement is always verified from the linked binary's symbols (§8.2). Sizes come from a golden-ratio sequence, P_j = 64·⌊64·frac(u + j/φ)⌋ + 4·((7j + v) mod 16), with seeds u, v ∈ [0, 1) and an integer offset chosen per run and recorded: spread over the 4 KB period and the 64-byte phase at once (12 pads: largest gap 620 B against 341 for even spacing; 12 of 16 phases mod 64; all 4 mod 16). The R2 sequence was rejected (its first coordinate is near 3/4, so pads cluster).
- **lld** (Linux): `--randomize-section-padding` (lld 20) as the code axis, `.text` only so code and data stay separable; `--shuffle-sections` as a coarser alternative. Record the resulting addresses either way.
- **Boundaries** are platform parameters: 4 KB on Apple M2 (measured); 32/64-byte fetch windows and 4 KB on x86 (to be measured, P006).

### 5.3 Data axis

Colouring must vary the *relative* offsets of buffers used together, so a constant offset per variant is useless: it moves every block equally. A variant's colour c_j is therefore a *step*: the k-th large allocation of the execution (default ≥64 KB) is offset by (c_j·(k+1)) mod stride, in whole cache lines (64 B by default; never finer than the project's alignment guarantee), where stride is the L1 set stride (16 KB on M2; 4 KB on typical x86). The steps come from a √2 Kronecker sequence, c_j = 64·⌊(stride/64)·frac(w + j√2)⌋, with seed w ∈ [0, 1) recorded per run. Colour 0 is the stock placement. Each pad is timed at colour 0 and at its own step, so code effects are measured at the real placement and each pair isolates data; K variants cost K/2 builds per arm.

A hooked build's colour 0 is not the shipped binary (the hook adds code), so the unhooked stock build is always timed as one more arm.

### 5.4 Targeted variants

Rare bad layouts (a 64-byte loop crosses 4 KB in ~1.5% of positions) are missed by any modest sample. For flagged cases, `culprits` lists the loops that cross only in slow variants and the share of layouts in which they cross; placemat then adds pads inside and outside each window and reports a weighted average and a worst case. The data analogue uses logged buffer addresses (§7). Today this is manual on Amber; automating it is part of the extraction.

### 5.5 Statistics and verdicts

- **Effect:** per layout variant, the median of paired ratios over its rounds; per pad, the geometric mean of its two variants; the effect is the geometric mean over pads with a t interval over pads, n_pads − 1 degrees of freedom (Kalibera & Jones's equation 4 at the build level). The two colours of a pad share a code layout, so pads, not variants, are the independent units. Also reported over the stock-data variants alone.
- **Attribution:** code (permutation of pad labels within a batch's rounds, on trimmed means); data (sign-flip permutation over pads of each pad's colour contrast; with n pads the smallest two-sided p is 2^(1−n), 0.0078 at 8 pads, so a data flag needs at least 8 pads, and coarse p-values tie); run (the ASLR region base or other per-execution covariates, by permutation; when significant, each variant's executions are post-stratified by the covariate before its median is taken, rather than adjusted with one pooled model, which once spread its error across variants and made false −5% changes).
- **Flags:** Benjamini-Hochberg across cases (q = 0.05) separately per axis, plus p ≤ 0.01 and an effect over the project's threshold (default 3%; 10% for cases under 5 ms). BH alone is not enough with coarse permutation p-values: one shared disturbance gave five cases the same tied p ≈ 0.03, and BH rejected all five.
- **Adaptive stopping:** start with 8 pads, add 4 at a time until the interval's half-width is under the target (default 1%) or a cap (default 24); stop on width only, never on significance. Each later batch re-times pad 0 as an anchor; the anchor's change between batches estimates drift, which is divided out of that batch's ratios (it reached 5% between batches on Amber).
- **Verdicts:** *change* (interval excludes zero and the effect exceeds the threshold), *placement* (the stock-layout result differs but the layout-averaged one does not, or a version is layout-sensitive), *data-dependent* (the stock-data and coloured halves differ), *noise*.
- **Honesty:** coverage under adaptive stopping is only approximately nominal (Amber's null run: 7 of 104 intervals missed zero, 6.7% against 5%, consistent with that); no minimum-time estimator (under perturbation it would pick the luckiest layout); exchangeability of pads within a batch is an assumption, stated in reports; and main against main is not a perfect null when base and branch run from different directories.

## 6. Code: measurement and pinning

### 6.1 Binary analysis

Per binary: symbols with addresses and sizes; small loops (backward branches within ≤256 B) and whether they cross the boundary; hot entry spans from the profile; per-function machine-code equality between two builds (for the steady state's affected-case selection). Mach-O via `otool`/`nm` (done for Amber); ELF via `objdump`/`nm` (P006).

### 6.2 Culprits

For a layout-sensitive case: the hot loops whose crossing state differs between its fast and slow variants, with the share of all layouts in which each crosses. It is Mytkowicz's causal analysis applied to loops.

### 6.3 Pinning

1. **Profile** the benchmark set; attribute samples by address (macOS `sample` strips LTO suffixes and lumps stubs into the last function).
2. **Hot set:** functions in order of combined share until each benchmark set reaches ~95% of in-binary samples.
3. **Order:** C3-style clustering behind heaviest callers, clusters capped (16 KB), sorted by density.
4. **Link:** ld64 `-order_file` or lld `--symbol-ordering-file` (GNU ld `--section-ordering-file`), plus `-falign-functions=16`: ld64 keeps each function's address mod 16 from the LTO object, so an order file alone pins only to within ~64 bytes.
5. **Pads:** pad functions listed in the order file, sized by an exact search over the start address mod the boundary, so that no listed function's small loop and no hot entry span crosses it, with the fewest pad bytes.
6. **Verify:** symbols present and in order (`nm`; never trust the linker's silence: a build script may discard its warnings), no crossings, then time (§3.2).
7. **Regenerate** pads on every build (they belong to the build, not the source); the steady state's placement check does this.

### 6.4 Comparing ordering strategies (P009)

The order in step 3 is a choice. placemat can build several and compare them on the project: by hotness, by density, Pettis-Hansen, C3/hfsort, hfsort+ and CDSort, per-benchmark clusters, and random order as a control. Each pinned layout freezes its own luck (on Amber each pinned build had a few cases ±5-10% off from where that layout put them), so a strategy is never judged from one build: the whole ordered region is shifted through designed offsets (a pad in front of it over the 4 KB period and the 64-byte phase), optionally with several equally good pad solutions, and averaged like any comparison. The report gives, per strategy, static measures (text and pad bytes; profile-weighted 4 KB and 16 KB pages spanned by hot code; crossings before padding) and measured ones (layout-averaged speed against stock; spread; per-case winners and losers).

## 7. Data: colouring for measurement and for real

Two ways to colour, depending on who owns the allocator:

- **Interposer** (projects on the system allocator): a small shared library loaded with `DYLD_INSERT_LIBRARIES` / `LD_PRELOAD` that offsets allocations at or above the threshold. To keep size class and wrapper overhead the same at every colour, it over-allocates by one set stride and adds its header in *every* variant, colour 0 included; the true stock build (no interposer) is timed as one more arm. Open details (§11): a side table or header for the original pointer; `free`/`realloc` of pointers it never returned (allocated before it loaded, or inside the C library); `realloc` that moves a block to a different colour (copy, and recompute); `malloc_size`/`malloc_usable_size` on an interior pointer; requested alignments above the colour step (`posix_memalign`, `aligned_alloc`); and macOS System Integrity Protection, which strips `DYLD_INSERT_LIBRARIES` from protected binaries.
- **Hook header** (custom allocators): interposing `mmap` does not help an allocator that aligns blocks internally (Amber's buddy allocator put every payload ≥64 KB at the same offset mod 16 KB however its region moved). The project includes `placemat.h` under a flag (`-DPLACEMAT_HOOK`); without the flag the binary is unchanged.

```c
/* placemat.h (sketch) */
size_t placemat_colour(size_t size, size_t spare_room); /* offset for a block of `size` with `spare_room` free bytes; 0 in stock runs */
void   placemat_log_alloc(const void *p, size_t size);  /* address logging for diagnostics */
void   placemat_log_free(const void *p);
/* environment: PLACEMAT_COLOUR (sequence seed or explicit colour), PLACEMAT_COLOUR_STEP (default 64),
   PLACEMAT_COLOUR_MIN (default 65536), PLACEMAT_ADDRLOG (0, 1 = regions, 2 = blocks) */
```

Rules the allocator side must keep (learnt on Amber): take the colour from existing spare room so size classes don't change; undo it on free so free lists never see coloured blocks; keep the small-block fast path untouched (an extra check on every allocation cost allocation-bound cases 3-10% until it moved out of line); keep growth points the same (a vector growing one item at a time must still move up a class where it did); preserve the alignment guarantee, and check it with the project's own assertion build.

The same hook doubles as the shape of a project's *real* fix: if a project adopts colouring (as Amber may), its colour choice can defer to `placemat_colour()` under the flag, so placemat can still vary it. Valgrind client requests can sit under the same flag (P004, P006).

Address logging answers the survey's first data question. Large buffers can sit at fixed offsets (a hidden bias: every run gets the same placement, good or bad; Amber's sat at the same offset mod 16 KB in every execution and every code pad), move with ASLR (run noise), or move with code pads (then the two axes are confounded unless data is controlled).

## 8. Interfaces

### 8.1 Benchmark protocol

A benchmark command prints one line per case and iteration count: `name value iterations [unit]`. placemat passes, per execution:
- `PLACEMAT_CASES`: the cases to run (others may be skipped);
- `PLACEMAT_SERIES`: for the regression series, a list of scale factors per case (e.g. `msum100=0.125,0.25,0.5,1`); the harness runs each listed case at each scale within the same execution, after a discarded warm-up, so scale is not confounded with rounds or ASLR. Cases not listed run once at their own count.
Adapters translate existing formats: Google Benchmark JSON, hyperfine JSON, and K scripts of `out["name";reps;{...}]` lines (Amber's). A harness that cannot skip cases or scale iterations still works, at higher cost.

### 8.2 Build protocol

Two ways in, because build systems differ:
1. **A compiler wrapper** (the default): placemat sets `CC`/`CXX` (and the linker driver) to `placemat-cc`, which adds the variant's flags, compiles the generated pad source into the link, and passes the order file. This works with make, CMake, Meson and most others without changes, as long as the build honours `CC`. For build systems that pin their own compiler (cargo, Bazel), a project adapter is needed.
2. **Environment variables** for build scripts that read them: `PLACEMAT_CFLAGS`, `PLACEMAT_LDFLAGS`, `PLACEMAT_PAD_SOURCE` (a generated C file holding the pad function, and, when pinning, the pad functions named in the order file), `PLACEMAT_ORDER_FILE`, and `PLACEMAT_OUT` (the output directory).
Either way, the build must leave the binary at a configured path, and placemat **verifies** every variant after linking: the pad function is present (it can be dead-stripped despite `used`, P006), it sits where intended, and the ordered functions are in order. Builds are cached by source tree, flags and pad; a variant usually needs one small object recompiled and a relink.

### 8.3 Outputs

Every run keeps its raw timings (JSON), so verdicts can be recomputed with new statistics without re-timing (`placemat reanalyse`). Reports: a Markdown summary (verdict per case, intervals, stock-layout result, spreads, flags, culprits) and the JSON behind it.

## 9. Platforms

| platform | status | code boundary | L1D set stride | code axis | pinning |
|---|---|---|---|---|---|
| macOS arm64 (Apple M2) | tested on Amber | 4 KB | 16 KB (128 KB, 8-way) | source pads | ld64 order file + `-falign-functions=16` + pads |
| Linux x86-64 | planned (P006) | to measure (32/64 B windows, 4 KB) | 4 KB typical | lld padding/shuffling, source pads | lld/GNU ordering + alignment + pads |
| Linux arm64 | planned (P006, P008) | to measure | per CPU | as x86-64 | as x86-64 |

Measurement tools by platform (P006, P007): Valgrind (Cachegrind, Callgrind, DHAT, Massif) on Linux; kperf counters, Instruments and llvm-mca on macOS.

## 10. Extraction from the Amber fork

| placemat part | from (Amber fork) |
|---|---|
| core design, runner, stats, report | `pbt/ldesign.py`, `pbt/layouts.py`, the stage in `pbt/evidence.py` |
| binary analysis, culprits | `loops4k.py`, `samefn.py`, `ldesign.py culprits` |
| pin | the order-file tools (`prof2.py`, `pick.py`, `order.py`, `pads.py`, `bininfo.py`) |
| data hook | the variant patch in `layouts.py`'s `hook()`, rewritten against `placemat.h` |
| bench adapter (K) | `out[]` parsing in `layouts.py` |

What stays with Amber: its configuration file, its allocator patches and their generator (they embed Amber's `src/m.c`; Amber is AGPL-3.0, so nothing derived from its source may enter MIT-licensed placemat; they are kept in the Amber fork's `pbt/patches/`), its benchmark scripts, and its machine lock (placemat should take a lock command as configuration rather than ship Amber's). The data-hook part of `layouts.py` (`HOOK_DECL`, `HOOK_FUNS`, `HOOK_EDITS`) quotes Amber source and is **not** extracted: placemat's hook is written fresh against `placemat.h`, and Amber's adapter (in the fork) maps it onto Amber's allocator.

**Regression test:** the Amber validations, re-run through placemat, must give the same verdicts: (a) main against main: no changes; (b) the branch with a placement slowdown: placement, not change; (c) the window kernels: data-dependent, with the prototype's gain a change; (d) the pinned build against main: code spread collapses; (e) the coloured build: two-speed switching gone. Reference results and inputs: [amber-validation/](amber-validation/) (reports, the case list, order and pad files, the profile, the targeted pads for (d), the K kernels, and the raw timings in `raw.tar.xz`). Validation (d) still needs the targeted pads chosen by hand; automating that (§5.4) is part of the extraction.

## 11. Open questions

1. **Boundaries off Apple Silicon:** which code boundaries matter on x86-64 and on other arm64 cores (P006).
2. **Interposer details:** which allocation functions to interpose per platform (`malloc`, `calloc`, `realloc`, `posix_memalign`, `aligned_alloc`, C++ `operator new` through malloc), and how to keep `malloc_usable_size` honest.
3. **Lock and quiet machine:** how placemat learns that the machine is quiet (load average, a user-supplied lock command), and what it does when it isn't.
4. **Run-level perturbation:** whether to vary environment size (hyperfine, Mytkowicz) as a third axis, or only record the ASLR base as now.
5. **Automated targeted variants** (§5.4) and their weighting.
6. **Pinning back ends:** whether to offer BOLT or Propeller as alternatives to the order-file pipeline on Linux.
7. **Names:** whether "survey / fix / check / audit" are the right command names.

## 12. Claims and credits

As far as the survey found (PRIOR-ART.md §(a)), placemat can claim four things, each a combination or a gap rather than a new ingredient: designed joint code-and-data variants with per-case attribution and FDR control; colouring a custom allocator's large buffers as an experimental factor; the measure-pin-verify workflow; and (modestly) Apple Silicon support. It must not claim to be first at layout randomisation, multi-level designs, paired or interleaved runs, adaptive stopping, colouring, or order files, and must not claim a layout-independent speed. The README credits Mytkowicz et al. 2009, Curtsinger & Berger 2013, Kalibera & Jones 2013 (with Kalibera, Bulej & Tůma 2005 and Kalibera & Tůma 2006), Georges et al. 2007, Abedi & Brecht 2017, Laaber et al. 2020, Bonwick 1994, Kessler & Hill 1992, Afek, Dice & Morrison 2011, jemalloc, Rivera & Tseng 1998, Pettis & Hansen 1990, Ottoni & Maher 2017, Benjamini & Hochberg 1995, and the .NET 6 loop-alignment and Intel JCC-erratum guidance on boundary rules, and builds on lld's padding and shuffling options.
