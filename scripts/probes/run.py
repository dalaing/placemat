#!/usr/bin/env python3
"""Address probes for the design's platform assumptions (DESIGN 5.2, 5.4, 5.5); no timing, so they can run
on shared CI machines.

    python3 scripts/probes/run.py [--runs N] [--json OUT]

- stack: argv's and a local's address over N executions per environment size (an empty environment plus
  one variable of S bytes): is the initial stack offset randomised within a page, and does it move with
  the bytes above the stack (DESIGN 5.5)?
- region: a 1 GB anonymous mapping's base, an 8 MB malloc block and the image over N executions: do the
  bases repeat, at what alignment, in how many populations (the region lottery, DESIGN 5.4)?
- pads: for each C compiler found, which phases mod 16 and mod 64 placemat's code-axis pads actually move
  the next function by, at -O2 (DESIGN 5.2).
- machine: the CPU (vendor, model, microcode), cache geometry, transparent huge pages and ASLR settings.
- toolchain: each compiler's alignment of functions and loops at -O2 (from its assembly), whether it adds
  endbr64/bti landing pads, and how the linker lays out the segments (separate code or not).
- pmu: which hardware counters a process may count on itself (perf_event_open): the generic ones, and
  the front-end events that show code placement without timing it (Intel: uops from the decoded-uop cache
  against the legacy decoders, 4K aliasing; AMD: op-cache hits and misses).

Writes Markdown to stdout (and to $GITHUB_STEP_SUMMARY when set) and, with --json, the raw summaries."""
from __future__ import annotations

import argparse
import collections
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from placemat.build import pad_source  # noqa: E402

SIZES = list(range(0, 65)) + [128, 256, 512, 1024, 2048, 4096, 8192, 16384]


def build(cc: str, src: Path, out: Path) -> Path:
    subprocess.run([cc, "-O2", "-o", str(out), str(src)], check=True)
    return out


def run(exe: Path, env: dict) -> list[int]:
    return [int(x) for x in subprocess.run([str(exe)], env=env, capture_output=True, text=True,
                                           check=True).stdout.split()]


def stack(exe: Path, runs: int) -> dict:
    per = {}
    for s in SIZES:
        rows = [run(exe, {"P": "x" * s}) for _ in range(runs)]
        argv = [a for a, _ in rows]
        loc = [l for _, l in rows]
        per[s] = {"argv_mod4k_distinct": len({a % 4096 for a in argv}),
                  "local_mod4k_distinct": len({l % 4096 for l in loc}),
                  "argv_mod4k_mode": collections.Counter(a % 4096 for a in argv).most_common(1)[0][0],
                  "local_mod64_distinct": len({l % 64 for l in loc}),
                  "argv_page_distinct": len({a // 4096 for a in argv})}
    modes = [per[s]["argv_mod4k_mode"] for s in range(0, 65)]
    steps = sorted({(modes[i] - modes[i + 1]) % 4096 for i in range(64)})
    jit = [per[s]["argv_mod4k_distinct"] for s in SIZES]
    return {"runs": runs, "per_size": per,
            "jitter_mod4k": {"min": min(jit), "max": max(jit)},
            "mode_steps_0_64": steps,
            "randomised_within_page": min(jit) > runs // 2}


def bit(x: int) -> int:
    return (x & -x).bit_length() - 1 if x else 64


def region(exe: Path, runs: int) -> dict:
    rows = [run(exe, {}) for _ in range(runs)]
    out = {"runs": runs}
    for k, i in (("region", 0), ("malloc_8mb", 1), ("image", 2), ("stack", 3)):
        xs = [r[i] for r in rows]
        c = collections.Counter(xs)
        out[k] = {"distinct": len(c), "repeating": sum(1 for v in c.values() if v > 1),
                  "top": [[hex(a), n] for a, n in c.most_common(5)],
                  "lowest_set_bit": dict(sorted(collections.Counter(bit(x) for x in xs).items())),
                  "distinct_mod_8mb": len({x % (8 << 20) for x in xs})}
    d = collections.Counter(r[0] - r[2] for r in rows)
    out["region_minus_image_distinct"] = len(d)
    return out


def compilers() -> list[str]:
    seen, out = set(), []
    for c in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if not (c and shutil.which(c)):
            continue
        v = subprocess.run([c, "--version"], capture_output=True, text=True).stdout   # gcc is clang on macOS
        v = v.split(None, 1)[1] if v.split() else v       # "cc (Ubuntu ...)" and "gcc (Ubuntu ...)" are one gcc
        if v not in seen:
            seen.add(v); out.append(c)
    return out


def pads(tmp: Path) -> dict:
    out = {}
    for cc in compilers():
        ver = subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
        addr = {}
        for p in range(0, 128, 4):
            src = tmp / f"pad{p}.c"
            src.write_text(pad_source(p) + "int target(int x) { return x * 3 + 1; }\n")
            obj = tmp / f"pad{p}.o"
            subprocess.run([cc, "-O2", "-c", str(src), "-o", str(obj)], check=True)
            nm = subprocess.run(["nm", str(obj)], capture_output=True, text=True, check=True).stdout
            m = re.search(r"^([0-9a-fA-F]+) T _?target$", nm, re.M)
            if m:
                addr[p] = int(m.group(1), 16)
        if not addr:
            continue
        sh = [addr[p] - addr[0] for p in sorted(addr)]
        out[cc] = {"version": ver, "shift_mod16": sorted({x % 16 for x in sh}),
                   "shift_mod64_distinct": len({x % 64 for x in sh}), "pads": len(sh)}
    return out


def _read(p: str) -> str:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def machine() -> dict:
    out = {"cpus": os.cpu_count()}
    info = _read("/proc/cpuinfo")
    for k in ("vendor_id", "model name", "cpu family", "model", "stepping", "microcode", "CPU implementer",
              "CPU part"):
        m = re.search(rf"^{re.escape(k)}\s*:\s*(.*)$", info, re.M)
        if m:
            out[k] = m.group(1)
    if not info and shutil.which("sysctl"):
        out["model name"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                           text=True).stdout.strip()
    caches = []
    for d in sorted(Path("/sys/devices/system/cpu/cpu0/cache").glob("index*")):
        caches.append({k: _read(str(d / k)) for k in ("level", "type", "size", "ways_of_associativity",
                                                      "coherency_line_size", "number_of_sets")})
    out["caches"] = caches
    out["thp"] = _read("/sys/kernel/mm/transparent_hugepage/enabled")
    out["aslr"] = _read("/proc/sys/kernel/randomize_va_space")
    out["perf_event_paranoid"] = _read("/proc/sys/kernel/perf_event_paranoid")
    out["pmus"] = sorted(p.name for p in Path("/sys/bus/event_source/devices").glob("*")) \
        if Path("/sys/bus/event_source/devices").exists() else []
    return out


ALIGN_SRC = ("long sum(const long *x, long n) { long s = 0; for (long i = 0; i < n; i++) s += x[i] * 3 + (s >> 7);"
             " return s; }\nint main(int c, char **v) { (void)v; return (int)sum(0, c - 1); }\n")


def toolchain(tmp: Path) -> dict:
    out = {}
    src = tmp / "align.c"
    src.write_text(ALIGN_SRC)
    for cc in compilers():
        asm = subprocess.run([cc, "-O2", "-S", "-o", "-", str(src)], capture_output=True, text=True).stdout
        aligns = collections.Counter(l.strip() for l in asm.splitlines() if l.strip().startswith((".p2align", ".align", ".balign")))
        exe = tmp / "align"
        subprocess.run([cc, "-O2", "-o", str(exe), str(src)], capture_output=True)
        lands = ""
        if shutil.which("objdump") and exe.exists():
            dis = subprocess.run(["objdump", "-d", str(exe)], capture_output=True, text=True).stdout
            lands = ",".join(k for k in ("endbr64", "bti", "paciasp") if k in dis)
        loads = ""
        if shutil.which("readelf") and exe.exists():
            ph = subprocess.run(["readelf", "-lW", str(exe)], capture_output=True, text=True).stdout
            loads = " ".join("".join(l.split()[6:-1]) or "?" for l in ph.splitlines()
                             if l.strip().startswith("LOAD"))       # flags (R, RE, RW), one per LOAD segment
        gcc_opts = ""
        if "gcc" in subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.lower() \
                and "clang" not in subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.lower():
            q = subprocess.run([cc, "-O2", "-Q", "--help=optimizers"], capture_output=True, text=True).stdout
            gcc_opts = " ".join(" ".join(l.split()) for l in q.splitlines() if "-falign-" in l)
        out[cc] = {"alignment_directives": dict(aligns), "landing_pads": lands or "none",
                   "load_segments": loads, "gcc_align_options": gcc_opts}
    ld = subprocess.run(["ld", "--version"], capture_output=True, text=True).stdout.splitlines() if shutil.which("ld") else []
    out["ld"] = ld[0] if ld else ""
    return out


# (name, type, config): type 0 = generic hardware event, 4 = raw. Intel raw config: event | umask << 8;
# AMD: event bits 7:0, umask << 8, event bits 11:8 << 32.
GENERIC = [("cycles", 0, 0), ("instructions", 0, 1), ("cache_misses", 0, 3), ("branch_misses", 0, 5)]
INTEL = [("idq_dsb_uops", 4, 0x0879), ("idq_mite_uops", 4, 0x0479), ("ld_blocks_partial_address_alias", 4, 0x0107)]
AMD = [("op_cache_hit", 4, (0x2 << 32) | (0x03 << 8) | 0x8F), ("op_cache_miss", 4, (0x2 << 32) | (0x04 << 8) | 0x8F),
       ("uops_from_decoder", 4, 0x01AA), ("uops_from_op_cache", 4, 0x02AA)]


def pmu(exe: Path, vendor: str) -> dict:
    ev = GENERIC + (INTEL if "Intel" in vendor else AMD if "AMD" in vendor else [])
    r = subprocess.run([str(exe)] + [f"{n}={t}:{c:x}" for n, t, c in ev], capture_output=True, text=True).stdout
    out = {}
    for l in r.splitlines():
        f = l.split()
        if f == ["unsupported"]:
            return {"unsupported": True}
        out[f[0]] = f"error {f[2]}" if f[1] == "error" else int(f[1])
    return out


def cpu_name() -> str:
    """The CPU's model (hosted runners mix AMD and Intel models from run to run)."""
    m = machine()
    return m.get("model name") or f"arm {m.get('CPU implementer', '?')}/{m.get('CPU part', '?')}"


def markdown(r: dict) -> str:
    st, rg, pd, mc = r["stack"], r["region"], r["pads"], r["machine_facts"]
    cpu = mc.get("model name") or f"{mc.get('CPU implementer', '?')}/{mc.get('CPU part', '?')}"
    L = [f"## placemat address probes: {r['system']} {r['machine']}", "",
         f"**Machine:** {cpu} ({mc.get('vendor_id', '')} family {mc.get('cpu family', '?')} model {mc.get('model', '?')}, "
         f"microcode {mc.get('microcode', '?')}), {mc.get('cpus')} CPUs; caches "
         + ", ".join(f"L{c['level']} {c['type']} {c['size']} {c['ways_of_associativity']}-way" for c in mc.get("caches", []))
         + f"; THP `{mc.get('thp', '')}`; ASLR {mc.get('aslr', '')}; perf_event_paranoid {mc.get('perf_event_paranoid', '')}; "
         f"PMUs {mc.get('pmus', [])}.", "",
         "**Counters** (counted on the process itself): " + ", ".join(f"{k} {v}" for k, v in r["pmu"].items()), "",
         "**Toolchain:** " + str(r["toolchain"].get("ld", "")), ""]
    for cc, x in r["toolchain"].items():
        if cc != "ld":
            L.append(f"- `{cc}`: alignment {x['alignment_directives']}; landing pads {x['landing_pads']}; "
                     f"LOAD segments {x['load_segments'] or '-'}" + (f"; {x['gcc_align_options']}" if x['gcc_align_options'] else ""))
    L += ["",
         f"**Stack** ({st['runs']} executions per environment size, {len(st['per_size'])} sizes): "
         f"argv's offset mod 4 KB took {st['jitter_mod4k']['min']}-{st['jitter_mod4k']['max']} distinct values "
         f"per size, so it is {'randomised within a page' if st['randomised_within_page'] else 'not randomised within a page'}; "
         f"between sizes 0-64 its most common offset moved in steps of {st['mode_steps_0_64']} bytes.", "",
         f"**Region lottery** ({rg['runs']} executions):", "",
         "| what | distinct | repeating | lowest set bit (count) | distinct mod 8 MB | most common |", "|---|---|---|---|---|---|"]
    for k in ("region", "malloc_8mb", "image", "stack"):
        x = rg[k]
        L.append(f"| {k} | {x['distinct']} | {x['repeating']} | {x['lowest_set_bit']} | {x['distinct_mod_8mb']} | "
                 + ", ".join(f"{a} ×{n}" for a, n in x["top"][:3]) + " |")
    L += ["", f"Region base minus image: {rg['region_minus_image_distinct']} distinct values.", "",
          "**Pads** (shift of the next function over pads 0-124 B in 4-byte steps, at -O2):", ""]
    for cc, x in pd.items():
        L.append(f"- `{cc}` ({x['version']}): phases mod 16 {x['shift_mod16']}, {x['shift_mod64_distinct']} distinct mod 64")
    return "\n".join(L) + "\n"


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--runs", type=int, default=50)
    a.add_argument("--json")
    a = a.parse_args()
    cc = compilers()[0]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = {"system": platform.system(), "machine": platform.machine(), "release": platform.release(),
             "stack": stack(build(cc, HERE / "stackprobe.c", t / "stackprobe"), a.runs),
             "region": region(build(cc, HERE / "regionprobe.c", t / "regionprobe"), 4 * a.runs),
             "pads": pads(t),
             "machine_facts": machine(),
             "toolchain": toolchain(t)}
        r["pmu"] = pmu(build(cc, HERE / "pmuprobe.c", t / "pmuprobe"), r["machine_facts"].get("vendor_id", ""))
    md = markdown(r)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
