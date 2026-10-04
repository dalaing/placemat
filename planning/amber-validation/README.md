# Amber validation reports

Reports from the runs that built and validated placemat's methods on the Amber K interpreter (Apple M2, macOS, Apple clang 17, ld64; 2026-10-04/05). They were written in the Amber work's scratch area; `$S/...` paths inside them refer to that area, which is not preserved. They are kept here as the evidence behind planning/ISSUES.md and as the reference results for placemat's regression test.

| file | what |
|---|---|
| hot-kernel-analysis.md | profile of Amber's three biggest time sinks and prototype fixes (measured against plain builds, before placement control) |
| order-file-report.md | pinning hot code with an order file, alignment and pads (P003) |
| joint-stage-summary.md | the joint code-and-data layout stage and its four validations (P002) |
| colouring-report.md | Amber's colouring allocator (P004's lessons) |
| hot-kernel-rerun.md | the hot-kernel prototypes re-measured on pinned, coloured builds |
