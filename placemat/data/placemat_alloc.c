/* placemat_alloc.c: a colouring allocator over an underlying malloc (placemat, MIT licence).
 * See placemat_alloc.h for the interface and placemat.h for the configuration.
 *
 * Build: cc -O2 -DPLACEMAT_HOOK -c placemat_alloc.c   (uses the C library's malloc underneath)
 * With -DPLACEMAT_ALLOC_INTERPOSER the underlying functions are placemat__real_* (interpose.c).
 *
 * Large block layout (H = sizeof(struct pm_hdr) = 32, A = max(unit, 16, requested alignment)):
 *
 *   u = underlying malloc(n + H + A + colour_span + step_span)
 *   p0 = align_up(u + H, A)                 the no-offset payload start (A-aligned)
 *   ret = p0 + offset                       offset = placemat_colour(k, n, colour_span + step_span)
 *   header at ret - H: { u, n, A, magic ^ ret }
 *
 * The spare room is colour_span + step_span, which every unwrapped offset fits (colour < colour_span,
 * step < step_span), so the interposer never wraps; for an alignment request above the unit the
 * offset is rounded down to that alignment and counted as wrapped.
 *
 * Ownership. free(), realloc() and malloc_usable_size() see pointers this allocator never returned:
 * small blocks it passed through, blocks allocated before the interposer loaded or by code that calls
 * the underlying allocator directly. Reading the "header" in front of such a pointer is unsafe (an
 * mmap'd block starts 16 bytes into its page, so 32 bytes before it may be unmapped) and a magic word
 * alone can be forged by user data. So every large block's returned pointer is recorded in an
 * open-addressing set in its own mmap'd table (no malloc), and a pointer is ours only if it is in the
 * set; the header's magic is then only a corruption check. Lookups (every free, small ones included)
 * are lock-free: a range filter first (two loads and compares for most small blocks), then a probe
 * validated by a sequence counter. Inserts and removes (large blocks only) take a mutex; removes
 * leave a tombstone, and when half the slots are used the table is rebuilt from its live entries
 * (growing to keep at least 4 slots per live block), so it neither fills up nor degrades with churn.
 * A pointer is removed before its memory goes back to the underlying allocator and inserted after it
 * is obtained, so a concurrent lookup of any other pointer is never confused. Only if the table
 * cannot be mapped are large blocks passed through uncoloured (a one-line warning on stderr).
 */
#include "placemat.h"
#include "placemat_alloc.h"

#include <errno.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
#include <pthread.h>
#include <sched.h>

#if defined(__APPLE__)
#include <malloc/malloc.h>
#elif defined(__linux__)
#include <malloc.h>
#endif

#ifndef PLACEMAT_HOOK
#error "compile placemat_alloc.c with -DPLACEMAT_HOOK"
#endif

#define PM_HIDDEN __attribute__((visibility("hidden")))

#ifdef PLACEMAT_ALLOC_INTERPOSER
PM_HIDDEN void *placemat__real_malloc(size_t n);
PM_HIDDEN void placemat__real_free(void *p);
PM_HIDDEN void *placemat__real_calloc(size_t n, size_t m);
PM_HIDDEN void *placemat__real_realloc(void *p, size_t n);
PM_HIDDEN int placemat__real_posix_memalign(void **out, size_t align, size_t n);
PM_HIDDEN size_t placemat__real_usable_size(const void *p);
#define REAL_MALLOC placemat__real_malloc
#define REAL_FREE placemat__real_free
#define REAL_CALLOC placemat__real_calloc
#define REAL_REALLOC placemat__real_realloc
#define REAL_POSIX_MEMALIGN placemat__real_posix_memalign
#define REAL_USABLE placemat__real_usable_size
#else
static size_t pm_libc_usable(const void *p)
{
#if defined(__APPLE__)
    return malloc_size(p);
#elif defined(__linux__)
    return malloc_usable_size((void *)p);
#else
    (void)p;
    return 0;
#endif
}
#define REAL_MALLOC malloc
#define REAL_FREE free
#define REAL_CALLOC calloc
#define REAL_REALLOC realloc
#define REAL_POSIX_MEMALIGN posix_memalign
#define REAL_USABLE pm_libc_usable
#endif

/* ---- header ---- */

struct pm_hdr {
    void *orig;   /* the underlying block */
    size_t size;  /* requested size */
    size_t align; /* A used for the payload */
    uint64_t magic;
};
#define PM_H sizeof(struct pm_hdr)
#define PM_MAGIC 0x706c6163656d6174ULL /* "placemat" */

static struct pm_hdr *pm_hdr_of(const void *p) { return (struct pm_hdr *)((char *)p - PM_H); }

/* ---- ownership set ---- */

/* A table is one mmap'd array: t[0] holds log2 of its slot count, slots are t[1 .. cap]. Keeping the
   size inside the table means a reader can never pair a table with another table's size. */
#define PM_EMPTY ((uintptr_t)0)
#define PM_TOMB ((uintptr_t)1)
#define PM_MIN_BITS 12

static pthread_mutex_t pm_mu = PTHREAD_MUTEX_INITIALIZER; /* writers: insert, remove, rebuild */
static unsigned long pm_gen;      /* odd while a rebuild is under way (seqlock for readers) */
static uintptr_t *pm_cur;         /* the current table (atomic) */
static uintptr_t *pm_old_tab;      /* the previous table, reused by the next rebuild of that size */
static size_t pm_used, pm_live;   /* slots not EMPTY / live entries in pm_cur (under pm_mu) */
static uintptr_t pm_lo = UINTPTR_MAX, pm_hi; /* range of pointers ever inserted (atomic) */
static int pm_atfork_done;

static size_t pm_hash(uintptr_t p, size_t bits) { return (size_t)((((uint64_t)p >> 4) * 0x9E3779B97F4A7C15ULL) >> (64 - bits)); }

static void pm_prefork(void) { pthread_mutex_lock(&pm_mu); }
static void pm_postfork(void) { pthread_mutex_unlock(&pm_mu); }

static uintptr_t *pm_map(size_t bits)
{
    void *m = mmap(0, (((size_t)1 << bits) + 1) * sizeof(uintptr_t), PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (m == MAP_FAILED) return 0;
    ((uintptr_t *)m)[0] = bits;
    return (uintptr_t *)m;
}

/* Under pm_mu: put p in table t (no rebuild). */
static void pm_put(uintptr_t *t, uintptr_t p)
{
    size_t bits = t[0], mask = ((size_t)1 << bits) - 1, i = pm_hash(p, bits);
    for (;; i = (i + 1) & mask) {
        uintptr_t v = __atomic_load_n(&t[1 + i], __ATOMIC_RELAXED);
        if (v == PM_EMPTY || v == PM_TOMB) {
            __atomic_store_n(&t[1 + i], p, __ATOMIC_RELEASE);
            if (v == PM_EMPTY) pm_used++;
            return;
        }
    }
}

/* Under pm_mu: copy the live entries into a clean table with at least 4 slots per live entry. Readers
   that overlap it see pm_gen change and look again; a table is never unmapped (a slow reader may still
   be probing it), only reused as the spare, so old tables cost address space, not correctness. */
static int pm_rebuild(void)
{
    size_t bits = PM_MIN_BITS;
    while (((size_t)1 << bits) < 4 * (pm_live + 1)) bits++;
    uintptr_t *t = pm_old_tab && pm_old_tab[0] == bits ? pm_old_tab : pm_map(bits);
    if (!t) return -1;
    unsigned long g = __atomic_load_n(&pm_gen, __ATOMIC_RELAXED);
    __atomic_store_n(&pm_gen, g + 1, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    for (size_t i = 1; i <= ((size_t)1 << bits); i++) __atomic_store_n(&t[i], PM_EMPTY, __ATOMIC_RELAXED);
    uintptr_t *old = pm_cur;
    pm_used = 0;
    if (old)
        for (size_t i = 1; i <= ((size_t)1 << old[0]); i++) {
            uintptr_t v = __atomic_load_n(&old[i], __ATOMIC_RELAXED);
            if (v != PM_EMPTY && v != PM_TOMB) pm_put(t, v);
        }
    __atomic_store_n(&pm_cur, t, __ATOMIC_RELAXED);
    __atomic_store_n(&pm_gen, g + 2, __ATOMIC_RELEASE);
    if (old && old != t) pm_old_tab = old;
    return 0;
}

static int pm_set_insert(uintptr_t p)
{
    if (!__atomic_load_n(&pm_atfork_done, __ATOMIC_ACQUIRE) && !__atomic_exchange_n(&pm_atfork_done, 1, __ATOMIC_ACQ_REL))
        pthread_atfork(pm_prefork, pm_postfork, pm_postfork); /* may allocate: not under pm_mu */
    pthread_mutex_lock(&pm_mu);
    if (!pm_cur || 2 * (pm_used + 1) > ((size_t)1 << pm_cur[0])) {
        if (pm_rebuild()) {
            pthread_mutex_unlock(&pm_mu);
            return -1;
        }
    }
    pm_put(pm_cur, p);
    pm_live++;
    uintptr_t lo = __atomic_load_n(&pm_lo, __ATOMIC_RELAXED), hi = __atomic_load_n(&pm_hi, __ATOMIC_RELAXED);
    if (p < lo) __atomic_store_n(&pm_lo, p, __ATOMIC_RELEASE);
    if (p > hi) __atomic_store_n(&pm_hi, p, __ATOMIC_RELEASE);
    pthread_mutex_unlock(&pm_mu);
    return 0;
}

static void pm_set_remove(uintptr_t p)
{
    pthread_mutex_lock(&pm_mu);
    uintptr_t *t = pm_cur;
    size_t bits = t[0], mask = ((size_t)1 << bits) - 1, i = pm_hash(p, bits);
    for (size_t n = 0; n <= mask; n++, i = (i + 1) & mask) {
        uintptr_t v = __atomic_load_n(&t[1 + i], __ATOMIC_RELAXED);
        if (v == p) {
            __atomic_store_n(&t[1 + i], PM_TOMB, __ATOMIC_RELEASE);
            pm_live--;
            break;
        }
        if (v == PM_EMPTY) break;
    }
    pthread_mutex_unlock(&pm_mu);
}

/* Lock-free: is p a live block of ours? Inserts and removes of *other* pointers cannot hide p (a slot
   on p's probe path is never emptied except by a rebuild, which readers detect through pm_gen). */
int placemat_owns(const void *ptr)
{
    uintptr_t p = (uintptr_t)ptr;
    if (p < __atomic_load_n(&pm_lo, __ATOMIC_ACQUIRE) || p > __atomic_load_n(&pm_hi, __ATOMIC_ACQUIRE)) return 0;
    for (;;) {
        unsigned long g = __atomic_load_n(&pm_gen, __ATOMIC_ACQUIRE);
        if (g & 1) {
            sched_yield();
            continue;
        }
        uintptr_t *t = __atomic_load_n(&pm_cur, __ATOMIC_ACQUIRE);
        int found = 0;
        if (t) {
            size_t bits = __atomic_load_n(&t[0], __ATOMIC_RELAXED), mask = ((size_t)1 << bits) - 1, i = pm_hash(p, bits);
            for (size_t n = 0; n <= mask; n++, i = (i + 1) & mask) {
                uintptr_t v = __atomic_load_n(&t[1 + i], __ATOMIC_RELAXED);
                if (v == p) {
                    found = 1;
                    break;
                }
                if (v == PM_EMPTY) break;
            }
        }
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (__atomic_load_n(&pm_gen, __ATOMIC_RELAXED) == g) return found;
    }
}

/* ---- large blocks ---- */

static size_t pm_base_align(void) { return placemat__st.unit > 16 ? placemat__st.unit : 16; }

static size_t pm_spare(void) { return placemat__st.colour_span + placemat__st.step_span; }

static __attribute__((noreturn, cold)) void pm_corrupt(const void *p)
{
    (void)p;
    placemat__write_err("placemat: corrupted header in front of a coloured block (heap underflow?)\n");
    abort();
}

static struct pm_hdr *pm_checked_hdr(const void *p)
{
    struct pm_hdr *h = pm_hdr_of(p);
    if (h->magic != (PM_MAGIC ^ (uint64_t)(uintptr_t)p)) pm_corrupt(p);
    return h;
}

/* Place a large block in the underlying block u (of n + extra bytes, extra from pm_extra). */
static void *pm_place(void *u, size_t n, size_t A, size_t req_align)
{
    size_t k = placemat_k(u);
    size_t off = placemat_colour(k, n, pm_spare());
    if (req_align > placemat__st.unit && off % req_align) {
        off -= off % req_align;
        placemat__note_wrapped();
    }
    uintptr_t p0 = ((uintptr_t)u + PM_H + A - 1) & ~(uintptr_t)(A - 1);
    char *ret = (char *)p0 + off;
    struct pm_hdr *h = pm_hdr_of(ret);
    h->orig = u;
    h->size = n;
    h->align = A;
    h->magic = PM_MAGIC ^ (uint64_t)(uintptr_t)ret;
    placemat_log_block(ret, n, k, off);
    return ret;
}

static int pm_extra(size_t n, size_t A, size_t *total)
{
    size_t extra = PM_H + A + pm_spare();
    if (n > SIZE_MAX - extra) return -1;
    *total = n + extra;
    return 0;
}

static int pm_full_warned;

static void pm_warn_full(void)
{
    if (!__atomic_exchange_n(&pm_full_warned, 1, __ATOMIC_RELAXED))
        placemat__write_err("placemat: cannot map the block table; large blocks are not coloured\n");
}

/* A large block, or NULL with *fallback set when it must go to the underlying allocator. */
static void *pm_alloc_large(size_t n, size_t req_align, int zero, int *fallback)
{
    size_t A = pm_base_align(), total;
    if (req_align > A) A = req_align;
    *fallback = 0;
    if (pm_extra(n, A, &total)) {
        errno = ENOMEM;
        return 0;
    }
    void *u = zero ? REAL_CALLOC(1, total) : REAL_MALLOC(total);
    if (!u) return 0;
    void *ret = pm_place(u, n, A, req_align);
    if (pm_set_insert((uintptr_t)ret)) {
        REAL_FREE(u);
        pm_warn_full();
        *fallback = 1;
        return 0;
    }
    return ret;
}

static void pm_free_large(void *p)
{
    struct pm_hdr *h = pm_checked_hdr(p);
    void *u = h->orig;
    h->magic = 0;
    pm_set_remove((uintptr_t)p);
    placemat_log_free(p);
    REAL_FREE(u);
}

/* ---- public functions ---- */

void *placemat_malloc(size_t n)
{
    placemat__ready();
    if (n < placemat__st.min) return REAL_MALLOC(n);
    int fallback;
    void *p = pm_alloc_large(n, 0, 0, &fallback);
    return fallback ? REAL_MALLOC(n) : p;
}

void *placemat_calloc(size_t n, size_t m)
{
    size_t total;
    placemat__ready();
    if (m && n > SIZE_MAX / m) {
        errno = ENOMEM;
        return 0;
    }
    total = n * m;
    if (total < placemat__st.min) return REAL_CALLOC(n, m);
    int fallback;
    void *p = pm_alloc_large(total, 0, 1, &fallback);
    return fallback ? REAL_CALLOC(n, m) : p;
}

void *placemat_memalign(size_t align, size_t n)
{
    placemat__ready();
    if (align == 0 || (align & (align - 1))) {
        errno = EINVAL;
        return 0;
    }
    if (n >= placemat__st.min) {
        int fallback;
        void *p = pm_alloc_large(n, align, 0, &fallback);
        if (!fallback) return p;
    }
    void *out = 0;
    if (align < sizeof(void *)) align = sizeof(void *);
    int e = REAL_POSIX_MEMALIGN(&out, align, n);
    if (e) {
        errno = e;
        return 0;
    }
    return out;
}

void placemat_free(void *p)
{
    if (!p) return;
    if (placemat_owns(p)) pm_free_large(p);
    else REAL_FREE(p);
}

size_t placemat_usable_size(const void *p)
{
    if (!p) return 0;
    if (placemat_owns(p)) return pm_checked_hdr(p)->size;
    return REAL_USABLE(p);
}

void *placemat_realloc(void *p, size_t n)
{
    if (!p) return placemat_malloc(n);
    placemat__ready();
    if (!placemat_owns(p)) {
        /* Not ours: a small block (or a foreign one). Growing to a large size makes it a coloured block. */
        if (n < placemat__st.min) return REAL_REALLOC(p, n);
        size_t old = REAL_USABLE(p);
        int fallback;
        void *q = pm_alloc_large(n, 0, 0, &fallback);
        if (fallback) return REAL_REALLOC(p, n);
        if (!q) return 0;
        memcpy(q, p, old < n ? old : n);
        REAL_FREE(p);
        return q;
    }

    struct pm_hdr *h = pm_checked_hdr(p);
    size_t oldsize = h->size, A = h->align, keep = oldsize < n ? oldsize : n;
    if (n < placemat__st.min || A != pm_base_align()) {
        /* Shrinking below the threshold, or a block with a requested alignment (realloc does not keep
           it): allocate afresh, copy, free. */
        void *q = placemat_malloc(n ? n : 1);
        if (!q) return 0;
        memcpy(q, p, keep);
        pm_free_large(p);
        return q;
    }

    /* Large to large: let the underlying realloc move or extend the block (mremap for big ones),
       then recompute k and the offset for its new address and move the payload there. */
    size_t total;
    if (pm_extra(n, A, &total)) {
        errno = ENOMEM;
        return 0;
    }
    void *u = h->orig;
    size_t rel = (size_t)((char *)p - (char *)u); /* < PM_H + A + spare, so rel + keep <= total */
    pm_set_remove((uintptr_t)p);
    void *u2 = REAL_REALLOC(u, total);
    if (!u2) {
        if (pm_set_insert((uintptr_t)p)) pm_warn_full(); /* only if mmap fails */
        return 0;
    }
    placemat_log_free(p);
    size_t k = placemat_k(u2);
    size_t off = placemat_colour(k, n, pm_spare());
    uintptr_t p0 = ((uintptr_t)u2 + PM_H + A - 1) & ~(uintptr_t)(A - 1);
    char *ret = (char *)p0 + off;
    memmove(ret, (char *)u2 + rel, keep);
    struct pm_hdr *h2 = pm_hdr_of(ret);
    h2->orig = u2;
    h2->size = n;
    h2->align = A;
    h2->magic = PM_MAGIC ^ (uint64_t)(uintptr_t)ret;
    placemat_log_block(ret, n, k, off);
    if (pm_set_insert((uintptr_t)ret)) {
        /* No table (mmap failed): hand back an uncoloured copy instead. */
        void *q = REAL_MALLOC(n);
        if (q) memcpy(q, ret, keep);
        REAL_FREE(u2);
        pm_warn_full();
        return q;
    }
    return ret;
}
