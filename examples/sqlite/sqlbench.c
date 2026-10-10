/* sqlbench.c: a small SQLite benchmark harness for placemat (MIT licence; uses only SQLite's public API).
 *
 * Each case builds its own in-memory database (untimed set-up), then times one body. The workloads
 * are modelled loosely on the categories of SQLite's speedtest1 (inserts with and without indexes,
 * scans, sorts, index builds, point lookups, updates, deletes, joins, recursive CTEs, JSON), written
 * afresh against the API: no SQLite source is copied here.
 *
 * Output, one line per case (placemat's benchmark protocol, DESIGN 8.1):
 *     name<TAB>microseconds<TAB>1<TAB>us
 *     # check name <checksum>          (skipped by placemat; the same in every build of a version)
 * Environment:
 *     PLACEMAT_CASES   comma-separated cases to run (default: all, in table order)
 *     SQLBENCH_LIST=1  print the case names and exit
 *     SQLBENCH_REPS=n  time each body n times, fresh set-up each time (default 1; one line per rep)
 *     SQLBENCH_PCSTAT=1  after each case, print "# pcache name used overflow": the page-cache slots in
 *                      use at the case's peak and the pages that did not fit in the slab
 * Build flags:
 *     -DPLACEMAT_HOOK  install placemat's colouring allocator (placemat_alloc.h) with
 *                      sqlite3_config(SQLITE_CONFIG_MALLOC) before sqlite3_initialize()
 *     -DSQLBENCH_PAGECACHE  give SQLite's page cache one slab of fixed-size slots
 *                      (sqlite3_config(SQLITE_CONFIG_PAGECACHE)), so the stride between B-tree pages,
 *                      and with the hook the slab's base, are set here rather than by malloc; see
 *                      pagecache_setup() and README.md ("Page placement")
 *     -DSQLBENCH_PC_STRIDE=n  (with SQLBENCH_PAGECACHE) a fixed slot stride in bytes, for the stride sweep
 *     -DSQLBENCH_CACHEGRIND  bracket each timed body with Cachegrind's start/stop client requests
 *                      (run with valgrind --tool=cachegrind --instr-at-start=no)
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "sqlite3.h"

#ifdef PLACEMAT_HOOK
#include "placemat_alloc.h"
#endif
#ifdef SQLBENCH_CACHEGRIND
#include <valgrind/cachegrind.h>
#define CG_START() CACHEGRIND_START_INSTRUMENTATION
#define CG_STOP() CACHEGRIND_STOP_INSTRUMENTATION
#else
#define CG_START() ((void)0)
#define CG_STOP() ((void)0)
#endif

/* ---- utilities ---------------------------------------------------------------------------------- */

static uint64_t now_ns(void)
{
#ifdef __APPLE__
    return clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
#else
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec;
#endif
}

#ifdef SQLBENCH_PAGECACHE
/* The page-cache slab. Each slot holds one page (its 4,096 data bytes first, so a B-tree page header
 * sits at the slot's start) and pcache1's header after it. The stride between slots decides how the
 * page headers map onto L1D sets: with a 16 KB set stride (M2) and 64-byte lines, a stride of 64*m
 * puts them in 256/gcd(m, 256) sets, e.g. all of them in 2 sets at 8,192 bytes.
 *
 *   stride = round_up(4096 + header, 64) + extra
 *   extra  = SQLBENCH_PC_STRIDE - base when that is compiled in (the sweep); otherwise, in hooked
 *            builds, at placemat's step setting, 64 * (splitmix64(PLACEMAT_STEP_SEED) mod
 *            (SQLBENCH_PC_SPAN / 64)) for hashed steps or PLACEMAT_STEP mod SQLBENCH_PC_SPAN for linear
 *            ones; 0 at the stock and coloured settings.
 *
 * The slab is one allocation, so in hooked builds placemat's colouring allocator moves its base (the
 * coloured setting moves every page together). SQLBENCH_PC_PAGES slots (default below) hold the
 * largest case's database (2,072 pages at its peak, delete_refill; 3.53.0); pages beyond it fall back to the allocator (SQLBENCH_PCSTAT shows how many). */
#ifndef SQLBENCH_PC_PAGES
#define SQLBENCH_PC_PAGES 4096
#endif
#ifndef SQLBENCH_PC_SPAN
#define SQLBENCH_PC_SPAN 4096
#endif
static uint64_t mix64(uint64_t x)
{
    x += 0x9e3779b97f4a7c15ull;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
    return x ^ (x >> 31);
}

static int pc_stride, pc_pages = SQLBENCH_PC_PAGES;

static void pagecache_setup(void)
{
    int hdr = 0;
    if (sqlite3_config(SQLITE_CONFIG_PCACHE_HDRSZ, &hdr) != SQLITE_OK) {
        fprintf(stderr, "sqlbench: SQLITE_CONFIG_PCACHE_HDRSZ failed\n");
        exit(1);
    }
    int base = (4096 + hdr + 63) & ~63;
    int extra = 0;
#ifdef SQLBENCH_PC_STRIDE
    if (SQLBENCH_PC_STRIDE < base || SQLBENCH_PC_STRIDE % 8) {
        fprintf(stderr, "sqlbench: SQLBENCH_PC_STRIDE %d must be a multiple of 8, at least %d\n", SQLBENCH_PC_STRIDE, base);
        exit(1);
    }
    extra = SQLBENCH_PC_STRIDE - base;
#elif defined(PLACEMAT_HOOK)
    const char *seed = getenv("PLACEMAT_STEP_SEED"), *step = getenv("PLACEMAT_STEP");
    if (seed && *seed)
        extra = 64 * (int)(mix64(strtoull(seed, 0, 0)) % (SQLBENCH_PC_SPAN / 64));
    else if (step && *step) {
        long long s = strtoll(step, 0, 0) % SQLBENCH_PC_SPAN;
        extra = (int)(s < 0 ? s + SQLBENCH_PC_SPAN : s);
    }
#endif
    pc_stride = base + extra;
    size_t n = (size_t)pc_stride * (size_t)pc_pages;
#ifdef PLACEMAT_HOOK
    void *slab = placemat_malloc(n);
#else
    void *slab = 0;
    if (posix_memalign(&slab, 16384, n)) slab = 0;
#endif
    if (!slab || sqlite3_config(SQLITE_CONFIG_PAGECACHE, slab, pc_stride, pc_pages) != SQLITE_OK) {
        fprintf(stderr, "sqlbench: page-cache slab of %d x %d bytes failed\n", pc_pages, pc_stride);
        exit(1);
    }
}
#endif

static uint64_t rng_state;
static void rng_seed(uint64_t s) { rng_state = s; }
static uint64_t rng(void)
{
    uint64_t z = (rng_state += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static void die(sqlite3 *db, const char *what)
{
    fprintf(stderr, "sqlbench: %s: %s\n", what, db ? sqlite3_errmsg(db) : "?");
    exit(1);
}

static void exec(sqlite3 *db, const char *sql)
{
    char *err = 0;
    if (sqlite3_exec(db, sql, 0, 0, &err) != SQLITE_OK) {
        fprintf(stderr, "sqlbench: %s\n  in: %.200s\n", err ? err : "?", sql);
        exit(1);
    }
}

static sqlite3_stmt *prep(sqlite3 *db, const char *sql)
{
    sqlite3_stmt *s = 0;
    if (sqlite3_prepare_v2(db, sql, -1, &s, 0) != SQLITE_OK) die(db, sql);
    return s;
}

/* Step a statement to completion; return the sum of column 0 over its rows (a checksum). */
static int64_t run_stmt(sqlite3 *db, sqlite3_stmt *s)
{
    int64_t sum = 0;
    int rc;
    while ((rc = sqlite3_step(s)) == SQLITE_ROW)
        sum += sqlite3_column_int64(s, 0);
    if (rc != SQLITE_DONE) die(db, "step");
    sqlite3_reset(s);
    return sum;
}

static int64_t query(sqlite3 *db, const char *sql)
{
    sqlite3_stmt *s = prep(db, sql);
    int64_t r = run_stmt(db, s);
    sqlite3_finalize(s);
    return r;
}

/* A word for a number: syllables chosen by its digits, so text keys sort unlike their numbers. */
static const char *SYL[16] = {"ka", "lo", "mi", "nu", "pe", "ra", "si", "to", "va", "ze", "bri", "cho",
                              "dra", "fle", "gru", "shi"};
static int word(uint64_t x, char *buf)
{
    int n = 0;
    do {
        const char *s = SYL[x & 15];
        while (*s) buf[n++] = *s++;
        x >>= 4;
    } while (x);
    buf[n] = 0;
    return n;
}

/* t1(a INTEGER, b INTEGER, c TEXT) with n rows: a = 1..n, b random below 2n, c a word for b. */
static void fill_t1(sqlite3 *db, const char *table, int n, int keyed)
{
    char sql[256], w[64];
    snprintf(sql, sizeof sql, "CREATE TABLE %s(a INTEGER %s, b INTEGER, c TEXT)", table,
             keyed ? "PRIMARY KEY" : "");
    exec(db, sql);
    snprintf(sql, sizeof sql, "INSERT INTO %s VALUES(?1,?2,?3)", table);
    sqlite3_stmt *s = prep(db, sql);
    exec(db, "BEGIN");
    for (int i = 1; i <= n; i++) {
        uint64_t b = rng() % (uint64_t)(2 * n);
        int len = word(b, w);
        sqlite3_bind_int(s, 1, i);
        sqlite3_bind_int64(s, 2, (int64_t)b);
        sqlite3_bind_text(s, 3, w, len, SQLITE_STATIC);
        if (sqlite3_step(s) != SQLITE_DONE) die(db, "fill");
        sqlite3_reset(s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
}

/* ---- cases -------------------------------------------------------------------------------------- */

typedef struct {
    const char *name;
    void (*setup)(sqlite3 *);
    int64_t (*body)(sqlite3 *);
} Case;

static void no_setup(sqlite3 *db) { (void)db; }

/* Inserts. */
static int64_t ins_plain(sqlite3 *db) { fill_t1(db, "t1", 150000, 0); return query(db, "SELECT count(*) FROM t1"); }
static int64_t ins_ipk(sqlite3 *db) { fill_t1(db, "t1", 150000, 1); return query(db, "SELECT max(a) FROM t1"); }

static void s_ins_idx(sqlite3 *db) { exec(db, "CREATE TABLE t2(a INTEGER, b INTEGER, c TEXT); CREATE INDEX t2b ON t2(b)"); }
static int64_t ins_idx_random(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "INSERT INTO t2 VALUES(?1,?2,?3)");
    char w[64];
    exec(db, "BEGIN");
    for (int i = 0; i < 80000; i++) {
        uint64_t b = rng() % 1000000;
        int len = word(b, w);
        sqlite3_bind_int(s, 1, i);
        sqlite3_bind_int64(s, 2, (int64_t)b);
        sqlite3_bind_text(s, 3, w, len, SQLITE_STATIC);
        if (sqlite3_step(s) != SQLITE_DONE) die(db, "insert");
        sqlite3_reset(s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
    return query(db, "SELECT count(*) FROM t2");
}

static void s_ins_3idx(sqlite3 *db)
{
    exec(db, "CREATE TABLE t3(a INTEGER, b INTEGER, c TEXT);"
             "CREATE INDEX t3a ON t3(a); CREATE INDEX t3b ON t3(b); CREATE INDEX t3c ON t3(c)");
}
static int64_t ins_3idx(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "INSERT INTO t3 VALUES(?1,?2,?3)");
    char w[64];
    exec(db, "BEGIN");
    for (int i = 0; i < 40000; i++) {
        uint64_t b = rng() % 1000000;
        int len = word(b, w);
        sqlite3_bind_int64(s, 1, (int64_t)(rng() % 1000000));
        sqlite3_bind_int64(s, 2, (int64_t)b);
        sqlite3_bind_text(s, 3, w, len, SQLITE_STATIC);
        if (sqlite3_step(s) != SQLITE_DONE) die(db, "insert");
        sqlite3_reset(s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
    return query(db, "SELECT count(*) FROM t3");
}

/* Scans and sorts over an unindexed 100k-row table. */
static void s_t1_100k(sqlite3 *db) { fill_t1(db, "t1", 100000, 0); }
static int64_t scan_between(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT count(*), avg(b) FROM t1 WHERE b BETWEEN ?1 AND ?2");
    int64_t sum = 0;
    for (int i = 0; i < 30; i++) {
        int lo = (int)(rng() % 190000);
        sqlite3_bind_int(s, 1, lo);
        sqlite3_bind_int(s, 2, lo + 10000);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}
static int64_t scan_like(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT count(*) FROM t1 WHERE c LIKE ?1");
    int64_t sum = 0;
    char pat[80], w[64];
    for (int i = 0; i < 12; i++) {
        word(rng() % 4096, w);
        snprintf(pat, sizeof pat, "%%%s%%", w);
        sqlite3_bind_text(s, 1, pat, -1, SQLITE_TRANSIENT);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}
static int64_t sort_text(sqlite3 *db) { return query(db, "SELECT b FROM t1 ORDER BY c, a"); }
static int64_t sort_limit(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT a FROM t1 WHERE b BETWEEN ?1 AND ?2 ORDER BY c LIMIT 10");
    int64_t sum = 0;
    for (int i = 0; i < 25; i++) {
        int lo = (int)(rng() % 100000);
        sqlite3_bind_int(s, 1, lo);
        sqlite3_bind_int(s, 2, lo + 80000);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}
static int64_t group_by(sqlite3 *db)
{
    return query(db, "SELECT sum(x) FROM (SELECT b % 997 AS g, count(*) AS x, max(c) FROM t1 GROUP BY g)")
           + query(db, "SELECT count(DISTINCT c) FROM t1");
}
static int64_t window_fn(sqlite3 *db)
{
    return query(db, "SELECT max(s) FROM (SELECT sum(b) OVER (ORDER BY a ROWS BETWEEN 50 PRECEDING AND CURRENT ROW) AS s,"
                     " rank() OVER (PARTITION BY b % 100 ORDER BY c) AS r FROM t1)");
}
static int64_t create_index(sqlite3 *db)
{
    exec(db, "CREATE INDEX i1b ON t1(b); CREATE INDEX i1c ON t1(c); CREATE INDEX i1bc ON t1(b, c)");
    return query(db, "SELECT count(*) FROM t1 INDEXED BY i1c WHERE c >= 'm'");
}

/* Indexed lookups. */
static void s_t1_idx(sqlite3 *db)
{
    fill_t1(db, "t1", 100000, 1);
    exec(db, "CREATE INDEX i1b ON t1(b); CREATE INDEX i1c ON t1(c)");
}
static int64_t idx_between(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT count(*), avg(b) FROM t1 WHERE b BETWEEN ?1 AND ?2");
    int64_t sum = 0;
    for (int i = 0; i < 12000; i++) {
        int lo = (int)(rng() % 200000);
        sqlite3_bind_int(s, 1, lo);
        sqlite3_bind_int(s, 2, lo + 100);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}
static int64_t idx_text_between(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT count(*) FROM t1 WHERE c BETWEEN ?1 AND ?2");
    int64_t sum = 0;
    char lo[64], hi[64];
    for (int i = 0; i < 40000; i++) {
        int n = word(rng() % 200000, lo);
        memcpy(hi, lo, (size_t)n + 1);
        hi[n - 1]++;
        sqlite3_bind_text(s, 1, lo, n, SQLITE_STATIC);
        sqlite3_bind_text(s, 2, hi, n, SQLITE_STATIC);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}
static int64_t ipk_lookup(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT b FROM t1 WHERE a = ?1");
    int64_t sum = 0;
    for (int i = 0; i < 150000; i++) {
        sqlite3_bind_int(s, 1, (int)(rng() % 100000) + 1);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}

static void s_textpk(sqlite3 *db)
{
    exec(db, "CREATE TABLE tk(k TEXT PRIMARY KEY, v INTEGER) WITHOUT ROWID");
    sqlite3_stmt *s = prep(db, "INSERT OR REPLACE INTO tk VALUES(?1,?2)");
    char w[64];
    exec(db, "BEGIN");
    for (int i = 0; i < 60000; i++) {
        int n = word((uint64_t)i * 2654435761u % 1000003u, w);
        sqlite3_bind_text(s, 1, w, n, SQLITE_STATIC);
        sqlite3_bind_int(s, 2, i);
        if (sqlite3_step(s) != SQLITE_DONE) die(db, "insert");
        sqlite3_reset(s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
}
static int64_t textpk_lookup(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT v FROM tk WHERE k = ?1");
    int64_t sum = 0;
    char w[64];
    for (int i = 0; i < 80000; i++) {
        int n = word((uint64_t)(rng() % 60000) * 2654435761u % 1000003u, w);
        sqlite3_bind_text(s, 1, w, n, SQLITE_STATIC);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}

/* Updates and deletes. */
static int64_t upd_between(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "UPDATE t1 SET b = b + 1, c = c || 'x' WHERE a BETWEEN ?1 AND ?2");
    exec(db, "BEGIN");
    for (int i = 0; i < 2000; i++) {
        int lo = (int)(rng() % 99900) + 1;
        sqlite3_bind_int(s, 1, lo);
        sqlite3_bind_int(s, 2, lo + 20);
        run_stmt(db, s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
    return query(db, "SELECT sum(b) FROM t1");
}
static int64_t upd_rows(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "UPDATE t1 SET b = ?2 WHERE a = ?1");
    exec(db, "BEGIN");
    for (int i = 0; i < 60000; i++) {
        sqlite3_bind_int(s, 1, (int)(rng() % 100000) + 1);
        sqlite3_bind_int(s, 2, (int)(rng() % 200000));
        run_stmt(db, s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
    return query(db, "SELECT sum(b) FROM t1");
}
static int64_t del_rows(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "DELETE FROM t1 WHERE a = ?1");
    exec(db, "BEGIN");
    for (int i = 0; i < 40000; i++) {
        sqlite3_bind_int(s, 1, (int)(rng() % 100000) + 1);
        run_stmt(db, s);
    }
    exec(db, "COMMIT");
    sqlite3_finalize(s);
    return query(db, "SELECT count(*) FROM t1");
}
static int64_t delete_refill(sqlite3 *db)
{
    exec(db, "BEGIN; CREATE TABLE t9 AS SELECT * FROM t1; DELETE FROM t1; INSERT INTO t1 SELECT * FROM t9;"
             "DROP TABLE t9; COMMIT");
    return query(db, "SELECT count(*) FROM t1");
}

/* Joins and subqueries. */
static void s_join(sqlite3 *db)
{
    fill_t1(db, "t1", 40000, 1);
    fill_t1(db, "t2", 40000, 1);
    exec(db, "CREATE INDEX i2b ON t2(b); CREATE TABLE t3 AS SELECT a, b % 1000 AS g, c FROM t1;"
             "CREATE INDEX i3g ON t3(g); CREATE TABLE t4(g INTEGER PRIMARY KEY, name TEXT);"
             "INSERT INTO t4 WITH RECURSIVE s(value) AS (VALUES(0) UNION ALL SELECT value + 1 FROM s WHERE value < 999)"
             " SELECT value, 'g' || value FROM s");
}
static int64_t join4(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "SELECT count(*) FROM t1 JOIN t2 ON t2.b = t1.b JOIN t3 ON t3.a = t2.a"
                               " JOIN t4 ON t4.g = t3.g WHERE t1.a BETWEEN ?1 AND ?1 + 400");
    int64_t sum = 0;
    for (int i = 0; i < 20; i++) {
        sqlite3_bind_int(s, 1, (int)(rng() % 39000) + 1);
        sum += run_stmt(db, s);
    }
    sqlite3_finalize(s);
    return sum;
}
static int64_t subquery(sqlite3 *db)
{
    return query(db, "SELECT sum((SELECT count(*) FROM t2 WHERE t2.b BETWEEN t1.b - 5 AND t1.b + 5)) FROM t1"
                     " WHERE a % 3 <> 2");
}

/* Computation in SQL. */
static int64_t cte_mandel(sqlite3 *db)
{
    return query(db,
                 "WITH RECURSIVE xaxis(x) AS (VALUES(-2.0) UNION ALL SELECT x + 0.025 FROM xaxis WHERE x < 1.2),"
                 " yaxis(y) AS (VALUES(-1.0) UNION ALL SELECT y + 0.05 FROM yaxis WHERE y < 1.0),"
                 " m(iter, cx, cy, x, y) AS (SELECT 0, x, y, 0.0, 0.0 FROM xaxis, yaxis"
                 "   UNION ALL SELECT iter + 1, cx, cy, x * x - y * y + cx, 2.0 * x * y + cy FROM m"
                 "   WHERE (x * x + y * y) < 4.0 AND iter < 28)"
                 " SELECT sum(iter) FROM (SELECT max(iter) AS iter FROM m GROUP BY cx, cy)");
}
static int64_t printf_round(sqlite3 *db)
{
    return query(db, "SELECT sum(length(printf('%.6f %d %s %08x', value / 7.0, value, 'abc' || value, value))"
                     " + round(value / 3.0, 2))"
                     " FROM (WITH RECURSIVE s(value) AS (VALUES(1) UNION ALL SELECT value + 1 FROM s WHERE value < 150000)"
                     " SELECT value FROM s)");
}

static void s_json(sqlite3 *db)
{
    exec(db, "CREATE TABLE tj(id INTEGER PRIMARY KEY, doc TEXT);"
             "INSERT INTO tj WITH RECURSIVE s(value) AS (VALUES(1) UNION ALL SELECT value + 1 FROM s WHERE value < 30000)"
             " SELECT value, json_object('id', value, 'name', 'n' || (value * 7919 % 10007),"
             " 'tags', json_array(value % 7, value % 11, value % 13), 'score', value * 0.5)"
             " FROM s");
}
static int64_t json_query(sqlite3 *db)
{
    return query(db, "SELECT sum(json_extract(doc, '$.tags[1]')) + count(*) FROM tj"
                     " WHERE json_extract(doc, '$.name') > 'n5'")
           + query(db, "SELECT sum(j.value) FROM tj, json_each(tj.doc, '$.tags') AS j WHERE tj.id % 4 = 0");
}

static int64_t integrity(sqlite3 *db)
{
    sqlite3_stmt *s = prep(db, "PRAGMA integrity_check");
    int64_t n = 0;
    while (sqlite3_step(s) == SQLITE_ROW) n++;
    sqlite3_finalize(s);
    return n;
}

static const Case CASES[] = {
    {"ins_plain", no_setup, ins_plain},
    {"ins_ipk", no_setup, ins_ipk},
    {"ins_idx_random", s_ins_idx, ins_idx_random},
    {"ins_3idx", s_ins_3idx, ins_3idx},
    {"scan_between", s_t1_100k, scan_between},
    {"scan_like", s_t1_100k, scan_like},
    {"sort_text", s_t1_100k, sort_text},
    {"sort_limit", s_t1_100k, sort_limit},
    {"group_by", s_t1_100k, group_by},
    {"window_fn", s_t1_100k, window_fn},
    {"create_index", s_t1_100k, create_index},
    {"idx_between", s_t1_idx, idx_between},
    {"idx_text_between", s_t1_idx, idx_text_between},
    {"ipk_lookup", s_t1_idx, ipk_lookup},
    {"textpk_lookup", s_textpk, textpk_lookup},
    {"upd_between", s_t1_idx, upd_between},
    {"upd_rows", s_t1_idx, upd_rows},
    {"del_rows", s_t1_idx, del_rows},
    {"delete_refill", s_t1_idx, delete_refill},
    {"join4", s_join, join4},
    {"subquery", s_join, subquery},
    {"cte_mandel", no_setup, cte_mandel},
    {"printf_round", no_setup, printf_round},
    {"json_query", s_json, json_query},
    {"integrity", s_t1_idx, integrity},
};
#define NCASES ((int)(sizeof CASES / sizeof CASES[0]))

static int wanted(const char *list, const char *name)
{
    if (!list || !*list) return 1;
    size_t n = strlen(name);
    for (const char *p = list; *p;) {
        const char *e = strchr(p, ',');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (len == n && !memcmp(p, name, n)) return 1;
        if (!e) break;
        p = e + 1;
    }
    return 0;
}

static void one(const Case *c, int rep)
{
    sqlite3 *db = 0;
    if (sqlite3_open(":memory:", &db) != SQLITE_OK) die(db, "open");
    rng_seed(0x5eed0000u + (uint64_t)(c - CASES));
    c->setup(db);
    CG_START();
    uint64_t t0 = now_ns();
    int64_t chk = c->body(db);
    uint64_t t1 = now_ns();
    CG_STOP();
    sqlite3_close(db);
    printf("%s\t%.3f\t1\tus\n", c->name, (double)(t1 - t0) / 1e3);
    if (getenv("SQLBENCH_PCSTAT")) {
        sqlite3_int64 cur, used = 0, over = 0;
        sqlite3_status64(SQLITE_STATUS_PAGECACHE_USED, &cur, &used, 1);
        sqlite3_status64(SQLITE_STATUS_PAGECACHE_OVERFLOW, &cur, &over, 1);
        printf("# pcache %s %lld %lld\n", c->name, (long long)used, (long long)over);
    }
    if (rep == 0) printf("# check %s %lld\n", c->name, (long long)chk);
    fflush(stdout);
}

int main(void)
{
    if (getenv("SQLBENCH_LIST")) {
        for (int i = 0; i < NCASES; i++) puts(CASES[i].name);
        return 0;
    }
#ifdef PLACEMAT_HOOK
    static sqlite3_mem_methods mm = {placemat_sqlite_malloc, placemat_sqlite_free, placemat_sqlite_realloc,
                                     placemat_sqlite_size, placemat_sqlite_roundup, placemat_sqlite_init,
                                     placemat_sqlite_shutdown, NULL};
    if (sqlite3_config(SQLITE_CONFIG_MALLOC, &mm) != SQLITE_OK) {
        fprintf(stderr, "sqlbench: sqlite3_config(SQLITE_CONFIG_MALLOC) failed\n");
        return 1;
    }
#endif
#ifdef SQLBENCH_PAGECACHE
    pagecache_setup();
#endif
    if (sqlite3_initialize() != SQLITE_OK) {
        fprintf(stderr, "sqlbench: sqlite3_initialize failed\n");
        return 1;
    }
    const char *list = getenv("PLACEMAT_CASES");
    int reps = getenv("SQLBENCH_REPS") ? atoi(getenv("SQLBENCH_REPS")) : 1;
    if (reps < 1) reps = 1;
    printf("# sqlbench: SQLite %s%s\n", sqlite3_libversion(),
#ifdef PLACEMAT_HOOK
           " (placemat hook)"
#else
           ""
#endif
    );
#ifdef SQLBENCH_PAGECACHE
    printf("# pagecache: %d slots of %d bytes\n", pc_pages, pc_stride);
#endif
    for (int i = 0; i < NCASES; i++)
        if (wanted(list, CASES[i].name))
            for (int r = 0; r < reps; r++) one(&CASES[i], r);
    sqlite3_shutdown();
    return 0;
}
