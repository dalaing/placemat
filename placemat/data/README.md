# placemat/data: C sources

MIT licence, written fresh for placemat (nothing here derives from Amber's AGPL source).

| file | what it is |
|---|---|
| `placemat.h` | The hook header for custom allocators (DESIGN §7). Header-only; active only with `-DPLACEMAT_HOOK`, otherwise every call is a no-op macro and the binary is unchanged. Its comment documents the API, the `PLACEMAT_*` environment, the exit-summary log format and the rules for allocator authors. Its offset arithmetic is the twin of `placemat/colour.py` (checked bit for bit by `tests/test_colour_c.py`). |
| `placemat_alloc.h`, `placemat_alloc.c` | A colouring allocator over the C library's malloc, for projects with a pluggable allocator API (adapters for zstd `ZSTD_customMem`, SQLite `sqlite3_mem_methods` and Lua `lua_Alloc` are in the header). Compile with `-DPLACEMAT_HOOK`. |
| `interpose.c` | The malloc interposer: `placemat_alloc.c` behind `malloc`/`free`/... for `LD_PRELOAD` (Linux) or `DYLD_INSERT_LIBRARIES` (macOS; SIP-protected and hardened-runtime binaries ignore it). Built by `placemat.cbuild.build_interposer()`. |

Using the hook in a project:

    cc -DPLACEMAT_HOOK -I "$(python3 -c 'from placemat import cbuild; print(cbuild.header_dir())')" ...

Building and loading the interposer from Python:

    from placemat import cbuild
    lib = cbuild.build_interposer(Path(tmpdir))
    subprocess.run(cmd, env={**os.environ, **cbuild.preload_env(lib), "PLACEMAT_LOG": log, ...})
