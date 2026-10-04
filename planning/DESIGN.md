# placemat: design

Draft, 2026-10-05. Companion documents: [ISSUES.md](ISSUES.md) (work items P001-P010), [PRIOR-ART.md](PRIOR-ART.md) (what exists, what to credit, what not to claim), [amber-validation/](amber-validation/) (the runs on Amber that this design rests on).

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
| **round** | one pass over every layout variant of the current batch, for every arm, in random order, each variant's arms back to back: a *block* in the design |
| **arm** | one version being compared: the base and one or more changes |
| **pad** | one code-layout build of an arm (a chosen code pad): the *build level*, the highest level of repetition, and the unit of the interval |
| **layout variant** | one (pad, data setting) combination: a pad's build timed at one data setting; each pad has three (no offset, its colour, its step), or two when only one of colour and step is varied |
| **colour** | a constant offset added to every coloured allocation: moves the data relative to fixed things (page boundaries, uncoloured allocations, static data, the stack) |
| **step** | a per-block offset that differs from block to block, so coloured buffers move relative to *each other* (their L1 set conflicts): hashed by default (block k gets a pseudo-random offset from the variant's seed), or linear (block k moves by s·(k+1)); k comes from the block's position (§5.3) |
| **data setting** | a (colour, step) pair, where the step is *off* or a step value/seed; (0, off) means no offset (the stock placement only for a hooked build of an allocator that otherwise places blocks identically; see §5.3) |
| **stock** | the build as the project ships it: no pad, no hook, no interposer |
| **code axis / data axis / run** | what a variant varies: where code sits; where large buffers sit; what differs between executions (ASLR, the machine) |
| **layout spread** | how much a case's time varies across variants of one build (a variance component at the build level) |
| **layout-sensitive case** | a case whose spread over the code (or data) axis exceeds the threshold, with a permutation p ≤ 0.01 that also survives the false-discovery-rate correction across cases |
| **hot entry span** | from a hot function's entry to its last sampled offset under 1 KB that holds at least 10% of the function's samples |
| **pinned** | built so the hot code's addresses do not depend on unrelated code |
| **coloured** | built (or run) so large allocations get colours and/or steps |
| **4 KB boundary crossing** | a hot loop (or a hot function's entry-to-loop span) straddling a 4 KB address boundary (not "page crossing": macOS arm64 pages are 16 KB, and the mechanism is unexplained) |
| **L1 set conflicts** | buffers used together mapping to the same L1 sets (not "4K aliasing", which is Intel's store-to-load false dependence) |

## 3. Lifecycle: initial setup, steady state, and periodic audits

The expensive measurement is not something a project should run on every change forever. It answers "do we have a problem?", then pays for the fixes, then mostly stands guard. placemat is organised around four modes; a project moves through them, and returns to the first two when something changes underneath it.

### 3.1 Survey: "do we have a problem?"

*When:* first use; after a compiler, linker, allocator or CPU change; when an audit (§3.4) finds something new.

*What:* the full design on the stock build. Main against itself (a null run: any "change" is a false positive, and the layout spread of every case is measured), and a handful of recent real changes. Both axes, all cases, generous variant counts, targeted variants for rare windows (§5.4).

*Output:* per case, its code spread, data spread and run spread, with the layout-sensitive cases listed and, for each, the "culprits" (the loops that cross a boundary only in the slow variants; the buffers that conflict only at the slow data settings). A verdict for the project:
- **No meaningful spread:** placemat's cheap mode (§3.3 without fixes) is enough; stop here.
- **Code spread:** pinning (§3.2) will probably help.
- **Data spread:** colouring (§3.2) will probably help, and may be a real speed-up, not just a measurement fix (it was on Amber: window kernels 2-2.8× faster, a grouping case 2.5×).
- **Run spread** (switching between speeds from run to run): usually data placement under ASLR; colouring removed it on Amber.

*Cost:* hours (on Amber about 1-3 h per comparison of ~200 cases with two variants per pad; varying both colour and step takes about 1.5× that). Run it rarely and keep its raw data.

### 3.2 Fix: pin and colour, then verify

*Pin* (§6.3): profile the benchmark set, choose the hot set (~95% of in-binary samples), write the order file, align functions, compute pads so no hot loop or hot entry span crosses a boundary, verify after linking. *Colour* (§7): build the project's allocator with colouring of large blocks (a real change to the project), or, for measurement only, use placemat's interposer or hook.

*Verify:* re-run the survey's comparisons on the fixed builds. The code spread should collapse for the pinned cases while real changes still show (on Amber: 9-21 layout-sensitive cases → 0; a false +13.5% → +0.9%, with the change's real costs, about +2-6%, intact). The data spread and the run switching should collapse once coloured. If they don't, the survey's culprits say where to look.

A project may stop after measuring, or take only one fix. placemat records which fixes are in place, because that decides what the steady state may skip.

### 3.3 Steady state: cheap checks on each change

*When:* every change worth timing (a pull request, a candidate fix).

*What:* placemat drops what the fixes have made unnecessary:
- **Only affected cases.** A case is re-timed if any function it can reach changed. placemat compares the two binaries' machine code per function, masking call and branch targets and data references (page/offset pairs such as arm64's `adrp`), which move whenever unpinned code or data moves. Reachability comes from the call graph, not only the stored hot set, plus a short fresh profile of the change's build to catch newly hot functions. Changes to data or read-only data with identical code, and changes to the allocation sequence, select every case that touches them, or all cases when that cannot be told. A random sample of skipped cases is spot-checked every time. On a typical change we expect 200 cases to become 10-30 (an estimate; P005).
- **Only the axes still open.** With colouring built into both arms, the data axis is skipped. A change that leaves the binary unchanged (scripts in the project's own language, docs, tests) skips the code axis.
- **Few variants.** Pinned and coloured builds have small spread, so most cases stop at the first batch: 8 pads of one variant each when the data axis is skipped (colouring built in), 4 pads otherwise. The project may lower the start where its spreads are known to be small.
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

An audit on pinned builds needs a data axis that does not move the pinned code. Options, in order: the project's own colouring, when it has one; placemat's interposer, for projects on the system allocator; or, for a custom allocator without colouring (Amber today), compile the hook into **both** pinned arms and build the order file and pads with the hook in, so the pinned layout is the hooked one. An added hook grows the allocator and moves pinned code (Amber's did), which is why it cannot simply be switched on in a pinned build. And in a hooked build, the stock data setting (0, off) is not the shipped code, so every audit also times the true stock builds (§5.3).

*Cheap drift detectors,* run with the steady state, decide when an audit is due:
1. **Hot-set coverage:** the stored profile's share of samples inside the order file, recomputed from a short profile of the current build; below a threshold (e.g. 90%), audit and regenerate.
2. **Placement check:** pads are regenerated on every pinned build (§6.3); the check then confirms the ordered functions are in order and aligned and that no hot loop or entry span crosses a boundary. A failure means the pad search could not satisfy the rule (a function grew past its slack): note it and audit.
3. **Alignment check:** the project's own alignment assertions (Amber: a `-DAMBER_ALIGNCHECK` build) on a coloured build.
4. **Instruction counts** (where available, P006/P007): a case whose instruction count changed but whose time did not, or the reverse, is worth a look.
5. **Anomalies:** a steady-state check that flags a case as layout-sensitive despite pinning.

*Cadence:* per release, after toolchain or hardware changes, and when a detector fires. Its results become the new survey baseline.

## 4. Architecture

```
placemat (Python, standard library only)
├── core
│   ├── design      layout variants: code pads, data colours and steps (§5)
│   ├── runner      rounds, arms, anchors, adaptive stopping, raw-data store
│   ├── stats       estimators, intervals, permutation tests, FDR (§5.5)
│   └── report      Markdown and JSON; re-analysis from raw data
├── adapters
│   ├── bench       name/value lines; Google Benchmark JSON; K out[] scripts (§8.1)
│   ├── build       the build protocol: flags, pad source, order file (§8.2)
│   ├── code        source pads; lld --shuffle-sections (on .text*)
│   └── data        malloc interposer; hook header for custom allocators (§7)
├── binary          Mach-O and ELF: symbols, loops, boundary crossings, per-function diffs
├── pin             profile import, hot-set choice, ordering, pad search, verification (§6.3)
└── cli             survey · fix (pin) · check · audit · culprits · reanalyse
```

The project supplies a small configuration file (`placemat.toml`): how to build with extra flags, how to run the benchmarks, the case list, the platform's boundaries and cache geometry (with defaults), the project's alignment guarantee, the colouring threshold, and which fixes are in place.

Implementation stays standard-library Python, as the Amber tooling is: no dependencies to install on a CI runner. Python 3.11 or later, for `tomllib`. Native pieces are small C files compiled on demand: the pad function source, the interposer, the hook header.

## 5. The experiment design

### 5.1 Levels and pairing

A **round** runs every layout variant of the current batch, for every arm, in a fresh random order; within a variant, the base and each change run back to back, in random order (randomised multiple interleaved trials, Abedi & Brecht 2017). Per-round ratios against the base are paired contrasts, which block out machine drift; shuffling the variants within each round keeps drift inside a batch from lining up with any one pad. Within an execution a case is timed at its iteration count; for cases under ~1 ms placemat times a geometric series of iteration counts and takes the regression slope (Criterion-style), falling back to the full-count time if the fit fails (a negative slope once lost a whole run). Today (as in the Amber stage) each batch runs 7 rounds after one discarded execution of each binary and data setting. Planned (P005): choose rounds per pad (the levels below the top) by Kalibera & Jones's dimensioning formula once a first batch has estimated the variance components; the number of pads, the top level, is set by the precision wanted (adaptive stopping, §5.5). Each execution also records its run covariates (the ASLR region base, as "the most common base or not").

### 5.2 Code axis

- **Source pads** (portable): an unused function of P bytes, kept by `__attribute__((used))`, placed before the code under study. On Amber it sits at the top of the first source file in link order; under LTO, "linked first" alone does not fix where a function lands, so placement is always verified from the linked binary's symbols (§8.2). Sizes come from a golden-ratio sequence, P_j = 64·⌊64·frac(u + j/φ)⌋ + 4·((7j + v) mod 16), with seeds u ∈ [0, 1) and integer v ∈ [0, 16), chosen per run and recorded: spread over the 4 KB period and the 64-byte phase at once (with the Amber stage's seed 0, whose `random.Random(0)` draws give u ≈ 0.844 and v = 13, 12 pads: largest gap 620 B against 341 for even spacing; 12 of 16 phases mod 64; all 4 mod 16; placemat derives u, v, w₁ and w₂ the same way, from one run seed with Python's `random.Random`, and records them). The R2 sequence was rejected (its first coordinate is near 3/4, so pads cluster).
- **lld** (Linux): `--shuffle-sections`, which takes a section glob, so it can be limited to `.text*` and keep code and data separable. `--randomize-section-padding` (lld 20) pads code and data sections alike (`.text*`, `.rodata`, `.data`, `.data.rel.ro`, `.bss`) with no filter, as merged (llvm PR #117653), so it mixes data movement into the code axis; use it only as a combined layout axis, or if a later lld adds a filter (check before relying on either). Source pads remain the portable code axis. Record the resulting addresses either way.
- **Boundaries** are platform parameters: 4 KB on Apple M2 (measured); 32/64-byte fetch windows and 4 KB on x86 (to be measured, P006).

### 5.3 Data axis

Large allocations (default ≥64 KB) can be moved in two independent ways, and placemat supports both:

- **Colour:** one constant offset c for every coloured allocation. It moves the data relative to things that stay put: page and huge-page boundaries (hardware prefetchers commonly stop at a page boundary; split accesses; TLB reach), uncoloured allocations, static data and the stack. It does not change coloured buffers' offsets relative to each other.
- **Step:** each block gets its own offset, varying coloured buffers' offsets relative to each other, which decides their L1 set conflicts. Hashed (default) or linear, below. (A step also moves block 0, so a step contrast is relative *plus* some absolute movement.)

**What k is.** For measurement, k is derived from the block's *position*, not from allocation order. The allocator knows its own structure, so under the hook it passes k: Amber's validated hook used the block's slot index within its region (its offset from the region base divided by the block size). The interposer works it out: k = ⌊(address − base) / G⌋, with base the first coloured block's address in the process and G one fixed granule for the process (the smallest coloured size); floor division and a non-negative mod, since mappings can lie below the base (glibc places them top-down). A raw address would carry the ASLR slide into k. Even with a per-process base, an allocator that randomises each large mapping separately makes k vary from run to run; the interposer logs each execution's k values, and the run test's covariate summarises them coarsely enough to repeat across executions (for example, whether a hash of the k values in allocation order matches the arm's most common one; under hashed steps the k values themselves, not only their gaps, decide the placement); when nothing repeats, the run covariate is reported as unavailable.

**Linear and hashed steps.** A linear step s·(k+1) (Amber's validated form, with √2 steps) spreads blocks one to a few slots apart well, but it has two limits. It is periodic: the relative offset depends only on Δk mod (span_s/unit), so blocks 256 slots apart (at 16 KB and 64 B) share their offset at every step. And k must be on one scale for all blocks: if each block's k is its slot index in its own size class (Amber's hook) or its address divided by its own size (an interposer using per-block divisors), a 64 KB block and a 1 MB block can share a k, and no step ever moves one relative to the other — exactly the mix of a window and tables in zstd. So placemat's general form is a **hashed step**: k is the block's offset from the base divided by one fixed granule for the whole process (the smallest coloured size, default 64 KB), and block k's step offset is unit·(H(seed_j ⊕ k) mod (span_s/unit)), with H a mixing hash and seed_j the variant's step seed. Every pair of blocks then gets a pseudo-random relative offset in each variant, whatever their sizes and distance. The linear form stays available as an option for any project (with k on the same fixed-granule scale); no project gets a different k rule. An allocator calling the hook should pass k on that same scale: k = ⌊(address − base) / G⌋ with one base for the whole process (its first region's base, or the first coloured block's address), never a base per region (blocks at the same offset in different regions would then share k) and never the raw address (which would bring back the ASLR lottery).

**Offsets and spans.** Block k's offset is (c mod span_c) plus its step offset (hashed by default, or linear (s·(k+1)) mod span_s), in whole units: unit = the cache line (64 B by default; never finer than the project's alignment guarantee); span_s = the L1 set stride (16 KB on M2; 4 KB on typical x86); span_c up to the page size or beyond (e.g. 2 MB for huge-page effects), as configured. Under the hook the cost is nothing (offsets come from a block's spare room); under the interposer each coloured block is over-allocated by span_c + span_s (2 MB on a 64 KB block is 32×, so large colour spans suit only projects whose coloured blocks are large or few). A project can vary colours only, steps only, or both.

**Which matters** depends on the allocator. Amber's buddy allocator aligns every large block to its own size, so all large buffers shared one offset mod 16 KB: their problem was relative, and its validation used steps only (a colour there moves every large block equally and leaves their conflicts unchanged). An allocator that already scatters large blocks, or a workload sensitive to page boundaries or to its position relative to small allocations, needs colours. The survey's address logging (§7) says which; the default is both.

**Design.** Pads, colours and steps come from one Kronecker sequence with independent generators (pads in 4-byte steps as in §5.2; colours in whole units and never zero; linear steps likewise never zero, while a hashed step gives each block its own offset, 0 for about 1 block in span_s/unit):
- pad P_j as in §5.2 (generator 1/φ);
- step: hashed mode, seed_j = ⌊2⁶⁴·frac(w₂ + j·√2)⌋ and block k's step offset unit·(H(seed_j ⊕ k) mod (span_s/unit)), with H the splitmix64 finaliser including its golden-gamma add, H(x) = mix(x + 0x9E3779B97F4A7C15) with mix(z) = z ⊕ (z ≫ 30), ×0xBF58476D1CE4E5B9, ⊕ (≫ 27), ×0x94D049BB133111EB, ⊕ (≫ 31), all mod 2⁶⁴, and k taken mod 2⁶⁴ (two's complement) before hashing, so the hook, the interposer (whose k can be negative) and Python re-analysis compute identical offsets; linear mode, s_j = unit·⌊(span_s/unit)·frac(w₂ + j·√2)⌋, replaced by one unit if it comes out 0, and block k's offset (s_j·(k+1)) mod span_s (√2 as in Amber's validated stage, where 2s and 3s stay spread: blocks used together are often 1-3 slots apart);
- colour c_j = unit·⌊(span_c/unit)·frac(w₁ + j·√3)⌋, likewise never 0;
with seeds w₁, w₂ ∈ [0, 1) recorded per run. Each pad is timed at the stock setting (0, off), at (c_j, off) and at (0, step_j), where step_j is seed_j (hashed) or s_j (linear): no extra builds (one build per pad per arm), half again as much timing as stock plus one data setting (Amber's two per pad), and a separate colour contrast and step contrast per pad. The code test uses the stock-setting variants. A project that varies only one of the two times two settings per pad, as Amber did. Fitting colour and step jointly (one setting per pad, then a regression) is not reliable: the effects live in narrow windows (an aliasing window is a line or two wide in 16 KB), not along a line in c or s; it is listed as exploratory (§11).

**The stock build.** Under a hook, (0, off) is the hooked build with no offset, not the shipped binary (the hook adds code); under the interposer it is not the stock placement either (§7). So the unhooked, uninterposed stock builds of both arms are timed as one more slot in the first batch, and the reports say which placement each figure is at.

### 5.4 Targeted variants

Rare bad layouts (a 64-byte loop crosses 4 KB in ~1.5% of positions) are missed by any modest sample. For flagged cases, `culprits` lists the loops that cross only in slow variants and the share of layouts in which they cross; placemat then adds pads inside and outside each window and reports a weighted average and a worst case. The data analogue uses logged buffer addresses (§7). Today this is manual on Amber; automating it is part of the extraction.

### 5.5 Statistics and verdicts

- **Effect:** per layout variant, the median over rounds of the paired ratio; per pad, the geometric mean of its variants, weighted equally (with three variants the offset settings carry 2/3 of the weight, against 1/2 in Amber's two-variant stage: a deliberate change of estimand, §10); the effect is the geometric mean over the m pads with a t interval over pads, m − 1 degrees of freedom (Kalibera & Jones's equation 4 at the build level). A pad's variants share a code layout, so pads, not variants, are the independent units. Also reported over each data setting alone (stock, coloured, stepped). The case is marked **colour-dependent** when the coloured result differs from the stock-setting one, and **step-dependent** when the stepped one does (each a paired t over pads at p < 0.005, i.e. 0.01 split over the two comparisons, and by more than the threshold; this needs 99.75% t quantiles, which the Amber stage does not tabulate); either makes it *data-dependent*.
- **Sensitivity**, tested per version (base and branch separately), then combined: a case's p-value is the smaller of the two, doubled (with more arms, the smallest times the number of arms):
  - **Code:** using the stock-data variants only (so data effects cannot raise code flags), permute pad labels within a batch's rounds (the anchor included); statistic the within-batch sum of squares of trimmed means (medians' range saturates under permutation). The code spread is likewise the range of the stock-data variants' medians. Assumes those variants are exchangeable within a round.
  - **Data:** two sign-flip tests, one for colour (each pad's stock-setting variant against its coloured one) and one for step (against its stepped one), each flipping the two labels *within rounds*; statistic the sum over pads of the squared per-contrast t. Flipping within rounds, not over pads, keeps the smallest attainable p far below the false-discovery threshold even with few pads (over pads alone it would be 2^(1−m), too coarse for BH across hundreds of cases). Assumes a pad's two variants are exchangeable within a round.
  - **Run:** the ASLR region base (as "the most common base over all of this arm's executions, or not", the mode taken per arm as in the Amber stage, so the category means the same thing in every variant), or another per-execution covariate, by a max-|z| permutation test over data settings; when it matters (p < 0.05), the tests use runs adjusted for it, and each variant's executions are post-stratified by it before its median is taken (a pooled adjustment once spread its error across variants and made false −5% changes). The post-stratification's categories are taken from the mode over both arms pooled (as the Amber stage does) only for covariates comparable across arms, such as the region base; for one that depends on the arm's own allocations (the hash of k values), each run is categorised against its own arm's mode.
- **Flags:** Benjamini-Hochberg across cases (q = 0.05) separately per family (code, colour, step, run), plus p ≤ 0.01 and an effect over the project's threshold (default 3%; 10% for cases under 5 ms). BH alone is not enough with coarse permutation p-values: one shared disturbance gave five cases the same tied p ≈ 0.03, and BH rejected all five.
- **Adaptive stopping:** batches are counted in **pads**, so a pad's variants are always timed in the same batch (the code test runs within batches and the data contrasts within rounds): start with 4 pads, add 2 at a time until the interval's half-width is under the target (default 1%) or a cap of 12 pads when each pad has two or three variants; with one variant per pad (data axis skipped), start with 8 pads, add 4, cap 24, as Amber's `--no-data` runs did, so the t interval keeps enough degrees of freedom; stop on width only, never on significance. A pad has 1, 2 or 3 variants (data axis skipped; colour or step only; both), so a first batch is 8, 8 or 12 variants (Amber counted 8 → 24 variants in both its modes: 4 → 12 pads with data, 8 → 24 pads without). Each later batch also re-times pad 0's stock-data variant as an **anchor**. Drift between batches already cancels in the paired ratios, so the anchor is used only to put later batches' absolute times on the first batch's scale (for the code test's ranges), never to rescale ratios: dividing ratios by one noisy anchor would correlate the pads and break the t interval. (Amber: drift between batches reached 5%.)
- **Verdicts:** *change* (the interval of the ratio excludes 1, i.e. no change, and the effect exceeds the threshold); *placement* (the stock builds' own paired result is measurable, meaning its interval, a percentile bootstrap over rounds with 4,000 resamples as in the Amber stage, excludes 1 and it exceeds the threshold, but the layout-averaged result is not; or a version is flagged for code, data or run sensitivity); otherwise *noise*. When the stock builds' measurable result has an interval that misses the stock-setting variants' interval, the difference is attributed to the **stock code layout** when (0, off) is the stock placement (a hook whose no-offset setting places blocks as the stock allocator does), since the two then differ in code only; otherwise (the interposer, or a hook that changes placement) to the **stock build**, code or placement undistinguished. Any verdict can carry the qualifier *data-dependent* (above).
- **Honesty:** coverage under adaptive stopping is only approximately nominal (Amber's null run: 7 of 104 intervals missed 1 (no change), 6.7% against 5%, consistent with that); no minimum-time estimator (under perturbation it would pick the luckiest layout); exchangeability of pads within a batch is an assumption, stated in reports; and main against main is not a perfect null when base and branch run from different directories.

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
5. **Pads:** pad functions listed in the order file, sized by an exact search over the start address mod the boundary that minimises a weighted cost (Amber's `pads.py`: 10⁶ per small-loop crossing in a listed function, 10⁴ per hot-entry-span crossing, 1 per pad byte; this is the C2 invocation, and build D added 10³ per 64-byte-line crossing of a function's hottest loop). Loop crossings are effectively forbidden; since any start mod 4096 costs at most 4,080 pad bytes, a hot-span crossing survives only when avoiding it would make a loop cross, and the verification step reports any that remain.
6. **Verify:** symbols present and in order (`nm`; never trust the linker's silence: a build script may discard its warnings), no crossings, then time (§3.2).
7. **Regenerate** pads on every build (they belong to the build, not the source); the steady state's placement check does this.

### 6.4 Comparing ordering strategies (P009)

The order in step 3 is a choice. placemat can build several and compare them on the project: by hotness, by density, Pettis-Hansen, C3/hfsort, hfsort+ and CDSort, per-benchmark clusters, and random order as a control. Each pinned layout freezes its own luck (on Amber each pinned build had a few cases ±5-10% off from where that layout put them), so a strategy is never judged from one build: the whole ordered region is shifted through designed offsets (a pad in front of it over the 4 KB period and the 64-byte phase), optionally with several equally good pad solutions, and averaged like any comparison. The report gives, per strategy, static measures (text and pad bytes; profile-weighted 4 KB and 16 KB pages spanned by hot code; crossings before padding) and measured ones (layout-averaged speed against stock; spread; per-case winners and losers).

## 7. Data: colouring for measurement and for real

Two ways to colour, depending on who owns the allocator:

- **Interposer** (projects on the system allocator): a small shared library loaded with `DYLD_INSERT_LIBRARIES` / `LD_PRELOAD` that offsets allocations at or above the threshold. To keep size class and wrapper overhead the same at every data setting, it over-allocates by span_c + span_s and adds its header in *every* variant, (0, off) included. Its (0, off) is therefore *not* the stock placement (the header shifts the payload, and large blocks from the system allocator are page-aligned), so under the interposer "code measured at the real placement" holds only approximately; the true stock builds (no interposer) are timed as one more slot in the first batch, and reports say which placement each figure is at. Open details (§11): a side table or header for the original pointer; `free`/`realloc` of pointers it never returned (allocated before it loaded, or inside the C library); `realloc` that moves a block to a different offset (copy, and recompute); `malloc_size`/`malloc_usable_size` on an interior pointer; requested alignments above the unit (`posix_memalign`, `aligned_alloc`); and macOS System Integrity Protection, which strips `DYLD_INSERT_LIBRARIES` from protected binaries.
- **Hook header** (custom allocators): interposing `mmap` does not help an allocator that aligns blocks internally (Amber's buddy allocator put every payload ≥64 KB at the same offset mod 16 KB however its region moved). The project includes `placemat.h` under a flag (`-DPLACEMAT_HOOK`); without the flag the binary is unchanged.

```c
/* placemat.h (sketch) */
size_t placemat_colour(size_t k, size_t size, size_t spare_room); /* offset for block k (the allocator's position index, §5.3) */
void   placemat_log_alloc(const void *p, size_t size);  /* address logging for diagnostics */
void   placemat_log_free(const void *p);
/* environment: PLACEMAT_COLOUR (constant offset c), PLACEMAT_STEP_MODE (hashed | linear),
   PLACEMAT_STEP_SEED (hashed: seed_j) or PLACEMAT_STEP (linear: step s); with neither set, no step is applied
   (a seed of 0 is a valid step, not "off"); setting both, or the one that does not match PLACEMAT_STEP_MODE,
   is an error at start-up,
   PLACEMAT_UNIT (granularity, default 64), PLACEMAT_COLOUR_SPAN (default the page size),
   PLACEMAT_STEP_SPAN (default the L1 set stride),
   PLACEMAT_MIN (smallest coloured size, default 65536), PLACEMAT_ADDRLOG (0, 1 = regions, 2 = blocks).
   Block k's offset is (c mod colour_span) + its step offset: unit*(splitmix64(seed ^ k) mod (step_span/unit))
   when hashed, (s*(k+1)) mod step_span when linear; k on one fixed granule from one per-process base (§5.3);
   when the block has less spare room, the offset is wrapped modulo the largest whole number of units
   that fits (0 below one unit). Wrapping keeps offsets spread rather than piling every short block on one
   value, but a wrapped block no longer has the variant's exact colour or step; every applied offset is
   logged (as a summary kept in memory and written at exit, not a line per block), and a variant where more
   than 10% of coloured blocks were wrapped is flagged in the report. */
```

Rules the allocator side must keep (learnt on Amber): take the colour from existing spare room so size classes don't change; undo it on free so free lists never see coloured blocks; keep the small-block fast path untouched (an extra check on every allocation cost allocation-bound cases 3-10% until it moved out of line); keep growth points the same (a vector growing one item at a time must still move up a class where it did); preserve the alignment guarantee, and check it with the project's own assertion build.

The same hook doubles as the shape of a project's *real* fix: if a project adopts colouring (as Amber may, with its own per-allocation steps), its choice can defer to `placemat_colour()` under the flag, so placemat can still vary it. Valgrind client requests can sit under the same flag (P004, P006).

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
| binary analysis, culprits | `ldesign.py culprits`; `loops4k.py` and `samefn.py` (only in `planning/amber-validation/tools/`) |
| pin | the order-file tools (`prof2.py`, `pick.py`, `order.py`, `pads.py`, `bininfo.py`): never committed to the Amber fork; their only copy is `planning/amber-validation/tools/` |
| data hook | the variant patch in `layouts.py`'s `hook()`, rewritten against `placemat.h` |
| bench adapter (K) | `out[]` parsing in `layouts.py` |

What stays with Amber: its configuration file, its allocator patches and their generator (they embed Amber's `src/m.c`; Amber is AGPL-3.0, so nothing derived from its source may enter MIT-licensed placemat; they are kept in the Amber fork's `pbt/patches/`), its benchmark scripts, and its machine lock (placemat should take a lock command as configuration rather than ship Amber's). Deliberate differences from the Amber stage: colours *and* steps with three variants per pad (Amber: steps only, two per pad), so the per-pad estimate weights the offset settings 2/3 (Amber 1/2) and the data test and flags split into colour and step families; batches counted in pads (Amber counted variants, two per pad); the never-zero fallback is one unit (Amber: 64·(1+z)); wrapped rather than dropped offsets when a block's spare room is short (Amber's hook dropped to 0); the interposer's k from a per-process base and one fixed granule; hashed steps by default (Amber: linear steps over a per-class slot index, which can give blocks of different sizes the same k). Amber's adapter follows the general rules (k on the fixed granule from one per-process base; hashed steps by default), so Amber is re-validated under placemat rather than reproduced exactly (user, 2026-10-05). The data-hook part of `layouts.py` (`HOOK_DECL`, `HOOK_FUNS`, `HOOK_EDITS`) quotes Amber source and is **not** extracted: placemat's hook is written fresh against `placemat.h`, and Amber's adapter (in the fork) maps it onto Amber's allocator.

**Regression test: re-validating Amber.** Amber is re-validated under placemat's general rules (hashed steps, k on the fixed granule, batches in pads, three data settings per pad), not reproduced with the prototype's exact choices (user, 2026-10-05). The runs differ in detail, so the test is the same *verdicts*, not the same numbers: (a) main against main: no changes, no code flags, data flags only where the stock placement really aliases (qgroup; the prototype's maxprior/minprior data flags were an artefact of its 16-byte colours and should not recur); (b) the branch with a placement slowdown (`grade` +8..+13% in the stock layout): placement, attributed to the stock code layout; (c) the window kernels: on main against main, data and run flags for msum, mavg and mdev with intervals containing 1; the window prototype against base, a change, step-dependent for msum, mavg and mdev but not for mmin100; (d) the pinned build against main: code spread collapses (with targeted pads for the rare windows); (e) the coloured build: the run-to-run two-speed switching gone. A different verdict is a finding to explain, not automatically a regression: the general rules were partly designed to fix weaknesses the Amber prototype had (for example, linear steps over per-class slots could not separate blocks of different sizes). Reference: [amber-validation/joint-stage-summary.md](amber-validation/joint-stage-summary.md).

## 11. Open questions

1. **Boundaries off Apple Silicon:** which code boundaries matter on x86-64 and on other arm64 cores (P006).
2. **Interposer details:** which allocation functions to interpose per platform (`malloc`, `calloc`, `realloc`, `posix_memalign`, `aligned_alloc`, C++ `operator new` through malloc), and how to keep `malloc_usable_size` honest.
3. **Lock and quiet machine:** how placemat learns that the machine is quiet (load average, a user-supplied lock command), and what it does when it isn't.
4. **Run-level perturbation:** whether to vary environment size (hyperfine, Mytkowicz) as a third axis, or only record the ASLR base as now.
5. **Automated targeted variants** (§5.4) and their weighting.
6. **Hashed steps:** they give every pair of blocks a pseudo-random relative offset, but lose the low-discrepancy coverage per pair that linear √2 steps give neighbours; validate on zstd (window plus tables) against linear steps.
7. **Exploratory: one data setting per pad with both colour and step,** split afterwards by regression on (c, s) or on features from logged addresses (which buffers share sets; which cross page boundaries). Unreliable as a default (effects sit in narrow windows), but it would halve the data-axis timing if it worked.
8. **Pinning back ends:** whether to offer BOLT or Propeller as alternatives to the order-file pipeline on Linux.
9. **Names:** whether "survey / fix / check / audit" are the right command names.

## 12. Claims and credits

As far as the survey found (PRIOR-ART.md §(a)), placemat can claim four things, each a combination or a gap rather than a new ingredient: designed joint code-and-data variants with per-case attribution and FDR control; colouring a custom allocator's large buffers as an experimental factor; the measure-pin-verify workflow; and (modestly) Apple Silicon support. It must not claim to be first at layout randomisation, multi-level designs, paired or interleaved runs, adaptive stopping, colouring, or order files, and must not claim a layout-independent speed. The README credits Mytkowicz et al. 2009, Curtsinger & Berger 2013, Kalibera & Jones 2013 (with Kalibera, Bulej & Tůma 2005 and Kalibera & Tůma 2006), Georges et al. 2007, Abedi & Brecht 2017, Laaber et al. 2020, Bonwick 1994, Kessler & Hill 1992, Afek, Dice & Morrison 2011, jemalloc, Rivera & Tseng 1998, Pettis & Hansen 1990, Ottoni & Maher 2017, Benjamini & Hochberg 1995, and the .NET 6 loop-alignment and Intel JCC-erratum guidance on boundary rules, and builds on lld's padding and shuffling options.
