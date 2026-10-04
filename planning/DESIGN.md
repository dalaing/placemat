# placemat: design

Draft, 2026-10-05. Companion documents: [ISSUES.md](ISSUES.md) (work items P001-P008), [PRIOR-ART.md](PRIOR-ART.md) (what exists, what to credit, what not to claim), [amber-validation/](amber-validation/) (the runs on Amber that this design rests on).

## 1. What placemat is for

placemat answers one question about a change to a compiled program: **is this timing difference caused by the change, or by where the code and data happened to land?**

A change to one function moves every function linked after it, and can move the program's data. On real machines that alone can make an unrelated benchmark 10-30% faster or slower, or twice as slow (Amber on an Apple M2: a dict amend 2× slower in one layout in 60; a grade +13% from one 64-byte loop starting to cross a 4 KB boundary; window kernels switching between two speeds from run to run because every large buffer sat at the same offset mod 16 KB). A single timing of each build cannot tell these from real effects. This is *measurement bias* (Mytkowicz et al., 2009).

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
| **layout variant** | one build of an arm with a chosen code pad and data colour: the *build level*, the highest level of repetition |
| **stock** | the build as the project ships it: no pad, no colour |
| **code axis / data axis / run** | what a variant varies: where code sits; where large buffers sit; what differs between executions (ASLR, the machine) |
| **layout spread** | how much a case's time varies across variants of one build (a variance component at the build level) |
| **layout-sensitive case** | a case whose spread over the code (or data) axis exceeds the threshold and survives the permutation test with false-discovery-rate control |
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

*Verify:* re-run the survey's comparisons on the fixed builds. The code spread should collapse for the pinned cases while real changes still show (on Amber: 9-21 layout-sensitive cases → 0; a false +13.5% → +0.9%, with the change's real +3-5% costs intact). The data spread and the run switching should collapse once coloured. If they don't, the survey's culprits say where to look.

A project may stop after measuring, or take only one fix. placemat records which fixes are in place, because that decides what the steady state may skip.

### 3.3 Steady state: cheap checks on each change

*When:* every change worth timing (a pull request, a candidate fix).

*What:* placemat drops what the fixes have made unnecessary:
- **Only affected cases.** In a pinned build, unchanged hot functions do not move. placemat diffs the two binaries' machine code per function and maps cases to functions through the stored profile; a case whose hot functions are byte-identical and unmoved is skipped or spot-checked. On a typical change, 200 cases become 10-30.
- **Only the axes still open.** With colouring built into both arms, the data axis is skipped. A change that leaves the binary unchanged (scripts in the project's own language, docs, tests) skips the code axis.
- **Few variants.** Pinned and coloured builds have small spread, so adaptive stopping starts at 4 variants and usually stops early.
- **One base for many arms.** Several candidates are timed against one base in the same rounds.

*Output:* the change's effect per affected case with an interval, the stock-layout result next to it, and a short list of anything that needs the full treatment (a case flagged layout-sensitive despite pinning, a hot function that moved). *Cost:* minutes (an estimate: 10-20 min for a typical change on Amber; to be measured, P005).

Without fixes, the steady state is the survey restricted to the cases the normal paired pass flags: slower, but still far cheaper than everything.

### 3.4 Audit: the long version, now and then

Fixes cover what the survey saw. They decay, and they can hide new problems. An audit is the survey again, on the fixed builds and on stock builds, run periodically and whenever a drift detector fires. Things it exists to catch:

- **Hot-set drift:** the program's time moves into functions that are not in the order file (new features, new benchmarks, a refactor that renamed or split a hot function). Pinning then covers less of what matters, silently.
- **Pad decay:** edits inside the pinned region shift it until the next pad; pads computed for an old build stop satisfying the boundary rule.
- **New classes of data problem:** a new allocation path that skips colouring; buffers below the colouring threshold that now matter; or a placement assumption nobody knew about. Our own example: placemat's first data axis coloured in 16-byte steps; that broke Amber's 32-byte alignment assumption (its allocator guarantees 64) and produced a false 25-35% "data effect" on two kernels. Only a wider look found it, and only knowing the project's alignment guarantee explained it. Audits should vary what the fixes hold fixed, within the project's guarantees, precisely to find such things.
- **Platform change:** a new compiler or linker version (flags that silently stop working: Apple's linker ignores unknown `-mllvm` options), a new CPU with different boundaries or cache geometry.
- **The fixes' own cost:** pads and colouring take space and a few instructions; an audit re-times stock against fixed builds so their cost and benefit stay known.

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

Implementation stays standard-library Python, as the Amber tooling is: no dependencies to install on a CI runner. Native pieces are small C files compiled on demand: the pad function source, the interposer, the hook header.

## 5. The experiment design

### 5.1 Levels and pairing

Each round runs every arm's current variant once, in random order (randomised multiple interleaved trials, Abedi & Brecht 2017); per-round ratios against the base are paired contrasts, which block out machine drift. Within an execution a case is timed at its iteration count; for cases under ~1 ms placemat times a geometric series of iteration counts and takes the regression slope (Criterion-style), falling back to the full-count time if the fit fails (a negative slope once lost a whole run). Variant counts and rounds per variant follow Kalibera & Jones's dimensioning formula once a first batch has estimated the variance components (P005).

### 5.2 Code axis

- **Source pads** (portable): an unused, link-surviving function of P bytes placed before the code under study (on Amber, at the top of the first object file). Sizes come from a golden-ratio sequence, P_j = 64·⌊64·frac(u + j/φ)⌋ + 4·((7j + v) mod 16): spread over the 4 KB period and the 64-byte phase at once (12 pads: largest gap 620 B against 341 for even spacing; 12 of 16 phases mod 64; all 4 mod 16). The R2 sequence was rejected (its first coordinate is near 3/4, so pads cluster).
- **lld** (Linux): `--randomize-section-padding` (lld 20) as the code axis, `.text` only so code and data stay separable; `--shuffle-sections` as a coarser alternative. Record the resulting addresses either way.
- **Boundaries** are platform parameters: 4 KB on Apple M2 (measured); 32/64-byte fetch windows and 4 KB on x86 (to be measured, P006).

### 5.3 Data axis

Colours are offsets applied to large allocations (default ≥64 KB), in whole cache lines (64 B by default; never finer than the project's alignment guarantee), spread over the L1 set stride (16 KB on M2; 4 KB on typical x86) by a √2 Kronecker sequence, C_j = 64·⌊(stride/64)·frac(w + j√2)⌋. Each pad is timed twice: at colour 0 (the stock placement, what users get) and at its colour, so code effects are measured at the real placement and each pair isolates data. K variants cost K/2 builds per arm.

### 5.4 Targeted variants

Rare bad layouts (a 64-byte loop crosses 4 KB in ~1.5% of positions) are missed by any modest sample. For flagged cases, `culprits` lists the loops that cross only in slow variants and the share of layouts in which they cross; placemat then adds pads inside and outside each window and reports a weighted average and a worst case. The data analogue uses logged buffer addresses (§7). Today this is manual on Amber; automating it is part of the extraction.

### 5.5 Statistics and verdicts

- **Effect:** per variant, the median of paired ratios; the geometric mean over variants; a t interval over variants (Kalibera & Jones's equation 4 at the build level). Reported both over all variants and over the stock-data half.
- **Attribution:** code (permutation of variant labels within a batch's rounds, on trimmed means), data (sign-flip permutation on the colour contrasts), run (the ASLR region base or other per-execution covariates, by permutation; when significant, executions are adjusted per variant).
- **Flags:** Benjamini-Hochberg across cases (q = 0.05) separately per axis, plus p ≤ 0.01 and an effect over the project's threshold (BH alone produced false flags from one p ≈ 0.03 event).
- **Adaptive stopping:** add variants in batches until the interval's half-width is under the target or a cap; stop on width only, never on significance; re-time an anchor variant each batch to catch drift (it reached 5% between batches on Amber).
- **Verdicts:** *change* (interval excludes zero and the effect exceeds the threshold), *placement* (the stock-layout result differs but the layout-averaged one does not, or a version is layout-sensitive), *data-dependent* (the stock-data and coloured halves differ), *noise*.
- **Honesty:** coverage under adaptive stopping is approximate (Amber's null run: 6.7% of intervals missed zero against 5% nominal); no minimum-time estimator (under perturbation it would pick the luckiest layout); exchangeability within a batch is an assumption, stated in reports.

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

## 7. Data: colouring for measurement and for real

Two ways to colour, depending on who owns the allocator:

- **Interposer** (projects on the system allocator): a small shared library loaded with `DYLD_INSERT_LIBRARIES` / `LD_PRELOAD` that offsets allocations at or above the threshold by the variant's colour (over-allocating by up to one set stride and recording the original pointer for `free`/`realloc`), keeping the platform's alignment guarantee.
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

Address logging answers the survey's first data question: are large buffers placed the same in every execution (then code padding moves data too, and the axes are confounded unless the data axis is controlled), or randomly (then data placement is run noise, which colouring turns into a controlled factor)?

## 8. Interfaces

### 8.1 Benchmark protocol

A benchmark command prints one line per case: `name value [unit]`, value in time units for a stated number of iterations. placemat passes, per execution:
- `PLACEMAT_CASES`: the cases to run (others may be skipped);
- `PLACEMAT_ITERS_SCALE`: a factor applied to each case's iteration count, for the regression series.
Adapters translate existing formats: Google Benchmark JSON, hyperfine JSON, and K scripts of `out["name";reps;{...}]` lines (Amber's). A harness that cannot skip cases or scale iterations still works, at higher cost.

### 8.2 Build protocol

placemat runs the project's build command with:
- `PLACEMAT_CFLAGS`, `PLACEMAT_LDFLAGS`: appended flags (alignment, order file, the hook define);
- `PLACEMAT_PAD_SOURCE`: a generated C file holding the pad function (and, when pinning, the pad functions named in the order file), to be compiled and linked first;
- `PLACEMAT_ORDER_FILE`: the order file, when pinning;
- an output directory; the build must leave the binary (or binaries) at a configured path.
Builds are cached by source tree, flags and pad; a variant usually needs one small object recompiled and a relink.

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

What stays with Amber: its configuration file, its allocator patch (it embeds Amber's `src/m.c`; Amber's licence), its benchmark scripts, and its machine lock (placemat should take a lock command as configuration rather than ship Amber's).

**Regression test:** the Amber validations, re-run through placemat, must give the same verdicts: (a) main against main: no changes; (b) the branch with a placement slowdown: placement, not change; (c) the window kernels: data-dependent, with the prototype's gain a change; (d) the pinned build against main: code spread collapses; (e) the coloured build: two-speed switching gone. Reference results: [amber-validation/](amber-validation/).

## 11. Open questions

1. **Boundaries off Apple Silicon:** which code boundaries matter on x86-64 and on other arm64 cores (P006).
2. **Interposer details:** which allocation functions to interpose per platform (`malloc`, `calloc`, `realloc`, `posix_memalign`, `aligned_alloc`, C++ `operator new` through malloc), and how to keep `malloc_usable_size` honest.
3. **Lock and quiet machine:** how placemat learns that the machine is quiet (load average, a user-supplied lock command), and what it does when it isn't.
4. **Run-level perturbation:** whether to vary environment size (hyperfine, Mytkowicz) as a third axis, or only record the ASLR base as now.
5. **Automated targeted variants** (§5.4) and their weighting.
6. **Pinning back ends:** whether to offer BOLT or Propeller as alternatives to the order-file pipeline on Linux.
7. **Names:** whether "survey / fix / check / audit" are the right command names.

## 12. Claims and credits

placemat should claim only the combination and two gaps (PRIOR-ART.md §(a)): designed joint code-and-data variants with per-case attribution and FDR control; colouring a custom allocator's large buffers as an experimental factor; the measure-pin-verify workflow; and (modestly) Apple Silicon support. It must not claim to be first at layout randomisation, multi-level designs, paired or interleaved runs, adaptive stopping, colouring, or order files, and must not claim a layout-independent speed. The README credits Mytkowicz et al. 2009, Curtsinger & Berger 2013, Kalibera & Jones 2013, Georges et al. 2007, Abedi & Brecht 2017, Laaber et al. 2020, Bonwick 1994, Kessler & Hill 1992, Afek, Dice & Morrison 2011, jemalloc, Rivera & Tseng 1998, Pettis & Hansen 1990, Ottoni & Maher 2017, and Benjamini & Hochberg 1995, and builds on lld's padding and shuffling options.
