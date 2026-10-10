# SQLite survey on Linux arm64 (P010, Linux pass)

**Question:** do SQLite's placement effects on Linux arm64 differ from those the Mac survey found
(../SURVEY.md: no meaningful spread at 3%; one 2-4% alignment-phase flag)?

## Set-up

- **Where:** the Lima VM `placemat` (Ubuntu 26.04, kernel 7.0, aarch64, 4 vCPUs, 4 GB, 4 KB pages) on the
  same Apple M2. placemat runs on the Mac and builds and times inside the VM through `[target] prefix =
  ["limactl", "shell", "placemat", "--"]`, so the Mac's machine lock (Amber's `pbt/quiet.py`, exclusive
  for timing) and noise probe (`max_noise = 0.05`, `wait = 2700`) gate every batch.
- **VM caveat:** timing inside a VM. The 4 vCPUs are threads of the Mac's hypervisor, scheduled by macOS
  on any of the M2's 8 cores (4 performance, 4 efficiency); the guest cannot pin to a core type. Guest
  page tables and the hypervisor's stage-2 translation add a TLB level the Mac runs do not have. Code
  and data placement *within* the guest are real (same addresses, same caches), but absolute times and
  any TLB-sensitive effect are the VM's, not bare-metal Linux's.
- **Toolchain:** Ubuntu clang 21.1.8 `-O2` with the same SQLite options as the Mac
  (`-DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DQS=0 -DSQLITE_OMIT_LOAD_EXTENSION`),
  GNU ld 2.46, glibc. Clang rather than GCC so that the compiler family matches the Mac's Apple clang 17
  and the pads move code in 4-byte steps as there (clang aligns arm64 functions to 4 bytes; GCC 15
  aligns to 16 with a skip limit).
- **Adapter:** `build.py` here is a copy of ../build.py with the placement check rewritten for ELF (the C
  runtime's `_start`, `frame_dummy` etc. precede the pad on ELF; the check is that no SQLite function
  precedes `placemat_pad` and the next text symbol is SQLite's and at least the pad's size on). Every
  padded link verified: a 100 B pad moves SQLite by 104 B, as on the Mac. The harness is ../sqlbench.c
  unchanged (CLOCK_MONOTONIC off macOS); 25 cases, ../cases.txt.
- **Data axis:** as on the Mac: placemat's colouring allocator through `sqlite3_config(SQLITE_CONFIG_MALLOC)`
  (`method = "allocator"`), blocks ≥ 32 KB, unit 64, colour and step spans 16 KB (four 4 KB pages here;
  the M2's L1D set stride is the same in the VM), hashed steps, run covariate `khash`. 159,252 coloured
  blocks in the null run, none wrapped. (On Linux the interposer would see SQLite's heap, since mem1.c calls
  `malloc` there, but the interposer is built on the Mac and cannot run on a target.)
- **Arms:** the same amalgamations as the Mac (`placemat-work/sqlite/3.52.0`, `3.53.0`, and the three
  check-in amalgamations).

## Runs

| run | command | design | timing | lock wait | noise probe (start/end) |
|---|---|---|---|---|---|
| `null-3.53` | `placemat survey`, 3.53.0 vs itself | 12 pads × 3 data settings, one batch | 1,323 s (593 executions) | 17,496 s | 9.2% / 4.4% (went ahead noisy after 2,700 s) |
| `3.52-vs-3.53` | `placemat run`, 3.52.0 → 3.53.0 | adaptive, 4 → 12 pads (16 cases stopped at 4) | 639 s (657) | 2,311 s | 2.0-4.2% / 3.0-9.1% |
| `checkins-0a5f27711` | `placemat run`, 0a5f27711 → bf66606d4 (page cache) and → e9f4537ea (OP_Column) | adaptive, 4 → 10 pads | 1,174 s (817) | 16,779 s | up to 16.7% (two batches went ahead noisy after 2,700 s) |

Raw data, reports and logs: `/Users/dave/work/ngn-k/placemat-work/linux/sqlite/runs/`. Work (binaries):
`.../linux/sqlite/work/`. The Mac reference for the null run is the gated `placemat survey` of the same
design (`placemat-work/sqlite/runs/null-3.53-gated.md`, finished 2026-10-06 00:06), for the release
comparison the Mac's `3.52-vs-3.53` (ungated, adaptive; ../SURVEY.md).

## Verdict

**Linux arm64 agrees with the Mac: SQLite has no meaningful layout spread at placemat's 3% threshold;
neither pinning nor colouring is called for.** The differences are of degree, and most of them come from
the VM's timing being much quieter than the Mac's (median within-pad s.d. 1.4% against 8.3% in the Mac's
gated survey), which lets placemat see smaller effects:

- **Code:** one code flag in the null run (`idx_between`, spread 5.0% / 4.3% in the two arms, p 0.00035 /
  0.002, rank p 0.00005 / 0.00025), the Mac's gated survey none. Code spreads 1.0-5.0% (Mac 1.3-7.6%).
  `idx_between`'s pattern is an 8-byte address phase of SQLite's code, slow at one phase by about 2%
  (below), and it replicates in the release run's 3.53.0 arm (+2.6%, p 0.0012). The within-round rank
  test is significant in five more null cases where the flag's test is not (`sort_limit` p 0.0021 /
  0.00005, `join4` 0.0015 / 0.00005, `ipk_lookup` 0.00085, `delete_refill` 0.0065 / 0.0092,
  `idx_text_between` 0.0091), all with spreads of 1-3.6%, under the threshold: real but small layout
  effects that the Mac's noisier timing could not resolve.
- **Data:** no colour, step or run flags in either run, as on the Mac.
- **The stock pair is trustworthy here.** In the null run no case got a *placement* verdict from the
  stock builds (the Mac's ungated null run had three false ones). In the release run the stock pair and
  the layout-averaged change differ by a median 0.4 points (at most 1.8) against 1.1 (at most 8.5) on
  the Mac, and the stock pair's correlation with Cachegrind's Ir changes is r = 0.53 here against
  −0.20 on the Mac. A one-build A/B comparison is much less misleading on this platform, mainly
  because each build's timing is less noisy, not because layout matters less.
- **Disturbed rounds:** none detected in the null or release run (the Mac's gated survey had one,
  +18.5%); three in the check-in run's first batch (+10..+14%).
- **Real changes replicate across platforms.** The two check-ins' measured effects agree with the Mac's
  to within about half a point per case (page cache: ipk_lookup −3.3% against −3.1%, both *changes*;
  OP_Column: cte_mandel −5.1% against −5.0%). The 3.52 → 3.53 release differs more between the platforms
  (inserts +0.1..+0.6% here, −1.0..−1.7% on the Mac; scan_between +3.3% here, +0.7% there).

### Null run (3.53.0 against itself, `placemat survey`)

No *change* verdicts; 23 of 25 intervals contain no change (the other two, `ins_idx_random` −0.2%
(−0.3, −0.0) and `group_by` +0.2% (+0.0, +0.3), are within ±0.2%); mean over cases +0.0% (−0.1, +0.1).
One *placement* verdict (`idx_between`, code). "If placement were ignored": no case could have shown a
false change (Mac gated survey: one, `subquery`, −3.5..+2.4%).

**`idx_between`.** `placemat culprits` (base arm): slow pads 3508, 1316, 3220 (+2.0% at their median,
worst +3.5%), "every slow pad shifts the code by 0 mod 16, every fast pad by 4, 8, 12 mod 16"; no loop
candidates. In the new arm only one pad (1316) passed culprits' slow cut, so it reported a phase at
mod 32 that any single pad satisfies (a culprits gap, below). Grouping all 12 pads by their code shift
mod 8 (a permutation test over pads; `placemat-work/linux/phase.py`): phase 0 slower by +1.9% / +1.5%
(p 0.014 / 0.044); at mod 16 +3.3% / +1.9%. The same grouping in the release run's 3.53.0 arm: +2.6%
(p 0.0012). So it is an alignment phase of SQLite's code, about 2%, of the same kind and size as the
Mac's one flag (scan_between in 0a5f27711, slow at 20 and 28 mod 32), in a different case.

**Replication between the arms.** Both arms are the same binaries, so a real layout effect makes their
per-pad medians agree. Pearson r of the two arms' per-pad stock-setting medians (`replication`
permutation p, `placemat-work/linux/repl.py`): r ≥ 0.7 in 9 of 25 cases on Linux (`idx_between` 0.81,
`ipk_lookup` 0.81, `printf_round` 0.78, `sort_text` 0.76, `sort_limit` 0.76, `delete_refill` 0.74,
`join4` 0.72, `upd_rows` 0.72, `group_by` 0.71), against 1 of 25 in the Mac's gated survey
(`sort_limit` 0.76). Caveat: the two arms of a variant run back to back in each round, so slow moments
of the machine also correlate them; this is evidence of consistency, not proof of layout.

### 3.52.0 → 3.53.0 (`placemat run`)

| case | Ir (Cachegrind, Linux clang -O2) | Mac layout-averaged | Mac stock pair | Linux layout-averaged (95%) | Linux stock pair |
|---|---|---|---|---|---|
| scan_between | +2.00% | +0.7% | −1.6% | **+3.3% (+2.5, +4.2) change** | +2.9% |
| idx_between | +3.47% | +2.4% | −5.7% | +2.6% (+1.3, +3.9), code flag | +4.1% |
| json_query | +1.58% | +0.4% | +3.5% | +1.8% (+1.0, +2.6) | +3.1% |
| join4 | +0.84% | +0.8% | −3.3% | +1.2% (+0.5, +2.0) | +0.9% |
| printf_round | −0.10% | −2.0% | −4.2% | −1.2% (−2.1, −0.2) | −1.3% |
| delete_refill | +0.42% | −0.4% | +0.5% | −0.7% (−1.3, −0.1) | −0.4% |
| ins_3idx | +0.47% | −1.0% | −4.4% | +0.2% (+0.0, +0.3) | +0.5% |
| ins_plain / ins_ipk / ins_idx_random | +0.68..+1.00% | −1.0 / −1.7 / −0.4% | −5.0 / −3.0 / −1.5% | +0.5 / +0.6 / +0.1% (intervals contain 0) | −0.3 / −1.2 / −0.1% |
| cte_mandel | +1.31% | +1.8% | +2.0% | −0.5% (−1.4, +0.4), code flag (base) | −1.7% |
| subquery | +3.51% | +0.4% | −2.9% | −0.3% (−1.2, +0.6), code flag (new) | −0.3% |
| others (14) | | | | within ±0.6%, intervals contain 0 | |

One *change* (scan_between +3.3%), four *placement* verdicts (idx_between, subquery and cte_mandel on
code flags; json_query on its stock pair, +3.1% (+2.2, +3.7) against +1.8% averaged), 20 noise;
18 of 25 intervals contain no change. Mean over cases +0.4% (+0.2, +0.5) (Mac +0.16%, Ir +1.1%).
Layout-averaged change against the Ir change: r = 0.47 (Mac 0.56); stock pair against Ir: r = 0.53
(Mac −0.20); Mac's layout-averaged against Linux's: r = 0.42.

Platform differences in the release change itself: the inserts get slightly slower on Linux (+0.1..+0.6%,
as Ir says) but 1.0-1.7% faster on the Mac (layout-averaged, intervals excluding 0); scan_between's
+2.0% Ir shows in full on Linux (+3.3%) but only +0.7% on the Mac. Same source, same compiler family,
different libc (malloc, memcpy), kernel and machine state: the release's cost is platform-dependent by
about 1-2 points per case, i.e. as large as the changes themselves.

"If placement were ignored": 3 false changes possible (idx_between −1.5..+6.9%, integrity −0.7..+3.4%,
printf_round −3.1..−0.1%) and scan_between's real change could have been missed (+2.1..+4.1%).

Culprits on the flags (each a single slow pad, so the phase each reports is not informative):
idx_between (new) pad 2268, "8 mod 16"; subquery (new) pad 1316 (+3.2%); cte_mandel (base) pad 3508
(+1.6%). The 8-byte phase grouping finds idx_between's 3.53.0 arm slow at phase 0 (+2.6%, p 0.0012, as
in the null run) and cte_mandel's 3.52.0 arm at phase 0 (+1.9%, p 0.03); subquery has no phase pattern
(it had 8 pads).

### The two timed check-ins (against their common parent 0a5f27711)

| case | page cache bf66606d4: Mac | Linux (95%) | OP_Column e9f4537ea: Mac | Linux (95%) |
|---|---|---|---|---|
| ipk_lookup | **−3.1% (−4.0, −2.2) change** | **−3.3% (−3.9, −2.6) change** | −0.1% | −0.4% |
| textpk_lookup | −2.1% (−3.0, −1.2) | −2.3% (−3.0, −1.6) | −0.1% | −0.0% |
| upd_rows | −2.4% (−3.1, −1.7) | −1.9% (−2.6, −1.1) | −0.2% | +0.1% |
| del_rows | −2.0% (−2.7, −1.4) | −2.0% (−2.9, −1.1) | −0.0% | +0.4% |
| integrity | −1.9% (−2.5, −1.3) | −1.9% (−2.6, −1.3) | +0.9% (+0.2, +1.6) | −0.0% |
| idx_text_between | −1.6% (−2.5, −0.7) | −1.5% (−2.0, −1.0) | −0.4% | −0.4% |
| upd_between | | −1.7% (−2.6, −0.8) | | −0.4% |
| ins_3idx, ins_idx_random, ins_ipk, subquery, delete_refill | −0.8 to −1.3% (Mac: ins_idx_random, ins_ipk, subquery, ins_plain) | −0.8 to −1.0% (intervals exclude 0) | ≈0 | within ±0.5% |
| cte_mandel | +0.5% | −0.1% | **−5.0% (−5.7, −4.3) change** | **−5.1% (−5.8, −4.3) change** |
| join4 | | +0.1% | | −1.2% (−2.0, −0.4) |
| scan_like | −0.0% | −0.3% | +1.1% (+0.3, +1.8) | −0.2% |
| window_fn | −0.4% | −0.0% | −0.8% (−1.3, −0.4) | −0.3% |

**The check-in results replicate across the platforms almost number for number.** The page-cache
change (`iKey % nHash` → `iKey & (nHash-1)`, zero instruction change in Cachegrind) is a real 1.5-3.3%
gain on the page-lookup cases on Linux too, ipk_lookup again a *change* (−3.3%); the OP_Column branch is
again −5% on cte_mandel and flat elsewhere. The Mac's small costs of OP_Column on scan_like (+1.1%) and
integrity (+0.9%) do not appear on Linux; Linux shows a gain on join4 (−1.2%) that the Mac did not.
Mean over cases: page cache −0.9% (−1.2, −0.6), OP_Column −0.4% (−0.7, −0.1).

No code, colour, step or run flags (the Mac flagged scan_between in the parent, 4.2%, an alignment phase;
on Linux its spread in the parent is 1.2%). Three disturbed rounds in batch 0 (+10..+14%). Stock-pair
verdicts: none. "If placement were ignored": 5 false changes possible (all page-cache arm: idx_text_between,
del_rows, scan_like, upd_rows, textpk_lookup) and 2 real changes that one layout could have missed
(ipk_lookup −4.7..−2.5%, cte_mandel −6.9..−2.4%). Here a single-layout comparison would have found the
right direction for every real gain but could have put spurious 3-5% figures beside them.


## Mac and Linux compared

| | Mac (M2, macOS, Apple clang 17, ld64) | Linux (same M2, VM, clang 21, GNU ld) |
|---|---|---|
| null: code flags | 0 (gated survey); 0 (ungated) | 1 (`idx_between`, ~2% 8-byte phase) |
| null: code spreads | 1.3-7.6% | 1.0-5.0% |
| null: rank-test p ≤ 0.01 without a flag | 1 case-arm (`join4` new 0.0016) | 7 case-arms in 5 cases |
| null: data / run flags | 0 / 0 | 0 / 0 |
| null: false *placement* (stock pair) | 3 of 25 (ungated); 0 (gated) | 0 |
| null: "a false change possible" | 1 (gated) | 0 |
| disturbed rounds | 1 (gated survey), 4 (ungated null) | 0 |
| within-pad s.d. (median over cases) | 8.3% (gated survey) | 1.4% |
| release: stock pair vs layout-averaged | median 1.1, max 8.5 points | median 0.4, max 1.8 points |
| release: r with Ir (layout-averaged / stock) | 0.56 / −0.20 | 0.47 / 0.53 |
| check-ins: page cache ipk_lookup / OP_Column cte_mandel | −3.1% / −5.0% (changes) | −3.3% / −5.1% (changes) |
| check-ins: code flags | 1 (scan_between in the parent, 4.2%) | 0 |

So SQLite's placement sensitivity is small on both platforms, of the same kind (an alignment phase
of a few percent in one or two cases), and in different cases. What differs most is measurement noise.

## placemat bugs and gaps hit (Linux pass)

See the zstd and Lua Linux surveys for the shared list; for SQLite:
1. **`culprits` phase claims with one slow pad, or at a modulus where every pad has its own phase,
   are vacuous.** With 12 pads covering 12 distinct phases mod 64, any split of slow and fast pads
   "separates" at mod 64, and a single slow pad always separates at the modulus where its phase is
   unique. culprits reported "an alignment effect" for every flagged case. Suggest: require at least
   two slow pads, and give the chance of a random split of the same sizes separating at that modulus
   (or a permutation p over pads, as `phase.py` does).
2. **`[target]` makes `memory_mb` meaningless** (it watches `limactl`'s client on the Mac, not the
   benchmark); set to 0 here.
3. The run went ahead noisy after 2,700 s (probe 9.2%) once; the VM's own timing was quiet (no
   disturbed rounds) but the gate cannot see inside the VM.
