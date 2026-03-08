# patch_sd_cid_bypass.py — SD Card CID Verification Bypass (v2)

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

## What the Patch Does (v2)

The v1 patch only bypassed the `u32CryptEnabledStatus` check (Site 2), but
this was insufficient when `cryptmarker.dat` exists on NAND — which it does
on any unit that has previously been paired with an SD card. In that case,
the firmware takes a different code path that calls the actual CID
verification function at `0x19318c`, completely bypassing the v1 patch site.

The v2 patch adds a second site (Site 1) that forces **all** execution
through the "marker not found" path, ensuring it always reaches Site 2.

## Patch Sites

### Site 1 — Force "marker not found" path (new in v2)

After calling `check_marker()` which reads `/dev/ffs/cryptmarker.dat`,
the code branches based on whether the marker was found:

```
Before: beqz $v0, 0x192580   → branch only if marker NOT found
After:  beqz $zero, 0x192580 → always branch ($zero is always 0)
```

This is a 1-byte change: register field `$v0` (0x02) → `$zero` (0x00).

| Field | Value |
|-------|-------|
| VMA | `0x00192520` |
| File offset | `0x00093520` |
| Original bytes | `17 00 40 10` (`beqz $v0, 0x192580`) |
| Patched bytes | `17 00 00 10` (`beqz $zero, 0x192580`) |
| Context before | `cc 4a 06 0c  8b 00 40 a2` (`jal 0x192b30` / `sb $zero,0x8b($s2)`) |
| Context after | `1f 00 10 3c  78 2b 07 26` (`lui $s0,0x1f` / `addiu $a3,$s0,0x2b78`) |

### Site 2 — Bypass crypt-enabled check (v1, unchanged)

The firmware already contains a built-in bypass code path. When the global
variable `u32CryptEnabledStatus` is 0 (crypt disabled), the verification
function logs `"Crypt disabled - signature verification always success"`
and returns 1 (success). This patch forces that bypass:

```
Before: bnez $v0, +0x48    → if (u32CryptEnabledStatus != 0) goto verify
After:  nop                 → always fall through to "crypt disabled" path
```

| Field | Value |
|-------|-------|
| VMA | `0x001925BC` |
| File offset | `0x000935BC` |
| Original bytes | `11 00 40 14` (`bnez $v0, +0x48`) |
| Patched bytes | `00 00 00 00` (`nop`) |
| Context before | `20 00 02 3c  44 62 42 8c` (`lui $v0,0x20` / `lw $v0,0x6244($v0)`) |
| Context after | `07 00 10 3c  1f 00 07 3c` (`lui $s0,7` / `lui $a3,0x1f`) |

Total: 4 bytes changed across 2 sites (1 byte at Site 1, 3 bytes at Site 2).

## Control Flow

```
fd_crypt_verify_signaturefile:
  ...
  jal   check_marker           ; reads /dev/ffs/cryptmarker.dat
  beqz  $v0, marker_not_found  ← SITE 1: $v0 changed to $zero (always branch)
  ; --- marker-found path (never reached after patch) ---
  jal   0x19318c               ; actual CID verification
  ...

marker_not_found:
  ...
  lui   $v0, 0x0020
  lw    $v0, 0x6244($v0)       ; load u32CryptEnabledStatus
  bnez  $v0, verify_enabled    ← SITE 2: patched to nop
  ...
  ; falls through to:
  "Crypt disabled - signature verification always success"
  return 1  (success)

verify_enabled:
  ; CID check, signature verification, etc.
  ; (never reached after both patches)
```

## Usage

### Apply the patch

```bash
python3 tools/patch_sd_cid_bypass.py apply sysprogosalio.elf sysprogosalio_patched.elf
```

The tool:
1. Verifies the input is an ELF file
2. Checks context bytes around both patch sites to confirm the correct binary
3. Applies both patches atomically
4. Verifies both patches were applied correctly
5. Reports SHA256 hashes of input and output

### Verify a file

```bash
python3 tools/patch_sd_cid_bypass.py verify sysprogosalio.elf
```

Reports whether the file is unpatched (original), patched (bypass active),
partially patched (v1 only), or unknown (wrong binary).

## End-to-End Workflow

```bash
# 1. Decompress the XOZL module
python3 tools/xozl_tool.py extract sysprogosalio.out /tmp/sysprogosalio.elf

# 2. Apply patch
python3 tools/patch_sd_cid_bypass.py apply /tmp/sysprogosalio.elf /tmp/sysprogosalio_patched.elf

# 3. Verify
python3 tools/patch_sd_cid_bypass.py verify /tmp/sysprogosalio_patched.elf

# 4. Repack into XOZL (recomputes both content CRC and whole-file CRC)
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
- The bypass leverages the firmware's own debug/development code paths — it is
  not a crash or overflow exploit, just branch redirections.
- The `u32CryptEnabledStatus` global at `0x00206244` is loaded via a
  `lui` + `lw` pair. The patch does not modify this variable; it simply
  prevents the branch that would use its value.
- The v2 patch is backwards-compatible: on units where `cryptmarker.dat` has
  been deleted, Site 1's change is a no-op (branch was already taken), and
  Site 2 still provides the bypass.
