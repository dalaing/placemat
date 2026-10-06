"""Binary analysis for Mach-O and ELF executables (DESIGN §6.1, §6.2, §3.3).

Functions with addresses and sizes, small loops (backward branches within a few hundred bytes) and
whether they cross a boundary, how far functions moved between two builds, and per-function
machine-code equality between two builds with layout-dependent addresses masked.

Formats and tools:
- Mach-O (arm64, x86_64; a universal binary is read at the host's architecture when it has one):
  load commands and the symbol table are read directly; code is disassembled with ``otool -tV``.
- ELF (aarch64, x86-64; 64-bit): section headers and ``.symtab`` (else ``.dynsym``) are read
  directly; code is disassembled with ``objdump -d --no-show-raw-insn`` (GNU or LLVM; when the
  installed objdump cannot disassemble the binary's architecture, ``llvm-objdump`` is tried).
  ``PLACEMAT_OBJDUMP`` overrides the choice.

The format is told from the file's magic bytes. Function names are normalised to the source level:
Mach-O's leading underscore is stripped; LTO and local suffixes such as ``.1005`` are kept.
Each binary is disassembled once per process (cached by path, size and modification time).
"""
from __future__ import annotations

import argparse
import bisect
import collections
import dataclasses
import functools
import glob
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

__all__ = [
    "Func", "Loop", "Same", "BinInfo", "info", "fmt", "arch", "text_section", "functions", "function_map",
    "link_name", "read_order", "Insn", "disassemble", "loops", "crosses", "crossing_loops", "crossing_shifts",
    "shifts", "same_code", "normalised", "cli",
]


@dataclasses.dataclass(frozen=True)
class Func:
    name: str
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclasses.dataclass(frozen=True)
class Loop:
    func: str | None
    start: int      # the branch target (the loop's first byte)
    end: int        # one past the backward branch

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclasses.dataclass
class Same:
    """One function compared between binaries a and b. ``same`` is True when the normalised
    machine code is identical; ``ndiff`` counts differing instructions (position by position, plus
    the difference in length, -1 when the function is missing from one side); ``first_diff`` is
    the offset in a of the first difference; ``name_b`` is b's name when it was matched by its
    suffix-stripped name."""
    name: str
    same: bool
    ndiff: int
    start_a: int | None
    size_a: int | None
    start_b: int | None
    size_b: int | None
    first_diff: int | None = None
    name_b: str | None = None


@dataclasses.dataclass(frozen=True)
class BinInfo:
    path: str
    fmt: str                      # "macho" | "elf"
    arch: str                     # "arm64" | "x86_64"
    text: tuple[int, int]         # (address, size) of the main text section
    image: tuple[int, int]        # (lowest, highest) address of any section
    base: int                     # Mach-O: __TEXT vmaddr; ELF: lowest PT_LOAD/section address
    slice_arch: str | None        # universal Mach-O: the slice read (passed to otool -arch)
    funcs: tuple[Func, ...]
    pie: bool = True              # position-independent (ELF ET_DYN; Mach-O executables are)


# ---------------------------------------------------------------------------------------------
# file parsing


_MH_MAGIC_64 = 0xFEEDFACF
_FAT_MAGIC = 0xCAFEBABE
_CPU_ARM64 = 0x0100000C
_CPU_X86_64 = 0x01000007
_EM_X86_64 = 62
_EM_AARCH64 = 183


def _host_arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x86_64" if m in ("x86_64", "amd64") else m


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("latin-1")


def _parse_macho(path: str, data: bytes, off: int, slice_arch: str | None) -> BinInfo:
    magic, cputype = struct.unpack_from("<II", data, off)
    if magic != _MH_MAGIC_64:
        raise ValueError(f"{path}: only 64-bit little-endian Mach-O is supported")
    arch = {_CPU_ARM64: "arm64", _CPU_X86_64: "x86_64"}.get(cputype, hex(cputype))
    ncmds = struct.unpack_from("<I", data, off + 16)[0]
    p = off + 32
    sections = []                 # (segname, sectname, addr, size), 1-based index = position + 1
    symtab = None
    base = 0
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmd == 0x19:           # LC_SEGMENT_64
            segname = _cstr(data[p + 8:p + 24])
            vmaddr, = struct.unpack_from("<Q", data, p + 24)
            nsects, = struct.unpack_from("<I", data, p + 64)
            if segname == "__TEXT":
                base = vmaddr
            q = p + 72
            for _ in range(nsects):
                sect = _cstr(data[q:q + 16])
                seg = _cstr(data[q + 16:q + 32])
                addr, size = struct.unpack_from("<QQ", data, q + 32)
                sections.append((seg, sect, addr, size))
                q += 80
        elif cmd == 0x2:          # LC_SYMTAB
            symtab = struct.unpack_from("<IIII", data, p + 8)
        p += cmdsize
    try:
        ti = next(i for i, s in enumerate(sections) if s[0] == "__TEXT" and s[1] == "__text")
    except StopIteration:
        raise ValueError(f"{path}: no __TEXT,__text section") from None
    text = (sections[ti][2], sections[ti][3])
    image = (min(s[2] for s in sections), max(s[2] + s[3] for s in sections))
    syms = []
    if symtab:
        symoff, nsyms, stroff, strsize = symtab
        for k in range(nsyms):
            strx, ntype, nsect, _desc, value = struct.unpack_from("<IBBHQ", data, off + symoff + 16 * k)
            if ntype & 0xE0 or (ntype & 0x0E) != 0x0E or nsect != ti + 1:
                continue
            name = _cstr(data[off + stroff + strx:off + stroff + strx + 4096])
            if not name or name.startswith("ltmp"):       # assembler temporaries
                continue
            syms.append((value, name[1:] if name.startswith("_") else name, 0))
    funcs = _sized(syms, text, prefer_st_size=False)
    return BinInfo(path, "macho", arch, text, image, base, slice_arch, funcs)


def _parse_elf(path: str, data: bytes) -> BinInfo:
    if data[4] != 2 or data[5] != 1:
        raise ValueError(f"{path}: only 64-bit little-endian ELF is supported")
    machine, = struct.unpack_from("<H", data, 18)
    arch = {_EM_X86_64: "x86_64", _EM_AARCH64: "arm64"}.get(machine, f"em{machine}")
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    shdrs = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize) for i in range(shnum)]
    sstr = shdrs[shstrndx]
    names = [_cstr(data[sstr[4] + h[0]:sstr[4] + h[0] + 256]) for h in shdrs]
    try:
        ti = names.index(".text")
    except ValueError:
        raise ValueError(f"{path}: no .text section") from None
    text = (shdrs[ti][3], shdrs[ti][5])
    alloc = [h for h in shdrs if h[2] & 0x2 and h[3]]       # SHF_ALLOC with an address
    image = (min(h[3] for h in alloc), max(h[3] + h[5] for h in alloc)) if alloc else text
    symsec = next((i for i, h in enumerate(shdrs) if h[1] == 2), None)   # SHT_SYMTAB
    if symsec is None:
        symsec = next((i for i, h in enumerate(shdrs) if h[1] == 11), None)  # SHT_DYNSYM
    syms = []
    if symsec is not None:
        h = shdrs[symsec]
        strh = shdrs[h[6]]
        for k in range(h[5] // 24):
            st_name, st_info, _other, shndx, value, size = struct.unpack_from("<IBBHQQ", data, h[4] + 24 * k)
            typ = st_info & 0xF
            if shndx != ti or typ not in (0, 2, 10):     # NOTYPE, FUNC, GNU_IFUNC
                continue
            name = _cstr(data[strh[4] + st_name:strh[4] + st_name + 4096])
            if not name or name.startswith(("$", ".L")):   # mapping symbols, local labels
                continue
            syms.append((value, name, size if typ != 0 else 0))
    funcs = _sized(syms, text, prefer_st_size=True)
    e_type, = struct.unpack_from("<H", data, 16)
    return BinInfo(path, "elf", arch, text, image, image[0], None, funcs, e_type == 3)


def _sized(syms, text, prefer_st_size):
    """(address, name, st_size) -> Funcs in address order, sizes from st_size when wanted and
    present, else up to the next symbol at a higher address, or the end of the section."""
    a0, sz = text
    syms = sorted(set(s for s in syms if a0 <= s[0] < a0 + sz), key=lambda s: (s[0], s[1]))
    addrs = sorted({s[0] for s in syms})
    out = []
    for a, name, st in syms:
        i = bisect.bisect_right(addrs, a)
        nxt = addrs[i] if i < len(addrs) else a0 + sz
        out.append(Func(name, a, st if prefer_st_size and st else nxt - a))
    return tuple(out)


@functools.lru_cache(maxsize=32)
def _info_cached(path: str, size: int, mtime: int) -> BinInfo:
    data = Path(path).read_bytes()
    if data[:4] == b"\x7fELF":
        return _parse_elf(path, data)
    magic_be, = struct.unpack_from(">I", data, 0)
    if magic_be == _FAT_MAGIC:
        n, = struct.unpack_from(">I", data, 4)
        slices = [struct.unpack_from(">IIIII", data, 8 + 20 * i) for i in range(n)]
        want = {"arm64": _CPU_ARM64, "x86_64": _CPU_X86_64}.get(_host_arch())
        pick = next((s for s in slices if s[0] == want), slices[0])
        sa = {_CPU_ARM64: "arm64", _CPU_X86_64: "x86_64"}.get(pick[0])
        return _parse_macho(path, data, pick[2], sa)
    if struct.unpack_from("<I", data, 0)[0] == _MH_MAGIC_64:
        return _parse_macho(path, data, 0, None)
    raise ValueError(f"{path}: not a Mach-O or ELF file")


def info(path) -> BinInfo:
    """Format, architecture, text section and functions of a binary (cached)."""
    p = os.path.realpath(str(path))
    st = os.stat(p)
    return _info_cached(p, st.st_size, st.st_mtime_ns)


def fmt(path) -> str:
    """'macho' or 'elf', from the magic bytes."""
    return info(path).fmt


def arch(path) -> str:
    """'arm64' or 'x86_64'."""
    return info(path).arch


def text_section(path) -> tuple[int, int]:
    """(address, size) of the text section (__TEXT,__text or .text)."""
    return info(path).text


def functions(path) -> list[Func]:
    """Functions in the text section, in address order (aliases share a start)."""
    return list(info(path).funcs)


def function_map(path) -> dict[str, Func]:
    """name -> Func (the first, by address, when a local name occurs more than once)."""
    out: dict[str, Func] = {}
    for f in info(path).funcs:
        out.setdefault(f.name, f)
    return out


def link_name(path_or_fmt, name: str, linker: str | None = None) -> str:
    """The name a linker order file needs for function ``name``.

    path_or_fmt: a binary (its format decides), or one of 'macho' / 'ld64' (``_name``), 'elf' / 'lld'
    (``name``, for ``--symbol-ordering-file``), 'gnu' (``.text.name``, the input section that
    ``-ffunction-sections`` gives it, for GNU ld's ``--section-ordering-file``). ``linker`` overrides
    the style for an ELF path ('lld' or 'gnu')."""
    style = _style(path_or_fmt, linker)
    if style == "ld64":
        return "_" + name
    if style == "gnu":
        return ".text." + name
    return name


def _style(path_or_fmt, linker=None) -> str:
    s = str(path_or_fmt)
    if linker:
        s = linker
    elif s not in ("macho", "ld64", "elf", "lld", "gnu"):
        s = fmt(path_or_fmt)
    return {"macho": "ld64", "ld64": "ld64", "elf": "lld", "lld": "lld", "gnu": "gnu"}[s]


# ---------------------------------------------------------------------------------------------
# disassembly


@dataclasses.dataclass(frozen=True)
class Insn:
    addr: int
    mnem: str            # without x86 prefixes (bnd, notrack, cs, ds, ...) and branch hints
    ops: str             # operands with comments stripped
    target: int | None   # direct branch target, for branches
    text: str            # mnemonic and operands as printed (comments stripped, spaces collapsed)


_LINE = re.compile(r"^\s*([0-9a-fA-F]+):?[ \t]+(.+)$")
_OBJ_HEADER = re.compile(r"^[0-9a-fA-F]+ <.*>:\s*$")
_ARM_BR = re.compile(r"^(b|b\.\w+|bc\.\w+|cbz|cbnz|tbz|tbnz)$")
_X86_BR = re.compile(r"^(j\w+|loop\w*)$")
_X86_PREFIX = {"bnd", "notrack", "cs", "ds", "es", "ss", "fs", "gs", "rex", "rex.w", "lock", "rep",
               "repz", "repnz", "repe", "repne", "data16", "addr32", "{disp32}", "{disp8}", "{vex}"}
_ANNOT = re.compile(r"(?:0x)?([0-9a-fA-F]+)\s*<([^>]*)>")
_LASTHEX = re.compile(r"(?:^|[\s,])(?:0x)?([0-9a-fA-F]+)\s*$")


def _objdump_cmds():
    env = os.environ.get("PLACEMAT_OBJDUMP")
    if env:
        return [env]
    cands = ["objdump", "llvm-objdump"]
    seen = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for p in sorted(glob.glob(os.path.join(d, "llvm-objdump-*")), reverse=True):
            cands.append(os.path.basename(p))
    out = []
    for c in cands:
        if c not in seen and shutil.which(c):
            seen.add(c)
            out.append(c)
    return out


def _run(cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def _raw_disassembly(bi: BinInfo) -> str:
    if bi.fmt == "macho":
        cmd = ["otool"] + (["-arch", bi.slice_arch] if bi.slice_arch else []) + ["-tV", bi.path]
        if shutil.which("otool"):
            r = _run(cmd)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        tools = _objdump_cmds()
        for t in tools:
            r = _run([t, "-d", "--no-show-raw-insn", "--section=__text", bi.path])
            if r.returncode == 0 and _LINE.search(r.stdout or "") and "can't disassemble" not in r.stderr:
                return r.stdout
        raise RuntimeError(f"cannot disassemble {bi.path}: need otool or llvm-objdump")
    errs = []
    for t in _objdump_cmds():
        r = _run([t, "-d", "--no-show-raw-insn", "-j", ".text", bi.path])
        bad = r.returncode != 0 or "can't disassemble" in r.stderr or "UNKNOWN" in r.stderr
        if not bad and re.search(r"^\s*[0-9a-f]+:\s", r.stdout, re.M):
            return r.stdout
        errs.append(f"{t}: {r.stderr.strip()[:200]}")
    raise RuntimeError(f"cannot disassemble {bi.path} ({bi.arch}): " + "; ".join(errs or ["no objdump found"]))


def _strip_comment(s: str, arch_: str) -> str:
    if arch_ == "arm64":
        for c in ("//", ";"):
            i = s.find(c)
            if i >= 0:
                s = s[:i]
    else:
        i = s.find("#")
        if i >= 0:
            s = s[:i]
    return s.strip()


def _parse_insn(addr: int, body: str, arch_: str) -> Insn | None:
    body = _strip_comment(body.replace("\t", " "), arch_)
    if not body:
        return None
    toks = body.split(None, 1)
    mnem = toks[0]
    ops = toks[1].strip() if len(toks) > 1 else ""
    if arch_ != "arm64":
        while mnem in _X86_PREFIX and ops:
            t2 = ops.split(None, 1)
            mnem, ops = t2[0], (t2[1].strip() if len(t2) > 1 else "")
        mnem = mnem.split(",")[0]          # jne,pt
    text = " ".join(body.split())
    target = None
    isbr = _ARM_BR.match(mnem) if arch_ == "arm64" else _X86_BR.match(mnem)
    if isbr and not ops.startswith("*"):
        m = None
        for m in _ANNOT.finditer(ops):
            pass
        if m:
            target = int(m.group(1), 16)
        else:
            m2 = _LASTHEX.search(ops)
            if m2 and (ops.rstrip().split(",")[-1].strip().startswith("0x") or arch_ != "arm64"):
                try:
                    target = int(m2.group(1), 16)
                except ValueError:
                    target = None
    return Insn(addr, mnem, ops, target, text)


@functools.lru_cache(maxsize=8)
def _disasm_cached(path: str, size: int, mtime: int) -> tuple[Insn, ...]:
    bi = _info_cached(path, size, mtime)
    out = []
    for line in _raw_disassembly(bi).splitlines():
        if _OBJ_HEADER.match(line):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        body = m.group(2)
        if body.startswith("<") or not body.strip():
            continue
        ins = _parse_insn(int(m.group(1), 16), body, bi.arch)
        if ins:
            out.append(ins)
    out.sort(key=lambda i: i.addr)
    return tuple(out)


def disassemble(path) -> list[Insn]:
    """Every instruction of the text section, in address order (cached per binary)."""
    p = os.path.realpath(str(path))
    st = os.stat(p)
    return list(_disasm_cached(p, st.st_size, st.st_mtime_ns))


def _insns(path):
    p = os.path.realpath(str(path))
    st = os.stat(p)
    return _disasm_cached(p, st.st_size, st.st_mtime_ns)


class _Locator:
    """Address -> the function containing it."""

    def __init__(self, funcs):
        # one entry per distinct start; aliases keep the first name
        firsts = {}
        for f in funcs:
            firsts.setdefault(f.start, f)
        self.funcs = sorted(firsts.values(), key=lambda f: f.start)
        self.starts = [f.start for f in self.funcs]

    def __call__(self, a: int) -> Func | None:
        i = bisect.bisect_right(self.starts, a) - 1
        if i >= 0 and a < self.funcs[i].end:
            return self.funcs[i]
        return None


# ---------------------------------------------------------------------------------------------
# loops and crossings


def loops(path, max_bytes: int = 256, same_function: bool = True) -> list[Loop]:
    """Every backward-branch loop of at most ``max_bytes`` (from the branch target to the end of
    the branch): arm64 b, b.cond, bc.cond, cbz/cbnz, tbz/tbnz; x86-64 jmp, every jcc and loop*.
    With ``same_function`` (the default), the target must lie in the branch's own function (a tail
    call to an earlier function is not a loop)."""
    bi = info(path)
    ins = _insns(path)
    loc = _Locator(bi.funcs)
    a0, sz = bi.text
    out = []
    for i, x in enumerate(ins):
        t = x.target
        if t is None or t > x.addr:
            continue
        end = ins[i + 1].addr if i + 1 < len(ins) else a0 + sz
        if bi.arch == "arm64":
            end = x.addr + 4
        if end - t > max_bytes:
            continue
        f = loc(x.addr)
        if same_function and (f is None or t < f.start):
            continue
        out.append(Loop(f.name if f else None, t, end))
    return out


def crosses(start: int, end: int, boundary: int = 4096) -> bool:
    """Whether [start, end) straddles a multiple of ``boundary``."""
    return start // boundary != (end - 1) // boundary


def crossing_loops(path, boundary: int = 4096, max_bytes: int = 256) -> list[Loop]:
    """The small loops (at most ``max_bytes``) whose body straddles a ``boundary``."""
    return [l for l in loops(path, max_bytes) if crosses(l.start, l.end, boundary)]


def crossing_shifts(spans, boundary: int = 4096, step: int = 4) -> set[int]:
    """The shifts d in range(0, boundary, step) at which any of ``spans`` (address, length) would
    straddle a ``boundary``: the crossing window of a set of loops over one period (for culprits:
    len(result) * step / boundary is the share of code layouts in which any of them crosses)."""
    bad = set()
    for s, n in spans:
        for d in range(0, boundary, step):
            if crosses(s + d, s + d + n, boundary):
                bad.add(d)
    return bad


def shifts(ref, other) -> tuple[int, float, int]:
    """(the most common shift of a function in ``other`` against ``ref``, the share of functions not
    moved, the number of functions compared), over function names present in both."""
    a, c = function_map(ref), function_map(other)
    s = collections.Counter(c[k].start - a[k].start for k in a if k in c)
    n = sum(s.values())
    return (s.most_common(1)[0][0] if s else 0), (s.get(0, 0) / n if n else 0.0), n


# ---------------------------------------------------------------------------------------------
# per-function machine-code equality

_SUFFIX = re.compile(r"(\.\d+)+$")
_HEX_ADDR = re.compile(r"(?<![#$\w])(?:0x)?([0-9a-fA-F]{4,})\b(?!\()")
_RIP = re.compile(r"[-+]?(?:0x)?[0-9a-fA-F]+(?=\(%rip\))|(?<=rip )[-+] (?:0x)?[0-9a-fA-F]+")
_IMM = r"#-?(?:0x[0-9a-fA-F]+|\d+)"
_MEMIMM = re.compile(r"\[(\w+), " + _IMM + r"\]")
_MACHO_SYM = re.compile(r"(?<![\w.$])_([A-Za-z_][\w.$]*)")
_PADDING = re.compile(r"^(nop\w*( .*)?|udf( #0x0| #0)?|int3|\.long 0x0+|\.long 0|xchg %ax, ?%ax|data16.*nop\w*)$")


def _strip_suffix(name: str) -> str:
    return _SUFFIX.sub("", name)


def _norm_function(ins_slice, f: Func, bi: BinInfo, mask_data: bool) -> list[str]:
    lo, hi = bi.image
    start, end = f.start, f.end
    arm = bi.arch == "arm64"

    def addr_repl(v: int, name: str | None = None) -> str:
        if start <= v < end:
            return f"+{v - start:#x}"
        if name:
            base, plus, off = name.partition("+")
            base = base.lstrip("_") if bi.fmt == "macho" else base
            return _strip_suffix(base) + (plus + off if plus else "")
        return "ADDR"

    macho = bi.fmt == "macho"

    def hexrepl(m):
        v = int(m.group(1), 16)
        if lo <= v < hi or start <= v < end:
            return addr_repl(v)
        return m.group(0)

    out = []
    # registers that receive an adrp page anywhere in the function: their page offsets are masked
    # wherever they are used (flow-insensitive: the page often reaches its use along a branch)
    pages = {"x" + x.ops.split(",")[0].strip()[1:] for x in ins_slice if arm and x.mnem == "adrp"}
    for x in ins_slice:
        ops = x.ops
        mn = x.mnem
        if arm:
            if mn == "adrp":
                out.append(f"adrp {ops.split(',')[0].strip()}, PAGE")
                continue
            if pages and "#" in ops:
                m = _MEMIMM.search(ops)
                if m and m.group(1) in pages:
                    ops = ops[:m.start()] + f"[{m.group(1)}, PAGEOFF]" + ops[m.end():]
                elif mn in ("add", "adds") and ops.count(",") == 2:
                    parts = [p.strip() for p in ops.split(",")]
                    if parts[1] in pages and parts[2].startswith("#"):
                        parts[2] = "PAGEOFF"
                        ops = ", ".join(parts)
            if mask_data:
                ops = re.sub(r"(\[\w+, )" + _IMM + r"\]", r"\1#IMM]", ops)
                if mn in ("add", "sub", "adds", "subs") and ops.count(",") == 2:
                    ops = re.sub(r",\s*" + _IMM + r"$", ", #IMM", ops)
        else:
            if "rip" in ops:
                ops = _RIP.sub("DISP", ops)
            if not bi.pie and "(" in ops:      # absolute data addresses as displacements (non-PIE)
                ops = re.sub(r"(?<![\w$])(0x[0-9a-fA-F]+)(?=\()",
                             lambda m: "ADDR" if lo <= int(m.group(1), 16) < hi else m.group(0), ops)
            if mask_data:
                ops = re.sub(r"[-+]?(?:0x)?[0-9a-fA-F]+(?=\()", "DISP", ops)
                ops = re.sub(r"\$-?0x[0-9a-fA-F]{4,}", "$IMM", ops)
            elif "$0x" in ops:
                ops = re.sub(r"\$(0x[0-9a-fA-F]+)",
                             lambda m: "$" + addr_repl(int(m.group(1), 16)) if lo <= int(m.group(1), 16) < hi
                             else m.group(0), ops)
        # symbolic targets first, then bare addresses inside the image
        if "<" in ops:
            ops = _ANNOT.sub(lambda m: addr_repl(int(m.group(1), 16), m.group(2)), ops)
        if macho and "_" in ops:
            ops = _MACHO_SYM.sub(lambda m: _strip_suffix(m.group(1)), ops)
        if "0x" in ops:
            ops = _HEX_ADDR.sub(hexrepl, ops)
        out.append((mn + " " + ops).strip() if ops else mn)
    while out and _PADDING.match(out[-1]):
        out.pop()
    return out


def _slices(path, names_funcs):
    ins = _insns(path)
    addrs = [x.addr for x in ins]
    res = {}
    for name, f in names_funcs:
        i = bisect.bisect_left(addrs, f.start)
        j = bisect.bisect_left(addrs, f.end)
        res[name] = ins[i:j]
    return res


def same_code(a, b, names=None, mask_data: bool = False) -> dict[str, Same]:
    """Per-function machine-code equality between binaries ``a`` and ``b``.

    Branch and call targets inside the function become offsets from its start; targets elsewhere
    become the target's symbol (LTO suffixes dropped) or are masked; arm64 adrp pages and the
    page offsets used with them, x86-64 RIP-relative displacements and absolute addresses inside
    the image are masked. With ``mask_data``, every load/store displacement and add/sub immediate
    (arm64) or memory displacement and immediate (x86-64) is masked too. Trailing padding (nop,
    udf, int3) is ignored. ``names=None`` compares every function present in both; a name missing
    from one side is matched by its suffix-stripped name when that is unique in both, else
    reported with ``same=False``. One disassembly per binary.

    ``mask_data`` (arm64: memory-operand and three-operand add/sub immediates; x86-64: memory
    displacements and immediates of 0x1000 or more) also hides changes to struct offsets and such
    constants, so it can call a changed function the same; without it, code that reaches data
    through a register loaded far from its use can be called different when only data moved. The
    adrp masking is flow-insensitive: every register that receives an adrp page in the function
    has its page offsets masked wherever it is the base."""
    ia, ib = info(a), info(b)
    fa, fb = function_map(a), function_map(b)
    if names is None:
        names = [n for n in fa if n in fb]
        # suffix-renamed functions present in both, matched by their unique base name
        names += [n for n in fa if n not in fb]
    by_base_b = collections.defaultdict(list)
    for n in fb:
        by_base_b[_strip_suffix(n)].append(n)
    by_base_a = collections.defaultdict(list)
    for n in fa:
        by_base_a[_strip_suffix(n)].append(n)
    pairs = []
    out: dict[str, Same] = {}
    for n in names:
        na = n if n in fa else (by_base_a[_strip_suffix(n)][0] if len(by_base_a[_strip_suffix(n)]) == 1 else None)
        nb = n if n in fb else (by_base_b[_strip_suffix(n)][0] if len(by_base_b[_strip_suffix(n)]) == 1 else None)
        if na is not None and nb is None and na in fb:
            nb = na
        if na is None or nb is None:
            out[n] = Same(n, False, -1, fa[na].start if na else None, fa[na].size if na else None,
                          fb[nb].start if nb else None, fb[nb].size if nb else None, None, nb)
            continue
        pairs.append((n, na, nb))
    sa = _slices(a, [(n, fa[na]) for n, na, _ in pairs])
    sb = _slices(b, [(n, fb[nb]) for n, _, nb in pairs])
    for n, na, nb in pairs:
        da = _norm_function(sa[n], fa[na], ia, mask_data)
        db = _norm_function(sb[n], fb[nb], ib, mask_data)
        nd = sum(1 for x, y in zip(da, db) if x != y) + abs(len(da) - len(db))
        first = None
        if nd:
            k = next((i for i, (x, y) in enumerate(zip(da, db)) if x != y), min(len(da), len(db)))
            first = (sa[n][k].addr - fa[na].start) if k < len(sa[n]) else fa[na].size
        out[n] = Same(n, nd == 0, nd, fa[na].start, fa[na].size, fb[nb].start, fb[nb].size, first,
                      nb if nb != n else None)
    return out


def normalised(path, name: str, mask_data: bool = False) -> list[str]:
    """The normalised instructions same_code compares, for one function (for inspection)."""
    f = function_map(path)[name]
    return _norm_function(_slices(path, [(name, f)])[name], f, info(path), mask_data)


# ---------------------------------------------------------------------------------------------
# command line


def _read_names(spec: str | None, path=None) -> list[str] | None:
    """Names from an order file (``_name``, ``name`` or GNU ``*(.text.name)`` lines) or a JSON
    selection ({'sel': [...]} or a list)."""
    if not spec:
        return None
    t = Path(spec).read_text()
    if spec.endswith(".json"):
        d = json.loads(t)
        return list(d["sel"] if isinstance(d, dict) else d)
    return read_order(spec, path)


def read_order(order_file, path=None) -> list[str]:
    """Function names from an order file of any style: ld64 (``_name``; the underscore stripped),
    lld (``name``), or a GNU ld section-ordering file (``*(.text.name ...)``). ``path``: the
    binary, or a style ('macho'/'ld64', 'elf'/'lld', 'gnu'), that says whether a leading underscore
    is the Mach-O prefix; without it, every name starting with '_' means ld64 style."""
    t = Path(order_file).read_text()
    if "*(" in t:
        out = []
        for m in re.finditer(r"\*\(\s*\.text\.(\S+?)[\s)]", t):
            if m.group(1) not in out:
                out.append(m.group(1))
        return out
    lines = [l.split("#")[0].strip() for l in t.splitlines()]
    lines = [l.split(":")[-1] if ".o:" in l else l for l in lines if l]
    macho = path is not None and _style(path) == "ld64"
    if path is None:
        macho = bool(lines) and all(l.startswith("_") for l in lines)
    return [l[1:] if macho and l.startswith("_") else l for l in lines]


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="placemat binary", description="Binary analysis (Mach-O and ELF).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("funcs", help="functions with address, size and address mod 64")
    p.add_argument("binary")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("loops", help="small backward-branch loops; --crossing for those straddling a boundary")
    p.add_argument("binary")
    p.add_argument("--max-bytes", type=int, default=256)
    p.add_argument("--boundary", type=int, default=4096)
    p.add_argument("--crossing", action="store_true")
    p.add_argument("--only", help="order file or selection JSON: keep loops in these functions")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("shifts", help="most common function shift and share unmoved between two builds")
    p.add_argument("ref")
    p.add_argument("other")
    p = sub.add_parser("samefn", help="per-function machine-code equality between two builds")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("names", nargs="*")
    p.add_argument("--names-from", help="order file or selection JSON")
    p.add_argument("--mask-data", action="store_true")
    p.add_argument("--diff-only", action="store_true")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "funcs":
        fs = functions(a.binary)
        if a.json:
            print(json.dumps([dataclasses.asdict(f) for f in fs]))
        else:
            t0, tsz = text_section(a.binary)
            bi = info(a.binary)
            print(f"# {bi.fmt} {bi.arch} text {t0:#x}+{tsz:#x}, {len(fs)} functions")
            for f in fs:
                print(f"{f.start:#x} {f.size:7d} {f.start % 64:2d} {f.name}")
    elif a.cmd == "loops":
        only = _read_names(a.only, a.binary)
        ls = loops(a.binary, a.max_bytes)
        n_all = len(ls)
        if a.crossing:
            ls = [l for l in ls if crosses(l.start, l.end, a.boundary)]
        if only is not None:
            s = set(only)
            ls = [l for l in ls if l.func in s]
        if a.json:
            print(json.dumps([dataclasses.asdict(l) for l in ls]))
        else:
            for l in ls:
                print(f"{l.func} {l.start:#x} {l.end:#x} {l.size}")
            print(f"# {len(ls)} listed; {n_all} backward-branch loops of at most {a.max_bytes} bytes in all",
                  file=sys.stderr)
    elif a.cmd == "shifts":
        s, share, n = shifts(a.ref, a.other)
        print(f"most common shift {s:+d} bytes; {share:.1%} of {n} functions unmoved")
    elif a.cmd == "samefn":
        names = a.names or _read_names(a.names_from, a.a) or None
        res = same_code(a.a, a.b, names, a.mask_data)
        if a.json:
            print(json.dumps({k: dataclasses.asdict(v) for k, v in res.items()}))
            return 0
        nd = 0
        for n, r in res.items():
            if not r.same:
                nd += 1
            if a.diff_only and r.same:
                continue
            if r.start_a is None or r.start_b is None:
                print(f"{n:24} missing in {'A' if r.start_a is None else 'B'}")
                continue
            st = "SAME" if r.same else f"DIFF({r.ndiff})"
            print(f"{n:24} {st:9} A {r.start_a:#x} %64={r.start_a % 64:2} n={r.size_a:6}  "
                  f"B {r.start_b:#x} %64={r.start_b % 64:2} n={r.size_b:6}"
                  + (f"  first diff +{r.first_diff:#x}" if r.first_diff is not None else ""))
        print(f"# {len(res) - nd} same, {nd} different or missing, of {len(res)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
