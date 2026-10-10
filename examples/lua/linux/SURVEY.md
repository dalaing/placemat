# Lua 5.4 survey on Linux arm64 (P010, Linux pass)

**Question:** does Lua's code-placement problem on the Mac (../SURVEY.md: 2-12% code spreads, mostly the
8-byte address phase of `luaV_execute`) show on Linux arm64 too, and the same way?

## Set-up

- **Where:** the Lima VM `placemat` (Ubuntu 26.04, kernel 7.0, aarch64, 4 vCPUs, 4 KB pages) on the same
  Apple M2. placemat runs on the Mac and builds and times inside the VM through `[target]`, so the Mac's
  machine lock and noise probe (`max_noise = 0.05`, `wait = 2700`) gate every batch.
- **VM caveat:** timing inside a VM; the 4 vCPUs are hypervisor threads that macOS schedules on any of the
  M2's 8 cores (4 performance, 4 efficiency), with no pinning from the guest; guest page tables add a
  stage-2 translation. Code addresses, alignment and caches are the real ones; absolute times and
  TLB-sensitive effects are the VM's.
- **Build:** ../build.py and ../luabench.c unchanged (`-DLUA_USE_LINUX`, `-ldl`), with `LUA_CC=clang`
  (`placemat.toml` here): Ubuntu clang 21.1.8 `-std=gnu99 -O2`, GNU ld 2.46, glibc. Clang so that the
  compiler family and the 4-byte function alignment match the Mac's Apple clang 17: every 4-byte pad step
  moves all of Lua, as on the Mac. `placemat-gcc.toml` is the same with GCC 15.2 (section "GCC" below).
  `../build.py` checks every padded link (no `lua*` function before `placemat_pad`; the next function at
  least the pad's size on).
- **Data axis:** as on the Mac: hooked builds create every state with `lua_newstate(placemat_lua_alloc)`,
  blocks ≥ 64 KB, unit 64, colour and step spans 16 KB (four 4 KB pages here; the M2's L1D set stride is
  unchanged in the VM), hashed steps, covariate `khash`, `stock_placement = false`. 203,104 coloured
  blocks in the null run, none wrapped.
- **Arms:** the release trees `placemat-work/lua/lua-5.4.6`, `lua-5.4.7`; 18 cases (../bench).

## Runs

| run | command | design | timing | lock wait | noise probe | disturbed rounds |
|---|---|---|---|---|---|---|
| `null-547` | `placemat survey`, 5.4.7 vs itself | 12 pads × 3 settings, one batch | 902 s (593 executions) | 19,145 s | 2.2% / 6.4% | none |
| `rel-546-547` | `placemat run`, 5.4.6 → 5.4.7 | adaptive 4 → 12 pads (6 cases to 12) | 699 s (657) | 8,141 s | up to 11.4% (batch 1 went ahead noisy after 2,700 s) | 4 (batches 3-4, +13..+24%) |
| `null-547-gcc` | `placemat survey`, 5.4.7 vs itself, GCC 15.2 (`placemat-gcc.toml`) | 12 pads × 3 settings, one batch | 902 s (593) | 72 s | 4.5% / 3.6% | none |

Raw data and reports: `/Users/dave/work/ngn-k/placemat-work/linux/lua/runs/`; binaries in
`.../linux/lua/work/`. Mac references: the gated `null-547` (adaptive, 4-12 pads) and gated
`rel-546-547` (finished 2026-10-06 03:36) in `placemat-work/lua/runs/`, and the code-only `code16-547`.
Analysis helpers (no timing): `placemat-work/linux/fnphase.py` (per-pad medians against a function's
address mod 8/16, with a permutation p over pads), `phase.py`, `repl.py`.

## Verdict

**Lua has a code-placement problem on Linux arm64 as on the Mac (code spreads 2.5-7% per case; code flags in
4 of 18 cases of the null run, 3 of them in both arms), and the 8-byte phase of `luaV_execute` is again a cause, but
which cases it hits, and with which sign, is different.** No data-placement problem on either platform
(no colour, step or run flags on Linux; the Mac's `tbl_array` flags, an artefact of the wrapper's (0, off),
do not appear here). Built with GCC, the Linux default, the phase is frozen (16-byte function
alignment) and the phase-driven spreads vanish, but single bad layouts of up to 6.5% remain. The release
change 5.4.6 → 5.4.7 measured layout-averaged agrees between the platforms (r = 0.60 over 18 cases); the two platforms' stock-build pairs do not (r = −0.40).

### Null run (5.4.7 against itself, `placemat survey`)

No *change* verdicts; all 18 intervals contain no change (mean over cases −0.0% (−0.2, +0.1)).
Code flags (per arm): `loop_float` 6.1% / 6.1%, `array_stream` 6.8% / 7.1%, `str_pattern` 5.3% / 3.8%
(all p ≤ 0.0044 in both arms), `json` 3.2% (one arm), and `closures` flagged in the new arm only
(4.0%, p 0.0085) without a combined flag. Below the 3% threshold but with p = 0.00005-0.001 in both arms
(test and rank test): `fib` 2.5% / 2.7%, `loop_int` 2.9% / 2.9%, `spectral` 2.5% / 2.4%. No colour,
step or run flags. "If placement were ignored": 2 false changes possible (`coroutines` −6.4..+6.7%,
`nbody` −3.1..+2.2%), against 7 in the Mac's gated null run.

**Replication between the arms** (identical binaries; Pearson r of per-pad medians, `repl.py`):
`array_stream` 1.00, `loop_float` 0.98, `loop_int` 0.98, `spectral` 0.97, `fib` 0.92, `str_format` 0.75,
`closures` 0.74, `str_pattern` 0.70; not for `coroutines` (−0.01), `nbody` (0.28), `tbl_sort` (−0.34),
whose large spreads (8-13%) are noise. (The arms of a variant run back to back, so machine slow-downs
correlate them too; with r ≈ 1 and 12 pads, layout is the likely cause.)

**The 8-byte phase of `luaV_execute`** (`fnphase.py`; luaV_execute's address mod 16 in the 12 pads is
8, 4, 0, 12, … so each phase has 3 pads and phase mod 8 alternates with j). Effect of luaV_execute
≡ 0 mod 8 against ≡ 4 mod 8 (base / new arm), against the Mac's code-only run (`code16-547`, same tool):

| case | Linux, clang 21 | p (mod 8, base / new) | Mac, Apple clang 17 (code16-547) |
|---|---|---|---|
| `fib` | −0.4% / −0.4% | 0.53 / 0.45 | **+4.5% / +3.2%** |
| `loop_int` | **+2.4% / +2.3%** | 0.002 / 0.001 | **−2.8% / −1.6%** |
| `array_stream` | +1.8% / +2.0% | 0.15 / 0.11 | −1.8% / −2.8% |
| `spectral` | +1.0% / +1.0% | 0.011 / 0.008 | +0.8% / +2.2% |
| `str_pattern` | −1.4% / −1.2% | 0.030 / 0.043 | (not in code16) |
| `loop_float` | +0.4% / +0.6% | 0.96 / 0.86 | under 1.5% |

So the phase effect exists on Linux, but fib's (the Mac's clearest) is gone and loop_int's is reversed.
The interpreter is compiled by a different clang (21 against Apple's 17), so `luaV_execute`'s handlers
sit at different offsets within the function and the phase that matters is a property of that code, not
of the platform. The largest Linux spreads are not phase effects: `loop_float`'s 6.1% is one pad (3508,
+5.7% in both arms) and `array_stream`'s 6.8-7.1% is mostly pad 2664 (+4.2%) and 3508 (+2.1%); culprits
names "alignment" for both, but with one or two slow pads among 12 that claim is vacuous (gap 1 below).
`culprits` found no candidate loops for `array_stream` and only cold-function loops elsewhere; the
targeted pads it suggests (for `loop_float`: inside 3268-3544, just outside 3204/3236/3636/3668) were
not timed.

### 5.4.6 → 5.4.7 (`placemat run`)

| case | Mac layout-averaged | Mac stock pair | Linux layout-averaged (95%) | Linux stock pair |
|---|---|---|---|---|
| fib | +1.2% (+0.4, +2.1) | −1.8% | +1.6% (−0.5, +3.7), code flag 5.4.6 | **+4.2% (+3.7, +5.4)** |
| json | +1.0% (+0.6, +1.4) | +0.4% | +1.7% (+1.0, +2.4) | −0.7% |
| spectral | +0.8% (−0.1, +1.6) | −0.3% | +1.0% (+0.1, +1.8) | −0.2% |
| tbl_array | +1.7% (+0.7, +2.6) | +2.5% | +0.6% (−0.2, +1.4) | +0.3% |
| tbl_sort | −2.4% (−3.1, −1.7) | −0.4% | −0.8% (−1.8, +0.3) | −0.3% |
| tbl_hash | −0.5% | +0.4% | +0.7% (+0.4, +1.0) | +0.8% |
| coroutines | +1.2% (+0.3, +2.1) | +2.2% | +0.1% | **−5.4% (−6.6, −3.1)** |
| loop_float | +0.8% | −0.1% | +0.5%, code flag 5.4.7 (15.8%) | **−3.1% (−3.9, −2.2)** |
| array_stream | +0.3% | +4.9% | +0.2%, code flag 5.4.7 | −2.1% |
| loop_int | −0.1% | +3.5% | −0.3%, code flags both | +0.5% |
| vararg | −0.1% | −3.3% | +0.3% | +2.3% |
| sieve | +0.6% | +5.1% | +0.3% | −0.1% |
| others (6) | within ±1.1% | | within ±0.7% | |

No *change* verdicts (all effects under 3%), 5 *placement* (fib, loop_int, loop_float, array_stream on
code flags; coroutines on its stock pair, weak), 13 noise; 13 of 18 intervals contain no change.
Layout-averaged changes Mac vs Linux: r = 0.60; stock pairs Mac vs Linux: r = −0.40; stock pair vs
layout-averaged within each platform: r = 0.14 (Mac), 0.33 (Linux). "If placement were ignored": 6 false
changes possible on Linux (fib −2.6..+5.8%, loop_float −0.7..+5.8%, coroutines, nbody, str_concat,
spectral), 11 on the Mac.

**fib, the confound in one case.** On Linux 5.4.6's `fib` has the Mac-like phase effect: `culprits`
(base arm) found 5 slow pads (+6.1% at their median), all with `luaV_execute` ≡ 0 mod 8, and code flag
13.0% (p 0.00015); 5.4.7's `fib` has none (null run above). The stock 5.4.6 build has
`luaV_execute` ≡ 12 mod 16 (the fast phase) and the stock 5.4.7 build ≡ 0 mod 16 (where 5.4.7 does not
care), so the stock pair says 5.4.7 is 4.2% slower; over layouts it is 1.6% slower (−0.5, +3.7). On the
Mac the stock pair said −1.8% and the layout average +1.2%. A one-build A/B gets fib's sign or size
wrong on both platforms, differently on each.

### GCC (`null-547-gcc`: the Linux default compiler)

GCC 15.2 `-O2` on aarch64 aligns functions to 16 bytes (with a skip limit), so a pad moves Lua's code in
16-byte steps (the shifts are 2464, 960, 3424, …) and `luaV_execute` sits at ≡ 12 mod 16 in every pad
build and in the stock build: the 8-byte phase is frozen, as `-falign-functions=16` froze it on the Mac
(`a16-547`).

- **The phase effects are gone.** `fib` 2.1% / 1.9% (p 0.91 / 0.2; arms' per-pad medians r = −0.17),
  `loop_int` 1.6% / 1.3% (r = −0.61), `spectral` 0.9% / 1.0%, `str_pattern` 2.4% / 1.7%: no replicated
  layout effect, against 2.5-5.3% with clang in the same VM.
- **Rare bad positions remain.** Code flags in both arms: `loop_float` 6.8% / 6.7%, `tbl_sort` 7.0% /
  7.3%, `array_stream` 3.1% / 3.7%. Each is one pad: 1316 makes `loop_float` +6.5%, `nbody` +4.5%,
  `array_stream` +2.9% and `spectral` +0.7% slower in both arms (r = 1.00 for loop_float); 2268 makes
  `tbl_sort` +2.3% slower. With clang, `loop_float`'s one bad pad was 3508 (+5.7%). These look like
  §5.6's rare windows (a hot loop or handler crossing a boundary at one position in the 4 KB period), not
  phases; `culprits` lists only cold-function loops (`auxstatus`, `db_getlocal`, …) for them, and the
  targeted pads it suggests (1104-1432 and 1040/1072/1544/1576 around 1316) were not timed.
- No colour, step or run flags; all intervals but one (`vararg` +0.4%) contain no change; "if placement
  were ignored": 2 false changes possible (`tbl_hash`, `coroutines`).

So on Linux the compiler decides most of Lua's placement sensitivity: GCC's 16-byte alignment removes
the phase effect that clang's 4-byte alignment exposes, and what is left is a few rare layouts per build
(one pad in twelve, up to 6.5%) that a pin would have to avoid by position, not by alignment.


## Mac and Linux compared

| | Mac (M2, macOS, Apple clang 17, ld64) | Linux (same M2, VM, clang 21, GNU ld) |
|---|---|---|
| null: code flags (both arms) | 5 cases (fib, loop_int, loop_float, array_stream, nbody) + tbl_sort in one | 3 cases (loop_float, array_stream, str_pattern) + json, closures in one |
| null: code spreads | 1-12% | 2-7% (tbl_sort's 13% and coroutines' 11% one arm, not replicated) |
| luaV_execute mod-8 phase | fib +4.5%, loop_int −2.8%, array_stream −2%, spectral +1-2% | fib none, loop_int +2.4%, array_stream +2% (n.s.), spectral +1%, str_pattern −1.3% |
| data flags | tbl_array colour+step (the wrapper's (0, off) at page + 64) | none |
| run (khash) flags | none | none (covariate unavailable: k hashes do not repeat) |
| null: "false change possible" | 7 of 18 | 2 of 18 |
| GCC (Linux only) | | phase frozen (luaV_execute ≡ 12 mod 16): fib/loop_int/spectral flat; one-pad spikes remain (loop_float +6.5% at pad 1316) |
| within-pad s.d. (median) | 2.0% | 1.8% |
| release: layout-averaged vs the other platform | r = 0.60 | |
| release: stock pair vs the other platform | r = −0.40 | |

## placemat bugs and gaps hit

1. **`culprits` phase claims are vacuous with one or two slow pads.** With 12 pads covering 12 phases
   mod 64, any slow/fast split separates at mod 64, and a lone slow pad separates at the first modulus
   where its phase is unique; every flagged Lua case got "an alignment effect". A permutation p over
   pads, or the chance that a random split of the same sizes separates at that modulus, would tell a
   real phase (fib in 5.4.6: 5 of 6 phase-0 pads slow) from an arbitrary split (loop_float: one pad).
   Also useful: grouping by a named hot function's address (as `fnphase.py` does with `luaV_execute`)
   rather than by the pad's shift, since with a stock build at another phase the shift is relative.
2. **The adapter's scripts in a parent directory are not in the build cache key.** This config lives in
   `linux/` and runs `../build.py` with `../luabench.c`; placemat hashes only the config directory's
   `.py/.sh/.h/.c` files, so edits to ../build.py or ../luabench.c (which the Mac survey's agent could
   make) would reuse stale binaries here. Hash the files named in `[build] command` too, or let
   `[build] inputs` name files relative to the config directory.
3. **`[target]` makes `memory_mb` meaningless** (it watches `limactl`'s client on the Mac), so it is 0 here.
4. **The noise gate cannot see the VM:** one batch went ahead noisy (11.4%) after 2,700 s, and the release
   run had four disturbed rounds; the probe runs on the Mac, not in the guest where the benchmark runs.
