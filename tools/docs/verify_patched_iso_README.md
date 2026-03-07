# verify_patched_iso.py — Patched ISO Verification Suite

End-to-end verification of a patched Navi 600/900 firmware ISO against the
original. Performs 7 categories of checks that mirror the head unit bootloader's
own validation sequence, designed to catch any issue that would cause a firmware
update to fail.

## Tests Performed

| # | Test | What It Checks |
|---|------|----------------|
| 1 | ELF structural integrity | Valid 32-bit MIPS LE ELF headers, program segments, entry point within executable segment |
| 2 | Binary diff audit | Only the intended bytes changed — reports exact count and regions |
| 3 | XOZL header comparison | All metadata fields identical to original; only compressed size and CRC differ |
| 4 | `verify all` simulation | Decompresses every `.out` via native `liblzo2` and verifies CRC32 |
| 5 | NAND flash capacity | Total firmware size fits within the 64 MB NAND flash |
| 6 | Installation script dry-run | Every file referenced by `sys_dnl.bat`, `sys_toc.cfg`, and `force.sys` exists |
| 7 | Cross-variant consistency | Patched files are byte-identical between Navi 600 and Navi 900 variants |

### Test details

**Test 1 (ELF integrity):** Decompresses each patched `.out`, parses the ELF32
header, and validates: magic bytes, class (32-bit), data encoding (LE), type
(ET_EXEC), machine (MIPS), program headers, LOAD segment boundaries, and that
the entry point falls within an executable LOAD segment.

**Test 2 (Binary diff):** Decompresses both original and patched ELFs, performs
a byte-by-byte comparison, counts differences, and verifies the count matches
expectations (93 for ProcHMI iPod patch, 3 for sysprogosalio CID patch).
Reports contiguous diff regions with address ranges.

**Test 3 (XOZL headers):** Compares every header field between patched and
original XOZL files. Version, reserved fields, header length, decompressed
size, and trailer must be identical. Only compressed size and CRC32 are
expected to differ (due to recompression).

**Test 4 (`verify all`):** Simulates the bootloader's `verify all` command.
For every `.out` file in both variants: reads the XOZL header, decompresses
the LZO payload using native `liblzo2` (via ctypes), verifies the output size,
and computes CRC32 to compare against the header value.

**Test 5 (NAND capacity):** Sums all file sizes in each variant directory and
reports NAND utilization as a percentage of the 64 MB flash. Fails if the total
exceeds capacity.

**Test 6 (Install scripts):** Parses `sys_dnl.bat`, `sys_toc.cfg`, and
`force.sys` for `copy`, `unpack`, and `program` directives. Verifies that
every referenced filename actually exists in the ISO's variant directory.

**Test 7 (Cross-variant):** Confirms that `ProcHMI.out` and
`sysprogosalio.out` are byte-identical between `g__eeu10` (Navi 600) and
`g_mpeu10` (Navi 900). This is expected because the same patched modules
are deployed to both variants.

## Usage

```bash
python3 tools/verify_patched_iso.py \
    --patched-iso  "Patched Firmware Isos/NAVI600_900 v2.08_patched.iso" \
    --original-iso "Original Firmware Isos/NAVI600_900 v2.08.iso"
```

### Options

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--patched-iso` | Yes | — | Path to the patched firmware ISO |
| `--original-iso` | Yes | — | Path to the original firmware ISO |
| `--nand-size` | No | 67108864 (64 MB) | NAND flash size in bytes |

## Output

Color-coded terminal output with PASS/FAIL per check, organized by test
category. Final result is a single PASS or FAIL line:

```
######################################################################
  FINAL RESULT: PASS  (all checks passed)
######################################################################
```

Exit code: 0 = all tests passed, 1 = one or more failures.

## Platform Requirements

- **macOS**: ISOs are mounted using `hdiutil attach`/`detach`. Two temporary
  mount points are created and cleaned up automatically.
- **Linux**: Not yet supported out of the box (would need `mount -o loop`).
  Contributions welcome.

## Dependencies

- Python 3.10+
- `liblzo2` native library — used via `ctypes` for LZO decompression
  (installed via Homebrew on macOS: `brew install lzo`)
- `hdiutil` (macOS built-in) for ISO mounting

The tool searches for `liblzo2` in standard paths:
- `/opt/homebrew/lib/liblzo2.dylib` (macOS ARM)
- `/usr/local/lib/liblzo2.so` (Linux)
- `/usr/lib/liblzo2.so`
- `/usr/lib/x86_64-linux-gnu/liblzo2.so.2`

## Expected Results for v2.08 Patched ISO

| Metric | Value |
|--------|-------|
| ProcHMI.out byte diffs | 93 (iPod auth retry patch) |
| sysprogosalio.out byte diffs | 3 (SD CID bypass patch) |
| NAND utilization (Navi 600) | ~78.8% |
| NAND utilization (Navi 900) | ~84.6% |
| Cross-variant consistency | Byte-identical |
| All CRC32 checks | Pass |
