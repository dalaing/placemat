"""placemat.build: the cache key (adapter scripts outside the config directory, the platform), nm's Mach-O
header symbol, and pinned links (-z separate-code on ELF; a build whose pinned functions miss their planned
offsets fails)."""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from placemat import binary as B
from placemat import build, config

from .test_binary import PINME, ToolchainCase


def project(d: Path, build_cmd: str, inputs: str = '["*.c"]', target: str = "") -> config.Config:
    (d / "cfg").mkdir(parents=True, exist_ok=True)
    (d / "cfg" / "placemat.toml").write_text(
        f'[project]\nroot = ".."\n[build]\ncommand = {build_cmd}\nbinary = "x"\ninputs = {inputs}\n' + target)
    return config.load(d / "cfg" / "placemat.toml")


class CacheKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "build.py").write_text("print('v1')\n")
        (self.dir / "harness.c").write_text("int main(void){return 0;}\n")
        (self.dir / "extra").mkdir()
        (self.dir / "extra" / "shared.h").write_text("#define A 1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_script_in_parent_directory(self):
        # examples/zstd/linux: command = ["python3", "{config_dir}/../build.py"]; the harness sits beside it
        cfg = project(self.dir, '["python3", "{config_dir}/../build.py"]')
        files = build.adapter_files(cfg)
        self.assertIn((self.dir / "build.py").resolve(), files)
        self.assertIn((self.dir / "harness.c").resolve(), files)
        k1 = build.cache_key(cfg)
        self.assertEqual(build.cache_key(cfg), k1)
        (self.dir / "harness.c").write_text("int main(void){return 1;}\n")
        k2 = build.cache_key(cfg)
        self.assertNotEqual(k2, k1)
        (self.dir / "build.py").write_text("print('v2')\n")
        self.assertNotEqual(build.cache_key(cfg), k2)

    def test_command_paths_relative_to_root_and_after_equals(self):
        cfg = project(self.dir, '["python3", "build.py", "--include=extra/shared.h"]')
        files = build.adapter_files(cfg)
        self.assertIn((self.dir / "build.py").resolve(), files)       # relative to the root, where it runs
        self.assertIn((self.dir / "extra" / "shared.h").resolve(), files)

    def test_inputs_outside_the_tree(self):
        cfg = project(self.dir, '["true"]', inputs='["src/*.c", "{config_dir}/../extra/*.h"]')
        self.assertIn((self.dir / "extra" / "shared.h").resolve(), build.adapter_files(cfg))
        k1 = build.cache_key(cfg)
        (self.dir / "extra" / "shared.h").write_text("#define A 2\n")
        self.assertNotEqual(build.cache_key(cfg), k1)
        # the arm's own inputs ignore the absolute pattern (it is in the key above)
        (self.dir / "arm" / "src").mkdir(parents=True)
        (self.dir / "arm" / "src" / "a.c").write_text("int a;\n")
        Bd = build.Builder(cfg, self.dir / "work")
        self.assertTrue(Bd.arm("a", str(self.dir / "arm")).tree.startswith("d"))

    def test_platform_in_key(self):
        local = project(self.dir / "l", '["true"]')
        self.assertIn(build.build_platform(local).split()[0], ("Darwin", "Linux"))
        # a target prefix (here one that runs locally) is part of the key, with the target's uname
        remote = project(self.dir / "r", '["true"]', target='[target]\nprefix = ["env"]\n')
        self.assertTrue(build.build_platform(remote).startswith("target env: "))
        self.assertNotEqual(build.cache_key(local), build.cache_key(remote))
        self.assertNotIn("?", build.build_platform(remote))


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("cc") and shutil.which("nm"), "Mach-O only")
class MachOHeader(unittest.TestCase):
    def test_header_is_not_a_function(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "t.c").write_text("int f(int x){return x*3;}\nint main(int c,char**v){return f(c);}\n")
            subprocess.run(["cc", "-O1", str(Path(d) / "t.c"), "-o", str(Path(d) / "t")], check=True, timeout=120)
            nm = subprocess.run(["nm", "-n", str(Path(d) / "t")], capture_output=True, text=True).stdout
            self.assertIn("__mh_execute_header", nm)                     # nm lists it as a T symbol
            syms = build.nm_symbols(Path(d) / "t")
            self.assertNotIn("_mh_execute_header", syms)
            self.assertNotIn("mh_execute_header", syms)
            self.assertEqual(min(syms, key=syms.get), "f")               # the first text symbol is code


class PinnedLinks(ToolchainCase):
    """A pinned arm with pin pads planned for every build (--pin): ELF links get -z separate-code through
    PLACEMAT_LDFLAGS, and a final link that moves the ordered region away from the plan fails the build."""
    BOUNDARY = 128

    def _project(self, tc, move: bool) -> tuple[build.Builder, build.Arm]:
        proj = self.dir / "proj"
        proj.mkdir()
        (proj / "pinme.c").write_text(PINME.read_text())
        cc = " ".join(shlex.quote(x) for x in tc.cc + ["-O2", "-fno-omit-frame-pointer"]
                      + [f for f in tc.flags if "separate-code" not in f])
        link = " ".join(shlex.quote(x) for x in tc.link)
        of = " ".join(tc.order_flags('"$PLACEMAT_ORDER_FILE"'))
        if tc.style == "ld64":                 # move __text in the final link only: a larger header pad
            mv = "-Wl,-headerpad,0x84"
            ldf = "$PLACEMAT_LDFLAGS"
        else:                                  # lld without -z separate-code: .rodata before .text grows
            (proj / "big.c").write_text("const char placemat_test_big[200] = {1};\n")
            mv = shlex.quote(str(proj / "big.c"))
            ldf = "" if move else "$PLACEMAT_LDFLAGS"
        (proj / "build.sh").write_text(
            "set -e\nF=\nX=\nO=\nP=\nM=\n"
            'echo "$PLACEMAT_LDFLAGS" > "$PLACEMAT_WORK/ldflags"\n'
            '[ -n "$PLACEMAT_ORDER_FILE" ] && F=-falign-functions=$PLACEMAT_ALIGN\n'
            '[ -n "$PLACEMAT_PIN_SOURCE" ] && X="$PLACEMAT_PIN_SOURCE"\n'
            + (f'[ -n "$PLACEMAT_PIN_SOURCE" ] && M={shlex.quote(mv)}\n' if move else "")
            + '[ -n "$PLACEMAT_PAD_SOURCE" ] && P="$PLACEMAT_PAD_SOURCE"\n'
            f'[ -n "$PLACEMAT_ORDER_FILE" ] && O={of}\n'
            f'{cc} $F $P "$PLACEMAT_SRC/pinme.c" $X $M {link} $O {ldf} -o "$PLACEMAT_OUT/pinme"\n')
        (proj / "placemat.toml").write_text(
            '[build]\ncommand = ["sh", "{config_dir}/build.sh"]\nbinary = "pinme"\ninputs = ["*.c"]\npin_align = 16\n'
            f'[machine]\nboundary = {self.BOUNDARY}\n')
        order = self.dir / "base.order"
        fmt = "macho" if tc.style == "ld64" else "elf"
        order.write_text("\n".join(B.link_name(fmt, n) for n in ["dot", "poly", "scan", "maxv", "sum"]) + "\n")
        Bd = build.Builder(config.load(proj / "placemat.toml"), self.dir / "work")
        return Bd, Bd.arm("pinned", str(proj), pin_order=str(order))

    def _native(self, styles):
        tcs = [tc for tc in self.each() if tc.runs and tc.style in styles]
        if not tcs:
            self.skipTest(f"no native {'/'.join(styles)} toolchain")
        return tcs[0]

    def test_separate_code_passed_on_elf(self):
        tc = self._native(("ld64", "lld", "gnu"))
        Bd, arm = self._project(tc, move=False)
        b = Bd.binary(arm, 4)
        self.assertEqual(Bd.warnings, [], Bd.warnings)
        flags = (self.dir / "work" / "keep" / arm.tree / "ldflags").read_text().split()
        if tc.style == "ld64":
            self.assertEqual(flags, [])
        else:
            self.assertEqual(flags, [build.SEPARATE_CODE])
            self.assertEqual(B.info(b).fmt, "elf")

    def test_misplaced_region_fails_the_build(self):
        tc = self._native(("ld64", "lld"))
        Bd, arm = self._project(tc, move=True)
        with self.assertRaises(SystemExit) as cm:
            Bd.binary(arm, 4)
        msg = str(cm.exception.code)
        self.assertIn("not where their pin pads were planned", msg)
        self.assertIn("misplaced:", msg)
        if tc.style != "ld64":
            self.assertIn("separate-code", msg)
        self.assertFalse((self.dir / "work" / "bin" / arm.tree / "p4" / "pinme").exists())   # not cached


if __name__ == "__main__":
    unittest.main()
