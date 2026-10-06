# placemat on zstd

An adapter for [zstd](https://github.com/facebook/zstd) (P010): a small benchmark harness over zstd's library API, a build script implementing placemat's build protocol, and the configuration. The survey's results are in [SURVEY.md](SURVEY.md).

Licence: MIT, like placemat. The adapter uses zstd's public and static-linking-only API (`zstd.h`) and compiles the arm's own zstd tree; it contains no zstd source (zstd is BSD/GPLv2 dual-licensed).

| file | what it is |
|---|---|
| `placemat.toml` | the configuration: the project root (a zstd clone, outside this repository), the build, the machine lock, the data axis, the suite and its 16 cases |
| `build.py` | the build protocol: libzstd compiled once per arm into `PLACEMAT_WORK/lib`; per variant the pad object, `zbench.o` and (hooked builds) `placemat_alloc.o` compiled and linked; placement verified with `nm` |
| `zbench.c` | the harness: generates its corpus in memory and prints `name value iterations us` per case; honours `PLACEMAT_CASES`; `zbench --list` lists the cases |

## Running

zstd's tree is not in this repository. Clone it into `placemat-work`, next to this repository (`root` in `placemat.toml` is relative to the file; change it there for another place):

    git clone https://github.com/facebook/zstd ../placemat-work/zstd/src     # from this repository's root
    PYTHONPATH=/path/to/placemat python3.12 -m placemat run --config examples/zstd/placemat.toml \
        --arm base=git:v1.5.6 --arm new=git:v1.5.7 \
        --cases c1-text,c3-text,c6-text,c12-text,c19-text,cneg5-text,c1-rec,c3-rec,c3-rand,cs3-text,d1-text,d3-text,d12-text,d3-rec,d3-rand,ds3-text \
        --work ../placemat-work/zstd/work --out ../placemat-work/zstd/runs

Builds are cached by the arm's tree, the build command and environment, this directory's `.py` and `.c` files and placemat's data sources (so editing `zbench.c` or `build.py` rebuilds), but not by platform: use one `--work` directory per platform.

## The build

- **libzstd:** every `.c` file in `lib/common`, `lib/compress` and `lib/decompress` except `zstdmt_compress.c` (single-threaded), at `-O3` (zstd's default) with `-DXXH_NAMESPACE=ZSTD_ -DZSTD_LEGACY_SUPPORT=0 -DZSTD_DISABLE_ASM` (the asm is x86-64 only anyway), compiled once per arm.
- **Link order:** pad object, libzstd's objects, `zbench.o`, then `placemat_alloc.o` in hooked builds. The pad moves all of libzstd; the hook's extra code sits after libzstd, so libzstd's addresses in a hooked pad-P build are those of an unhooked pad-P build (the hooked and unhooked `zbench.o` differ slightly, but they are linked after the library).
- **Verification:** after linking, `placemat_pad` must precede every libzstd function and the next text symbol must start at least `PLACEMAT_PAD` bytes after it; otherwise the build fails. No LTO, so "linked first" is where the pad lands; the check makes sure. What may legitimately precede the pad: the C runtime's code, Mach-O's `__mh_execute_header`, and on Linux with GCC and GNU ld the `.text.startup` (`main`) and `.text.unlikely` (`*.cold`) input sections, which the default linker script places before `.text`, so they do not move with the pad (the build prints a note when `main` is before it).
- **Linux:** the same files build and run on Linux arm64 (tested in the Lima VM `placemat`, GCC 15, GNU ld 2.46; P006, P008). Use a separate `--work` directory per platform: placemat's build cache is not keyed by platform, so a shared work directory would hand Mach-O binaries to Linux (the VM also mounts this tree read-only, so its work directory must be inside the VM anyway).

## The cases

16 cases, each 30-150 ms per sample on the M2 (one untimed warm-up operation first, which also checks the round trip). The corpus is generated in each execution from fixed seeds: 8 MB of word-like text (log-uniform word frequencies over a made-up 6,000-word vocabulary), 8 MB of 48-byte binary records, and 8 MB of random bytes.

| case | what |
|---|---|
| `cneg5-text`, `c1-text`, `c3-text` | one-shot `ZSTD_compress2`, 8 MB of text, levels −5 (fast), 1 (fast), 3 (dfast) |
| `c6-text`, `c12-text`, `c19-text` | the same at levels 6 (lazy, 2 MB), 12 (lazy2, 1 MB), 19 (btultra, 256 KB) |
| `c1-rec`, `c3-rec` | records, levels 1 and 3 |
| `c3-rand` | random bytes, level 3 (zstd detects them as incompressible) |
| `cs3-text` | streaming compression (`ZSTD_compressStream2`, 64 KB input chunks), level 3 |
| `d1-text`, `d3-text`, `d12-text` | one-shot `ZSTD_decompressDCtx` of frames made at levels 1, 3, 12 |
| `d3-rec`, `d3-rand` | the same for records and random bytes (raw blocks: a copy) |
| `ds3-text` | streaming decompression (`ZSTD_decompressStream`, 64 KB output buffer), level 3 |

## Data colouring: the hook through `ZSTD_customMem`, not the interposer

zstd uses the system allocator by default, so both of placemat's data adapters (DESIGN §7) would work. This adapter uses `data.method = "allocator"` (placemat's colouring allocator through a project allocator API; it builds like `"hook"`, and the survey's null run used `"hook"` with the same settings): hooked builds (compiled with `$PLACEMAT_CFLAGS`, i.e. `-DPLACEMAT_HOOK`) create every context with `ZSTD_createCCtx_advanced`/`ZSTD_createDCtx_advanced` and a `ZSTD_customMem` over placemat's colouring allocator (`placemat_alloc.h`'s `placemat_zstd_alloc`/`placemat_zstd_free`), and allocate the harness's own buffers with `placemat_malloc`. Unhooked builds use `malloc` and the default contexts. Why:

- **It is the route P010 chose for zstd:** "colourable without patching the project": the pluggable allocator API installs placemat's allocator from outside, and `placemat_alloc.h` ships the adapter for exactly this.
- **It colours exactly the buffers the benchmark is about:** zstd's workspaces and the harness's input and output buffers, nothing in the C library or the runtime. The interposer would colour every large `malloc` in the process; here that would be the same blocks, but only by accident of the harness.
- **No `DYLD_INSERT_LIBRARIES`:** nothing depends on SIP or hardened-runtime rules, and the same build works unchanged on Linux.
- **The code axis stays clean:** the colouring code is linked after libzstd, so it does not move zstd's code (the interposer would not either, being a separate image; a hook linked *before* the library would).

What it costs, and the settings that follow (DESIGN §5.3):

- **`stock_placement = false`.** `placemat_alloc` adds a header and over-allocates every large block by colour_span + step_span at every data setting, (0, off) included, so the hooked no-offset placement is not the stock one (macOS's allocator gives large blocks page alignment; under the wrapper a payload starts 64 bytes into its block). The unhooked stock builds are timed as their own slot, and a stock-builds-only result is attributed to the "stock build", not the stock code layout.
- **`covariate = "khash"`.** `placemat_alloc` computes k from the underlying allocator's addresses with one per-process base (the first large block), as the interposer does, so the run covariate is whether the hash of the k values in allocation order matches the arm's most common one. The hook default, the region base, would be an ASLR'd address that never repeats.
- **`unit = 64`.** zstd asks the allocator for nothing beyond `malloc`'s alignment and aligns its own tables to 64 inside the workspace; 64-byte units keep whole cache lines (P002 lesson 1).

**What any allocator-level colouring can and cannot move in zstd.** The compressor carves all its tables (hash, chain, binary tree, optimal parser state) and, when streaming, its window and input buffer from **one** workspace allocation (`ZSTD_cwksp`, aligned to 64 inside). So the tables never move relative to each other or to a streaming window: a step moves the workspace as a whole against the caller's input (the window in one-shot compression) and output. The decompressor has the DCtx (entropy tables and a 64 KB literal buffer) as one block and, when streaming, one block for its input buffer and window. DESIGN §5.3's motivating example ("a window and tables in zstd") holds for one-shot compression (window = the caller's input, tables = the workspace) and for streaming decompression (window block against the DCtx), not within a streaming compressor.
