#!/usr/bin/env python3
"""
Minimal ULI extractor/repacker for Opel/Blaupunkt GM firmware containers.

Known layout (inferred):
- 0x00: "ULI "
- 0x08: file count (u32 LE)
- 0x0C: table entries, 24 bytes each (6 x u32 LE)
  [type, unknown, chunk_offset, chunk_size, reserved0, reserved1]
- each chunk:
  - first 0x100 bytes: path header (ASCII C-string + padding)
  - bytes after 0x100: payload data
- some files have trailing bytes after the last referenced chunk; preserved by repack.

This tool is intended for research/modding workflows and makes no guarantees that
repacked files will be accepted by all target bootloaders.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
from pathlib import Path
from typing import List, Dict, Any


ENTRY_SIZE = 24
CHUNK_HEADER_SIZE = 0x100
MAGIC = b"ULI "


def read_u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def parse_uli(path: Path) -> Dict[str, Any]:
    data = path.read_bytes()
    if data[:4] != MAGIC:
        raise ValueError(f"{path} is not a ULI file (magic mismatch)")

    count = read_u32(data, 0x08)
    table_off = 0x0C
    entries: List[Dict[str, Any]] = []

    for i in range(count):
        off = table_off + i * ENTRY_SIZE
        if off + ENTRY_SIZE > len(data):
            raise ValueError(f"Truncated table at entry {i}")
        t, unk, chunk_off, chunk_size, r0, r1 = struct.unpack_from("<6I", data, off)
        if chunk_off + chunk_size > len(data):
            raise ValueError(f"Chunk out of bounds at entry {i}")
        chunk = data[chunk_off : chunk_off + chunk_size]
        hdr = chunk[:CHUNK_HEADER_SIZE] if len(chunk) >= CHUNK_HEADER_SIZE else chunk
        nul = hdr.find(b"\x00")
        if nul == -1:
            nul = len(hdr)
        raw_name = hdr[:nul]
        name = raw_name.decode("latin1", errors="replace")
        payload = chunk[CHUNK_HEADER_SIZE:] if len(chunk) >= CHUNK_HEADER_SIZE else b""
        entries.append(
            {
                "index": i,
                "type": t,
                "unknown": unk,
                "offset": chunk_off,
                "size": chunk_size,
                "reserved0": r0,
                "reserved1": r1,
                "name": name,
                "chunk_header_b64": base64.b64encode(hdr).decode("ascii"),
                "payload_size": len(payload),
            }
        )

    last_end = max((e["offset"] + e["size"] for e in entries), default=table_off + count * ENTRY_SIZE)
    trailer = data[last_end:]
    return {
        "source_size": len(data),
        "count": count,
        "entries": entries,
        "trailer_b64": base64.b64encode(trailer).decode("ascii"),
    }


def path_from_name(name: str) -> Path:
    if name.startswith("mkdir "):
        name = name[len("mkdir ") :]
    if name.startswith("/dev/nand0/"):
        name = name[len("/dev/nand0/") :]
    elif name.startswith("/dev/nor0/"):
        name = name[len("/dev/nor0/") :]
    return Path(name)


def extract(uli_path: Path, out_dir: Path) -> None:
    meta = parse_uli(uli_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    for e in meta["entries"]:
        name = e["name"]
        rel = path_from_name(name)
        target = out_dir / rel
        if name.startswith("mkdir "):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-read payload from source for exactness
        src = uli_path.read_bytes()
        chunk = src[e["offset"] : e["offset"] + e["size"]]
        payload = chunk[CHUNK_HEADER_SIZE:] if len(chunk) >= CHUNK_HEADER_SIZE else b""
        target.write_bytes(payload)

    (out_dir / "_uli_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Extracted {meta['count']} entries to {out_dir}")


def repack(extracted_dir: Path, out_uli: Path, require_same_sizes: bool = False) -> None:
    manifest_path = extracted_dir / "_uli_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = meta["entries"]

    count = int(meta["count"])
    out = bytearray()
    out += MAGIC
    out += b"\x00\x00\x00\x00"
    out += struct.pack("<I", count)
    table_off = 0x0C
    out += b"\x00" * (count * ENTRY_SIZE)

    new_table: List[List[int]] = []

    for e in entries:
        name = e["name"]
        rel = path_from_name(name)
        entry_path = extracted_dir / rel
        chunk_off = len(out)

        hdr = base64.b64decode(e["chunk_header_b64"])
        if len(hdr) < CHUNK_HEADER_SIZE:
            hdr = hdr + b"\x00" * (CHUNK_HEADER_SIZE - len(hdr))
        elif len(hdr) > CHUNK_HEADER_SIZE:
            hdr = hdr[:CHUNK_HEADER_SIZE]

        if name.startswith("mkdir "):
            payload = b""
        else:
            if not entry_path.exists():
                raise FileNotFoundError(f"Missing extracted payload: {entry_path}")
            payload = entry_path.read_bytes()

        old_payload_size = int(e["payload_size"])
        if require_same_sizes and len(payload) != old_payload_size:
            raise ValueError(
                f"Payload size changed for {name}: old={old_payload_size}, new={len(payload)} "
                "(use same-size edits or disable --require-same-sizes)"
            )

        chunk = hdr + payload
        out += chunk
        chunk_size = len(chunk)
        new_table.append(
            [
                int(e["type"]),
                int(e["unknown"]),
                int(chunk_off),
                int(chunk_size),
                int(e["reserved0"]),
                int(e["reserved1"]),
            ]
        )

    # write table
    for i, vals in enumerate(new_table):
        struct.pack_into("<6I", out, table_off + i * ENTRY_SIZE, *vals)

    # preserve unknown trailing bytes from source file
    trailer = base64.b64decode(meta.get("trailer_b64", ""))
    out += trailer

    out_uli.parent.mkdir(parents=True, exist_ok=True)
    out_uli.write_bytes(out)
    print(f"Repacked {out_uli} ({len(out)} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract/repack ULI firmware containers")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_extract = sub.add_parser("extract", help="Extract a .uli into a directory")
    ap_extract.add_argument("uli", type=Path)
    ap_extract.add_argument("out_dir", type=Path)

    ap_repack = sub.add_parser("repack", help="Repack an extracted directory into .uli")
    ap_repack.add_argument("extracted_dir", type=Path)
    ap_repack.add_argument("out_uli", type=Path)
    ap_repack.add_argument(
        "--require-same-sizes",
        action="store_true",
        help="Fail if any payload size differs from original manifest",
    )

    args = ap.parse_args()
    if args.cmd == "extract":
        extract(args.uli, args.out_dir)
    elif args.cmd == "repack":
        repack(args.extracted_dir, args.out_uli, require_same_sizes=args.require_same_sizes)


if __name__ == "__main__":
    main()
