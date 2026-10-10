# placemat

placemat is a layout-aware benchmarking tool for compiled programs. It answers one question about a change: **is this timing difference caused by the change, or by where the code and data happened to land?**

A change that alters one function's size moves every function linked after it. Where a program's large buffers land depends on the allocator and on address-space layout randomisation (ASLR): they may move from run to run, or sit at the same unlucky offset every time. Either can make an unrelated benchmark faster or slower by more than the change itself. Two examples come from Amber, an interpreter for the K array language, on Apple M2. A date benchmark ran 10% slower in a build that left two of its hot loops straddling a 4 KB address boundary, and ran at normal speed once they were moved off it (why such a crossing costs time on M2 is not known). Some moving-window kernels (sum, average, deviation) ran 2 to 2.8 times slower than when their buffers were moved apart: the large buffers they used together all sat at the same offset modulo 16 KB, so they mapped to the same L1 cache sets (*L1 set conflicts*). A single timing of each build cannot tell such effects from real ones. The systematic error this causes is *measurement bias* (Mytkowicz et al., 2009).

placemat builds your program many times with its code moved, runs each build with its large buffers moved, and times your benchmarks each time. Your project supplies a build script and a benchmark command that follow placemat's protocols. For the data axis it also picks a *data method* (see How it works).

placemat is designed to do three things ([Status](#status) says what is built today):

1. **Measure.** Time the base and each change across a designed, reproducible set of layouts, so the result no longer depends on one layout. A *case* is one named benchmark measurement. placemat attributes each case's *spread* (how much its time varies across layouts and executions) to code placement, data placement (placemat's designed offsets), or a run-level cause: something that differs between executions, such as where ASLR put the heap.
2. **Fix (optional).** Take control of placement. In Kalibera & Jones's terms, an ordinary build leaves placement *uncontrolled*: fixed, unknown, and a source of bias. Measuring makes it *random* in their sense (varied over many layouts and averaged); fixing makes it *controlled*. Pin hot code with a linker order file (a list telling the linker which functions to place first), function alignment and padding. The padding keeps hot loops off 4 KB boundaries, and where it can, each hot function's *hot entry span* (from its entry to its hot code). And, as a change to your own allocator, offset large allocations so that buffers used together stop sharing L1 sets.
3. **Watch.** Once fixed, check changes cheaply, and repeat the full measurement now and then to catch what the fixes no longer cover.

It is not a profiler, and not an optimiser: it does not search for the fastest layout. It never claims a layout-independent "true speed". It reports effects with intervals over layouts, next to the result for the **stock** build, the one you actually ship. (When hot code is pinned, only the unpinned code moves, so the interval is over layouts that share that pinning, not over all layouts.)

A full measurement is expensive. placemat was extracted from a prototype built for Amber. Comparing two versions, that prototype spent about 12-50 s per case, with each code layout timed at two data settings: at that rate, an estimated 40 minutes to three hours for 200 cases. placemat's default of three data settings per code layout should take about 1.5 times as long. The design is in [planning/DESIGN.md](planning/DESIGN.md); open work is in [planning/ISSUES.md](planning/ISSUES.md).

## How it works

You give placemat two or more **arms** (the base and one or more changes: git revisions or source directories) and a list of cases.

- **Code axis.** Each arm is built several times. Each build links an unused *pad function* of a different size before the code under study, so everything after it moves. One such build is called a **pad**, after its pad function. Pad *j*'s pad function has the same size in every arm, so the arms can be compared pad by pad. The sizes come from a low-discrepancy sequence (deterministic, evenly spread and extendable) that spreads the pads over offsets modulo 4 KB (the code boundary, configurable) and over the sixteen 4-byte positions within 64 bytes.
- **Data axis.** Each pad is timed at up to three **data settings**, which need no rebuild. The **stock setting** adds no offset (with the colouring allocator or the interposer it is still not the shipped placement: see Verdicts). A **colour** is one constant offset added to every large allocation (64 KB or more by default), moving the data relative to page boundaries, smaller allocations, static data and the stack. A **step** gives each large block its own offset (by default reproducible and pseudo-random), moving buffers relative to each other and so changing their L1 set conflicts. A *data method* applies the offsets: a hook, if the project's own allocator places large blocks; placemat's colouring allocator, if it takes a pluggable allocator; placemat's malloc interposer, if it uses the C library's malloc (on macOS the interposer cannot load into SIP-protected binaries, or into hardened-runtime binaries without the allow-dyld-environment-variables entitlement); or none, which turns the data axis off. A hook or plugged-in allocator is compiled only into the padded builds; your shipped binary is unchanged. One pad timed at one data setting is a **layout variant**.
- **Rounds.** Pads are timed in batches: a first batch (4 pads by default), then, for each case whose interval (see Estimate) is still wider than ±1%, 2 more pads at a time, up to 12 (defaults with the data axis on; with it off, 8, 4 and 24). Each batch runs 7 rounds by default. A **round** times each layout variant of the batch once for every arm. Layout variants come in a fresh random order, and each variant's arms run back to back, also in random order. Because a variant's arms run back to back, comparing them round by round cancels slow machine drift.
- **Stock builds.** Every layout variant carries a pad function, and the hook, allocator or interposer if one is used, so none is the shipped build. The stock builds (no pad, and no hook, allocator or interposer) are therefore timed as well, as one more slot in each round of the first batch only.
- **Estimate.** Each change's **effect** is its time as a ratio to the base's (1.05 means 5% slower; 1 means no change), averaged geometrically over all its layout variants, with a t interval over pads: pads, not layout variants, are the independent units. Because stopping is adaptive, the 95% intervals are only approximately 95%: in two null runs of the prototype on Amber (the base timed against itself), 7 of 104 intervals (6.7%) missed "no change", which is consistent with that. Details: [DESIGN §5.7](planning/DESIGN.md#55-statistics-and-verdicts).
- **Flags.** Permutation tests ask whether a clear part of a case's spread is due to code, colour, step, or a run-level covariate (a per-execution measurement). A case is flagged for one of these when p ≤ 0.01, the result survives Benjamini-Hochberg false-discovery control across cases (which keeps the expected share of chance flags at or below 5% for each kind of flag, however many cases are tested), and the size of that part of the spread (the code spread, the colour or step contrast, or the run covariate's effect) exceeds the case's threshold (see Verdicts). The run-level covariate is, by default, the address where ASLR placed the allocator's region (for the colouring allocator and the interposer, a hash of the blocks' positions). It is read from the data method's log, so there is no run test when the data axis is off.

### Verdicts

For each case and each arm against the base:

- **change**: the interval excludes 1 (no change), and the effect exceeds the threshold (3% by default; 10% for cases whose base time, over all their iterations, is under 5 ms).
- **placement**: not a change, but placement moves this case's time: the case is flagged, or the stock builds compared directly (base against the changed arm, round by round) differ measurably: their interval (a bootstrap over the first batch's rounds) excludes 1 and the difference exceeds the threshold. That comparison is one layout timed over the first batch's rounds; it says nothing about other layouts.
- **noise**: neither. This means no effect above the threshold was found, not that there is none: check the interval's width.

A verdict lists what the case was flagged for (code, colour, step or run): that is the attribution of its spread. If the stock builds' difference is measurable and its interval does not overlap the interval of the effect computed from the stock-setting layout variants alone, the difference is also attributed to the **stock code layout** when the stock setting places data as the shipped program does, so that only code differs; otherwise to the **stock build**, with code and data not separated. `stock_placement` in placemat.toml says which applies: by default true for a hook or when the data axis is off, false for the colouring allocator and the interposer, which add a header at every setting.

A verdict can also be **colour-dependent** or **step-dependent** (either also marks it *data-dependent*): the effect differs measurably between the stock setting and the colour (or step) setting.

Every placemat run keeps its raw timings, so a report can be recomputed with new statistics without timing again.

## Getting started

> **TODO.** A worked example on a small open-source project will go here once the first adapter outside Amber is finished (zstd, SQLite and Lua are in progress in [examples/](examples/)).

A project supplies four things:

| what | where it is described |
|---|---|
| `placemat.toml`: how to build, how to run the benchmarks, the data method, the thresholds | the docstring of [placemat/config.py](placemat/config.py) |
| a build script for the **build protocol**: placemat runs it once per pad of each arm, and once per arm for its stock build, with the pad's source file and other settings in environment variables | the docstring of [placemat/build.py](placemat/build.py) |
| a benchmark command for the **benchmark protocol**: one `name value iterations [unit]` line per case (adapters exist for Google Benchmark and hyperfine JSON, and for K `out[]` scripts) | the docstring of [placemat/bench.py](placemat/bench.py) |
| for the data axis, either a call to [placemat/data/placemat.h](placemat/data/placemat.h) in the project's allocator, an allocator API that can take placemat's colouring allocator ([placemat_alloc.h](placemat/data/placemat_alloc.h)), or nothing (the malloc interposer) | [placemat/data/README.md](placemat/data/README.md) |

Requirements: Python 3.11 or later (standard library only), a C compiler, and `nm`/`otool` (macOS) or `nm`/`objdump` (Linux).

## Commands

> **Not settled.** The command names follow DESIGN §3's modes, which are still an open question (DESIGN §11 item 9). Today `check` and `survey` are `run` with different defaults, and there is no single `fix` command yet (pinning is the `pin` subcommands).

```sh
python3 -m placemat run --config placemat.toml --arm base=git:main --arm change=git:my-branch --cases-file cases.txt
python3 -m placemat reanalyse .placemat/runs/NAME.raw.json --out NAME.md   # the report again, from raw timings
python3 -m placemat culprits .placemat/runs/NAME.raw.json 'suite: case'    # loops crossing 4 KB only in a case's slow pads
python3 -m placemat twospeed .placemat/runs/NAME.raw.json                  # run-to-run switching between two speeds
python3 -m placemat affected BASE_BINARY CHANGE_BINARY profile.json   # cases whose reachable code changed
python3 -m placemat check --config placemat.toml --arm base=… --arm change=… --cases-file cases.txt --affected profile.json
python3 -m placemat design --pads 12                             # the designed variants
python3 -m placemat binary {funcs,loops,shifts,samefn} ...       # binary analysis (Mach-O, ELF)
python3 -m placemat pin {profile,pick,order,spans,pads,verify} ...    # pinning hot code
```

Each run writes `NAME.raw.json`, a Markdown report `NAME.md`, and the JSON behind the report, under `.placemat/runs/` next to the configuration (or `--out`).

## Timing on a shared machine

> **TODO.** What placemat should recommend here is not settled yet.

Placement effects are often a few percent, so the machine must be quiet. placemat can take a machine lock (a command named in the configuration) for each batch of timings, wait for the load average to fall, and wait for a **noise probe** (a fixed workload timed several times) to come out steady in two readings in a row (judged on the middle half of 21 timings, so a stall or two does not fail it). `placemat noiselog` records the probe once a minute while timing runs (no lock; about 0.3 s of one core per reading), so a report can show what the machine was like during each batch, and reports mark batches that went ahead noisy. The example configurations gate at 10%. The probe's results are recorded with each run, so a run that went ahead on a noisy machine says so. On a shared Apple M2 under load, the probe itself varied by 10-20% between repetitions; a plausible cause, not yet shown, is threads sometimes being scheduled on efficiency cores.

## Status

> **This section changes as work lands.** Current state: [planning/PROGRESS.md](planning/PROGRESS.md).

- **Platforms.** Developed and validated on macOS arm64 (Apple M2). Binary analysis, pinning and the data adapters are tested on Linux arm64 (ELF; lld and GNU ld), and pinning on macOS with ld64. Which code boundaries matter on x86-64 and other arm64 cores has not been measured; [scripts/box.sh](scripts/README.md) repeats the surveys on a dedicated Linux machine, with a machine overlay (`PLACEMAT_MACHINE`) for that machine's lock and cache geometry.
- **Validation.** placemat was extracted from tooling built in a fork of the Amber K interpreter, and is being re-validated there (DESIGN §10). Further validation on zstd, SQLite and Lua is in progress.
- **Not yet built:** automated targeted variants for rare bad layouts (`culprits` suggests the pads to try); detection of allocation-sequence changes when choosing which cases a change affects; threaded benchmarks.

## Tests

```sh
python3 -m unittest discover -s tests -t . -v
```

## Claims and credits

placemat combines known ingredients. As far as our prior-art survey found ([planning/PRIOR-ART.md](planning/PRIOR-ART.md)), its new contributions are combinations or gaps filled:

- designed joint code-and-data layout variants, with per-case attribution and false-discovery control;
- colouring a custom allocator's large buffers as an experimental factor;
- measuring layout sensitivity, then pinning hot code with an order file and pads against a boundary rule, then using the same harness to show that the code-layout spread has collapsed while real changes still show;
- and, modestly, support for Apple Silicon.

It does not claim to be first at finding layout sensitivity, layout randomisation, multi-level experimental designs (including the build level as a random effect), paired or interleaved runs, adaptive stopping, allocator or page colouring, or linker order files.

It builds on:

- Mytkowicz, Diwan, Hauswirth & Sweeney, *Producing wrong data without doing anything obviously wrong!* (ASPLOS 2009);
- Curtsinger & Berger, *Stabilizer: statistically sound performance evaluation* (ASPLOS 2013);
- Kalibera & Jones, *Rigorous benchmarking in reasonable time* (ISMM 2013), with Kalibera, Bulej & Tůma, *Benchmark precision and random initial state* (SPECTS 2005), and Kalibera & Tůma, *Precise regression benchmarking with random effects* (EPEW 2006);
- Georges, Buytaert & Eeckhout, *Statistically rigorous Java performance evaluation* (OOPSLA 2007);
- Abedi & Brecht, *Conducting repeatable experiments in highly variable cloud computing environments* (ICPE 2017);
- Laaber, Würsten, Gall & Leitner, *Dynamically reconfiguring software microbenchmarks* (ESEC/FSE 2020);
- Bonwick, *The slab allocator: an object-caching kernel memory allocator* (USENIX Summer 1994);
- Kessler & Hill, *Page placement algorithms for large real-indexed caches* (ACM TOCS 1992);
- Afek, Dice & Morrison, *Cache index-aware memory allocation* (ISMM 2011);
- jemalloc's `cache_oblivious` option (4.0, 2015);
- Rivera & Tseng, *Data transformations for eliminating conflict misses* (PLDI 1998);
- Pettis & Hansen, *Profile guided code positioning* (PLDI 1990);
- Ottoni & Maher, *Optimizing function placement for large-scale data-center applications* (hfsort and C3; CGO 2017);
- Benjamini & Hochberg, *Controlling the false discovery rate: a practical and powerful approach to multiple testing* (JRSS B 1995);
- and the .NET 6 loop-alignment work and Intel's JCC-erratum guidance on boundary rules.

Related, planned but not yet used: lld's `--shuffle-sections` (as the ELF code axis, limited to `.text*`) and `--randomize-section-padding` (which also moves data, so at most a combined axis).

## Licence

MIT, © Dave Laing: see [LICENSE](LICENSE).
