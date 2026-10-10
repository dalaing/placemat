/* placemat_alloc.h: a colouring allocator over the C library's malloc (placemat, MIT licence).
 *
 * For projects with a pluggable allocator (zstd ZSTD_customMem, SQLite sqlite3_mem_methods, Lua
 * lua_Alloc), and for placemat's malloc interposer. Compile placemat_alloc.c with -DPLACEMAT_HOOK
 * and link it in; the colouring is configured by the PLACEMAT_* environment (see placemat.h).
 *
 * Blocks of at least PLACEMAT_MIN bytes are over-allocated by a header, the alignment and
 * colour_span + step_span, at EVERY data setting (no offset included), so the underlying size class
 * and the wrapper's overhead do not depend on the setting; their payload is aligned to
 * max(PLACEMAT_UNIT, 16, requested alignment) and then moved by placemat_colour(). Smaller blocks go
 * straight to the underlying allocator. k is floor((underlying address - base) / PLACEMAT_MIN), base
 * being the first large block's underlying address in the process.
 *
 * placemat_free/realloc/usable_size accept any pointer from the underlying allocator too: ownership
 * is decided by a lock-free set of the large blocks this allocator returned (placemat_owns), never
 * by reading memory in front of a pointer it did not return.
 */
#ifndef PLACEMAT_ALLOC_H
#define PLACEMAT_ALLOC_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

void *placemat_malloc(size_t n);
void placemat_free(void *p);
void *placemat_realloc(void *p, size_t n);
void *placemat_calloc(size_t n, size_t m);
/* align: a power of two; NULL (errno EINVAL) otherwise. */
void *placemat_memalign(size_t align, size_t n);
/* The requested size for a large block; the underlying allocator's answer otherwise (0 if unknown). */
size_t placemat_usable_size(const void *p);
/* Nonzero if p is a live large block returned by this allocator. */
int placemat_owns(const void *p);

/* Adapters for common pluggable allocator APIs (no project headers needed). */

/* zstd: ZSTD_customMem m = { placemat_zstd_alloc, placemat_zstd_free, NULL }; */
static inline void *placemat_zstd_alloc(void *opaque, size_t n) { (void)opaque; return placemat_malloc(n); }
static inline void placemat_zstd_free(void *opaque, void *p) { (void)opaque; placemat_free(p); }

/* Lua: lua_newstate(placemat_lua_alloc, NULL) */
static inline void *placemat_lua_alloc(void *ud, void *p, size_t osize, size_t nsize)
{
    (void)ud;
    (void)osize;
    if (nsize == 0) {
        placemat_free(p);
        return NULL;
    }
    return placemat_realloc(p, nsize);
}

/* SQLite: sqlite3_mem_methods m = { placemat_sqlite_malloc, placemat_sqlite_free,
   placemat_sqlite_realloc, placemat_sqlite_size, placemat_sqlite_roundup, placemat_sqlite_init,
   placemat_sqlite_shutdown, NULL }; sqlite3_config(SQLITE_CONFIG_MALLOC, &m); */
static inline void *placemat_sqlite_malloc(int n) { return n > 0 ? placemat_malloc((size_t)n) : NULL; }
static inline void placemat_sqlite_free(void *p) { placemat_free(p); }
static inline void *placemat_sqlite_realloc(void *p, int n) { return n > 0 ? placemat_realloc(p, (size_t)n) : NULL; }
static inline int placemat_sqlite_size(void *p) { return p ? (int)placemat_usable_size(p) : 0; }
static inline int placemat_sqlite_roundup(int n) { return (n + 7) & ~7; }
static inline int placemat_sqlite_init(void *x) { (void)x; return 0; }
static inline void placemat_sqlite_shutdown(void *x) { (void)x; }

#ifdef __cplusplus
}
#endif

#endif /* PLACEMAT_ALLOC_H */
