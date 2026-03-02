#!/usr/bin/env python3
"""
Disassemble a region of a MIPS binary (ELF or raw blob).

Decodes MIPS32 Little-Endian by default. Uses instruction-at-a-time decoding
so it never stops early on data embedded in code sections.

Usage:
  python3 mips_disasm.py ProcHMI.elf --offset 0x4f0700 --size 0x100
  python3 mips_disasm.py dragon.bin --offset 0x4ab50 --size 0x200 --mips64
  python3 mips_disasm.py ProcHMI.elf --offset 0x9a87a0 --size 0x80 --strings
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, CS_MODE_MIPS64


def find_string_at(data: bytes, offset: int, min_len: int = 4) -> str | None:
    """Try to read a printable ASCII string starting at offset."""
    if offset < 0 or offset >= len(data):
        return None
    end = offset
    while end < len(data) and end - offset < 256:
        b = data[end]
        if b == 0:
            break
        if b < 0x20 or b > 0x7e:
            return None
        end += 1
    s = data[offset:end].decode("ascii", errors="replace")
    return s if len(s) >= min_len else None


def disassemble(data: bytes, offset: int, size: int, base: int,
                mips64: bool, show_strings: bool, show_hex: bool) -> None:
    mode = (CS_MODE_MIPS64 if mips64 else CS_MODE_MIPS32) | CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_MIPS, mode)

    end = min(offset + size, len(data))
    count = 0

    for pos in range(offset, end - 3, 4):
        addr = base + pos
        chunk = data[pos:pos + 4]
        word = struct.unpack_from("<I", chunk, 0)[0]

        insns = list(md.disasm(chunk, addr))
        if insns:
            ins = insns[0]
            line = f"0x{addr:08x}:  {ins.mnemonic:8s} {ins.op_str}"
            if show_hex:
                line = f"0x{addr:08x}:  {word:08x}  {ins.mnemonic:8s} {ins.op_str}"
        else:
            line = f"0x{addr:08x}:  .word    0x{word:08x}"
            if show_hex:
                line = f"0x{addr:08x}:  {word:08x}  .word    0x{word:08x}"

        if show_strings and insns:
            ins = insns[0]
            if ins.mnemonic == "lui":
                pass  # will be resolved with next addiu
            elif ins.mnemonic in ("addiu", "ori"):
                parts = ins.op_str.split(",")
                if len(parts) >= 3:
                    try:
                        imm = int(parts[2].strip(), 0)
                        s = find_string_at(data, imm & 0xFFFFFFFF)
                        if s:
                            line += f'  ; -> "{s}"'
                    except ValueError:
                        pass
            elif ins.mnemonic == "jal":
                parts = ins.op_str.strip()
                try:
                    target = int(parts, 0)
                    s = find_string_at(data, target & 0xFFFFFFFF)
                    if s:
                        line += f'  ; -> "{s}"'
                except ValueError:
                    pass

        print(line)
        count += 1

    print(f"\n  {count} instructions, offset 0x{offset:x}..0x{end:x}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Disassemble MIPS binary regions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", type=Path, help="ELF or raw binary file")
    ap.add_argument("--offset", type=lambda x: int(x, 0), default=0,
                    help="File offset to start (hex or decimal)")
    ap.add_argument("--size", type=lambda x: int(x, 0), default=0x100,
                    help="Bytes to disassemble (default: 0x100)")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0,
                    help="Base address for display (default: 0, meaning offset=address)")
    ap.add_argument("--mips64", action="store_true",
                    help="Use MIPS64 mode (for dragon.bin)")
    ap.add_argument("--strings", action="store_true",
                    help="Try to resolve string references")
    ap.add_argument("--hex", action="store_true",
                    help="Show raw hex alongside disassembly")
    args = ap.parse_args()

    data = args.file.read_bytes()
    if args.offset >= len(data):
        raise SystemExit(f"Offset 0x{args.offset:x} beyond file size 0x{len(data):x}")

    disassemble(data, args.offset, args.size, args.base, args.mips64, args.strings, args.hex)


if __name__ == "__main__":
    main()
