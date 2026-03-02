# xozl_tool.py — XOZL Firmware Module Tool

Tool for inspecting, extracting, and packing the `.out` firmware modules used by
the Opel Navi 600/900 (GM-GE platform, Bosch head unit).

## Background

The firmware update files (`ProcHMI.out`, `ProcBase.out`, `ProcMM.out`, etc.)
are not raw ELF binaries. They use a proprietary container format called **XOZL**
that wraps a standard **LZO1X** compressed ELF with a 36-byte header and a
version trailer.

During a firmware update, the bootloader (`dragon.bin`) reads each `.out` file,
checks the first 4 bytes for the `XOZL` magic, decompresses the LZO payload
into RAM, verifies the CRC32, and then loads it as a MIPS ELF.

## XOZL File Layout

```
┌──────────────────────────────────────────────────────┐
│  0x00  Magic: "XOZL" (4 bytes, LE 0x4c5a4f58)       │
│  0x04  Reserved (4 bytes, always 0)                  │
│  0x08  Version major (4 bytes, u32 LE, value: 1)     │
│  0x0C  Version minor (4 bytes, u32 LE, value: 2)     │
│  0x10  Reserved (4 bytes, always 0)                  │
│  0x14  Header length (4 bytes, u32 LE, value: 0x24)  │
│  0x18  Decompressed size (4 bytes, u32 LE)           │
│  0x1C  Compressed size (4 bytes, u32 LE)             │
│  0x20  CRC32 of decompressed content (4 bytes)       │
├──────────────────────────────────────────────────────┤  ← offset 0x24
│  LZO1X compressed payload (comp_size bytes)          │
│  ...                                                 │
├──────────────────────────────────────────────────────┤  ← offset 0x24 + comp_size
│  Version trailer (variable length)                   │
│  e.g. "GM10.8V208" + binary metadata                 │
└──────────────────────────────────────────────────────┘
```

### Header fields

| Offset | Size | Type | Field | Notes |
|--------|------|------|-------|-------|
| 0x00 | 4 | char[4] | magic | Always `XOZL` (LE `0x4c5a4f58`) |
| 0x04 | 4 | u32 LE | reserved0 | Always 0 |
| 0x08 | 4 | u32 LE | ver_major | Container version major (observed: 1) |
| 0x0C | 4 | u32 LE | ver_minor | Container version minor (observed: 2) |
| 0x10 | 4 | u32 LE | reserved1 | Always 0 |
| 0x14 | 4 | u32 LE | header_len | Always `0x24` (36). Payload starts here. |
| 0x18 | 4 | u32 LE | decomp_size | Exact byte count of the decompressed ELF |
| 0x1C | 4 | u32 LE | comp_size | Exact byte count of the LZO compressed stream |
| 0x20 | 4 | u32 LE | crc32 | CRC32 (polynomial 0xEDB88320, init=0) of the **decompressed** content |

### Compression

The payload uses **LZO1X** compression, the same algorithm implemented by
[miniLZO](http://www.oberhumer.com/opensource/lzo/) and the `python-lzo` library.
This was confirmed by successfully decompressing the original factory firmware
with `lzo.decompress()`.

The `python-lzo` wrapper adds a 5-byte header to its output (`0xf0` + 4-byte
big-endian uncompressed size). This tool strips those 5 bytes when packing, and
passes `False` for the `header` parameter when decompressing, to work with the
raw LZO stream expected by the XOZL format.

### Version trailer

After the compressed payload, the file contains a version trailer. For Navi
600/900 firmware v2.08, this is the ASCII string `GM10.8V208` followed by binary
metadata (flags, version fields). The trailer is not part of the compressed data
and is not covered by the CRC32.

### CRC32

The CRC32 in the header is computed over the **decompressed** ELF content, not
the compressed payload. It uses standard CRC32 with init value 0 (Python:
`binascii.crc32(data, 0) & 0xFFFFFFFF`).

## Commands

### `info` — Inspect XOZL header

Parses the 36-byte header and prints all fields as JSON.

```
python3 xozl_tool.py info ProcHMI.out
```

Example output:

```json
{
  "path": "ProcHMI.out",
  "file_size": 5646412,
  "reserved0": 0,
  "version_major": 1,
  "version_minor": 2,
  "reserved1": 0,
  "header_len": 36,
  "decompressed_size": 15627488,
  "compressed_size": 5646304,
  "crc32": 1065615343,
  "payload_off": 36,
  "payload_size": 5646376,
  "trailer_size": 72,
  "trailer": "GM10.8V208"
}
```

### `extract` — Decompress XOZL to ELF

Reads the XOZL header, extracts the compressed payload, decompresses it with
LZO1X, verifies the size matches `decomp_size` and the CRC32 matches, then
writes the raw ELF to disk.

```
python3 xozl_tool.py extract ProcHMI.out ProcHMI.elf
```

The command will abort with an error if:
- The file doesn't start with `XOZL` magic
- LZO decompression fails (corrupted or truncated payload)
- Decompressed size doesn't match the header
- CRC32 doesn't match (data corruption)

### `pack` — Compress ELF to XOZL

Reads a raw ELF file, compresses it with LZO1X-1 (`lzo.compress`), constructs a
valid XOZL header, and writes the `.out` file. Performs a round-trip verification
(decompress the compressed output and compare byte-for-byte) before writing.

```
python3 xozl_tool.py pack ProcHMI_patched.elf ProcHMI_patched.out --ref ProcHMI.out
```

**The `--ref` flag** (recommended): copies the 36-byte header template and the
version trailer from an existing `.out` file. Only the three variable fields
are updated:
- `0x18` decomp_size — set to the new ELF size
- `0x1C` comp_size — set to the new LZO stream size
- `0x20` crc32 — recomputed from the new ELF content

This ensures the magic, version numbers, reserved fields, and version trailer
are identical to the original — the patched file is structurally indistinguishable
from a factory file except for the payload data and these three fields.

**Without `--ref`**: the tool constructs a minimal header from scratch using the
observed default values (ver 1.2, header_len 0x24) and an empty trailer.

## How `pack` works internally

```
1. Read the input ELF into memory
2. Compute CRC32 of the ELF (init=0)
3. Compress with lzo.compress() → produces 5-byte header + LZO stream
4. Strip the 5-byte python-lzo header to get the raw LZO stream
5. Round-trip verify: decompress the raw stream, compare with original ELF
6. If --ref: copy header bytes [0x00..0x23] and trailer from reference .out
   If no --ref: build header from defaults
7. Write updated decomp_size, comp_size, crc32 into the header
8. Concatenate: header (36 bytes) + LZO stream + trailer
9. Write to output file
```

## Dependencies

- **Python 3.10+**
- **python-lzo** — `pip install python-lzo` (wraps the C miniLZO library)

The `info` command works without `python-lzo` since it only reads the header.
The `extract` and `pack` commands require it for LZO compression/decompression.

## Bootloader context

The Navi 600/900 bootloader (`dragon.bin`) dispatches loaded files based on the
first 4 bytes:

| Magic | Handler | Description |
|-------|---------|-------------|
| `XOZL` | LZO decompress + ELF load | Standard firmware module |
| `\x7fELF` | Direct ELF load | Raw uncompressed ELF (larger file, no compression) |
| `ULI ` | ULI resource handler | Image/font resource archives |

The XOZL path decompresses the payload into a memory buffer, then proceeds with
the same ELF segment loader used by the raw ELF path. Both paths end up at the
same common loader routine in the bootloader.
