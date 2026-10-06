# Amber validation reports

Reports from the runs that built and validated placemat's methods on the Amber K interpreter (Apple M2, macOS, Apple clang 17, ld64; 2026-10-04/05). They were written in the Amber work's scratch area; `$S/...` paths inside them refer to that area, which is not preserved. They are kept here as the evidence behind planning/ISSUES.md and as the reference results for placemat's regression test.

| file | what |
|---|---|
| hot-kernel-analysis.md | profile of Amber's three biggest time sinks and prototype fixes (measured against plain builds, before placement control) |
| order-file-report.md | pinning hot code with an order file, alignment and pads (P003) |
| joint-stage-summary.md | the joint code-and-data layout stage and its four validations (P002) |
| colouring-report.md | Amber's colouring allocator (P004's lessons) |
| hot-kernel-rerun.md | the hot-kernel prototypes re-measured on pinned, coloured builds |
| heap-and-stack.md | the region lottery, heap history and stack placement (P013, P014; 2026-10-07): the evidence from placemat's own Amber regression runs and the reasoning behind DESIGN §5.4 and §5.5 |

## Inputs, tools and raw data (copied 2026-10-05, so the regression test does not depend on the scratch area)

- `inputs/`: the case list (`cases.txt`, plus `cases-hot2.txt` with three scout cases the stage had missed), the order file (`hot.order`), its selection and coverage (`sel.json`), the re-attributed profile (`prof2.json`), hot entry spans (`hotspans.json`: main's offsets), hottest small loops (`hotloops64.json`), the pad files per build (`pads/`), the prototype order files (`orders/`), the K benchmark kernels (`k/`: win, grp, fnd, red and the equality checks), and the targeted pads used in validation (d) (`targeted-pads.txt`).
- `tools/`: the prototype scripts as they ran (order-file tools; colouring-run helpers prefixed `col-`; `loops4k.py`, `samefn.py`). `loops4k.py` and `samefn.py` exist nowhere else, and the order-file tools are otherwise only on the Amber fork's `fusion` branches (`pbt/fusion/tools/of/`), so this folder is their extraction source; the stage itself (`layouts.py`, `ldesign.py`, `evidence.py`) is extracted from the Amber fork's `pbt/`.
- `raw.tar.xz`: raw timings and stage outputs of the joint-stage, order-file, colouring and re-run experiments.
- Not here: the colouring patch itself and its generator. They embed Amber's `src/m.c`, and Amber is AGPL-3.0, so they stay in the Amber fork (`pbt/patches/`).
