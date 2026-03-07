# patch_sd_cid_bypass.py — SD Card CID Verification Bypass

Binary patch for `sysprogosalio.elf` that disables SD card CID-based
cryptographic verification on the Opel Navi 600/900 (firmware v2.08).

## Background

Navigation map SD cards are cryptographically tied to the original card's
CID (Card Identification register). When a card is mounted, the OS
abstraction layer (`sysprogosalio`) checks a marker file
(`/dev/ffs/cryptmarker.dat`) on the internal NAND flash. If the marker is
present, `fd_crypt_verify_signaturefile` verifies the SD card's CID-based
cryptographic signature. If the signature doesn't match, the card is rejected.

This means cloned or replacement SD cards are rejected — even with
identical data — because the CID is a hardware-burned identifier that
cannot be spoofed on standard SD cards.

## What the Patch Does

The firmware already contains a built-in bypass code path. When the global
variable `u32CryptEnabledStatus` is 0 (crypt disabled), the verification
function logs:

```
"Crypt disabled - signature verification always success"
```

and returns 1 (success) without performing any CID or signature checks.

The patch forces this bypass by replacing a single conditional branch
instruction with a `nop`:

```
Before: bnez $v0, +0x48    → if (u32CryptEnabledStatus != 0) goto verify
After:  nop                 → always fall through to "crypt disabled" path
```

## Patch Details

| Field | Value |
|-------|-------|
| Function | `vEnableCrypt` / `fd_crypt_verify_signaturefile` |
| VMA | `0x001925BC` |
| File offset | `0x000935BC` |
| Original bytes | `11 00 40 14` (`bnez $v0, +0x48`) |
| Patched bytes | `00 00 00 00` (`nop`) |
| Context before | `20 00 02 3c  44 62 42 8c` (`lui $v0,0x20` / `lw $v0,0x6244($v0)`) |
| Context after | `07 00 10 3c  1f 00 07 3c` (`lui $s0,7` / `lui $a3,0x1f`) |

Only 3 non-zero bytes change to zero (the 4th byte was already `0x00`).

## Control Flow

```
vEnableCrypt:
  ...
  lui   $v0, 0x0020                    ; load &u32CryptEnabledStatus high
  lw    $v0, 0x6244($v0)              ; load u32CryptEnabledStatus
  bnez  $v0, verify_enabled   ← PATCHED TO NOP
  ...
  ; falls through to:
  "Crypt disabled - signature verification always success"
  return 1  (success)

verify_enabled:
  ; CID check, signature verification, etc.
  ; (never reached after patch)
```

## Usage

### Apply the patch

```bash
python3 tools/patch_sd_cid_bypass.py apply sysprogosalio.elf sysprogosalio_patched.elf
```

The tool:
1. Verifies the input is an ELF file
2. Checks context bytes around the patch site to confirm the correct binary
3. Applies the patch (replaces `bnez` with `nop`)
4. Verifies the patch was applied correctly
5. Reports SHA256 hashes of input and output

### Verify a file

```bash
python3 tools/patch_sd_cid_bypass.py verify sysprogosalio.elf
```

Reports whether the file is unpatched (original), patched (bypass active),
or unknown (wrong binary). Returns exit code 0 on success, 1 on error.

## End-to-End Workflow

```bash
# 1. Decompress the XOZL module
python3 tools/xozl_tool.py extract sysprogosalio.out /tmp/sysprogosalio.elf

# 2. Apply patch
python3 tools/patch_sd_cid_bypass.py apply /tmp/sysprogosalio.elf /tmp/sysprogosalio_patched.elf

# 3. Verify
python3 tools/patch_sd_cid_bypass.py verify /tmp/sysprogosalio_patched.elf

# 4. Repack into XOZL
python3 tools/xozl_tool.py pack /tmp/sysprogosalio_patched.elf sysprogosalio_patched.out --ref sysprogosalio.out

# 5. Validate
python3 tools/validate_xozl.py sysprogosalio_patched.out --elf /tmp/sysprogosalio_patched.elf --ref sysprogosalio.out
```

## Dependencies

- Python 3.10+
- No third-party packages required (uses only `sys`, `struct`, `hashlib`)

## Compatibility

- Firmware v2.08 (`GM10.8V208`) only
- Applies identically to both Navi 600 (`g__eeu10`) and Navi 900 (`g_mpeu10`)
  — the `sysprogosalio.out` modules are byte-identical across variants

## Technical Notes

- The `cryptmarker.dat` file resides on the internal NAND flash at
  `/dev/ffs/cryptmarker.dat`, not on the SD card itself. It is created by
  the `Card_check_marker_file` function during initial SD card setup.
- The bypass leverages the firmware's own debug/development code path — it is
  not a crash or overflow exploit, just a branch redirection.
- The `u32CryptEnabledStatus` global at `0x00206244` is loaded via a
  `lui` + `lw` pair. The patch does not modify this variable; it simply
  prevents the branch that would use its value.
