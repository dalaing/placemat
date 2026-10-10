"""placemat.h against placemat/colour.py: offsets, k, configuration errors, the exit summary, no-op build."""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from placemat import cbuild
from placemat.colour import M64, block_offset, placemat_k, splitmix64

CC = cbuild.find_cc()

DRIVER = r"""
#include "placemat.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    char line[1024], cmd[32], a[256], b[256];
    unsigned long long x, y, z, w;
    while (fgets(line, sizeof line, stdin)) {
        if (sscanf(line, "%31s", cmd) != 1) continue;
        if (!strcmp(cmd, "set") && sscanf(line, "%*s %255s %255s", a, b) == 2) setenv(a, b, 1);
        else if (!strcmp(cmd, "unset") && sscanf(line, "%*s %255s", a) == 1) unsetenv(a);
        else if (!strcmp(cmd, "init")) { placemat__reset_for_tests(); printf("active %d\n", placemat_active()); }
        else if (!strcmp(cmd, "active")) printf("active %d\n", placemat_active());
        else if (!strcmp(cmd, "colour") && sscanf(line, "%*s %llu %llu %llu", &x, &y, &z) == 3)
            printf("%zu\n", placemat_colour((size_t)x, (size_t)y, (size_t)z));
        else if (!strcmp(cmd, "kof") && sscanf(line, "%*s %llu %llu %llu", &x, &y, &z) == 3)
            printf("%lld\n", (long long)(int64_t)placemat__k_of((uintptr_t)x, (uintptr_t)y, (size_t)z));
        else if (!strcmp(cmd, "k") && sscanf(line, "%*s %llu", &x) == 1)
            printf("%lld\n", (long long)(int64_t)placemat_k((const void *)(uintptr_t)x));
        else if (!strcmp(cmd, "region") && sscanf(line, "%*s %llu %llu", &x, &y) == 2)
            placemat_region((const void *)(uintptr_t)x, (size_t)y);
        else if (!strcmp(cmd, "block") && sscanf(line, "%*s %llu %llu %llu %llu", &x, &y, &z, &w) == 4)
            placemat_log_block((const void *)(uintptr_t)x, (size_t)y, (size_t)z, (size_t)w);
        else if (!strcmp(cmd, "alloc") && sscanf(line, "%*s %llu %llu", &x, &y) == 2)
            placemat_log_alloc((const void *)(uintptr_t)x, (size_t)y);
        else if (!strcmp(cmd, "free") && sscanf(line, "%*s %llu", &x) == 1)
            placemat_log_free((const void *)(uintptr_t)x);
        else if (!strcmp(cmd, "mix") && sscanf(line, "%*s %llu", &x) == 1)
            printf("%llu\n", (unsigned long long)placemat_splitmix64(x));
        else { printf("bad command: %s", line); return 3; }
        fflush(stdout);
    }
    return 0;
}
"""

NOHOOK = r"""
#include "placemat.h"
#include <stdio.h>
#include <stdlib.h>

size_t place(char *p, size_t n, size_t spare)
{
    size_t off = 0;
    if (n >= 65536 && placemat_active()) {
        size_t k = placemat_k(p);
        off = placemat_colour(k, n, spare);
        placemat_region(p, n);
        placemat_log_block(p + off, n, k, off);
        placemat_log_alloc(p + off, n);
        placemat_vg_malloclike(p + off, n);
    }
    return off;
}

void unplace(char *p)
{
    placemat_log_free(p);
    placemat_vg_freelike(p);
}

int main(int argc, char **argv)
{
    (void)argv;
    char *p = malloc(200000);
    size_t off = place(p, 100000 * (size_t)argc, 100000);
    unplace(p + off);
    printf("%zu %zu %d\n", off, placemat_k(p), placemat_active());
    free(p);
    return 0;
}
"""

# The same as NOHOOK with every placemat call removed by hand.
PLAIN = r"""
#include <stdio.h>
#include <stdlib.h>

size_t place(char *p, size_t n, size_t spare)
{
    size_t off = 0;
    (void)p; (void)n; (void)spare;
    return off;
}

void unplace(char *p)
{
    (void)p;
}

int main(int argc, char **argv)
{
    (void)argv;
    char *p = malloc(200000);
    size_t off = place(p, 100000 * (size_t)argc, 100000);
    unplace(p + off);
    printf("%zu %zu %d\n", off, (size_t)0, 0);
    free(p);
    return 0;
}
"""

ENV_KEYS = ("PLACEMAT_COLOUR", "PLACEMAT_STEP_MODE", "PLACEMAT_STEP_SEED", "PLACEMAT_STEP", "PLACEMAT_UNIT",
            "PLACEMAT_COLOUR_SPAN", "PLACEMAT_STEP_SPAN", "PLACEMAT_MIN", "PLACEMAT_ADDRLOG", "PLACEMAT_LOG")


def clean_env(**kw: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS
           and k not in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES")}
    env.update(kw)
    return env


def compile_c(src: str, out: Path, *flags: str) -> Path:
    c = out.with_suffix(".c")
    c.write_text(src)
    subprocess.run([CC, "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I", str(cbuild.header_dir()),
                    *flags, str(c), "-o", str(out)], check=True, capture_output=True, text=True)
    return out


def s64(k: int) -> int:
    """The signed value of a 64-bit pattern."""
    k &= M64
    return k - (1 << 64) if k >> 63 else k


def parse_summaries(text: str) -> list[dict]:
    """Split a PLACEMAT_LOG into per-process blocks."""
    out, cur = [], None
    for line in text.splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "placemat-log":
            assert w[1] == "1", line
            cur = {"pid": int(w[2].removeprefix("pid=")), "regions": [], "blocks": [], "frees": [], "lines": [line]}
            continue
        assert cur is not None, line
        cur["lines"].append(line)
        if w[0] == "config":
            cur["config"] = dict(x.split("=", 1) for x in w[1:])
        elif w[0] == "base":
            cur["base"] = int(w[1], 16)
        elif w[0] == "region":
            cur["regions"].append((int(w[1], 16), int(w[2])))
        elif w[0] == "khash":
            assert re.fullmatch(r"0x[0-9a-f]{16}", w[1]), line
            cur["khash"] = int(w[1], 16)
        elif w[0] in ("coloured", "wrapped"):
            cur[w[0]] = int(w[1])
        elif w[0] == "block":
            cur["blocks"].append((int(w[1], 16), int(w[2]), None if w[3] == "-" else int(w[3]),
                                  None if w[4] == "-" else int(w[4])))
        elif w[0] == "free":
            cur["frees"].append(int(w[1], 16))
        elif w[0] == "end":
            out.append(cur)
            cur = None
        else:
            raise AssertionError(f"unknown line {line!r}")
    assert cur is None, "unterminated block"
    return out


def khash(ks) -> int:
    h = 0
    for k in ks:
        h = splitmix64(h ^ (k & M64))
    return h


@unittest.skipUnless(CC, "no C compiler")
class ColourC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="placemat-test-")
        cls.dir = Path(cls.tmp.name)
        cls.driver = compile_c(DRIVER, cls.dir / "driver", "-DPLACEMAT_HOOK")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_driver(self, commands: list[str], env: dict | None = None, check: bool = True):
        r = subprocess.run([str(self.driver)], input="\n".join(commands) + "\n", capture_output=True, text=True,
                           env=env if env is not None else clean_env(), timeout=60)
        if check:
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        return r

    def test_splitmix(self):
        xs = [0, 1, 2, M64, 1 << 63, 0x9E3779B97F4A7C15] + [random.Random(5).getrandbits(64) for _ in range(20)]
        out = self.run_driver([f"mix {x}" for x in xs]).stdout.split()
        self.assertEqual([int(v) for v in out], [splitmix64(x) for x in xs])

    def test_offsets_match_python(self):
        rng = random.Random(20261005)
        ks = [0, 1, 2, 3, 255, 256, 257, 1000, -1, -2, -3, -256, -1000, (1 << 63) - 1, -(1 << 63), 1 << 40, -(1 << 40)]
        seeds = [0, 1, 2, M64, 1 << 63, 0x9E3779B97F4A7C15, 0xDEADBEEFCAFEF00D]
        configs = []
        for unit in (64, 16, 128, 4096):
            for cspan in (16384, 4096, 2 << 20):
                for sspan in (16384, 4096, 1024):
                    if sspan < unit or cspan < unit:
                        continue
                    for mode in (None, "hashed", "linear"):
                        configs.append((unit, cspan, sspan, mode))
        commands, expected = [], []
        for unit, cspan, sspan, mode in configs:
            for rep in range(3):
                colour = rng.choice([0, unit, 17 * unit, cspan - unit, cspan, cspan + 3 * unit,
                                     -unit, 5 * cspan + unit, unit * rng.randrange(1 << 20)])
                step = None
                if mode == "hashed":
                    step = rng.choice(seeds + [rng.getrandbits(64)])
                elif mode == "linear":
                    step = unit * rng.choice([1, 3, sspan // unit - 1, sspan // unit, sspan // unit + 1,
                                              -1, -7, rng.randrange(1, 1 << 30), (1 << 50) + 1])
                minimum = rng.choice([65536, 4096, 1])
                env = {"PLACEMAT_UNIT": str(unit), "PLACEMAT_COLOUR_SPAN": str(cspan), "PLACEMAT_STEP_SPAN": str(sspan),
                       "PLACEMAT_MIN": hex(minimum) if rep == 1 else str(minimum), "PLACEMAT_COLOUR": str(colour)}
                for key in ("PLACEMAT_STEP_SEED", "PLACEMAT_STEP", "PLACEMAT_STEP_MODE"):
                    commands.append(f"unset {key}")
                if mode == "hashed":
                    env["PLACEMAT_STEP_SEED"] = hex(step) if rep == 2 else str(step)
                    if rep:
                        env["PLACEMAT_STEP_MODE"] = "hashed"
                elif mode == "linear":
                    env["PLACEMAT_STEP"] = str(step)
                    env["PLACEMAT_STEP_MODE"] = "linear"
                commands += [f"set {k} {v}" for k, v in env.items()]
                commands.append("init")
                active = int(colour % cspan != 0 or mode is not None)
                expected.append(f"active {active}")
                for _ in range(25):
                    k = rng.choice(ks + [rng.randrange(-(1 << 20), 1 << 20), rng.randrange(-(1 << 63), 1 << 63)])
                    size = rng.choice([minimum - 1, minimum, minimum + 12345, 1 << 30])
                    spare = rng.choice([0, unit - 1, unit, unit + 1, 3 * unit + 5, sspan, cspan + sspan,
                                        rng.randrange(1 << 16), M64])
                    commands.append(f"colour {k & M64} {size} {spare}")
                    if not active or size < minimum:
                        want = 0
                    else:
                        want = block_offset(k, colour, mode, step, unit=unit, colour_span=cspan, step_span=sspan,
                                            spare=spare)
                    expected.append(str(want))
        got = self.run_driver(commands).stdout.splitlines()
        self.assertEqual(len(got), len(expected))
        bad = [(c, g, e) for c, g, e in zip([c for c in commands if c.startswith(("colour", "init"))], got, expected)
               if g != e]
        self.assertEqual(bad[:5], [], f"{len(bad)} mismatches of {len(expected)}")

    def test_k_matches_python(self):
        rng = random.Random(7)
        cases = [(0x7f0000100000, 0x7f0000100000, 65536), (0x7f0000100000 - 1, 0x7f0000100000, 65536),
                 (0x7f0000100000 - 65536, 0x7f0000100000, 65536), (0x7f0000100000 - 65537, 0x7f0000100000, 65536),
                 (0x1000, 0x7fffffff0000, 65536), (0x7fffffff0000, 0x1000, 4096)]
        for _ in range(200):
            base = rng.randrange(1 << 47)
            cases.append((max(1, base + rng.randrange(-(1 << 40), 1 << 40)), base, rng.choice([65536, 4096, 1 << 20, 3, 1])))
        out = self.run_driver([f"kof {a} {b} {g}" for a, b, g in cases]).stdout.split()
        self.assertEqual([int(v) for v in out], [placemat_k(a, b, g) for a, b, g in cases])

    def test_process_base(self):
        # base from the first region reported; later regions do not move it
        base = 0x7f1234560000
        cmds = ["set PLACEMAT_MIN 65536", "init", f"region {base} 1048576", f"region {base - (1 << 30)} 4096"]
        addrs = [base, base + 65535, base + 65536, base - 1, base - 65536, base - 65537, base - (1 << 30)]
        cmds += [f"k {a}" for a in addrs]
        out = self.run_driver(cmds).stdout.split()[2:]
        self.assertEqual([int(v) for v in out], [placemat_k(a, base, 65536) for a in addrs])
        # base from the first block passed to placemat_k
        first = 0x5555000123
        addrs = [first, first - 100, first + 70000, first + (5 << 20)]
        out = self.run_driver(["set PLACEMAT_MIN 4096", "init"] + [f"k {a}" for a in addrs]).stdout.split()[2:]
        self.assertEqual([int(v) for v in out], [placemat_k(a, first, 4096) for a in addrs])

    def test_startup_errors(self):
        bad = [
            ({"PLACEMAT_STEP_SEED": "1", "PLACEMAT_STEP": "64"}, "both set"),
            ({"PLACEMAT_STEP": "64"}, "PLACEMAT_STEP_MODE=linear"),
            ({"PLACEMAT_STEP": "64", "PLACEMAT_STEP_MODE": "hashed"}, "step mode is hashed"),
            ({"PLACEMAT_STEP_SEED": "5", "PLACEMAT_STEP_MODE": "linear"}, "PLACEMAT_STEP_MODE=linear"),
            ({"PLACEMAT_STEP_MODE": "sideways"}, "hashed or linear"),
            ({"PLACEMAT_STEP_SEED": "0x1" + "0" * 16}, "PLACEMAT_STEP_SEED"),
            ({"PLACEMAT_STEP_SEED": "-1"}, "PLACEMAT_STEP_SEED"),
            ({"PLACEMAT_COLOUR": "100"}, "multiple of PLACEMAT_UNIT"),
            ({"PLACEMAT_COLOUR": "12abc"}, "PLACEMAT_COLOUR"),
            ({"PLACEMAT_UNIT": "48"}, "power of two"),
            ({"PLACEMAT_STEP_SPAN": "32"}, "PLACEMAT_STEP_SPAN"),
            ({"PLACEMAT_STEP_MODE": "linear", "PLACEMAT_STEP": "100"}, "multiple of PLACEMAT_UNIT"),
        ]
        for env, needle in bad:
            with self.subTest(env=env):
                r = self.run_driver(["active"], env=clean_env(**env), check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("placemat: configuration error", r.stderr)
                self.assertIn(needle, r.stderr)
                self.assertEqual(r.stdout, "")
        good = [{}, {"PLACEMAT_STEP_SEED": "0"}, {"PLACEMAT_STEP_SEED": hex(M64)},
                {"PLACEMAT_STEP_MODE": "linear", "PLACEMAT_STEP": "-64"}, {"PLACEMAT_COLOUR": "", "PLACEMAT_STEP": ""}]
        for env in good:
            with self.subTest(env=env):
                r = self.run_driver(["active"], env=clean_env(**env))
                want = int(any(v for v in env.values() if v and v != "linear"))
                self.assertEqual(r.stdout, f"active {want}\n")

    def test_summary_log(self):
        log = self.dir / "summary.log"
        log.unlink(missing_ok=True)
        seed = 0x0123456789ABCDEF
        env = clean_env(PLACEMAT_LOG=str(log), PLACEMAT_ADDRLOG="2", PLACEMAT_COLOUR="128", PLACEMAT_STEP_SEED=hex(seed),
                        PLACEMAT_COLOUR_SPAN="16384", PLACEMAT_STEP_SPAN="16384")
        base = 0x7f0000000000
        ks = [0, 3, -2, 7, 3]
        cmds = [f"region {base} 4194304", f"region {base + (8 << 20)} 65536"]
        offsets = []
        for i, k in enumerate(ks):
            spare = 100 if i == 2 else 1 << 20  # block 2 must wrap
            cmds.append(f"colour {k & M64} 65536 {spare}")
            off = block_offset(k, 128, "hashed", seed, colour_span=16384, step_span=16384, spare=spare)
            offsets.append(off)
            cmds.append(f"block {base + (k & 0xffff) * 65536 + off} 65536 {k & M64} {off}")
        cmds += ["colour 9 100 0", f"alloc {base + 64} 70000", f"free {base}"]
        r1 = self.run_driver(cmds, env=env)
        self.assertEqual([int(x) for x in r1.stdout.split()], offsets + [0])
        self.run_driver(["active"], env=env)  # a second process appends its own block
        blocks = parse_summaries(log.read_text())
        self.assertEqual(len(blocks), 2)
        s = blocks[0]
        self.assertEqual(s["lines"][0].split()[:2], ["placemat-log", "1"])
        self.assertEqual(s["config"], {"colour": "128", "step_mode": "hashed", "step": "0x0123456789abcdef",
                                       "unit": "64", "colour_span": "16384", "step_span": "16384", "min": "65536",
                                       "addrlog": "2"})
        self.assertEqual(s["base"], base)
        self.assertEqual(s["regions"], [(base, 4194304), (base + (8 << 20), 65536)])
        self.assertEqual(s["coloured"], 5)
        self.assertEqual(s["wrapped"], 1)
        self.assertEqual(s["khash"], khash(ks))
        self.assertEqual([(b[2], b[3]) for b in s["blocks"]], [(k, o) for k, o in zip(ks, offsets)] + [(None, None)])
        self.assertEqual(s["frees"], [base])
        keys = [ln.split()[0] for ln in s["lines"]]
        self.assertEqual(keys, ["placemat-log", "config", "base", "region", "region", "khash", "coloured", "wrapped"]
                         + ["block"] * 6 + ["free", "end"])
        t = blocks[1]
        self.assertNotEqual(t["pid"], s["pid"])
        self.assertEqual((t["coloured"], t["wrapped"], t["khash"], t["blocks"]), (0, 0, 0, []))
        self.assertNotIn("base", t)

    def test_summary_destinations(self):
        # ADDRLOG=1 without PLACEMAT_LOG: summary on stderr, regions but no blocks
        r = self.run_driver(["region 4096 8192", "colour 1 65536 0", "block 4096 65536 1 0"],
                            env=clean_env(PLACEMAT_ADDRLOG="1", PLACEMAT_COLOUR="64"))
        (s,) = parse_summaries(r.stderr)
        self.assertEqual((s["regions"], s["blocks"], s["coloured"], s["wrapped"]), ([(4096, 8192)], [], 1, 1))
        self.assertEqual(s["khash"], khash([1]))
        # nothing configured: nothing written, inactive, colour returns 0 and counts nothing
        r = self.run_driver(["active", "colour 1 65536 100000"], env=clean_env())
        self.assertEqual((r.stdout, r.stderr), ("active 0\n0\n", ""))
        # PLACEMAT_LOG alone (the no-offset setting) is active: blocks are counted and hashed
        log = self.dir / "noofs.log"
        log.unlink(missing_ok=True)
        r = self.run_driver(["active", "colour 5 65536 0", "colour 6 65535 0"], env=clean_env(PLACEMAT_LOG=str(log)))
        self.assertEqual(r.stdout, "active 1\n0\n0\n")
        (s,) = parse_summaries(log.read_text())
        self.assertEqual((s["coloured"], s["wrapped"], s["khash"], s["config"]["step_mode"]), (1, 0, khash([5]), "none"))

    def test_fork_child_writes_own_block(self):
        src = r"""
        #include "placemat.h"
        #include <stdio.h>
        #include <sys/wait.h>
        int main(void) {
            placemat_colour(1, 1 << 20, 1 << 20);
            pid_t c = fork();
            if (c == 0) { placemat_colour(2, 1 << 20, 1 << 20); return 0; }
            waitpid(c, 0, 0);
            placemat_colour(3, 1 << 20, 1 << 20);
            return 0;
        }
        """
        exe = compile_c(src, self.dir / "forker", "-DPLACEMAT_HOOK")
        log = self.dir / "fork.log"
        log.unlink(missing_ok=True)
        subprocess.run([str(exe)], env=clean_env(PLACEMAT_LOG=str(log), PLACEMAT_COLOUR="64"), check=True, timeout=30)
        child, parent = parse_summaries(log.read_text())
        self.assertEqual((child["coloured"], child["khash"]), (1, khash([2])))
        self.assertEqual((parent["coloured"], parent["khash"]), (2, khash([1, 3])))

    def test_no_hook_is_noop(self):
        exe = compile_c(NOHOOK, self.dir / "nohook")
        r = subprocess.run([str(exe)], capture_output=True, text=True, check=True,
                           env=clean_env(PLACEMAT_COLOUR="64", PLACEMAT_ADDRLOG="2"))
        self.assertEqual((r.stdout, r.stderr), ("0 0 0\n", ""))
        # the object code is the same as the program with the calls removed by hand
        objs = []
        for name, src in (("a", NOHOOK), ("b", PLAIN)):
            d = self.dir / f"obj-{name}"
            d.mkdir(exist_ok=True)
            (d / "t.c").write_text(src)
            subprocess.run([CC, "-O2", "-I", str(cbuild.header_dir()), "-c", "t.c", "-o", "t.o"], cwd=d, check=True)
            objs.append((d / "t.o").read_bytes())
        self.assertEqual(objs[0], objs[1])
        nm = subprocess.run(["nm", str(self.dir / "obj-a" / "t.o")], capture_output=True, text=True).stdout
        self.assertNotIn("placemat", nm)

    def test_hook_with_valgrind_flag_compiles(self):
        # with PLACEMAT_VALGRIND the wrappers use valgrind.h when it exists, else stay no-ops
        exe = compile_c(NOHOOK, self.dir / "vg", "-DPLACEMAT_HOOK", "-DPLACEMAT_VALGRIND")
        r = subprocess.run([str(exe)], capture_output=True, text=True, check=True, env=clean_env(PLACEMAT_COLOUR="64"))
        off, k, active = (int(x) for x in r.stdout.split())
        self.assertEqual((off, k, active), (64, 0, 1))


if __name__ == "__main__":
    unittest.main()
