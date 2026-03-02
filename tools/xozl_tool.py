#!/usr/bin/env python3
"""
Inspect, extract, and pack XOZL firmware modules for Opel Navi 600/900.

XOZL format (36-byte header + LZO1X compressed payload + version trailer):
  0x00  magic         "XOZL" (LE 0x4c5a4f58)
  0x04  reserved      u32   (always 0)
  0x08  ver_major     u32   (1)
  0x0C  ver_minor     u32   (2)
  0x10  reserved      u32   (always 0)
  0x14  header_len    u32   (always 0x24 = 36)
  0x18  decomp_size   u32   decompressed ELF size in bytes
  0x1C  comp_size     u32   LZO compressed payload size in bytes
  0x20  crc32         u32   CRC32 of the decompressed content (init=0)
  0x24  [payload]           LZO1X compressed data
  0x24+comp_size [trailer]  version string (e.g. "GM10.8V208") + metadata

Commands:
  info    <file.out>                       Print XOZL metadata as JSON
  extract <file.out> <output.elf>          Decompress XOZL → ELF
  pack    <input.elf> <output.out> [--ref] Compress ELF → XOZL
"""

from __future__ import annotations

import argparse
import binascii
import json
import struct
from pathlib import Path
from typing import Dict, Any

MAGIC = b"XOZL"
HEADER_SIZE = 0x24


def parse_xozl(path: Path) -> Dict[str, Any]:
    b = path.read_bytes()
    if len(b) < HEADER_SIZE:
        raise ValueError(f"{path}: too small for XOZL header ({len(b)} < {HEADER_SIZE})")
    if b[:4] != MAGIC:
        raise ValueError(f"{path}: magic mismatch (got {b[:4]!r}, expected {MAGIC!r})")

    comp_size = struct.unpack_from("<I", b, 0x1C)[0]
    trailer_off = HEADER_SIZE + comp_size
    trailer = b[trailer_off:] if trailer_off < len(b) else b""

    return {
        "path": str(path),
        "file_size": len(b),
        "reserved0": struct.unpack_from("<I", b, 0x04)[0],
        "version_major": struct.unpack_from("<I", b, 0x08)[0],
        "version_minor": struct.unpack_from("<I", b, 0x0C)[0],
        "reserved1": struct.unpack_from("<I", b, 0x10)[0],
        "header_len": struct.unpack_from("<I", b, 0x14)[0],
        "decompressed_size": struct.unpack_from("<I", b, 0x18)[0],
        "compressed_size": comp_size,
        "crc32": struct.unpack_from("<I", b, 0x20)[0],
        "payload_off": HEADER_SIZE,
        "payload_size": max(0, len(b) - HEADER_SIZE),
        "trailer_size": len(trailer),
        "trailer": trailer.rstrip(b"\x00\x01").decode("ascii", errors="replace"),
    }


def cmd_info(path: Path) -> None:
    print(json.dumps(parse_xozl(path), indent=2))


def cmd_extract(path: Path, out_path: Path) -> None:
    try:
        import lzo
    except ImportError:
        raise SystemExit("python-lzo is required.  Install: pip install python-lzo")

    info = parse_xozl(path)
    raw = path.read_bytes()
    payload = raw[HEADER_SIZE : HEADER_SIZE + info["compressed_size"]]

    elf = lzo.decompress(payload, False, info["decompressed_size"] + 4096)
    if len(elf) != info["decompressed_size"]:
        raise RuntimeError(
            f"Size mismatch: got {len(elf)}, expected {info['decompressed_size']}"
        )

    crc = binascii.crc32(elf, 0) & 0xFFFFFFFF
    if crc != info["crc32"]:
        raise RuntimeError(
            f"CRC32 mismatch: computed 0x{crc:08x}, header 0x{info['crc32']:08x}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(elf)
    print(f"Extracted {out_path} ({len(elf):,} bytes, CRC32 0x{crc:08x})")


def cmd_pack(elf_path: Path, out_path: Path, ref_path: Path | None) -> None:
    try:
        import lzo
    except ImportError:
        raise SystemExit("python-lzo is required.  Install: pip install python-lzo")

    elf_data = elf_path.read_bytes()
    crc = binascii.crc32(elf_data, 0) & 0xFFFFFFFF

    compressed_full = lzo.compress(elf_data)
    lzo_stream = compressed_full[5:]  # strip python-lzo's 5-byte header

    verify = lzo.decompress(lzo_stream, False, len(elf_data) + 4096)
    if verify != elf_data:
        raise RuntimeError("LZO round-trip verification failed — data corruption!")

    if ref_path:
        ref = ref_path.read_bytes()
        if ref[:4] != MAGIC:
            raise ValueError(f"{ref_path}: not an XOZL file")
        header = bytearray(ref[:HEADER_SIZE])
        ref_comp_size = struct.unpack_from("<I", ref, 0x1C)[0]
        trailer = ref[HEADER_SIZE + ref_comp_size:]
    else:
        header = bytearray(HEADER_SIZE)
        header[0:4] = MAGIC
        struct.pack_into("<I", header, 0x08, 1)
        struct.pack_into("<I", header, 0x0C, 2)
        struct.pack_into("<I", header, 0x14, 0x24)
        trailer = b""

    struct.pack_into("<I", header, 0x18, len(elf_data))
    struct.pack_into("<I", header, 0x1C, len(lzo_stream))
    struct.pack_into("<I", header, 0x20, crc)

    result = bytes(header) + lzo_stream + trailer
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(result)

    print(f"Packed {elf_path.name} -> {out_path}")
    print(f"  ELF size:        {len(elf_data):>12,}")
    print(f"  Compressed:      {len(lzo_stream):>12,}")
    print(f"  XOZL .out size:  {len(result):>12,}")
    print(f"  CRC32:           0x{crc:08x}")
    if trailer:
        print(f"  Trailer:         {trailer.rstrip(b'\x00\x01').decode('ascii', errors='replace')!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="XOZL firmware module tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="Print XOZL header as JSON")
    p_info.add_argument("file", type=Path)

    p_extract = sub.add_parser("extract", help="Decompress XOZL → ELF")
    p_extract.add_argument("file", type=Path, help="Input .out file")
    p_extract.add_argument("out_file", type=Path, help="Output decompressed ELF")

    p_pack = sub.add_parser("pack", help="Compress ELF → XOZL .out")
    p_pack.add_argument("elf_file", type=Path, help="Decompressed ELF to compress")
    p_pack.add_argument("out_file", type=Path, help="Output .out file")
    p_pack.add_argument("--ref", type=Path, default=None,
                        help="Reference .out to copy header constants and version trailer")

    args = ap.parse_args()
    if args.cmd == "info":
        cmd_info(args.file)
    elif args.cmd == "extract":
        cmd_extract(args.file, args.out_file)
    elif args.cmd == "pack":
        cmd_pack(args.elf_file, args.out_file, args.ref)


if __name__ == "__main__":
    main()
