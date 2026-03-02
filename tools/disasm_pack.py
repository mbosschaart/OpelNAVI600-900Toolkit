#!/usr/bin/env python3
"""
Generate a disassembly pack for all firmware modules in an Opel Navi 600/900
update tree.

For each XOZL .out file:
  1. Decompress via xozl_tool (LZO1X) to get the raw ELF
  2. Parse ELF headers (sections, segments, entry point)
  3. Disassemble the .text section (or executable PT_LOAD segment)

Also disassembles dragon.bin (the bootloader, raw MIPS64 blob).

Output structure:
  <out_dir>/
    index.json              Master index with metadata for all modules
    modules/<name>/
      elf_meta.json         ELF header info, sections, segments
      text.asm              .text section disassembly
    dragon/
      dragon.asm            Full bootloader disassembly (MIPS64)

Usage:
  python3 disasm_pack.py                          # defaults
  python3 disasm_pack.py --adit-dir <path> --out-dir <path>
  python3 disasm_pack.py --full                   # disassemble entire ELF, not just .text
  python3 disasm_pack.py --module ProcHMI.out     # single module only
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, CS_MODE_MIPS64

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from xozl_tool import parse_xozl, MAGIC, HEADER_SIZE

REPO_DIR = SCRIPT_DIR.parent
DEFAULTS = {
    "adit_dir": REPO_DIR / "firmware/dnl/bin/system/adit/g__eeu10",
    "out_dir": REPO_DIR / "208_source",
}


def read_u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]

def read_u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def parse_elf32(elf: bytes) -> dict:
    if elf[:4] != b"\x7fELF":
        return {"error": "not an ELF file"}
    if elf[4] != 1:
        return {"error": f"not ELF32 (class={elf[4]})"}
    if elf[5] != 1:
        return {"error": f"not little-endian (data={elf[5]})"}

    meta = {
        "class": 32,
        "machine": read_u16(elf, 0x12),
        "entry": read_u32(elf, 0x18),
        "phoff": read_u32(elf, 0x1C),
        "shoff": read_u32(elf, 0x20),
        "flags": read_u32(elf, 0x24),
        "shnum": read_u16(elf, 0x30),
        "shstrndx": read_u16(elf, 0x32),
        "sections": [],
        "segments": [],
        "text_offset": None,
        "text_vaddr": None,
        "text_size": None,
    }

    e_phoff = meta["phoff"]
    e_phentsize = read_u16(elf, 0x2A)
    e_phnum = read_u16(elf, 0x2C)
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if off + e_phentsize > len(elf):
            break
        seg = {
            "type": read_u32(elf, off),
            "offset": read_u32(elf, off + 0x04),
            "vaddr": read_u32(elf, off + 0x08),
            "filesz": read_u32(elf, off + 0x10),
            "memsz": read_u32(elf, off + 0x14),
            "flags": read_u32(elf, off + 0x18),
        }
        meta["segments"].append(seg)

    e_shoff = meta["shoff"]
    e_shentsize = read_u16(elf, 0x2E)
    e_shnum = meta["shnum"]
    e_shstrndx = meta["shstrndx"]

    shstr = b""
    if e_shoff and e_shnum and e_shstrndx < e_shnum:
        str_hdr_off = e_shoff + e_shstrndx * e_shentsize
        if str_hdr_off + e_shentsize <= len(elf):
            str_off = read_u32(elf, str_hdr_off + 0x10)
            str_sz = read_u32(elf, str_hdr_off + 0x14)
            if str_off + str_sz <= len(elf):
                shstr = elf[str_off:str_off + str_sz]

    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + e_shentsize > len(elf):
            break
        name_off = read_u32(elf, off)
        name = ""
        if name_off < len(shstr):
            end = shstr.find(b"\x00", name_off)
            name = shstr[name_off:end if end != -1 else len(shstr)].decode("ascii", errors="replace")

        sec = {
            "name": name,
            "type": read_u32(elf, off + 0x04),
            "addr": read_u32(elf, off + 0x0C),
            "offset": read_u32(elf, off + 0x10),
            "size": read_u32(elf, off + 0x14),
        }
        meta["sections"].append(sec)

        if name == ".text":
            meta["text_offset"] = sec["offset"]
            meta["text_vaddr"] = sec["addr"]
            meta["text_size"] = sec["size"]

    return meta


def disasm_region(data: bytes, offset: int, size: int, base_addr: int, mips64: bool) -> str:
    mode = (CS_MODE_MIPS64 if mips64 else CS_MODE_MIPS32) | CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_MIPS, mode)

    end = min(offset + size, len(data))
    lines: list[str] = []

    for pos in range(offset, end - 3, 4):
        addr = base_addr + (pos - offset)
        chunk = data[pos:pos + 4]
        insns = list(md.disasm(chunk, addr))
        if insns:
            ins = insns[0]
            lines.append(f"0x{addr:08x}:  {ins.mnemonic:8s} {ins.op_str}")
        else:
            w = struct.unpack_from("<I", chunk, 0)[0]
            lines.append(f"0x{addr:08x}:  .word    0x{w:08x}")

    return "\n".join(lines) + "\n"


def decompress_xozl(xozl_path: Path) -> bytes:
    import lzo
    raw = xozl_path.read_bytes()
    if raw[:4] != MAGIC:
        raise ValueError(f"{xozl_path}: not XOZL")
    comp_size = struct.unpack_from("<I", raw, 0x1C)[0]
    decomp_size = struct.unpack_from("<I", raw, 0x18)[0]
    payload = raw[HEADER_SIZE:HEADER_SIZE + comp_size]
    return lzo.decompress(payload, False, decomp_size + 4096)


def process_module(xozl_path: Path, mod_dir: Path, full: bool) -> dict:
    mod_dir.mkdir(parents=True, exist_ok=True)
    name = xozl_path.name

    print(f"  {name}: ", end="", flush=True)
    elf = decompress_xozl(xozl_path)
    print(f"decompressed {len(elf):,} bytes, ", end="", flush=True)

    meta = parse_elf32(elf)
    (mod_dir / "elf_meta.json").write_text(json.dumps(meta, indent=2))

    text_off = meta.get("text_offset")
    text_vaddr = meta.get("text_vaddr")
    text_size = meta.get("text_size")

    if full:
        exec_seg = next((s for s in meta.get("segments", [])
                         if s["type"] == 1 and s["flags"] & 1), None)
        if exec_seg:
            region_off = exec_seg["offset"]
            region_size = exec_seg["filesz"]
            region_vaddr = exec_seg["vaddr"]
        elif text_off is not None:
            region_off = text_off
            region_size = text_size
            region_vaddr = text_vaddr
        else:
            region_off = 0
            region_size = min(len(elf), 0x20000)
            region_vaddr = 0
    else:
        if text_off is not None:
            region_off = text_off
            region_size = text_size
            region_vaddr = text_vaddr
        else:
            region_off = 0
            region_size = min(len(elf), 0x20000)
            region_vaddr = 0

    asm = disasm_region(elf, region_off, region_size, region_vaddr, mips64=False)
    asm_path = mod_dir / "text.asm"
    asm_path.write_text(asm)
    instr_count = asm.count("\n")
    print(f"{instr_count:,} instructions")

    return {
        "xozl_path": str(xozl_path),
        "elf_size": len(elf),
        "elf_meta": {k: v for k, v in meta.items() if k not in ("sections", "segments")},
        "disasm_region": {
            "offset": region_off,
            "vaddr": region_vaddr,
            "size": region_size,
            "instructions": instr_count,
        },
    }


def process_dragon(dragon_path: Path, dragon_dir: Path) -> dict:
    dragon_dir.mkdir(parents=True, exist_ok=True)
    data = dragon_path.read_bytes()
    print(f"  dragon.bin: {len(data):,} bytes, ", end="", flush=True)

    asm = disasm_region(data, 0, len(data), 0, mips64=True)
    asm_path = dragon_dir / "dragon.asm"
    asm_path.write_text(asm)
    instr_count = asm.count("\n")
    print(f"{instr_count:,} instructions")

    return {
        "path": str(dragon_path),
        "size": len(data),
        "instructions": instr_count,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adit-dir", type=Path, default=DEFAULTS["adit_dir"],
                    help="Firmware variant directory containing .out files and dragon.bin")
    ap.add_argument("--out-dir", type=Path, default=DEFAULTS["out_dir"],
                    help="Output directory for disassembly pack")
    ap.add_argument("--full", action="store_true",
                    help="Disassemble full executable segment (not just .text)")
    ap.add_argument("--module", type=str, default=None,
                    help="Process single module only (e.g. ProcHMI.out)")
    ap.add_argument("--skip-dragon", action="store_true",
                    help="Skip dragon.bin disassembly")
    args = ap.parse_args()

    try:
        import lzo  # noqa: F401
    except ImportError:
        raise SystemExit("python-lzo is required.  Install: pip install python-lzo")

    out_dir: Path = args.out_dir
    adit_dir: Path = args.adit_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    index: dict = {"modules": {}, "dragon": None}

    if args.module:
        targets = [adit_dir / args.module]
        if not targets[0].exists():
            raise SystemExit(f"Module not found: {targets[0]}")
    else:
        targets = sorted(adit_dir.glob("*.out"))

    print(f"Disassembly pack: {len(targets)} module(s), output → {out_dir}")
    print()

    for p in targets:
        mod_dir = out_dir / "modules" / p.stem
        try:
            info = process_module(p, mod_dir, args.full)
            index["modules"][p.name] = info
        except Exception as e:
            print(f"  {p.name}: ERROR — {e}")
            index["modules"][p.name] = {"error": str(e)}

    dragon_path = adit_dir / "dragon.bin"
    if not args.skip_dragon and dragon_path.exists():
        print()
        info = process_dragon(dragon_path, out_dir / "dragon")
        index["dragon"] = info

    (out_dir / "index.json").write_text(json.dumps(index, indent=2))
    print(f"\nDone. Index: {out_dir / 'index.json'}")


if __name__ == "__main__":
    main()
