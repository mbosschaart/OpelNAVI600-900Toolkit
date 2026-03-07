# validate_xozl.py — XOZL Integrity Validator

Comprehensive validation suite for XOZL `.out` files. Confirms that a file
is structurally correct and will be accepted by the Navi 600/900 bootloader.

## Tests Performed

| # | Test | What It Checks |
|---|------|----------------|
| 1 | Header structure | Magic bytes, version, reserved fields, header length, size consistency |
| 2 | LZO decompression | Payload decompresses successfully with standard LZO1X |
| 3 | CRC32 integrity | Decompressed content CRC matches the header value |
| 4 | Size verification | Decompressed size matches the header's `decomp_size` field |
| 5 | ELF magic | Decompressed output starts with `\x7fELF` |
| 6 | Cross-validation | Byte-for-byte comparison with a known-good decompressed ELF (when `--elf` given) |
| 7 | Header comparison | Field-by-field comparison with the original `.out` (when `--ref` given) |
| 8 | Original compatibility | Confirms the original also decompresses with standard LZO1X |

## Usage

### Basic validation

```bash
python3 tools/validate_xozl.py ProcHMI_patched.out
```

Runs tests 1–5: header parsing, decompression, CRC, size, and ELF magic.

### Full validation with cross-checks

```bash
python3 tools/validate_xozl.py ProcHMI_patched.out \
    --elf ProcHMI_patched.elf \
    --ref original/ProcHMI.out
```

Adds test 6 (byte-for-byte match against the decompressed ELF you built from)
and tests 7–8 (header comparison with the original factory file).

## Options

| Flag | Required | Description |
|------|----------|-------------|
| `xozl_file` | Yes | The `.out` file to validate |
| `--elf PATH` | No | Decompressed ELF for cross-validation |
| `--ref PATH` | No | Original `.out` file for header comparison |

## Output

Color-coded terminal output with PASS/FAIL/WARN/INFO per test. Example:

```
============================================================
  Target: ProcHMI_patched.out
============================================================
  [PASS] Magic: XOZL
  [INFO] Version: 1.2
  [PASS] Header length: 0x24 (36)
  [INFO] Decompressed size: 15,627,488 bytes
  [INFO] Compressed size:   5,699,481 bytes
  [PASS] Compressed size fits within file

  LZO Decompression (target):
  [PASS] Decompression succeeded
  [PASS] Size matches header
  [PASS] CRC32 matches: 0x3f84d3ef
  [PASS] Valid ELF output

  Cross-validation vs ProcHMI_patched.elf:
  [PASS] Byte-for-byte match

  Header comparison (patched vs original):
  [PASS] ver_major: 1
  [PASS] ver_minor: 2
  [PASS] reserved0: 0
  [PASS] reserved1: 0
  [PASS] header_len: 36
  [PASS] Trailer matches
  [INFO] Compression ratio: patched=0.365, original=0.361

  [PASS] Original XOZL uses standard LZO1X — bootloader compatible

============================================================
  RESULT: PASS
============================================================
```

Exit code: 0 = all tests passed, 1 = one or more failures.

## When to Use

- After `xozl_tool.py pack` — to confirm the repacked file is valid
- After any binary modification — to verify nothing broke
- As part of the `build_patch.sh` pipeline (step 5)
- To check original factory `.out` files for research purposes

## Dependencies

- Python 3.10+
- `python-lzo` (`pip install python-lzo`) — required for LZO decompression

The tool will print a clear error if `python-lzo` is not installed.
