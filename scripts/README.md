# scripts

## box.sh: the surveys on a dedicated Linux machine

`box.sh` repeats placemat's validation surveys (P010: SQLite, Lua, zstd; optionally Amber) on a
Linux machine of its own, x86-64 or arm64, from a clone of this repository. A machine with nothing
else on it times at the speed of the benchmarks rather than of the lock: on the shared Mac a run spent
most of its wall clock waiting (zstd's copy8: 3.7 h for about 6 minutes of CPU).

    git clone https://github.com/dalaing/placemat ~/placemat && cd ~/placemat
    scripts/box.sh setup          # packages (apt), sources, the machine overlay, the tests, a probe baseline
    sudo scripts/box.sh tune      # optional, until reboot: performance governor, turbo off, apt timers stopped
    scripts/box.sh start          # the queue, in the background; safe to log out
    scripts/box.sh status         # the queue's log and the latest run's progress

Results land in `~/placemat-work/<project>/runs/<run>.{md,json,raw.json}`, named as on the Mac, so
`placemat reanalyse` and the SURVEYs' comparisons apply as they are. The queue skips any run whose
report exists, so `start` again resumes after a reboot or a failure.

**What `setup` does.** Installs a toolchain (GCC, clang, lld, binutils), git, curl, Tcl (SQLite's
amalgamation build) and Valgrind; fetches SQLite 3.52.0 and 3.53.0 (sqlite.org) and builds the
amalgamations of the three timed check-ins from the GitHub mirror; fetches Lua 5.4.6 and 5.4.7
(checked against the SURVEY's sha256) and exports the two history commits and their parents from
lua/lua; clones zstd; writes the stride sweep's arm directories; writes the **machine overlay**
`~/placemat-work/machine.toml`; runs the tests; and seeds the noise probe's level baseline.

**The machine overlay** (`PLACEMAT_MACHINE`, placemat/config.py) holds what belongs to the machine
rather than the project, and overrides every project config: a `flock` lock in place of the Mac's
`quiet.py`, no `[target]`, the noise gate (5%, 45 minutes), the cache line, and the data spans read
from sysfs (`colour_span` the page size, `step_span` the L1D set stride: 4 KB on current x86 cores, so
the step axis covers every L1D set). The project configs in `examples/` are used unchanged.

**The queue**, in stages (`STAGES=...` to choose; `scripts/box.sh queue sweep` runs one in the foreground):

| stage | runs |
|---|---|
| `surveys` | the null runs: Lua 5.4.7, SQLite 3.53.0, zstd 1.5.7 each against itself |
| `sweep` | SQLite's page placement: the slot stride as arms (examples/sqlite/README.md) |
| `releases` | Lua 5.4.6 → 5.4.7, SQLite 3.52.0 → 3.53.0, zstd 1.5.6 → 1.5.7 |
| `history` | the performance commits the SURVEYs revisit: SQLite's page-cache and OP_Column check-ins, zstd's copy8 and decseq, Lua's 2ff34717 and b34a97a4 |
| `pagecache` | SQLite with its pages on the data axis: null run and 3.52.0 → 3.53.0 |
| `gcc` | the Lua null run with the other compiler (its own work directory) |
| `amber` | Amber's regression runs, if a fork was fetched (below) |

Builds use clang (`CC_KIND=gcc` for GCC), as the Mac and the Linux VM runs did. `DRY=1 scripts/box.sh
queue` prints every run's command instead of running it.

**Amber** is optional: its placemat adapter (`pbt/placemat/` in the fork) must be committed to a
branch first. `AMBER_GIT=<url> AMBER_REF=<branch> scripts/box.sh setup` clones it next to this
repository, where `validate.sh` finds placemat and the work directory by default. `AMBER_RUNS`
defaults to every run but (d): (d)'s order file and pads were planned for M2 binaries. DESIGN §10's
pass criteria were set from M2 results, so on another machine the runs are a survey to explain, not
a pass/fail check.

**Machine notes.** A cloud VM (dedicated vCPUs) is enough for `setup` and to check that everything
builds and runs; for timing, bare metal is better (no neighbours on the L3, and the governor and
turbo are yours to set). One timing run at a time: the lock enforces it for the queue's own runs, so
start nothing else timing-heavy on the machine meanwhile.

## CI: deterministic checks on GitHub-hosted runners

`.github/workflows/linux.yml` (tests, box) and `probes.yml` (the platform probes) run what needs no quiet
machine, on Linux x86-64 and arm64 (`ubuntu-24.04`, `ubuntu-24.04-arm`). Hosted runners are shared VMs on varying CPUs, so nothing there is a placement timing.

| job | when | what it checks |
|---|---|---|
| `tests` | every push and pull request | the unit tests with native gcc, clang, lld and GNU ld: ELF analysis, pinning, the data adapters on glibc |
| `probes` (`probes.yml`) | weekly, on demand, and on pushes that change `scripts/probes/` or the pad source | `scripts/probes/run.py`: the initial stack offset's randomisation and its steps with the environment's size (DESIGN §5.5); where a 1 GB mapping and an 8 MB malloc block land, and whether bases repeat (the region lottery, §5.4); which phases the code-axis pads move code by under each compiler (§5.2); the machine (CPU, caches, huge pages, ASLR), each compiler's alignment and landing-pad defaults and the linker's segments; which hardware counters a process may use (generic, and the front-end events: Intel's decoded-uop cache against the legacy decoders and 4K aliasing, AMD's op cache); and `scripts/probes/layout.py`, a static scan of Lua and SQLite at pads of 0-48 B (small loops across 32- and 64-byte windows, branches the JCC erratum affects); and `scripts/probes/counters.py`, Lua's interpreter across pads 0-60 B timed in rounds (pads in a fresh random order per round; within-pad noise against between-pad spread, a permutation p, the time by `luaV_execute`'s address phase) with hardware events (instructions, cycles, L1I and iTLB refills, branch mispredictions, front-end stalls) counted by `pmustat` where the runner allows it, and how well they track the time (the arm64 runners do, with `perf_event_paranoid` lowered; the x86 ones have no PMU). Results in the run's summary and as artifacts |
| `box` | weekly and on demand (x86-64) | `box.sh setup` end to end (packages, sources, SQLite amalgamations from the mirror, the overlay from real sysfs, the tests), `DRY=1 box.sh queue`, a one-batch `placemat check` of Lua's `fib` with the noise gate off (the pipeline, not the timings), and Cachegrind instruction counts for SQLite's page-cache check-in |

The probes also run locally: `python3 scripts/probes/run.py [--runs N] [--json OUT]` (macOS and Linux).

