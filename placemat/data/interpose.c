/* interpose.c: placemat's malloc interposer (placemat, MIT licence).
 *
 * A shared library that colours large allocations of an unmodified program (DESIGN §7), built
 * together with placemat_alloc.c (see placemat/cbuild.py):
 *
 *   cc -O2 -fPIC -fvisibility=hidden -DPLACEMAT_HOOK -DPLACEMAT_ALLOC_INTERPOSER \
 *      -shared interpose.c placemat_alloc.c -o libplacemat.so -ldl -pthread        (Linux)
 *   cc -O2 -fvisibility=hidden -DPLACEMAT_HOOK -DPLACEMAT_ALLOC_INTERPOSER \
 *      -dynamiclib interpose.c placemat_alloc.c -o libplacemat.dylib               (macOS)
 *
 *   LD_PRELOAD=libplacemat.so prog            DYLD_INSERT_LIBRARIES=libplacemat.dylib prog
 *
 * Linux: defines malloc, free, calloc, realloc, posix_memalign, aligned_alloc, memalign, valloc,
 * pvalloc and malloc_usable_size; the C library's own are found with dlsym(RTLD_NEXT). dlsym may
 * itself allocate (calloc for its error state), so while the real functions are being looked up,
 * allocations come from a small static bump arena; arena blocks are never given back (free ignores
 * them, realloc copies out of them).
 *
 * macOS: dyld interposing (a __DATA,__interpose section of {replacement, original} pairs) for malloc,
 * free, calloc, realloc, reallocf, posix_memalign, aligned_alloc, valloc and malloc_size. Calls made
 * from this library itself are not interposed, so it calls the system functions directly.
 * System Integrity Protection strips DYLD_* variables when a protected binary is executed (anything
 * in /bin, /usr/bin, /System, ...), including when a shell script runs via /bin/sh, and binaries built
 * with the hardened runtime ignore them unless they have the allow-dyld-environment-variables
 * entitlement: run the benchmark binary itself, not through a protected shell or tool.
 * Not interposed on macOS: the malloc_zone_* functions. A large block from malloc() handed to
 * malloc_zone_free/realloc, or asked about with malloc_zone_from_ptr, is not recognised there
 * (it is an interior pointer of a system block); programs that mix the two APIs on one block are
 * not supported.
 *
 * Pointers this library never returned (blocks from before it loaded, from the C library's
 * internals, small blocks it passed through) are recognised by placemat_owns(), a set of the large
 * blocks it returned, and passed to the real functions; see placemat_alloc.c.
 */
#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif

#include "placemat.h"
#include "placemat_alloc.h"

#include <errno.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>

#define PM_EXPORT __attribute__((visibility("default")))
#define PM_HIDDEN __attribute__((visibility("hidden")))

static size_t pm_pagesize(void)
{
    long p = sysconf(_SC_PAGESIZE);
    return p > 0 ? (size_t)p : 4096;
}

static int pm_pow2(size_t a) { return a && !(a & (a - 1)); }

#if defined(__linux__)

#include <dlfcn.h>
#include <malloc.h>

/* ---- bootstrap arena ---- */

static char pm_arena[1 << 16] __attribute__((aligned(64)));
static size_t pm_arena_used;

static int pm_in_arena(const void *p) { return (const char *)p >= pm_arena && (const char *)p < pm_arena + sizeof pm_arena; }

static void *pm_arena_alloc(size_t n, size_t align)
{
    if (align < 16) align = 16;
    size_t start = __atomic_load_n(&pm_arena_used, __ATOMIC_RELAXED), end;
    uintptr_t p;
    do {
        p = ((uintptr_t)pm_arena + start + 16 + align - 1) & ~(uintptr_t)(align - 1);
        end = (size_t)(p - (uintptr_t)pm_arena);
        if (n > sizeof pm_arena - end) return 0;
        end += n;
    } while (!__atomic_compare_exchange_n(&pm_arena_used, &start, end, 1, __ATOMIC_RELAXED, __ATOMIC_RELAXED));
    ((size_t *)p)[-1] = n; /* arena memory is zero and never reused, so calloc needs nothing more */
    return (void *)p;
}

static size_t pm_arena_size(const void *p) { return ((const size_t *)p)[-1]; }

/* ---- the real functions ---- */

static void *(*r_malloc)(size_t);
static void (*r_free)(void *);
static void *(*r_calloc)(size_t, size_t);
static void *(*r_realloc)(void *, size_t);
static int (*r_posix_memalign)(void **, size_t, size_t);
static void *(*r_aligned_alloc)(size_t, size_t);
static void *(*r_memalign)(size_t, size_t);
static void *(*r_valloc)(size_t);
static void *(*r_pvalloc)(size_t);
static size_t (*r_usable)(void *);
static int pm_resolve_state; /* 0 not yet, 1 in progress, 2 done */

static void pm_resolve(void)
{
    int z = 0;
    if (!__atomic_compare_exchange_n(&pm_resolve_state, &z, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return;
    /* While this runs, a call into malloc (from dlsym, or another thread) sees a null pointer and
       uses the arena. Each pointer is published once it is known. */
#define PM_SYM(var, name) __atomic_store_n((void **)&var, dlsym(RTLD_NEXT, name), __ATOMIC_RELEASE)
    PM_SYM(r_calloc, "calloc");
    PM_SYM(r_malloc, "malloc");
    PM_SYM(r_free, "free");
    PM_SYM(r_realloc, "realloc");
    PM_SYM(r_posix_memalign, "posix_memalign");
    PM_SYM(r_aligned_alloc, "aligned_alloc");
    PM_SYM(r_memalign, "memalign");
    PM_SYM(r_valloc, "valloc");
    PM_SYM(r_pvalloc, "pvalloc");
    PM_SYM(r_usable, "malloc_usable_size");
#undef PM_SYM
    __atomic_store_n(&pm_resolve_state, 2, __ATOMIC_RELEASE);
}

#define PM_REAL(var) (__atomic_load_n(&var, __ATOMIC_ACQUIRE) ? var : (pm_resolve(), __atomic_load_n(&var, __ATOMIC_ACQUIRE)))

PM_HIDDEN void *placemat__real_malloc(size_t n)
{
    void *(*f)(size_t) = PM_REAL(r_malloc);
    return f ? f(n) : pm_arena_alloc(n, 16);
}

PM_HIDDEN void placemat__real_free(void *p)
{
    void (*f)(void *) = PM_REAL(r_free);
    if (f) f(p); /* else: only possible during the lookup, for a pointer we cannot know; leak it */
}

PM_HIDDEN void *placemat__real_calloc(size_t n, size_t m)
{
    void *(*f)(size_t, size_t) = PM_REAL(r_calloc);
    if (f) return f(n, m);
    if (m && n > SIZE_MAX / m) return 0;
    return pm_arena_alloc(n * m, 16);
}

PM_HIDDEN void *placemat__real_realloc(void *p, size_t n)
{
    void *(*f)(void *, size_t) = PM_REAL(r_realloc);
    return f ? f(p, n) : 0;
}

PM_HIDDEN int placemat__real_posix_memalign(void **out, size_t a, size_t n)
{
    int (*f)(void **, size_t, size_t) = PM_REAL(r_posix_memalign);
    if (f) return f(out, a, n);
    *out = pm_arena_alloc(n, a);
    return *out ? 0 : ENOMEM;
}

PM_HIDDEN size_t placemat__real_usable_size(const void *p)
{
    size_t (*f)(void *) = PM_REAL(r_usable);
    return f ? f((void *)p) : 0;
}

/* ---- the interposed functions ---- */

PM_EXPORT void *malloc(size_t n) { return placemat_malloc(n); }

PM_EXPORT void *calloc(size_t n, size_t m) { return placemat_calloc(n, m); }

PM_EXPORT void free(void *p)
{
    if (!p || pm_in_arena(p)) return;
    placemat_free(p);
}

PM_EXPORT void *realloc(void *p, size_t n)
{
    if (p && pm_in_arena(p)) {
        void *q = placemat_malloc(n);
        if (q) {
            size_t old = pm_arena_size(p);
            memcpy(q, p, old < n ? old : n);
        }
        return q;
    }
    return placemat_realloc(p, n);
}

PM_EXPORT size_t malloc_usable_size(void *p)
{
    if (!p) return 0;
    if (pm_in_arena(p)) return pm_arena_size(p);
    return placemat_usable_size(p);
}

static int pm_large(size_t n)
{
    placemat__ready();
    return n >= placemat__st.min;
}

PM_EXPORT int posix_memalign(void **out, size_t a, size_t n)
{
    if (!pm_large(n)) return placemat__real_posix_memalign(out, a, n);
    if (a < sizeof(void *) || !pm_pow2(a)) return EINVAL;
    int saved = errno;
    void *p = placemat_memalign(a, n);
    if (!p) {
        int e = errno ? errno : ENOMEM;
        errno = saved;
        return e;
    }
    *out = p;
    return 0;
}

PM_EXPORT void *aligned_alloc(size_t a, size_t n)
{
    if (!pm_large(n)) {
        void *(*f)(size_t, size_t) = PM_REAL(r_aligned_alloc);
        return f ? f(a, n) : pm_arena_alloc(n, a);
    }
    return placemat_memalign(a, n);
}

PM_EXPORT void *memalign(size_t a, size_t n)
{
    if (!pm_large(n)) {
        void *(*f)(size_t, size_t) = PM_REAL(r_memalign);
        return f ? f(a, n) : pm_arena_alloc(n, a);
    }
    /* glibc's memalign rounds a non-power-of-two alignment up */
    size_t p2 = 16;
    while (p2 < a) p2 <<= 1;
    return placemat_memalign(p2, n);
}

PM_EXPORT void *valloc(size_t n)
{
    if (!pm_large(n)) {
        void *(*f)(size_t) = PM_REAL(r_valloc);
        return f ? f(n) : pm_arena_alloc(n, pm_pagesize());
    }
    return placemat_memalign(pm_pagesize(), n);
}

PM_EXPORT void *pvalloc(size_t n)
{
    size_t ps = pm_pagesize();
    if (n > SIZE_MAX - ps) {
        errno = ENOMEM;
        return 0;
    }
    if (!pm_large(n)) {
        void *(*f)(size_t) = PM_REAL(r_pvalloc);
        return f ? f(n) : pm_arena_alloc((n + ps - 1) & ~(ps - 1), ps);
    }
    return placemat_memalign(ps, (n + ps - 1) & ~(ps - 1));
}

#elif defined(__APPLE__)

#include <malloc/malloc.h>
#include <stdlib.h>

/* The system functions: calls from this image are not interposed. */
PM_HIDDEN void *placemat__real_malloc(size_t n) { return malloc(n); }
PM_HIDDEN void placemat__real_free(void *p) { free(p); }
PM_HIDDEN void *placemat__real_calloc(size_t n, size_t m) { return calloc(n, m); }
PM_HIDDEN void *placemat__real_realloc(void *p, size_t n) { return realloc(p, n); }
PM_HIDDEN int placemat__real_posix_memalign(void **out, size_t a, size_t n) { return posix_memalign(out, a, n); }
PM_HIDDEN size_t placemat__real_usable_size(const void *p) { return malloc_size(p); }

static int pm_large(size_t n)
{
    placemat__ready();
    return n >= placemat__st.min;
}

static void *pm_malloc(size_t n) { return placemat_malloc(n); }
static void pm_free(void *p) { placemat_free(p); }
static void *pm_calloc(size_t n, size_t m) { return placemat_calloc(n, m); }
static void *pm_realloc(void *p, size_t n) { return placemat_realloc(p, n); }
static size_t pm_malloc_size(const void *p) { return p ? placemat_usable_size(p) : 0; }

static void *pm_reallocf(void *p, size_t n)
{
    void *q = placemat_realloc(p, n);
    if (!q && p && n) placemat_free(p);
    return q;
}

static int pm_posix_memalign(void **out, size_t a, size_t n)
{
    if (!pm_large(n)) return posix_memalign(out, a, n);
    if (a < sizeof(void *) || !pm_pow2(a)) return EINVAL;
    int saved = errno;
    void *p = placemat_memalign(a, n);
    if (!p) {
        int e = errno ? errno : ENOMEM;
        errno = saved;
        return e;
    }
    *out = p;
    return 0;
}

static void *pm_aligned_alloc(size_t a, size_t n)
{
    if (!pm_large(n)) return aligned_alloc(a, n);
    return placemat_memalign(a, n);
}

static void *pm_valloc(size_t n)
{
    if (!pm_large(n)) return valloc(n);
    return placemat_memalign(pm_pagesize(), n);
}

#define PM_INTERPOSE(replacement, original)                                                   \
    __attribute__((used)) static const struct { const void *r; const void *o; }             \
        pm_interpose_##original __attribute__((section("__DATA,__interpose"))) = {          \
            (const void *)(uintptr_t)&replacement, (const void *)(uintptr_t)&original }

PM_INTERPOSE(pm_malloc, malloc);
PM_INTERPOSE(pm_free, free);
PM_INTERPOSE(pm_calloc, calloc);
PM_INTERPOSE(pm_realloc, realloc);
PM_INTERPOSE(pm_reallocf, reallocf);
PM_INTERPOSE(pm_posix_memalign, posix_memalign);
PM_INTERPOSE(pm_aligned_alloc, aligned_alloc);
PM_INTERPOSE(pm_valloc, valloc);
PM_INTERPOSE(pm_malloc_size, malloc_size);

#else
#error "interpose.c supports Linux (LD_PRELOAD) and macOS (DYLD_INSERT_LIBRARIES)"
#endif
