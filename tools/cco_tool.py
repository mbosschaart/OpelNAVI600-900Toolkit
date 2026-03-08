#!/usr/bin/env python3
"""
Decode Navi600/Navi900 `.cco.bin` blobs extracted from `dialogs.sdp`.

Observed container layout (little-endian):
- 0x00: "ULI "
- 0x08: count (u32) == 1
- 0x0c: type (u32) == 2
- 0x14: payload offset (u32) == 0x28
- 0x18: decompressed size (u32)
- 0x1c: compressed size (u32)
- 0x20: checksum/unknown (u32) (not required for decode)
- payload: `compressed_size` bytes

Decoding pipeline:
1) De-obfuscate each byte: `x = (~b) ^ 1`
2) LZSS-style decompress to `decompressed_size`:
   - 4 KiB window, initialized with 0x20 (space)
   - per-flag byte: process 8 tokens, MSB-first
   - bit=1 => literal byte
   - bit=0 => backref (2 bytes):
       offset = ((b2 & 0xF0) << 4) | b1
       length = (b2 & 0x0F) + 0x12
       copy from window at (wpos - offset) mod 4096

No third-party dependencies.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


MAGIC = b"ULI "
DEFAULT_HDR_LEN = 0x28
WINDOW_SIZE = 4096


def u32le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def deobfuscate(buf: bytes) -> bytes:
    # (~b) ^ 1, in 8-bit domain
    return bytes((((~b) & 0xFF) ^ 0x01) for b in buf)


def lzss_decompress(data: bytes, out_len: int) -> bytes:
    out = bytearray()
    win = bytearray([0x20] * WINDOW_SIZE)
    wpos = 0
    i = 0

    while i < len(data) and len(out) < out_len:
        flags = data[i]
        i += 1

        for bitn in range(8):
            if len(out) >= out_len:
                break
            if i >= len(data):
                raise ValueError("Truncated stream (unexpected end while decoding tokens)")

            bit = (flags >> (7 - bitn)) & 1  # MSB-first
            if bit == 1:
                c = data[i]
                i += 1
                out.append(c)
                win[wpos] = c
                wpos = (wpos + 1) & (WINDOW_SIZE - 1)
                continue

            if i + 1 >= len(data):
                raise ValueError("Truncated stream (missing backref bytes)")
            b1 = data[i]
            b2 = data[i + 1]
            i += 2

            offset = ((b2 & 0xF0) << 4) | b1
            length = (b2 & 0x0F) + 0x12

            src = (wpos - offset) & (WINDOW_SIZE - 1)
            for _ in range(length):
                c = win[src]
                out.append(c)
                win[wpos] = c
                wpos = (wpos + 1) & (WINDOW_SIZE - 1)
                src = (src + 1) & (WINDOW_SIZE - 1)
                if len(out) >= out_len:
                    break

    if len(out) != out_len:
        raise ValueError(f"Decoded size mismatch: got {len(out)} bytes, expected {out_len}")
    return bytes(out)


def decode_cco_blob(blob: bytes) -> bytes:
    if blob[:4] != MAGIC:
        raise ValueError("Magic mismatch (expected 'ULI ')")

    payload_off = u32le(blob, 0x14)
    out_size = u32le(blob, 0x18)
    in_size = u32le(blob, 0x1C)

    if payload_off < 0x20 or payload_off > len(blob):
        raise ValueError(f"Invalid payload offset 0x{payload_off:x}")
    if payload_off + in_size > len(blob):
        raise ValueError(
            f"Truncated payload: need {in_size} bytes at 0x{payload_off:x}, file has {len(blob)} bytes"
        )

    comp = blob[payload_off : payload_off + in_size]
    x = deobfuscate(comp)
    return lzss_decompress(x, out_size)


def cmd_info(path: Path) -> int:
    b = path.read_bytes()
    if b[:4] != MAGIC:
        raise ValueError("Magic mismatch (expected 'ULI ')")

    count = u32le(b, 0x08)
    t = u32le(b, 0x0C)
    payload_off = u32le(b, 0x14)
    out_size = u32le(b, 0x18)
    in_size = u32le(b, 0x1C)
    unk = u32le(b, 0x20) if len(b) >= 0x24 else None

    print(f"path: {path}")
    print(f"size: {len(b)} bytes")
    print(f"count: {count}")
    print(f"type: {t}")
    print(f"payload_off: 0x{payload_off:x}")
    print(f"compressed_size: 0x{in_size:x} ({in_size})")
    print(f"decompressed_size: 0x{out_size:x} ({out_size})")
    if unk is not None:
        print(f"unknown_0x20: 0x{unk:x}")
    return 0


def cmd_decode(inp: Path, out: Path) -> int:
    decoded = decode_cco_blob(inp.read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(decoded)
    print(f"Wrote {len(decoded)} bytes to {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Navi600/Navi900 .cco.bin (ULI type=2) blobs")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_info = sub.add_parser("info", help="Print header fields")
    ap_info.add_argument("cco_bin", type=Path)

    ap_decode = sub.add_parser("decode", help="Decode to a raw decompressed blob")
    ap_decode.add_argument("cco_bin", type=Path)
    ap_decode.add_argument("out", type=Path, nargs="?")

    args = ap.parse_args()

    if args.cmd == "info":
        return cmd_info(args.cco_bin)
    if args.cmd == "decode":
        out: Path
        if args.out is None:
            out = args.cco_bin.with_suffix(args.cco_bin.suffix + ".decomp.bin")
        else:
            out = args.out
        return cmd_decode(args.cco_bin, out)

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())

