# zstd survey on Linux arm64 (P010, Linux pass)

**Question:** do zstd's placement effects on Linux arm64 differ from the Mac's (../SURVEY.md: no placement
problem worth fixing; real code effects of 1-2.5%, under the 3% threshold, seen only in a quiet run)?

## Set-up

- **Where:** the Lima VM `placemat` (Ubuntu 26.04, kernel 7.0, aarch64, 4 vCPUs, 4 GB, 4 KB pages) on the
  same Apple M2. placemat runs on the Mac and builds and times inside the VM through `[target]`, so the
  Mac's machine lock and noise probe (`max_noise = 0.05`, `wait = 2700`) gate every batch.
- **VM caveat:** timing inside a VM; the 4 vCPUs are hypervisor threads that macOS schedules on any of the
  M2's 8 cores (4 performance, 4 efficiency) with no pinning from the guest, and guest memory goes through
  a second (stage-2) translation. Code addresses, alignment and caches are the real ones; absolute times
  and TLB-sensitive effects are the VM's.
- **Build:** ../build.py and ../zbench.c unchanged, with `ZB_CC=clang` (`placemat.toml` here): Ubuntu
  clang 21.1.8 `-O3`, `-DXXH_NAMESPACE=ZSTD_ -DZSTD_LEGACY_SUPPORT=0 -DZSTD_DISABLE_ASM`, GNU ld 2.46,
  glibc, single-threaded libzstd. Clang (not the Linux default GCC) so the compiler family and the 4-byte
  function alignment match the Mac's Apple clang 17. ../build.py verifies every padded link
  (`placemat_pad` before all of libzstd, the next symbol at least the pad's size on); about 3% of
  functions (the C runtime's) precede the pad.
- **Data axis:** as on the Mac: placemat's colouring allocator through `ZSTD_customMem`
  (`method = "allocator"`), blocks ≥ 64 KB, unit 64, colour and step spans 16 KB (four 4 KB pages here;
  the M2's L1D set stride is unchanged in the VM), hashed steps, covariate `khash` (reported unavailable:
  k hashes do not repeat, as on the Mac). 27,696 coloured blocks in the null run, none wrapped.
- **Arms:** git revisions of `placemat-work/zstd/src`, as on the Mac.

## Runs

| run | command | arms | design | timing | lock wait | noise probe | disturbed rounds |
|---|---|---|---|---|---|---|---|
| `null` | `placemat survey` | v1.5.7 vs itself | 12 pads × 3 settings, one batch | 904 s (593 executions) | 10,388 s | 3.1% / 3.9% | none |
| `v156-v157` | `placemat run` | v1.5.6 → v1.5.7 | adaptive 4 → 12 (15 cases stopped at 4) | 354 s (657) | 2,510 s | 0.5-3.7% / 3.4-14.1% | none |
| `copy8` | `placemat run` | 7eefc221 → 1e9d2006 | adaptive (13 cases at 4 pads) | 425 s (657) | 5,467 s | 1.0-3.3% / 1.8-12.3% | none |
| `decseq` | `placemat run` | 3c3b8274 → a28e8182 (6 decompression cases) | adaptive 4 → 6 | 185 s (321) | 2,713 s | 2.5-3.5% / 2.1-2.2% | none |

Raw data and reports: `/Users/dave/work/ngn-k/placemat-work/linux/zstd/runs/`; binaries in
`.../linux/zstd/work/`. Mac references: `placemat-work/zstd/runs/` (`null`, ungated; `v156-v157`, `copy8`,
`decseq`, gated). The Mac's gated null survey (`null-gated`) was still running when this was written.
Analysis helpers (no timing): `placemat-work/linux/phase.py`, `repl.py`.

## Verdict

**Same verdict as the Mac: no placement problem worth fixing in zstd on Linux arm64.** Code spreads are
1.4-6.7% per case in the null run (the Mac's ungated null 1.6-33%), with two code flags (`c6-text` in both
arms, ~6%, and `c1-text` in one), three colour flags of 2.5-6% at their worst pad, and real, replicated
layout effects of 1-3% in most cases, below the 3% threshold, the size the Mac's one quiet run (copy8)
showed. The VM's timing is far quieter than the Mac's was (median within-pad s.d. 1.7% against 22% in
the Mac's ungated null), so these small effects are resolved here, not larger.

Where the platforms differ is not placement but **what a change does**: the AArch64 `ZSTD_copy8` change
(1e9d2006), flat on the Mac (Apple clang 17), makes every decompression case **3-6% slower** on Linux
with clang 21: a real, code-generation effect, four *changes*, no code flags. The AArch64 `ZSTD_decodeSequence` rewrite (a28e8182) is a real 10-15% gain on both platforms.

### Null run (v1.5.7 against itself, `placemat survey`)

No *change* verdicts; all 16 intervals contain no change; mean over cases +0.0% (−0.1, +0.1).

| case | code spread (p, rank p) base / new | colour / step | culprits / phase |
|---|---|---|---|
| `c6-text` | **6.0% (0.0001, 0.00045) / 6.5% (0.0002, 0.0004)**, flag | **colour flag** (p 0.0078; typical +0.9%, worst pad 3.4%) | slow pads 1316, 2268, 1712, 76 (+2.2%, worst +5.1%); no loops; no clean 8/16-byte phase (p 0.07-0.37) |
| `c1-text` | 3.4% (0.038, 0.0065) / **4.2% (0.0006, 0.0022), flag** | | one slow pad (3508, +1.8%) |
| `c3-rand` | 6.2% (0.0092, 0.0014), flag (one arm) / 5.3% | **colour flag** (p 0.0001; worst pad 6.2% / 5.8%) | slow pads 3508, 3776 (+2.4%) |
| `d3-rec` | 2.8% / 3.4% | **colour flag** (p 0.003; worst pad 2.5% / 3.5%) | |
| `d12-text` | 2.8% / 3.1% (0.0051, 0.017), flag (one arm) | | |
| `cneg5-text` | 2.8% (0.0069, 0.02) / 2.6% (0.0021, 0.0002) | | |
| `c3-rec` | 2.7% (0.02, 0.017) / 1.4% | | phase 0 mod 8 slower by 1.0% (p 0.01) |
| `d3-rand` | 8.5% / 5.1% (n.s.) | | the 8 MB copy case; the Mac's spreads were 27-33% |
| the other 9 | 1.8-3.9%, p ≥ 0.03 | | |

Three of the flags are *colour* flags, which the Mac's survey runs never raised: the coloured setting
(one colour offset for all blocks ≥ 64 KB) differs from the stock setting by up to 3.4-6.2% at some pads.
The typical (median) colour effect is under 1% in each, so these are a few bad colour/pad combinations,
not a systematic data effect; `c3-rand` (incompressible input, so mostly copies of 8 MB) is the clearest.
No step flags, as on the Mac: giving each buffer its own offset changes nothing measurable, which fits
zstd's design (one workspace allocation holds all the compressor's tables; ../README.md).

**Replication between the arms** (identical binaries; `repl.py`, Pearson r of the per-pad medians):
r ≥ 0.5 in 12 of 16 cases, highest `cneg5-text` 0.94, `d3-text` 0.80, `c6-text` 0.77, `c3-rec` 0.77,
`d3-rec` 0.77, `d12-text` 0.75. So most cases have a reproducible layout effect of 1.4-3% (pad to pad),
just as the Mac's quiet copy8 run found (1-2.5%), but only c6-text's (one pad, 2268, +4.7%) reaches the
threshold. The arms of a variant run back to back, so machine slow-downs correlate them too.

"If placement were ignored": 2 false changes possible (`d3-rand` −4.8..+1.0%, `c3-rand` −1.8..+3.6%),
against 4 in the Mac's (ungated) null. Culprits found loop candidates only for c1-text (53 loops, in
`FSE_normalizeCount`, `HUF_buildCTable_wksp`, the match finders; 9.6% of layouts cross; targeted pads
3288-3580 not timed); its phase claims (e.g. c6-text "24, 40, 48, 60 mod 64") are vacuous with 12 pads
covering 12 phases mod 64 (gap 1 below). The permutation test over pads (`phase.py`) finds no clean
8- or 16-byte phase in any zstd case (best: c3-rec, phase 0 mod 8 slower by 1.0%, p 0.01; a weak
tendency for phase 0 mod 8 to be slower in the compressors).

### v1.5.6 → v1.5.7

| case | Mac layout-averaged | Mac stock pair | Linux layout-averaged (95%) | Linux stock pair |
|---|---|---|---|---|
| c3-rand | **−23.5% change** | −22.8% | **−22.8% (−23.1, −22.6) change** | −21.9% |
| c3-text | **+3.3% (+2.4, +4.1) change** | +3.6% | +2.3% (+2.0, +2.6) | +2.2% |
| c3-rec | +1.8% (+0.9, +2.7) | −2.0% | +1.6% (+1.1, +2.1) | +1.9% |
| c12-text | +1.0% | −0.8% | +0.9% (+0.5, +1.3) | +0.8% |
| c1-text | +0.9% | −0.1% | +0.6% (+0.5, +0.8) | +0.8% |
| c19-text | +0.8% | +0.4% | +0.4% (+0.2, +0.6) | +0.1% |
| cneg5-text | −0.3% | −1.7% | +0.5% (+0.4, +0.7) | +0.3% |
| the decoders | +0.2..+0.6% | | −0.3..+0.3% | |

One *change* on Linux (c3-rand), no flags; mean over cases −1.1% (−1.2, −1.0), as on the Mac (−1.1%).
c3-text's +2.3% is just under the threshold here (the Mac's +3.3% just over). The layout-averaged changes
agree between the platforms (r = 0.89 over the 15 cases other than c3-rand), the stock pairs do not
(r = 0.16): on the Mac the stock pair differed from the layout-averaged change by a median 0.5 points (up
to 3.8), here by 0.2 (up to 1.2). Code spreads on Linux 0.3-1.5% except `d3-rand` (12.7% / 14.5%,
not significant); the Mac's 3.5-17%. 11 of 16 Linux intervals exclude no change (small, consistent
compressor slowdowns of 0.4-1.6%), against 5 on the Mac.

### The shortlisted history commits

**`1e9d2006` "AArch64: Use better block copy8" against `7eefc221`** (`copy8`):

| case | claim (Neoverse V2, clang 19/20, GCC 14/15) | Mac, Apple clang 17 | Linux, clang 21 (95%) | Linux stock pair |
|---|---|---|---|---|
| d1-text | +0.03..+1.8% faster | +0.6% (+0.3, +0.9) slower | **+3.7% (+3.6, +3.8) slower, change** | +3.5% |
| d3-text | | +0.4% | **+5.8% (+4.9, +6.7) slower, change** | +5.0% |
| d12-text | | +0.2% | **+5.8% (+5.3, +6.2) slower, change** | +6.1% |
| ds3-text | | +0.3% (+0.0, +0.7) | **+5.3% (+5.1, +5.6) slower, change** | +5.5% |
| d3-rec | | +0.3% | +2.9% (+2.4, +3.3) slower (*placement* on its stock pair) | +3.4% |
| d3-rand | | | +0.5% (−1.2, +2.2) | −3.6% |
| compression (10 cases) | | within ±0.6% | within ±0.2% | |

Mean over cases +1.4% (+1.2, +1.6) on Linux (Mac +0.1%). No code, colour, step or run flags; code
spreads 0.1-2.9% except d3-rand. This is not placement: the effect is the same at every pad and setting
and in the stock builds. It is code generation: with clang 21 the new `ZSTD_copy8` (`memcpy` instead of
NEON `vld1`/`vst1`) grows the sequence decoders (`placemat binary samefn` on the two stock builds:
`ZSTD_decompressSequencesLong` 8,756 → 9,984 B, `ZSTD_decompressSequencesSplitLitBuffer` 5,572 →
6,332 B, `ZSTD_decompressSequences` 2,536 → 2,736 B, `ZSTD_safecopy` 772 → 1,004 B; 499 of 506
functions unchanged). The commit's claim was measured on another core with older compilers; on the M2
it is flat with Apple clang 17 and a 3-6% loss with clang 21. The single-build A/B (stock pair) gets this
one right on Linux, because the effect is large against a 1-2% layout spread.

**`a28e8182` "AArch64: Improve ZSTD_decodeSequence performance" against `3c3b8274`** (`decseq`, the six
decompression cases):

| case | Mac, Apple clang 17 | Linux, clang 21 (95%) | Linux stock pair |
|---|---|---|---|
| d1-text | −10.8% (−11.6, −10.1) | **−9.8% (−10.4, −9.3) change** | −8.8% |
| d3-text | −11.8% (−12.5, −11.1) | **−14.7% (−15.3, −14.1) change** | −15.7% |
| d12-text | −12.5% (−13.1, −12.0) | **−14.7% (−15.3, −14.0) change** | −15.9% |
| d3-rec | −14.9% (−15.6, −14.2) | **−14.6% (−15.3, −13.9) change** | −13.0% |
| ds3-text | −10.6% (−11.3, −9.8) | **−14.4% (−14.9, −13.9) change** | −13.3% |
| d3-rand | −0.4% (−2.3, +1.5) | +0.3% (−0.3, +0.9) | +2.0% |

Mean over cases −11.5% (−11.9, −11.1). The same at every data setting (stock, coloured, stepped within a
point), no flags, code spreads 0.8-2.5% (d3-rand 4.5%). The commit claimed +11-24% with clang 19/20 and
"about 0%" with a clang 21 carrying an LLVM alias-analysis fix; on the M2 the Ubuntu clang 21.1.8 build
still gains 10-15%, slightly more than Apple clang 17 on three of the cases. A single-build comparison
would have got it right (stock pairs within 1.5 points), as on the Mac.

## Mac and Linux compared

| | Mac (M2, macOS, Apple clang 17, ld64) | Linux (same M2, VM, clang 21, GNU ld) |
|---|---|---|
| null: code flags | 2 (c3-text, d3-text, one arm each; ungated, noisy) | 2 (c6-text both arms ~6%; c1-text one arm) |
| null: colour / step / run flags | 0 / 0 / unavailable | 3 (c6-text, c3-rand, d3-rec) / 0 / unavailable |
| null: code spreads | 1.6-33% (ungated) | 1.4-6.7% (d3-rand 8.5%) |
| within-pad s.d. (median) | 22% (ungated null) | 1.7% |
| real layout effects below threshold | 1-2.5% (quiet copy8 run) | 1-3%, replicated between arms in 12 of 16 cases |
| null: "false change possible" | 4 of 16 | 2 of 16 |
| v1.5.6 → v1.5.7 | c3-rand −23.5%, c3-text +3.3% (changes) | c3-rand −22.8% (change), c3-text +2.3% |
| layout-averaged vs the other platform (v1.5.7) | r = 0.89 | |
| stock pair vs the other platform | r = 0.16 | |
| copy8 (1e9d2006) on decompression | flat (±0.6%) | 3-6% slower (4 changes) |
| decseq (a28e8182) on decompression | −10.6..−14.9% (5 changes) | −9.8..−14.7% (5 changes) |

## placemat bugs and gaps hit

1. **`culprits` phase claims are vacuous with 12 pads at mod 64** (12 distinct phases, so every slow/fast
   split separates) and with a single slow pad; every flagged case got "an alignment effect". A
   permutation p over pads (as `phase.py` computes) would separate real phases from arbitrary splits.
2. **The adapter's scripts in a parent directory are not in the build cache key.** This config runs
   `../build.py` with `../zbench.c`; placemat hashes only the config directory's `.py/.sh/.h/.c` files, so
   an edit to either (by the Mac survey, say) would silently reuse stale Linux binaries. (Files hashed at
   the start of these runs: build.py sha1 078d29f8, zbench.c 3e7ba8fd.)
3. **`[target]` makes `memory_mb` meaningless** (it watches `limactl`'s client on the Mac), so it is 0
   here; and the noise probe runs on the Mac, not in the guest (batches went ahead at 9-14% probe spreads
   without the VM's timing being disturbed, and vice versa elsewhere).
4. **Colour flags with a typical effect under 1%.** A colour flag needs the *largest* per-pad
   |coloured/stock − 1| over the threshold; with 12 pads and a 1.5% within-pad s.d. a single bad
   pad/colour pair carries it. The report gives typical and worst, which is enough to read it, but the
   flag says "colour-sensitive" for what is one or two layouts.
