# Hot-kernel prototypes re-measured on pinned, coloured builds (2026-10-05)
(Re-run agent's report, saved by the main session. Base: cq2 = main d596e57a + colouring (col/colour-pq.diff) + order file C2 procedure. Each prototype P2 = cq2's tree + the prototype's .c file, pads regenerated. `evidence.py --dirs col/cq2 hot2/P2 --design --no-data` over of/cases.txt (200 cases). Loads 1.99-2.98 at every batch start/end; one small follow-up at 6.67 discarded and re-run.)

## Summary
- **mw (A260)**: about half its original sum/avg/dev gain was the L1 aliasing that colouring removes. Against cq2: msum −20.9%, mavg −40.9%, mdev −32.1% (A271's prediction −20/−41/−34); mmin/mmax (64≤w≤4096) −31..−43% unchanged (algorithmic); sc_mavg256 −35%, sc_mmax64 −19%, sc_msum16 −11%.
- **gagg (A262) and fnd (A259 + A263)**: real; reproduce within ~2 points case by case.
- **Placement artefacts confirmed**: maxprior/minprior +6.5/+5.4 → −0.7/−0.1; iscan +3.0 → 0.0; take +2.8 → −1.0..+0.6; b_find_bin_5m −7.2 → +0.4; takekeys10x1/10x10 −4.6/−4.1 → −2.2/−0.4.
- **Nothing measurably slower** in any run (every interval above 0 is under +2.7%, noise or placement verdicts).

## Runs (vs cq2)
| run | geomean (200) | win.k | grp.k | fnd.k | bench.k | bench-std | microbench | microbench-q | red.k | flags code/data/run |
|---|---|---|---|---|---|---|---|---|---|---|
| mw2 | −3.24% (−3.32, −3.16) | −28.8 | +0.2 | +0.0 | +0.1 | −16.8 | −0.07 | +0.01 | −0.05 | 4/0/0 |
| gagg2 | −1.45% (−1.52, −1.39) | −0.1 | −33.4 | +0.0 | +0.1 | −0.1 | −0.01 | −1.64 | +0.05 | 8/0/0 |
| fnd2 | −16.74% (−16.80, −16.67) | +0.1 | +0.2 | −72.8 | −63.6 | +0.5 | −10.2 | −1.70 | −0.18 | 6/0/0 |
| all2 | −4.55% (−4.61, −4.49) | −28.6 | −33.1 | +0.1 | −0.3 | −16.1 | +0.03 | −1.86 | +0.45 | 0/0/0 |
Code flags are all set*/qhh*/sc_find/dictplus (placement-sensitive cases), none a target case.

## Windows (mw2 vs cq2; original = mw vs plain main)
s_msum100 −64.2 → **−20.9%**; s_mavg100 −72.2 → **−40.9%**; s_mdev100 −60.0 → **−32.1%**; s_mmin100 −30.6 → −31.8%; s_mmax100 −31.2 → −32.4%; s_mavg10 −68.7 → −40.8%; s_mavg1000 −66.6 → −40.0%; s_mmin10 (w<64, old kernel) noise; s_mmin1000 −41.8 → −43.1%; std_msum20 −55.0 → −19.6%; std_mavg20 −61.6 → −40.5%; std_mdev20 −55.8 → −34.4%; sc_msum16 −10.9%; sc_mavg256 −35.1%; sc_mmax64 −18.9%; bench-std msum/mavg/mdev −66/−60/−45 → −17.7/−38.7/−33.3%; mmin/mmax w=20 noise. No two-speed switching in cq2 or mw2 (p90/p10 1.03-1.13).
Stacking vs main (chained col C2→cq2 and cq2→mw2): s_msum100 colouring −57%, both ≈ −66% (mw alone −64%); s_mavg100 −58 / ≈−75 (−72); s_mdev100 −42 / ≈−61 (−60); std_mavg20 −50 / ≈−70 (−62); bench-std msum −62 / ≈−69 (−66); mavg −46 / ≈−67 (−60); mdev −14 / ≈−43 (−45); mmin/mmax w≥64 0 / −31..−43 (mw only); qgroup −60 / −60 (colouring only).

## Grouping (gagg2 vs cq2; original in brackets)
s_grp10 −47.2% (−48.1); s_grp1k −38.3 (−38.0); s_grp100k −23.1 (−21.3); sc_group_10/10k/100k −47.4/−36.3/−27.5 (−47.1/−36.2/−27.4; separate 3-case run, hot2/k/grpsc.k); sc_qsql_select −29.0 (−29.3); q_groupby_sum −26.3 (−25.9); q_filter_groupby −33.5 (−31.1); qselby −39.5 (−40.2).

## Find, distinct, in (fnd2 vs cq2; original in brackets)
bench.k find lin 100k/500k/2M/5M −15.4/−66.2/−82.8/−85.3 (−15.5/−67.4/−81.5/−81.4); `in` lin/bin −84.3/−83.7 (−82.6/−81.7); fnd.k b_find_lin 100k/2m/5m −13.1/−83.8/−86.9; b_in −83.9/−83.9; sc_distinct/_100k/s_distinct −96.0/−84.6/−79.4; ifind −98.8; idistinct/qdistinct −34.1/−36.8; setdictbigkeys −39.5; takekeys 1kx1/1kx10/1kx1k −66.7/−60.3/−21.8; 100kx1/100kx10/100kx1k −87.1/−78.9/−33.5 (−40.7); dictplus −6.3; ffind/fdistinctshort −3.1/−3.0. bench.k binary find gains (−21.6/−59.8/−29.6) are a script memory-state side effect (the preceding linear case no longer builds a ~200 MB table); the isolated bIL case is +0.4% (noise).

## all2 vs cq2
Windows within ~1 point of mw2, grouping as gagg2, sc_group −47.3/−36.0/−27.7; no interaction; 0 code flags.

## Ranking (placement-free gains)
1. fnd (A259 + A263): biggest and broadest. 2. gagg (A262): 15% of the maintainer's time, −21..−48%. 3. Colouring (A269): windows −42..−62%, qgroup −60%, removes the two-speed switching; general, goes before mw. 4. mw (A260) on top of colouring: mavg/mdev −32..−41%, mmin/mmax −31..−43% (colouring cannot reach these), msum −20%; its PR should quote gains on a non-aliasing placement, not the original −55..−72%.

## Builds and notes
mw2 +18.7 KB text, gagg2 +8.2 KB, fnd2 +4.1 KB (one new ordered function `_rngW` after `_unq`), all2 +26.9 KB; all with ordered functions in order, no hot loop or entry span across 4 KB; identity scripts "same" vs main for all. hotspans.json holds main's offsets, so spans for changed kernels are approximate. Stage note: out[...] cases not at the start of a line were silently skipped (hot/k/grp.k's three sc_group cases); fixed in hot/k/grp.k and the stage now warns (pbt/layouts.py).
Files: hot2/ builds cq2 mw0/2 gagg0/2 fnd0/2 all0/2, pads*/, *.order; scripts mk.sh chk.py ident.sh run.sh runsc.sh queue*.sh cmp.py k/grpsc.k cases.txt; res/cq2-{mw,gagg,fnd,all}/, res/sc-{gagg,all}/, res/runs.log, lv/raw/.
