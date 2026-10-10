"""The malloc interposer (data/interpose.c + data/placemat_alloc.c) under a small C program and real programs."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from placemat import cbuild
from placemat.colour import block_offset
from tests.test_colour_c import clean_env, khash, parse_summaries

CC = cbuild.find_cc()

PROG = r"""
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __APPLE__
#include <malloc/malloc.h>
#define USABLE(p) malloc_size(p)
#else
#include <malloc.h>
#define USABLE(p) malloc_usable_size(p)
#endif

static int check(const unsigned char *p, size_t n, unsigned char v)
{
    for (size_t i = 0; i < n; i++)
        if (p[i] != v) return 0;
    return 1;
}

static void show(const char *what, void *p, size_t n)
{
    printf("%s %#lx %zu %zu %lu\n", what, (unsigned long)(uintptr_t)p, n, (size_t)USABLE(p),
           (unsigned long)((uintptr_t)p % 16384));
}

int main(void)
{
    static const size_t sizes[] = {300000, 2000000, 100000, 65536};
    void *big[4], *small[3];
    for (int i = 0; i < 4; i++) {
        big[i] = malloc(sizes[i]);
        memset(big[i], i + 1, sizes[i]);
        show("big", big[i], sizes[i]);
    }
    small[0] = malloc(100);
    small[1] = malloc(65535);
    small[2] = calloc(10, 10);
    show("small", small[0], 100);
    show("small", small[1], 65535);
    show("small", small[2], 100);

    unsigned char *c = calloc(1000, 100);
    show("calloc", c, 100000);
    printf("calloc_zero %d\n", check(c, 100000, 0));

    unsigned char *r = malloc(1000);
    memset(r, 7, 1000);
    r = realloc(r, 200000);
    show("realloc_grow", r, 200000);
    memset(r + 1000, 7, 199000);
    r = realloc(r, 3000000);
    show("realloc_big", r, 3000000);
    printf("realloc_big_ok %d\n", check(r, 200000, 7));
    memset(r, 9, 3000000);
    r = realloc(r, 150000);
    show("realloc_down", r, 150000);
    printf("realloc_down_ok %d\n", check(r, 150000, 9));
    r = realloc(r, 500);
    show("realloc_small", r, 500);
    printf("realloc_small_ok %d\n", check(r, 500, 9));

    void *a = 0;
    int e = posix_memalign(&a, 4096, 100000);
    show("memalign4096", a, 100000);
    printf("memalign4096_ok %d %d\n", e, (int)((uintptr_t)a % 4096 == 0));
    void *a2 = aligned_alloc(256, 131072);
    show("aligned256", a2, 131072);
    printf("aligned256_ok %d\n", (int)((uintptr_t)a2 % 256 == 0));
    void *a3 = 0;
    e = posix_memalign(&a3, 64, 1000);
    printf("memalign_small_ok %d %d\n", e, (int)((uintptr_t)a3 % 64 == 0));
    void *a4 = 0;
    printf("memalign_einval %d\n", posix_memalign(&a4, 24, 100000) == EINVAL);

    char *s = strdup("hello");
    printf("strdup %s\n", s);
    free(s);
    free(a);
    free(a2);
    free(a3);
    free(r);
    free(c);
    for (int i = 0; i < 3; i++) free(small[i]);
    for (int i = 0; i < 4; i++) free(big[i]);
    free(NULL);
    printf("done\n");
    return 0;
}
"""

ROBUST = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int cmp(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

int main(void)
{
    enum { N = 20000 };
    char **v = malloc(N * sizeof *v);
    for (int i = 0; i < N; i++) {
        char buf[32];
        snprintf(buf, sizeof buf, "s%07d", (i * 7919) % N);
        v[i] = strdup(buf);
    }
    qsort(v, N, sizeof *v, cmp);
    size_t cap = 16, len = 0;
    char *grow = malloc(cap);
    for (int i = 0; i < N; i++) {
        size_t n = strlen(v[i]);
        if (len + n + 1 > cap) { cap *= 2; grow = realloc(grow, cap); }
        memcpy(grow + len, v[i], n + 1);
        len += n;
        free(v[i]);
    }
    printf("%s %zu %.8s\n", v == NULL ? "fail" : "ok", len, grow);
    free(grow);
    free(v);
    /* churn: many short-lived large blocks, a few long-lived ones */
    void *keep[64];
    for (int i = 0; i < 200000; i++) {
        void *p = malloc(5000 + i % 3000);
        if (i % 4000 == 0) keep[i / 4000 % 64] = p;
        else free(p);
    }
    for (int i = 0; i < 50; i++) free(keep[i]);
    printf("churn ok\n");
    return 0;
}
"""

THREADS = r"""
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *work(void *arg)
{
    uintptr_t id = (uintptr_t)arg, x = id * 2654435761u + 1;
    unsigned char *held[8] = {0};
    size_t len[8] = {0};
    for (int i = 0; i < 3000; i++) {
        x = x * 6364136223846793005ull + 1442695040888963407ull;
        int j = (int)((x >> 33) % 8);
        if (held[j]) {
            for (size_t b = 0; b < len[j]; b += 4093)
                if (held[j][b] != (unsigned char)(id + j)) { printf("corrupt\n"); exit(1); }
            if ((x >> 20) & 1) { free(held[j]); held[j] = 0; continue; }
            size_t n = 1000 + (x >> 40) % 300000;
            unsigned char *q = realloc(held[j], n);
            if (!q) exit(1);
            if (n > len[j]) memset(q + len[j], (int)(id + j), n - len[j]);
            held[j] = q; len[j] = n;
            continue;
        }
        len[j] = 1000 + (x >> 40) % 300000;
        held[j] = malloc(len[j]);
        memset(held[j], (int)(id + j), len[j]);
    }
    for (int j = 0; j < 8; j++) free(held[j]);
    return 0;
}

int main(void)
{
    pthread_t t[4];
    for (uintptr_t i = 0; i < 4; i++) pthread_create(&t[i], 0, work, (void *)i);
    for (int i = 0; i < 4; i++) pthread_join(t[i], 0);
    printf("ok\n");
    return 0;
}
"""

SPAN = 16384


def parse_prog(out: str) -> dict:
    lines: dict = {}
    for ln in out.splitlines():
        w = ln.split()
        lines.setdefault(w[0], []).append(w[1:])
    return lines


@unittest.skipUnless(CC, "no C compiler")
@unittest.skipUnless(sys.platform in ("darwin", "linux"), "interposer supports macOS and Linux")
class Interposer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="placemat-test-")
        cls.dir = Path(cls.tmp.name)
        cls.lib = cbuild.build_interposer(cls.dir / "lib", cc=CC)
        cls.prog = cls.dir / "prog"
        cls.robust = cls.dir / "robust"
        cls.threads = cls.dir / "threads"
        for exe, src in ((cls.prog, PROG), (cls.robust, ROBUST), (cls.threads, THREADS)):
            exe.with_suffix(".c").write_text(src)
            subprocess.run([CC, "-O1", "-Wall", "-pthread", "-o", str(exe), str(exe.with_suffix(".c"))], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_under(self, exe, *args, interpose=True, **env):
        log = self.dir / f"log-{self._testMethodName}-{len(env)}-{abs(hash(tuple(sorted(env.items()))))}"
        log.unlink(missing_ok=True)
        e = clean_env(PLACEMAT_LOG=str(log), PLACEMAT_COLOUR_SPAN=str(SPAN), PLACEMAT_STEP_SPAN=str(SPAN))
        e.update(env)
        if interpose:
            e.update(cbuild.preload_env(self.lib))
        r = subprocess.run([str(exe), *args], capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        summaries = parse_summaries(log.read_text()) if log.exists() else []
        return r, summaries

    def check_prog(self, mode=None, step=None, colour=0):
        env = {"PLACEMAT_ADDRLOG": "2"}
        if colour:
            env["PLACEMAT_COLOUR"] = str(colour)
        if mode == "hashed":
            env["PLACEMAT_STEP_SEED"] = hex(step)
        elif mode == "linear":
            env.update(PLACEMAT_STEP_MODE="linear", PLACEMAT_STEP=str(step))
        r, sums = self.run_under(self.prog, **env)
        self.assertEqual(len(sums), 1, r.stderr)
        (s,) = sums
        out = parse_prog(r.stdout)
        # program-level behaviour
        self.assertEqual(out["calloc_zero"], [["1"]])
        for key in ("realloc_big_ok", "realloc_down_ok", "realloc_small_ok", "aligned256_ok", "memalign_einval"):
            self.assertEqual(out[key], [["1"]], key)
        self.assertEqual(out["memalign4096_ok"], [["0", "1"]])
        self.assertEqual(out["memalign_small_ok"], [["0", "1"]])
        self.assertEqual(out["strdup"], [["hello"]])
        self.assertIn("done", out)

        blocks = {b[0]: b for b in s["blocks"]}
        expected_large = [("big", i) for i in range(4)] + [("calloc", 0), ("realloc_grow", 0), ("realloc_big", 0),
                                                          ("realloc_down", 0), ("memalign4096", 0), ("aligned256", 0)]
        # every colour call is logged right after it, so khash folds the logged k values in order
        self.assertEqual(s["khash"], khash(b[2] for b in s["blocks"]))
        self.assertEqual(s["coloured"], len(s["blocks"]))
        self.assertGreaterEqual(s["coloured"], len(expected_large))
        self.assertEqual(s["blocks"][0][2], 0, "the first coloured block defines the base, so its k is 0")
        wrapped, pos = 0, 0
        for key, i in expected_large:
            addr_s, n, usable, mod = out[key][i]
            addr = int(addr_s, 16)
            # the log lists coloured blocks in allocation order (the runtime may add its own in between)
            while pos < len(s["blocks"]) and s["blocks"][pos][:2] != (addr, int(n)):
                pos += 1
            self.assertLess(pos, len(s["blocks"]), f"{key} {i} not logged as a coloured block")
            _, size, k, off = s["blocks"][pos]
            pos += 1
            self.assertEqual(int(usable), int(n), "usable size is the requested size")
            self.assertEqual(int(mod), addr % 16384)
            want = block_offset(k, colour, mode, step, colour_span=SPAN, step_span=SPAN)
            if key == "memalign4096":
                want, wrapped = want - want % 4096, wrapped + (want % 4096 != 0)
            elif key == "aligned256":
                want, wrapped = want - want % 256, wrapped + (want % 256 != 0)
            self.assertEqual(off, want, f"{key} {i} k={k}")
            self.assertEqual((addr - off) % 64, 0, "payload start is unit aligned before the offset")
            if key == "big" and i < 2:
                # 300 KB and 2 MB come from page-aligned mappings (mmap on glibc, the large zone on
                # macOS): the no-offset payload is 64 bytes into its page, so the block lands at
                # 64 + offset mod the page size.
                page = os.sysconf("SC_PAGE_SIZE")
                self.assertEqual(addr % page, (64 + off) % page, f"{key} {i}")
        self.assertEqual(s["wrapped"], wrapped)
        for key in ("small",):
            for addr_s, n, usable, _ in out[key]:
                self.assertNotIn(int(addr_s, 16), blocks)
                self.assertGreaterEqual(int(usable), int(n))
        # frees: every block the program freed is logged as freed
        freed = set(s["frees"])
        for key, i in expected_large:
            if key not in ("realloc_grow", "realloc_big", "realloc_down"):
                self.assertIn(int(out[key][i][0], 16), freed, key)
        return out, s

    def test_without_interposer(self):
        r, sums = self.run_under(self.prog, interpose=False, PLACEMAT_COLOUR="1088")
        self.assertEqual(sums, [])
        self.assertIn("done", r.stdout)

    def test_no_offset_goes_through_wrapper(self):
        out, s = self.check_prog()
        self.assertEqual(s["config"]["step_mode"], "none")
        self.assertTrue(all(b[3] == 0 for b in s["blocks"]))

    def test_colour(self):
        out, s = self.check_prog(colour=1088)
        self.assertEqual(s["config"]["colour"], "1088")
        # without steps every block has the same offset: 1088 (or that rounded to a requested alignment)
        self.assertEqual({b[3] for b in s["blocks"]} - {1024, 0}, {1088})

    def test_hashed_step(self):
        out, s = self.check_prog(mode="hashed", step=0xC0FFEE123456789A)
        self.assertGreater(len({b[3] for b in s["blocks"]}), 3)

    def test_hashed_step_with_colour(self):
        self.check_prog(mode="hashed", step=0, colour=640)

    def test_linear_step(self):
        self.check_prog(mode="linear", step=192)

    def test_no_log_still_wraps(self):
        # nothing configured, no log: blocks are still over-allocated with a header (usable size is the request)
        r, sums = self.run_under(self.prog, PLACEMAT_LOG="")
        self.assertEqual(sums, [])
        out = parse_prog(r.stdout)
        for addr_s, n, usable, _ in out["big"]:
            self.assertEqual(int(usable), int(n))
            self.assertEqual(int(addr_s, 16) % 64, 0)

    def test_robust_program(self):
        r, sums = self.run_under(self.robust, PLACEMAT_STEP_SEED="7", PLACEMAT_MIN="4096")
        self.assertEqual(r.stdout, "ok 160000 s0000000\nchurn ok\n")
        self.assertEqual(r.stderr, "")
        (s,) = sums
        self.assertGreater(s["coloured"], 200000)

    def test_threads(self):
        # four threads allocating, growing, checking and freeing large and small blocks at once
        r, sums = self.run_under(self.threads, PLACEMAT_STEP_SEED="3", PLACEMAT_COLOUR="64")
        self.assertEqual(r.stdout, "ok\n")
        (s,) = sums
        self.assertGreater(s["coloured"], 1000)

    @unittest.skipUnless(sys.platform == "linux", "Linux system programs")
    def test_linux_system_programs(self):
        for cmd in (["/bin/ls", "-la", "/usr/lib"], [sys.executable, "-c", "import json, sqlite3; print(len(json.dumps(list(range(100000)))))"],
                    ["/bin/sh", "-c", "echo hi | sort"]):
            with self.subTest(cmd=cmd[0]):
                r, sums = self.run_under(cmd[0], *cmd[1:], PLACEMAT_STEP_SEED="11", PLACEMAT_COLOUR="4096")
                self.assertGreaterEqual(len(sums), 1)
                self.assertTrue(r.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "macOS non-SIP program")
    def test_macos_python(self):
        # Homebrew/pyenv Pythons are not SIP-protected; /usr/bin/python3 is (and would silently not load it).
        exe = Path(sys.executable).resolve()
        if str(exe).startswith(("/usr/", "/System/", "/bin/")):
            self.skipTest("SIP-protected interpreter")
        r, sums = self.run_under(exe, "-c", "import json; print(len(json.dumps(list(range(100000)))))",
                                 PLACEMAT_STEP_SEED="11", PLACEMAT_COLOUR="4096")
        if not sums:
            self.skipTest("interpreter ignores DYLD_INSERT_LIBRARIES (hardened runtime?)")
        self.assertEqual(r.stdout.strip(), "688890")
        self.assertGreater(sums[0]["coloured"], 0)


if __name__ == "__main__":
    unittest.main()
