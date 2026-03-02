#!/usr/bin/env python3
"""
Validate XOZL .out files against the bootloader's expectations.

Tests performed:
  1. XOZL header structure (magic, sizes, version, header_len)
  2. CRC32 integrity (header CRC vs actual decompressed content)
  3. LZO1X decompression of the compressed payload
  4. Round-trip verification (decompress → verify → compare)
  5. Cross-validation against a known-good decompressed ELF
  6. Header comparison against original .out (when --ref given)

Usage:
  python3 validate_xozl.py <file.out>
  python3 validate_xozl.py <file.out> --elf <decompressed.elf> --ref <original.out>
"""

from __future__ import annotations

import argparse
import binascii
import struct
import sys
from pathlib import Path

MAGIC = b"XOZL"
HEADER_SIZE = 0x24

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"
INFO = "\033[94mINFO\033[0m"


def read_xozl(path: Path) -> dict:
    b = path.read_bytes()
    comp_size = struct.unpack_from("<I", b, 0x1C)[0]
    return {
        "raw": b,
        "magic": b[:4],
        "reserved0": struct.unpack_from("<I", b, 0x04)[0],
        "ver_major": struct.unpack_from("<I", b, 0x08)[0],
        "ver_minor": struct.unpack_from("<I", b, 0x0C)[0],
        "reserved1": struct.unpack_from("<I", b, 0x10)[0],
        "header_len": struct.unpack_from("<I", b, 0x14)[0],
        "decomp_size": struct.unpack_from("<I", b, 0x18)[0],
        "comp_size": comp_size,
        "crc32": struct.unpack_from("<I", b, 0x20)[0],
        "payload": b[HEADER_SIZE : HEADER_SIZE + comp_size],
        "trailer": b[HEADER_SIZE + comp_size :],
    }


def test_header(label: str, xozl: dict) -> bool:
    ok = True
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    if xozl["magic"] == MAGIC:
        print(f"  [{PASS}] Magic: XOZL")
    else:
        print(f"  [{FAIL}] Magic: {xozl['magic']!r}")
        ok = False

    print(f"  [{INFO}] Version: {xozl['ver_major']}.{xozl['ver_minor']}")

    if xozl["header_len"] == 0x24:
        print(f"  [{PASS}] Header length: 0x24 (36)")
    else:
        print(f"  [{WARN}] Header length: 0x{xozl['header_len']:02x} (expected 0x24)")

    print(f"  [{INFO}] Decompressed size: {xozl['decomp_size']:,} bytes")
    print(f"  [{INFO}] Compressed size:   {xozl['comp_size']:,} bytes")
    print(f"  [{INFO}] CRC32 (header):    0x{xozl['crc32']:08x}")

    available = len(xozl["raw"]) - HEADER_SIZE
    if xozl["comp_size"] <= available:
        print(f"  [{PASS}] Compressed size fits within file")
    else:
        print(f"  [{FAIL}] Compressed size {xozl['comp_size']} > available {available}")
        ok = False

    if xozl["trailer"]:
        tr = xozl["trailer"].rstrip(b"\x00\x01").decode("ascii", errors="replace")
        print(f"  [{INFO}] Trailer: {tr!r}")
    return ok


def test_lzo_decompress(label: str, xozl: dict) -> bytes | None:
    try:
        import lzo
    except ImportError:
        print(f"  [{FAIL}] python-lzo not installed (pip install python-lzo)")
        return None

    print(f"\n  LZO Decompression ({label}):")
    try:
        result = lzo.decompress(xozl["payload"], False, xozl["decomp_size"] + 4096)
        print(f"  [{PASS}] Decompression succeeded")
        print(f"  [{INFO}] Output: {len(result):,} bytes")

        if len(result) == xozl["decomp_size"]:
            print(f"  [{PASS}] Size matches header")
        else:
            print(f"  [{FAIL}] Size: got {len(result):,}, expected {xozl['decomp_size']:,}")

        crc = binascii.crc32(result, 0) & 0xFFFFFFFF
        if crc == xozl["crc32"]:
            print(f"  [{PASS}] CRC32 matches: 0x{crc:08x}")
        else:
            print(f"  [{FAIL}] CRC32: got 0x{crc:08x}, expected 0x{xozl['crc32']:08x}")
            return result

        if result[:4] == b"\x7fELF":
            print(f"  [{PASS}] Valid ELF output")
        else:
            print(f"  [{FAIL}] Not a valid ELF")

        return result
    except Exception as e:
        print(f"  [{FAIL}] Decompression failed: {e}")
        return None


def test_cross_validate(decompressed: bytes | None, elf_path: Path | None) -> bool:
    if not elf_path or decompressed is None:
        return True

    elf_data = elf_path.read_bytes()
    print(f"\n  Cross-validation vs {elf_path.name}:")

    if decompressed == elf_data:
        print(f"  [{PASS}] Byte-for-byte match")
        return True

    if len(decompressed) != len(elf_data):
        print(f"  [{FAIL}] Size: {len(decompressed):,} vs {len(elf_data):,}")
    else:
        diffs = sum(1 for a, b in zip(decompressed, elf_data) if a != b)
        first = next(i for i, (a, b) in enumerate(zip(decompressed, elf_data)) if a != b)
        print(f"  [{FAIL}] {diffs:,} byte diffs, first at 0x{first:08x}")
    return False


def test_compare_headers(patched: dict, original: dict) -> bool:
    ok = True
    print(f"\n  Header comparison (patched vs original):")

    for field in ["ver_major", "ver_minor", "reserved0", "reserved1", "header_len"]:
        if patched[field] == original[field]:
            print(f"  [{PASS}] {field}: {patched[field]}")
        else:
            print(f"  [{FAIL}] {field}: patched={patched[field]}, original={original[field]}")
            ok = False

    ptr = patched["trailer"].rstrip(b"\x00\x01")
    otr = original["trailer"].rstrip(b"\x00\x01")
    if ptr == otr:
        print(f"  [{PASS}] Trailer matches")
    else:
        print(f"  [{WARN}] Trailer differs")

    rp = patched["comp_size"] / patched["decomp_size"] if patched["decomp_size"] else 0
    ro = original["comp_size"] / original["decomp_size"] if original["decomp_size"] else 0
    print(f"  [{INFO}] Compression ratio: patched={rp:.3f}, original={ro:.3f}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xozl_file", type=Path, help="XOZL .out file to validate")
    ap.add_argument("--elf", type=Path, help="Decompressed ELF for cross-validation")
    ap.add_argument("--ref", type=Path, help="Original .out for header comparison")
    args = ap.parse_args()

    all_ok = True

    xozl = read_xozl(args.xozl_file)
    all_ok &= test_header(f"Target: {args.xozl_file.name}", xozl)
    decomp = test_lzo_decompress("target", xozl)
    if decomp is None:
        all_ok = False
    all_ok &= test_cross_validate(decomp, args.elf)

    if args.ref:
        ref = read_xozl(args.ref)
        test_header(f"Reference: {args.ref.name}", ref)
        ref_decomp = test_lzo_decompress("original", ref)
        all_ok &= test_compare_headers(xozl, ref)

        if ref_decomp is not None:
            print(f"\n  [{PASS}] Original XOZL uses standard LZO1X — bootloader compatible")
        else:
            print(f"\n  [{WARN}] Original did not decompress with python-lzo")

    print(f"\n{'='*60}")
    if all_ok:
        print(f"  RESULT: {PASS}")
    else:
        print(f"  RESULT: {FAIL}")
    print(f"{'='*60}\n")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
