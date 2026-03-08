# cco_tool.py — CCO/Dialog Blob Decoder

Decodes `.cco.bin` blobs extracted from `dialogs.sdp` on the Opel Navi 600/900.
These blobs contain dialog definitions and UI resources used by the HMI layer.

## Format

CCO blobs use a ULI container (same magic as `.uli` resource archives) with
type=2 and an obfuscated LZSS-compressed payload:

| Offset | Size | Field |
|--------|------|-------|
| `0x00` | 4 | Magic: `ULI ` |
| `0x08` | 4 | Count (always 1) |
| `0x0C` | 4 | Type (always 2) |
| `0x14` | 4 | Payload offset (typically `0x28`) |
| `0x18` | 4 | Decompressed size |
| `0x1C` | 4 | Compressed size |
| `0x20` | 4 | Checksum/unknown |
| `0x28+` | var | Compressed payload |

All fields are little-endian unsigned 32-bit integers.

### Decoding pipeline

1. **De-obfuscate** each payload byte: `x = (~b) ^ 1` (bitwise NOT, then XOR 1)
2. **LZSS decompress** with a 4 KiB sliding window initialized to `0x20` (space):
   - Read a flags byte; process 8 tokens MSB-first
   - Bit = 1: literal byte (copy to output and window)
   - Bit = 0: back-reference (2 bytes) — offset from high nibble of byte 2
     shifted left 4 plus byte 1; length from low nibble of byte 2 plus 18

## Commands

### info — Print header fields

```bash
python3 tools/cco_tool.py info <file.cco.bin>
```

Displays the container header: magic, count, type, payload offset, compressed
size, decompressed size, and the unknown field at `0x20`.

### decode — Decompress to raw output

```bash
python3 tools/cco_tool.py decode <file.cco.bin> [output.bin]
```

De-obfuscates and LZSS-decompresses the payload. If no output path is given,
writes to `<input>.decomp.bin`.

## Example

```bash
# Inspect a dialog blob
python3 tools/cco_tool.py info dialogs/main_menu.cco.bin

# Decode it
python3 tools/cco_tool.py decode dialogs/main_menu.cco.bin decoded_menu.bin
```

## Dependencies

- Python 3.10+
- No third-party packages required

## Source

CCO blobs are found inside `dialogs.sdp`, a container file on the firmware's
NAND filesystem. The SDP file can be extracted using standard ULI tools, and
individual `.cco.bin` entries decoded with this tool.
