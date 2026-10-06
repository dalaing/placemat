/* zbench.c: a small zstd benchmark harness speaking placemat's benchmark protocol (placemat, MIT).
 *
 * Uses only zstd's public (and static-linking-only) API; it contains no zstd source.
 *
 * Prints one line per case: `name value iterations us`, value being the wall time in microseconds
 * of `iterations` back-to-back operations, after one untimed warm-up operation (which also checks
 * the round trip). PLACEMAT_CASES (comma-separated) selects cases; unset, every case runs.
 * `zbench --list` prints the case names.
 *
 * The corpus is generated in memory from a fixed seed, so every execution compresses the same bytes:
 *   text  8 MB of word-like text (log-uniform word frequencies over a 6000-word made-up vocabulary)
 *   rec   8 MB of 48-byte binary records (timestamps, ids, types, a few random bytes, pooled strings)
 *   rand  8 MB of random bytes (incompressible: zstd stores raw blocks)
 *
 * Data colouring (DESIGN §7): built with -DPLACEMAT_HOOK, every zstd context is created with a
 * ZSTD_customMem over placemat_alloc (placemat_zstd_alloc/free), and the harness's own buffers
 * (corpus, compressed and output buffers) come from placemat_malloc, so every large buffer the
 * benchmark touches is coloured. Without the flag, plain malloc and the default contexts: the build
 * as zstd's users get it.
 */
#define ZSTD_STATIC_LINKING_ONLY
#include "zstd.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef PLACEMAT_HOOK
#include "placemat_alloc.h"
static const ZSTD_customMem zmem = {placemat_zstd_alloc, placemat_zstd_free, NULL};
#define BMALLOC placemat_malloc
#define BFREE placemat_free
#define NEW_CCTX() ZSTD_createCCtx_advanced(zmem)
#define NEW_DCTX() ZSTD_createDCtx_advanced(zmem)
#else
#define BMALLOC malloc
#define BFREE free
#define NEW_CCTX() ZSTD_createCCtx()
#define NEW_DCTX() ZSTD_createDCtx()
#endif

#define MB (1u << 20)

static void die(const char *what, const char *detail)
{
    fprintf(stderr, "zbench: %s%s%s\n", what, detail ? ": " : "", detail ? detail : "");
    exit(1);
}

static void *xmalloc(size_t n)
{
    void *p = BMALLOC(n);
    if (!p) die("out of memory", 0);
    return p;
}

static double now_us(void)
{
    struct timespec t;
#ifdef CLOCK_MONOTONIC_RAW
    clock_gettime(CLOCK_MONOTONIC_RAW, &t);
#else
    clock_gettime(CLOCK_MONOTONIC, &t);
#endif
    return t.tv_sec * 1e6 + t.tv_nsec / 1e3;
}

/* ---- corpus ---- */

static uint64_t rs;

static uint64_t rnd(void)
{
    uint64_t z = (rs += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static double unif(void) { return (rnd() >> 11) * (1.0 / 9007199254740992.0); }

/* log-uniform index in [0, n): small indices are common */
static size_t skew(size_t n)
{
    size_t i = (size_t)exp(unif() * log((double)n + 1)) - 1;
    return i < n ? i : n - 1;
}

#define TEXT_N (8 * MB)
#define REC_N (8 * MB)
#define RAND_N (8 * MB)

static void gen_text(unsigned char *o, size_t n)
{
    enum { V = 6000 };
    static char words[V][12];
    static unsigned char wl[V];
    static const char freq[] = "etaoinshrdlcumwfgypbvkjxqz";
    rs = 1;
    for (int i = 0; i < V; i++) {
        int len = 1 + (int)(rnd() % 3) + (int)(rnd() % 4) + (i > 200 ? (int)(rnd() % 4) : 0);
        for (int j = 0; j < len; j++) words[i][j] = freq[skew(26)];
        wl[i] = (unsigned char)len;
    }
    size_t at = 0, line = 0;
    int cap = 1;
    while (at < n) {
        size_t w = skew(V);
        for (int j = 0; j < wl[w] && at < n; j++, line++) {
            char c = words[w][j];
            o[at++] = (unsigned char)(cap && j == 0 ? c - 32 : c);
        }
        cap = 0;
        uint64_t r = rnd() % 100;
        if (r < 6 && at < n) o[at++] = ',', line++;
        else if (r < 11 && at < n) o[at++] = '.', line++, cap = 1;
        if (at < n) {
            if (line > 72) o[at++] = '\n', line = 0;
            else o[at++] = ' ', line++;
        }
    }
}

static void gen_rec(unsigned char *o, size_t n)
{
    static char pool[64][16];
    rs = 2;
    for (int i = 0; i < 64; i++)
        for (int j = 0; j < 16; j++) pool[i][j] = (char)('a' + rnd() % 26);
    uint64_t ts = 1700000000000ULL;
    uint32_t ctr = 0;
    for (size_t at = 0; at + 48 <= n; at += 48) {
        unsigned char *r = o + at;
        ts += rnd() % 1000;
        uint32_t id = (uint32_t)(1000 + skew(1000));
        uint16_t ty = (uint16_t)skew(16);
        uint64_t x = rnd();
        memcpy(r, &ts, 8);
        memcpy(r + 8, &id, 4);
        memcpy(r + 12, &ty, 2);
        memset(r + 14, 0, 2);
        memcpy(r + 16, &x, 8);
        memcpy(r + 24, pool[skew(64)], 16);
        ctr++;
        memcpy(r + 40, &ctr, 4);
        memset(r + 44, 0xff, 4);
    }
    memset(o + n - n % 48, 0, n % 48);
}

static void gen_rand(unsigned char *o, size_t n)
{
    rs = 3;
    for (size_t at = 0; at + 8 <= n; at += 8) {
        uint64_t x = rnd();
        memcpy(o + at, &x, 8);
    }
}

struct corpus {
    const char *name;
    size_t n;
    void (*gen)(unsigned char *, size_t);
    unsigned char *p;
};
static struct corpus corpora[] = {
    {"text", TEXT_N, gen_text, 0},
    {"rec", REC_N, gen_rec, 0},
    {"rand", RAND_N, gen_rand, 0},
};

static struct corpus *corpus(const char *name)
{
    for (size_t i = 0; i < sizeof corpora / sizeof *corpora; i++)
        if (!strcmp(corpora[i].name, name)) {
            struct corpus *c = &corpora[i];
            if (!c->p) {
                c->p = xmalloc(c->n);
                c->gen(c->p, c->n);
            }
            return c;
        }
    die("no corpus", name);
    return 0;
}

/* ---- cases ---- */

enum op { COMPRESS, DECOMPRESS, CSTREAM, DSTREAM };

struct bcase {
    const char *name;
    enum op op;
    int level;
    const char *corpus;
    size_t n; /* bytes of the corpus used (a prefix) */
    int reps;
};

/* Iteration counts chosen for roughly 30-150 ms per case on an Apple M2 (v1.5.7). */
static const struct bcase cases[] = {
    {"c1-text", COMPRESS, 1, "text", 8 * MB, 3},
    {"c3-text", COMPRESS, 3, "text", 8 * MB, 2},
    {"c6-text", COMPRESS, 6, "text", 2 * MB, 2},
    {"c12-text", COMPRESS, 12, "text", 1 * MB, 2},
    {"c19-text", COMPRESS, 19, "text", MB / 4, 2},
    {"cneg5-text", COMPRESS, -5, "text", 8 * MB, 6},
    {"c1-rec", COMPRESS, 1, "rec", 8 * MB, 4},
    {"c3-rec", COMPRESS, 3, "rec", 8 * MB, 3},
    {"c3-rand", COMPRESS, 3, "rand", 8 * MB, 30},
    {"cs3-text", CSTREAM, 3, "text", 8 * MB, 2},
    {"d1-text", DECOMPRESS, 1, "text", 8 * MB, 8},
    {"d3-text", DECOMPRESS, 3, "text", 8 * MB, 8},
    {"d12-text", DECOMPRESS, 12, "text", 2 * MB, 30},
    {"d3-rec", DECOMPRESS, 3, "rec", 8 * MB, 8},
    {"d3-rand", DECOMPRESS, 3, "rand", 8 * MB, 100},
    {"ds3-text", DSTREAM, 3, "text", 8 * MB, 8},
};
#define NCASES (sizeof cases / sizeof *cases)

#define CHUNK (64 * 1024)

static void zcheck(size_t r, const char *what)
{
    if (ZSTD_isError(r)) die(what, ZSTD_getErrorName(r));
}

static size_t compress1(ZSTD_CCtx *c, void *dst, size_t cap, const void *src, size_t n)
{
    size_t r = ZSTD_compress2(c, dst, cap, src, n);
    zcheck(r, "ZSTD_compress2");
    return r;
}

static size_t cstream1(ZSTD_CCtx *c, void *dst, size_t cap, const unsigned char *src, size_t n)
{
    ZSTD_outBuffer out = {dst, cap, 0};
    zcheck(ZSTD_CCtx_reset(c, ZSTD_reset_session_only), "ZSTD_CCtx_reset");
    for (size_t at = 0; at < n; at += CHUNK) {
        size_t m = n - at < CHUNK ? n - at : CHUNK;
        ZSTD_inBuffer in = {src + at, m, 0};
        ZSTD_EndDirective e = at + m == n ? ZSTD_e_end : ZSTD_e_continue;
        size_t r;
        do {
            r = ZSTD_compressStream2(c, &out, &in, e);
            zcheck(r, "ZSTD_compressStream2");
        } while (e == ZSTD_e_end ? r != 0 : in.pos < in.size);
    }
    return out.pos;
}

static size_t decompress1(ZSTD_DCtx *d, void *dst, size_t cap, const void *src, size_t n)
{
    size_t r = ZSTD_decompressDCtx(d, dst, cap, src, n);
    zcheck(r, "ZSTD_decompressDCtx");
    return r;
}

/* Streaming decompression into a 64 KB output buffer (the frame's window lives in the DCtx). */
static size_t dstream1(ZSTD_DCtx *d, unsigned char *chunk, const unsigned char *src, size_t n, uint64_t *sum)
{
    size_t total = 0, r = 1;
    ZSTD_inBuffer in = {src, n, 0};
    zcheck(ZSTD_DCtx_reset(d, ZSTD_reset_session_only), "ZSTD_DCtx_reset");
    while (r) {
        ZSTD_outBuffer out = {chunk, CHUNK, 0};
        r = ZSTD_decompressStream(d, &out, &in);
        zcheck(r, "ZSTD_decompressStream");
        total += out.pos;
        if (out.pos) *sum += chunk[out.pos - 1];
        if (!r || (in.pos == in.size && out.pos == 0)) break;
    }
    return total;
}

static void run(const struct bcase *k)
{
    struct corpus *cp = corpus(k->corpus);
    const unsigned char *src = cp->p;
    size_t n = k->n, cap = ZSTD_compressBound(n);
    unsigned char *buf = xmalloc(cap);
    double t0 = 0, t1 = 0;
    if (k->op == COMPRESS || k->op == CSTREAM) {
        ZSTD_CCtx *c = NEW_CCTX();
        if (!c) die("ZSTD_createCCtx", 0);
        zcheck(ZSTD_CCtx_setParameter(c, ZSTD_c_compressionLevel, k->level), "level");
        size_t z = k->op == COMPRESS ? compress1(c, buf, cap, src, n) : cstream1(c, buf, cap, src, n);
        if (ZSTD_getFrameContentSize(buf, z) != (k->op == COMPRESS ? n : ZSTD_CONTENTSIZE_UNKNOWN))
            die("bad frame", k->name);
        t0 = now_us();
        for (int i = 0; i < k->reps; i++)
            z = k->op == COMPRESS ? compress1(c, buf, cap, src, n) : cstream1(c, buf, cap, src, n);
        t1 = now_us();
        ZSTD_freeCCtx(c);
    } else {
        /* the compressed input is made with the same library, at the case's level */
        ZSTD_CCtx *c = NEW_CCTX();
        if (!c) die("ZSTD_createCCtx", 0);
        zcheck(ZSTD_CCtx_setParameter(c, ZSTD_c_compressionLevel, k->level), "level");
        size_t z = compress1(c, buf, cap, src, n);
        ZSTD_freeCCtx(c);
        ZSTD_DCtx *d = NEW_DCTX();
        if (!d) die("ZSTD_createDCtx", 0);
        uint64_t sum = 0;
        if (k->op == DECOMPRESS) {
            unsigned char *out = xmalloc(n);
            if (decompress1(d, out, n, buf, z) != n || memcmp(out, src, n)) die("round trip failed", k->name);
            t0 = now_us();
            for (int i = 0; i < k->reps; i++) sum += decompress1(d, out, n, buf, z);
            t1 = now_us();
            BFREE(out);
        } else {
            unsigned char *chunk = xmalloc(CHUNK);
            if (dstream1(d, chunk, buf, z, &sum) != n) die("round trip failed", k->name);
            t0 = now_us();
            for (int i = 0; i < k->reps; i++) dstream1(d, chunk, buf, z, &sum);
            t1 = now_us();
            BFREE(chunk);
        }
        if (sum == 42) fputc(' ', stderr); /* keep the work observable */
        ZSTD_freeDCtx(d);
    }
    BFREE(buf);
    printf("%s %.3f %d us\n", k->name, t1 - t0, k->reps);
    fflush(stdout);
}

static int wanted(const char *list, const char *name)
{
    size_t n = strlen(name);
    for (const char *p = list; *p;) {
        const char *e = strchr(p, ',');
        size_t m = e ? (size_t)(e - p) : strlen(p);
        if (m == n && !strncmp(p, name, n)) return 1;
        if (!e) break;
        p = e + 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--list")) {
        for (size_t i = 0; i < NCASES; i++) puts(cases[i].name);
        return 0;
    }
    const char *want = getenv("PLACEMAT_CASES");
    if (want && !*want) want = 0;
    if (want) { /* say which requested names this harness does not have */
        char *w = strdup(want);
        for (char *t = strtok(w, ","); t; t = strtok(0, ",")) {
            int found = 0;
            for (size_t i = 0; i < NCASES; i++) found |= !strcmp(cases[i].name, t);
            if (!found) fprintf(stderr, "zbench: no case %s\n", t);
        }
        free(w);
    }
    printf("# zbench zstd %s\n", ZSTD_versionString());
    for (size_t i = 0; i < NCASES; i++)
        if (!want || wanted(want, cases[i].name)) run(&cases[i]);
    return 0;
}
