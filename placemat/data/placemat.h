/* placemat.h: data-placement hook for custom allocators (placemat, MIT licence).
 *
 * Include this header in an allocator and compile with -DPLACEMAT_HOOK to let placemat move large
 * blocks (DESIGN §5.3, §7). Without -DPLACEMAT_HOOK every function below is a no-op macro that
 * evaluates to 0 (or to nothing), so the binary is the one the project ships.
 *
 * Header-only: the functions are static inline; the per-process state is one weak global shared by
 * every translation unit of the image that includes this header, so an allocator spread over several
 * files still has one base, one set of counters and one summary. Needs GCC or Clang (weak symbols,
 * __atomic builtins); on glibc older than 2.34 link with -pthread (pthread_atfork).
 *
 * ---------------------------------------------------------------------------------------------
 * API (all names starting placemat__ are private)
 *
 *   int    placemat_active(void);
 *          Nonzero when a colour, a step or any logging is configured. Allocators call it only on
 *          their large-block path (out of line), never on the small-block fast path.
 *   void   placemat_region(const void *base, size_t n);
 *          Optional: report a region the allocator maps. The first call fixes the per-process base
 *          for k (unless a block was already passed to placemat_k). Logged at PLACEMAT_ADDRLOG >= 1.
 *   size_t placemat_k(const void *block);
 *          k = floor((block - base) / G), in signed 64-bit arithmetic, then cast to size_t (two's
 *          complement), with G = PLACEMAT_MIN and base the first region reported, else the first
 *          block passed here. Pass the block's address *before* any offset is added.
 *   size_t placemat_colour(size_t k, size_t size, size_t spare_room);
 *          The offset for block k: 0 when nothing is configured or size < PLACEMAT_MIN; otherwise
 *          (colour mod colour_span) + the step offset (hashed: unit * (splitmix64(seed ^ k) mod
 *          (step_span / unit)); linear: (s * (k + 1)) mod step_span, as Python integers), wrapped
 *          when it exceeds spare_room: unit * ((offset / unit) mod (spare_room / unit)), or 0 when
 *          spare_room < unit. Bit-for-bit the same as placemat/colour.py's block_offset(spare=...).
 *          Every call with size >= PLACEMAT_MIN while placemat_active() is counted ("coloured",
 *          at every setting, no offset included) and folded into khash; offsets that had to be
 *          wrapped are counted ("wrapped").
 *   void   placemat_log_block(const void *p, size_t size, size_t k, size_t offset);
 *   void   placemat_log_alloc(const void *p, size_t size);   (k and offset logged as "-")
 *   void   placemat_log_free(const void *p);
 *          Address logging at PLACEMAT_ADDRLOG >= 2, kept in memory (first 4096 events) and written
 *          in the exit summary; never a line per block to stderr.
 *   uint64_t placemat_splitmix64(uint64_t x);   H(x) = mix(x + 0x9E3779B97F4A7C15).
 *   placemat_vg_malloclike(p, size), placemat_vg_freelike(p)
 *          Valgrind client requests when PLACEMAT_VALGRIND is defined and <valgrind/valgrind.h>
 *          exists; otherwise no-ops.
 *
 * Environment (read once, on first use):
 *   PLACEMAT_COLOUR       c, a constant offset (a multiple of PLACEMAT_UNIT; decimal or 0x hex)
 *   PLACEMAT_STEP_MODE    hashed (default) | linear
 *   PLACEMAT_STEP_SEED    hashed step seed, 0 .. 2^64-1 (0 is a valid seed, not "off")
 *   PLACEMAT_STEP         linear step s (a multiple of PLACEMAT_UNIT)
 *                         With neither, no step. Both, or the one that does not match the mode, is a
 *                         start-up error: a message on stderr and _exit(2).
 *   PLACEMAT_UNIT         granularity, a power of two (default 64); must be >= the allocator's
 *                         alignment guarantee
 *   PLACEMAT_COLOUR_SPAN  default the page size (sysconf)
 *   PLACEMAT_STEP_SPAN    default 16384; it should be the L1D set stride (16 KB on Apple M2, 4 KB on
 *                         typical x86); placemat always passes it explicitly
 *   PLACEMAT_MIN          smallest coloured size and the granule G of k (default 65536)
 *   PLACEMAT_ADDRLOG      0 (default), 1 = regions, 2 = regions and blocks
 *   PLACEMAT_LOG          file to which the exit summary is APPENDED; if unset and ADDRLOG > 0 the
 *                         summary goes to stderr; if neither, no summary.
 *   An empty variable counts as unset. Spans must be positive multiples of the unit.
 *
 * Exit summary (one block per process, appended with one write(); placemat's runner parses it;
 * unknown keywords should be ignored by readers):
 *   placemat-log 1 pid=<pid>
 *   config colour=<c> step_mode=<none|hashed|linear> step=<0x seed|s|-> unit=<u> colour_span=<n>
 *          step_span=<n> min=<n> addrlog=<n>                                  (one line)
 *   base 0x<hex>                       (when a base was fixed)
 *   region 0x<hex> <bytes>             (ADDRLOG >= 1; first 64)
 *   khash 0x<16 hex>                   (h = splitmix64(h ^ k) over coloured blocks in order, h0 = 0)
 *   coloured <n>
 *   wrapped <n>
 *   block 0x<hex> <size> <k signed> <offset>   (ADDRLOG >= 2; "-" "-" from placemat_log_alloc)
 *   free 0x<hex>                       (ADDRLOG >= 2; block and free events share the 4096 cap)
 *   end
 * A forked child starts its own counters (pthread_atfork) and writes its own block.
 *
 * ---------------------------------------------------------------------------------------------
 * Rules for allocator authors (DESIGN §7, learnt on Amber):
 *  1. Take the offset from spare room the block already has (class size minus requested size), so
 *     size classes do not change: pass that spare room to placemat_colour and it wraps the offset
 *     to fit.
 *  2. Undo the offset on free, so free lists and coalescing never see an offset pointer (store the
 *     offset or recompute it; the block's start must be recoverable).
 *  3. Keep the small-block fast path untouched: test the size first, and call placemat_active()
 *     and the rest out of line, only for blocks of at least PLACEMAT_MIN.
 *  4. Keep growth points: a block that grows one item at a time must change class exactly where it
 *     did before (never count the offset against the requested size).
 *  5. Preserve the alignment guarantee: PLACEMAT_UNIT must be >= the allocator's alignment (offsets
 *     are whole units, colours and linear steps are checked to be multiples of the unit); check it
 *     with the project's own assertion build.
 *  6. Compute k with placemat_k() on the block's un-offset address, one base for the whole process
 *     (never a base per region, never a per-size divisor), so every block is on one scale.
 *
 * Sketch:
 *     p = take_block(cls);                          // unchanged fast path above this
 *     if (n >= LARGE && placemat_active()) {        // in an out-of-line function
 *         size_t k = placemat_k(p);
 *         size_t off = placemat_colour(k, n, class_size(cls) - n);
 *         remember_offset(p, off);                  // undone on free
 *         p += off;
 *         placemat_log_block(p, n, k, off);
 *     }
 *
 * Do not combine a hooked allocator with the interposer in one process: each keeps its own state
 * and writes its own summary block (same pid). Shared libraries that each include this header have
 * one state per image on macOS; on ELF the weak state is usually merged process-wide.
 */
#ifndef PLACEMAT_H
#define PLACEMAT_H

#include <stddef.h>
#include <stdint.h>

#ifdef PLACEMAT_HOOK

#if !defined(__GNUC__) && !defined(__clang__)
#error "placemat.h with PLACEMAT_HOOK needs GCC or Clang"
#endif

#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sched.h>
#include <pthread.h>
#include <sys/mman.h>

#define PLACEMAT__FN static inline __attribute__((unused))
#define PLACEMAT__SLOW static __attribute__((unused, noinline, cold))
#define PLACEMAT__MAX_REGIONS 64
#define PLACEMAT__MAX_EVENTS 4096

struct placemat__event {
    uintptr_t p;
    size_t size;
    size_t k;
    size_t off;
    int kind; /* 1 block, 2 block without k/offset, 3 free */
};

struct placemat__state {
    int init;          /* 0 not read, 1 reading, 2 ready */
    int registered;    /* atexit/atfork registered */
    int active;
    int step_mode;     /* 0 none, 1 hashed, 2 linear */
    int64_t colour;
    uint64_t seed;     /* hashed */
    int64_t s;         /* linear */
    size_t unit, colour_span, step_span, min;
    int addrlog;
    int log_fd_stderr; /* summary to stderr */
    int log_file;      /* summary to log_path */
    char log_path[4096];
    uintptr_t base;    /* 0 = not fixed yet */
    uint64_t coloured, wrapped, khash;
    int lock;
    size_t nregions;
    struct { uintptr_t base; size_t n; } regions[PLACEMAT__MAX_REGIONS];
    size_t nevents;
    struct placemat__event events[PLACEMAT__MAX_EVENTS];
};

/* One state per linked image, shared by every translation unit that includes this header. */
__attribute__((weak)) struct placemat__state placemat__st;

/* ---- arithmetic (twins of placemat/colour.py) ---- */

PLACEMAT__FN uint64_t placemat_splitmix64(uint64_t x)
{
    uint64_t z = x + 0x9E3779B97F4A7C15ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/* a mod m for signed a, m > 0, with Python's sign rule (result in [0, m)). */
PLACEMAT__FN uint64_t placemat__pymod(int64_t a, uint64_t m)
{
    if (a >= 0) return (uint64_t)a % m;
    uint64_t r = (0 - (uint64_t)a) % m;
    return r ? m - r : 0;
}

/* (a * b) mod m for a, b < m, without overflow. */
PLACEMAT__FN uint64_t placemat__mulmod(uint64_t a, uint64_t b, uint64_t m)
{
#ifdef __SIZEOF_INT128__
    return (uint64_t)(((unsigned __int128)a * b) % m);
#else
    uint64_t r = 0;
    while (b) {
        if (b & 1) r = (r >= m - a) ? r - (m - a) : r + a;
        a = (a >= m - a) ? a - (m - a) : a + a;
        b >>= 1;
    }
    return r;
#endif
}

/* floor((addr - base) / g) as a signed 64-bit value, cast to size_t. */
PLACEMAT__FN size_t placemat__k_of(uintptr_t addr, uintptr_t base, size_t g)
{
    int64_t d = (int64_t)((uint64_t)addr - (uint64_t)base);
    int64_t q = d / (int64_t)g;
    if ((d % (int64_t)g) != 0 && d < 0) q -= 1;
    return (size_t)(uint64_t)q;
}

/* Step offset of block k (k is two's complement: negative below the base). */
PLACEMAT__FN size_t placemat__step(int mode, uint64_t seed, int64_t s, size_t k, size_t unit, size_t span)
{
    if (mode == 1)
        return unit * (size_t)(placemat_splitmix64(seed ^ (uint64_t)k) % (uint64_t)(span / unit));
    if (mode == 2) {
        uint64_t k1 = placemat__pymod((int64_t)(uint64_t)k, span) + 1; /* (k + 1) mod span */
        if (k1 == span) k1 = 0;
        return (size_t)placemat__mulmod(placemat__pymod(s, span), k1, span);
    }
    return 0;
}

PLACEMAT__FN size_t placemat__wrap(size_t off, size_t spare, size_t unit)
{
    if (off <= spare) return off;
    size_t n = spare / unit;
    if (n == 0) return 0;
    return unit * ((off / unit) % n);
}

/* ---- configuration ---- */

PLACEMAT__FN void placemat__write_err(const char *s)
{
    size_t n = 0;
    while (s[n]) n++;
    while (n) {
        ssize_t w = write(2, s, n);
        if (w <= 0) return;
        s += w;
        n -= (size_t)w;
    }
}

__attribute__((noreturn)) PLACEMAT__SLOW void placemat__die(const char *what, const char *detail)
{
    /* Leave the state usable (nothing configured) in case anything runs before _exit. */
    placemat__st.active = 0;
    placemat__st.step_mode = 0;
    placemat__st.log_file = placemat__st.log_fd_stderr = 0;
    __atomic_store_n(&placemat__st.init, 2, __ATOMIC_RELEASE);
    placemat__write_err("placemat: configuration error: ");
    placemat__write_err(what);
    if (detail) {
        placemat__write_err(": ");
        placemat__write_err(detail);
    }
    placemat__write_err("\n");
    _exit(2);
}

PLACEMAT__FN const char *placemat__env(const char *name)
{
    const char *v = getenv(name);
    return (v && *v) ? v : 0;
}

/* Parse decimal or 0x hex; negative allowed when sign_ok. Returns 0 on success. */
PLACEMAT__FN int placemat__parse(const char *v, int sign_ok, uint64_t *out, int *neg)
{
    const char *p = v;
    *neg = 0;
    if (*p == '-') {
        if (!sign_ok) return -1;
        *neg = 1;
        p++;
    } else if (*p == '+') {
        p++;
    }
    uint64_t base = 10, r = 0;
    if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        base = 16;
        p += 2;
    }
    if (!*p) return -1;
    for (; *p; p++) {
        uint64_t d;
        if (*p >= '0' && *p <= '9') d = (uint64_t)(*p - '0');
        else if (base == 16 && *p >= 'a' && *p <= 'f') d = (uint64_t)(*p - 'a' + 10);
        else if (base == 16 && *p >= 'A' && *p <= 'F') d = (uint64_t)(*p - 'A' + 10);
        else return -1;
        if (r > (UINT64_MAX - d) / base) return -1;
        r = r * base + d;
    }
    *out = r;
    return 0;
}

PLACEMAT__FN uint64_t placemat__env_u64(const char *name, uint64_t dflt, int *set)
{
    const char *v = placemat__env(name);
    uint64_t r;
    int neg;
    if (set) *set = v != 0;
    if (!v) return dflt;
    if (placemat__parse(v, 0, &r, &neg)) placemat__die(name, "expected a non-negative integer (decimal or 0x hex, at most 2^64-1)");
    return r;
}

PLACEMAT__FN int64_t placemat__env_i64(const char *name, int *set)
{
    const char *v = placemat__env(name);
    uint64_t r;
    int neg;
    *set = v != 0;
    if (!v) return 0;
    if (placemat__parse(v, 1, &r, &neg) || r > (neg ? (uint64_t)1 << 63 : ((uint64_t)1 << 63) - 1))
        placemat__die(name, "expected a signed 64-bit integer (decimal or 0x hex)");
    return neg ? (int64_t)(0 - r) : (int64_t)r;
}

PLACEMAT__FN int placemat__streq(const char *a, const char *b)
{
    while (*a && *a == *b) a++, b++;
    return *a == *b;
}

PLACEMAT__FN void placemat__configure(void)
{
    struct placemat__state *st = &placemat__st;
    int set_colour, set_seed, set_step, set_cspan;
    long page = sysconf(_SC_PAGESIZE);

    st->unit = (size_t)placemat__env_u64("PLACEMAT_UNIT", 64, 0);
    if (st->unit == 0 || (st->unit & (st->unit - 1)))
        placemat__die("PLACEMAT_UNIT", "must be a power of two");
    st->colour_span = (size_t)placemat__env_u64("PLACEMAT_COLOUR_SPAN", page > 0 ? (uint64_t)page : 4096, &set_cspan);
    st->step_span = (size_t)placemat__env_u64("PLACEMAT_STEP_SPAN", 16384, 0);
    st->min = (size_t)placemat__env_u64("PLACEMAT_MIN", 65536, 0);
    if (st->colour_span == 0 || st->colour_span % st->unit)
        placemat__die("PLACEMAT_COLOUR_SPAN", "must be a positive multiple of PLACEMAT_UNIT");
    if (st->step_span == 0 || st->step_span % st->unit)
        placemat__die("PLACEMAT_STEP_SPAN", "must be a positive multiple of PLACEMAT_UNIT");
    if (st->min == 0 || st->min > ((uint64_t)1 << 62))
        placemat__die("PLACEMAT_MIN", "must be positive");
    st->colour = placemat__env_i64("PLACEMAT_COLOUR", &set_colour);
    if (placemat__pymod(st->colour, st->unit) != 0)
        placemat__die("PLACEMAT_COLOUR", "must be a multiple of PLACEMAT_UNIT");

    const char *mode = placemat__env("PLACEMAT_STEP_MODE");
    int hashed = 1;
    if (mode && placemat__streq(mode, "linear")) hashed = 0;
    else if (mode && !placemat__streq(mode, "hashed"))
        placemat__die("PLACEMAT_STEP_MODE", "must be hashed or linear");
    int dummy;
    set_seed = placemat__env("PLACEMAT_STEP_SEED") != 0;
    set_step = placemat__env("PLACEMAT_STEP") != 0;
    if (set_seed && set_step)
        placemat__die("PLACEMAT_STEP_SEED and PLACEMAT_STEP are both set", "set PLACEMAT_STEP_SEED for hashed steps or PLACEMAT_STEP for linear steps, not both");
    if (set_step && hashed)
        placemat__die("PLACEMAT_STEP is set but the step mode is hashed", "set PLACEMAT_STEP_MODE=linear, or use PLACEMAT_STEP_SEED");
    if (set_seed && !hashed)
        placemat__die("PLACEMAT_STEP_SEED is set but PLACEMAT_STEP_MODE=linear", "use PLACEMAT_STEP for linear steps");
    st->step_mode = 0;
    st->seed = 0;
    st->s = 0;
    if (set_seed) {
        st->step_mode = 1;
        st->seed = placemat__env_u64("PLACEMAT_STEP_SEED", 0, &dummy);
    } else if (set_step) {
        st->step_mode = 2;
        st->s = placemat__env_i64("PLACEMAT_STEP", &dummy);
        if (placemat__pymod(st->s, st->unit) != 0)
            placemat__die("PLACEMAT_STEP", "must be a multiple of PLACEMAT_UNIT");
    }
    (void)set_colour;
    (void)set_cspan;

    uint64_t al = placemat__env_u64("PLACEMAT_ADDRLOG", 0, 0);
    st->addrlog = al > 2 ? 2 : (int)al;
    const char *path = placemat__env("PLACEMAT_LOG");
    st->log_file = 0;
    st->log_fd_stderr = 0;
    if (path) {
        size_t n = 0;
        while (path[n]) n++;
        if (n >= sizeof st->log_path) placemat__die("PLACEMAT_LOG", "path too long");
        for (size_t i = 0; i <= n; i++) st->log_path[i] = path[i];
        st->log_file = 1;
    } else if (st->addrlog > 0) {
        st->log_fd_stderr = 1;
    }
    st->active = placemat__pymod(st->colour, st->colour_span) != 0 || st->step_mode != 0 ||
                 st->addrlog > 0 || st->log_file;
}

/* ---- exit summary ---- */

struct placemat__buf { char *p; size_t n, cap; };

PLACEMAT__FN void placemat__puts(struct placemat__buf *b, const char *s)
{
    while (*s && b->n < b->cap) b->p[b->n++] = *s++;
}

PLACEMAT__FN void placemat__putu(struct placemat__buf *b, uint64_t v)
{
    char t[24];
    int i = 23;
    t[i] = 0;
    do t[--i] = (char)('0' + v % 10); while (v /= 10);
    placemat__puts(b, t + i);
}

PLACEMAT__FN void placemat__puti(struct placemat__buf *b, int64_t v)
{
    if (v < 0) {
        placemat__puts(b, "-");
        placemat__putu(b, 0 - (uint64_t)v);
    } else {
        placemat__putu(b, (uint64_t)v);
    }
}

PLACEMAT__FN void placemat__putx(struct placemat__buf *b, uint64_t v, int width)
{
    char t[20];
    int i = 19;
    t[i] = 0;
    do t[--i] = "0123456789abcdef"[v & 15]; while ((v >>= 4) || 19 - i < width);
    placemat__puts(b, "0x");
    placemat__puts(b, t + i);
}

PLACEMAT__FN void placemat__lock(void)
{
    while (__atomic_exchange_n(&placemat__st.lock, 1, __ATOMIC_ACQUIRE))
        sched_yield();
}

PLACEMAT__FN void placemat__unlock(void)
{
    __atomic_store_n(&placemat__st.lock, 0, __ATOMIC_RELEASE);
}

PLACEMAT__SLOW void placemat__write_summary(void)
{
    struct placemat__state *st = &placemat__st;
    if (!st->log_file && !st->log_fd_stderr) return;
    size_t cap = 8192 + (size_t)PLACEMAT__MAX_REGIONS * 48 + (size_t)PLACEMAT__MAX_EVENTS * 96;
    void *mem = mmap(0, cap, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED) return;
    struct placemat__buf b = { (char *)mem, 0, cap };

    placemat__lock();
    placemat__puts(&b, "placemat-log 1 pid=");
    placemat__putu(&b, (uint64_t)getpid());
    placemat__puts(&b, "\nconfig colour=");
    placemat__puti(&b, st->colour);
    placemat__puts(&b, st->step_mode == 1 ? " step_mode=hashed step=" : st->step_mode == 2 ? " step_mode=linear step=" : " step_mode=none step=-");
    if (st->step_mode == 1) placemat__putx(&b, st->seed, 16);
    if (st->step_mode == 2) placemat__puti(&b, st->s);
    placemat__puts(&b, " unit=");
    placemat__putu(&b, st->unit);
    placemat__puts(&b, " colour_span=");
    placemat__putu(&b, st->colour_span);
    placemat__puts(&b, " step_span=");
    placemat__putu(&b, st->step_span);
    placemat__puts(&b, " min=");
    placemat__putu(&b, st->min);
    placemat__puts(&b, " addrlog=");
    placemat__putu(&b, (uint64_t)st->addrlog);
    placemat__puts(&b, "\n");
    uintptr_t base = __atomic_load_n(&st->base, __ATOMIC_ACQUIRE);
    if (base) {
        placemat__puts(&b, "base ");
        placemat__putx(&b, base, 0);
        placemat__puts(&b, "\n");
    }
    for (size_t i = 0; i < st->nregions; i++) {
        placemat__puts(&b, "region ");
        placemat__putx(&b, st->regions[i].base, 0);
        placemat__puts(&b, " ");
        placemat__putu(&b, st->regions[i].n);
        placemat__puts(&b, "\n");
    }
    placemat__puts(&b, "khash ");
    placemat__putx(&b, __atomic_load_n(&st->khash, __ATOMIC_RELAXED), 16);
    placemat__puts(&b, "\ncoloured ");
    placemat__putu(&b, __atomic_load_n(&st->coloured, __ATOMIC_RELAXED));
    placemat__puts(&b, "\nwrapped ");
    placemat__putu(&b, __atomic_load_n(&st->wrapped, __ATOMIC_RELAXED));
    placemat__puts(&b, "\n");
    for (size_t i = 0; i < st->nevents; i++) {
        struct placemat__event *e = &st->events[i];
        placemat__puts(&b, e->kind == 3 ? "free " : "block ");
        placemat__putx(&b, e->p, 0);
        if (e->kind != 3) {
            placemat__puts(&b, " ");
            placemat__putu(&b, e->size);
            if (e->kind == 1) {
                placemat__puts(&b, " ");
                placemat__puti(&b, (int64_t)(uint64_t)e->k);
                placemat__puts(&b, " ");
                placemat__putu(&b, e->off);
            } else {
                placemat__puts(&b, " - -");
            }
        }
        placemat__puts(&b, "\n");
    }
    placemat__unlock();
    placemat__puts(&b, "end\n");

    int fd = 2;
    if (st->log_file) fd = open(st->log_path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0644);
    if (fd >= 0) {
        const char *p = b.p;
        size_t n = b.n;
        while (n) {
            ssize_t w = write(fd, p, n);
            if (w <= 0) break;
            p += w;
            n -= (size_t)w;
        }
        if (fd != 2) close(fd);
    }
    munmap(mem, cap);
}

PLACEMAT__SLOW void placemat__atexit(void) { placemat__write_summary(); }

PLACEMAT__SLOW void placemat__atfork_child(void)
{
    /* The child is a new process: its own counters and events (the address space, so the base and
       the regions, are inherited). The lock may have been held by a thread that does not exist here. */
    struct placemat__state *st = &placemat__st;
    st->lock = 0;
    st->coloured = st->wrapped = st->khash = 0;
    st->nevents = 0;
}

PLACEMAT__SLOW void placemat__init_slow(void)
{
    int expected = 0;
    if (__atomic_compare_exchange_n(&placemat__st.init, &expected, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        placemat__configure(); /* no allocation in here: it may run inside malloc */
        __atomic_store_n(&placemat__st.init, 2, __ATOMIC_RELEASE);
        /* atexit/pthread_atfork may allocate (and so re-enter an interposed malloc): register them only
           once the state is ready. */
        if ((placemat__st.log_file || placemat__st.log_fd_stderr) &&
            !__atomic_exchange_n(&placemat__st.registered, 1, __ATOMIC_ACQ_REL)) {
            atexit(placemat__atexit);
            pthread_atfork(0, 0, placemat__atfork_child);
        }
    } else {
        while (__atomic_load_n(&placemat__st.init, __ATOMIC_ACQUIRE) != 2)
            sched_yield();
    }
}

PLACEMAT__FN void placemat__ready(void)
{
    if (__builtin_expect(__atomic_load_n(&placemat__st.init, __ATOMIC_ACQUIRE) != 2, 0))
        placemat__init_slow();
}

/* Tests only: forget everything and read the environment again (single-threaded use). */
PLACEMAT__SLOW void placemat__reset_for_tests(void)
{
    struct placemat__state *st = &placemat__st;
    st->base = 0;
    st->coloured = st->wrapped = st->khash = 0;
    st->nregions = st->nevents = 0;
    __atomic_store_n(&st->init, 0, __ATOMIC_RELEASE);
    placemat__ready();
}

/* ---- public API ---- */

PLACEMAT__FN int placemat_active(void)
{
    placemat__ready();
    return placemat__st.active;
}

PLACEMAT__FN void placemat_region(const void *base, size_t n)
{
    struct placemat__state *st = &placemat__st;
    uintptr_t zero = 0;
    placemat__ready();
    if (base) __atomic_compare_exchange_n(&st->base, &zero, (uintptr_t)base, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    if (st->addrlog >= 1) {
        placemat__lock();
        if (st->nregions < PLACEMAT__MAX_REGIONS) {
            st->regions[st->nregions].base = (uintptr_t)base;
            st->regions[st->nregions].n = n;
            st->nregions++;
        }
        placemat__unlock();
    }
}

PLACEMAT__FN size_t placemat_k(const void *block)
{
    struct placemat__state *st = &placemat__st;
    placemat__ready();
    uintptr_t base = __atomic_load_n(&st->base, __ATOMIC_ACQUIRE);
    if (__builtin_expect(base == 0, 0)) {
        uintptr_t zero = 0;
        if (__atomic_compare_exchange_n(&st->base, &zero, (uintptr_t)block, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
            base = (uintptr_t)block;
        else
            base = zero; /* someone else fixed it first */
    }
    return placemat__k_of((uintptr_t)block, base, st->min);
}

/* The raw (unwrapped) offset of block k at the configured setting. */
PLACEMAT__FN size_t placemat__offset(size_t k)
{
    struct placemat__state *st = &placemat__st;
    return (size_t)placemat__pymod(st->colour, st->colour_span) +
           placemat__step(st->step_mode, st->seed, st->s, k, st->unit, st->step_span);
}

PLACEMAT__FN void placemat__note_wrapped(void)
{
    __atomic_fetch_add(&placemat__st.wrapped, 1, __ATOMIC_RELAXED);
}

PLACEMAT__FN size_t placemat_colour(size_t k, size_t size, size_t spare_room)
{
    struct placemat__state *st = &placemat__st;
    placemat__ready();
    if (!st->active || size < st->min) return 0;
    size_t off = placemat__offset(k);
    __atomic_fetch_add(&st->coloured, 1, __ATOMIC_RELAXED);
    uint64_t h = __atomic_load_n(&st->khash, __ATOMIC_RELAXED);
    while (!__atomic_compare_exchange_n(&st->khash, &h, placemat_splitmix64(h ^ (uint64_t)k), 1,
                                        __ATOMIC_RELAXED, __ATOMIC_RELAXED))
        ;
    if (off > spare_room) {
        placemat__note_wrapped();
        off = placemat__wrap(off, spare_room, st->unit);
    }
    return off;
}

PLACEMAT__FN void placemat__log_event(int kind, const void *p, size_t size, size_t k, size_t off)
{
    struct placemat__state *st = &placemat__st;
    placemat__ready();
    if (st->addrlog < 2) return;
    placemat__lock();
    if (st->nevents < PLACEMAT__MAX_EVENTS) {
        struct placemat__event *e = &st->events[st->nevents++];
        e->kind = kind;
        e->p = (uintptr_t)p;
        e->size = size;
        e->k = k;
        e->off = off;
    }
    placemat__unlock();
}

PLACEMAT__FN void placemat_log_block(const void *p, size_t size, size_t k, size_t offset)
{
    placemat__log_event(1, p, size, k, offset);
}

PLACEMAT__FN void placemat_log_alloc(const void *p, size_t size) { placemat__log_event(2, p, size, 0, 0); }

PLACEMAT__FN void placemat_log_free(const void *p) { placemat__log_event(3, p, 0, 0, 0); }

/* ---- Valgrind ---- */
#if defined(PLACEMAT_VALGRIND) && defined(__has_include)
#if __has_include(<valgrind/valgrind.h>)
#include <valgrind/valgrind.h>
#define PLACEMAT__HAVE_VG 1
#endif
#endif
#ifdef PLACEMAT__HAVE_VG
#define placemat_vg_malloclike(p, size) VALGRIND_MALLOCLIKE_BLOCK((p), (size), 0, 0)
#define placemat_vg_freelike(p) VALGRIND_FREELIKE_BLOCK((p), 0)
#else
#define placemat_vg_malloclike(p, size) ((void)(p), (void)(size))
#define placemat_vg_freelike(p) ((void)(p))
#endif

#else /* !PLACEMAT_HOOK: everything is a no-op and the binary is unchanged */

#define placemat_active() 0
#define placemat_region(base, n) ((void)(base), (void)(n))
#define placemat_k(block) ((void)(block), (size_t)0)
#define placemat_colour(k, size, spare_room) ((void)(k), (void)(size), (void)(spare_room), (size_t)0)
#define placemat_log_block(p, size, k, offset) ((void)(p), (void)(size), (void)(k), (void)(offset))
#define placemat_log_alloc(p, size) ((void)(p), (void)(size))
#define placemat_log_free(p) ((void)(p))
#define placemat_vg_malloclike(p, size) ((void)(p), (void)(size))
#define placemat_vg_freelike(p) ((void)(p))

#endif /* PLACEMAT_HOOK */
#endif /* PLACEMAT_H */
