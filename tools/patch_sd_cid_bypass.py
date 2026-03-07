#!/usr/bin/env python3
"""
Patch: SD Card CID Bypass for Opel Navi 600/900 (sysprogosalio.out)

Patches fd_crypt_verify_signaturefile() in sysprogosalio.elf to always
take the "Crypt disabled - signature verification always success" code
path, bypassing all SD card CID and cryptographic signature checks.

The patch changes a single MIPS instruction:
  0x001925BC: bnez $v0, 0x192604   (branch to verification if crypt enabled)
  becomes:
  0x001925BC: nop                   (always fall through to "crypt disabled" path)

This leverages the firmware's built-in bypass mechanism. When the code
falls through, it hits the existing path that logs:
  "Crypt disabled-signature verification always success"
and returns success (1) without performing any CID or signature checks.

Usage:
  python3 patch_sd_cid_bypass.py apply  <input.elf> <output.elf>
  python3 patch_sd_cid_bypass.py verify <file.elf>
"""

import sys
import struct
import hashlib

PATCH_VMA = 0x001925BC
PATCH_DESCRIPTION = "fd_crypt_verify_signaturefile: bnez $v0 → nop (bypass CID check)"

ELF_TEXT_VMA = 0x00100000
ELF_TEXT_FILE_OFF = 0x1000

OLD_BYTES = bytes.fromhex("11004014")  # bnez $v0, +0x11
NEW_BYTES = bytes.fromhex("00000000")  # nop

CONTEXT_BEFORE = bytes.fromhex("2000023c4462428c")  # lui $v0,0x20 / lw $v0,0x6244($v0)
CONTEXT_AFTER  = bytes.fromhex("0700103c1f00073c")  # lui $s0,7 / lui $a3,0x1f


def file_offset(vma):
    return ELF_TEXT_FILE_OFF + (vma - ELF_TEXT_VMA)


def verify(data, label=""):
    off = file_offset(PATCH_VMA)
    current = data[off:off+4]

    ctx_before = data[off-8:off]
    ctx_after = data[off+4:off+12]

    if ctx_before != CONTEXT_BEFORE:
        print(f"ERROR: Context before patch site does not match. Wrong binary?")
        print(f"  Expected: {CONTEXT_BEFORE.hex()}")
        print(f"  Found:    {ctx_before.hex()}")
        return None

    if ctx_after != CONTEXT_AFTER:
        print(f"ERROR: Context after patch site does not match. Wrong binary?")
        print(f"  Expected: {CONTEXT_AFTER.hex()}")
        print(f"  Found:    {ctx_after.hex()}")
        return None

    if current == OLD_BYTES:
        print(f"[{label}] UNPATCHED — original bnez instruction present")
        print(f"  VMA 0x{PATCH_VMA:08X}, file offset 0x{off:06X}")
        print(f"  Bytes: {current.hex()} (bnez $v0, 0x192604)")
        return "unpatched"

    if current == NEW_BYTES:
        print(f"[{label}] PATCHED — nop instruction present (CID bypass active)")
        print(f"  VMA 0x{PATCH_VMA:08X}, file offset 0x{off:06X}")
        print(f"  Bytes: {current.hex()} (nop)")
        return "patched"

    print(f"ERROR: Unexpected bytes at patch site")
    print(f"  Expected original: {OLD_BYTES.hex()}")
    print(f"  Expected patched:  {NEW_BYTES.hex()}")
    print(f"  Found:             {current.hex()}")
    return None


def apply_patch(in_path, out_path):
    with open(in_path, "rb") as f:
        data = bytearray(f.read())

    magic = data[:4]
    if magic != b'\x7fELF':
        print(f"ERROR: {in_path} is not an ELF file (magic: {magic.hex()})")
        return False

    print(f"Input:  {in_path} ({len(data)} bytes)")
    print(f"SHA256: {hashlib.sha256(data).hexdigest()}")
    print()

    state = verify(data, "pre-patch")
    if state is None:
        return False
    if state == "patched":
        print("\nAlready patched, nothing to do.")
        return True

    off = file_offset(PATCH_VMA)
    data[off:off+4] = NEW_BYTES

    print()
    state = verify(data, "post-patch")
    if state != "patched":
        print("ERROR: Verification after patch failed!")
        return False

    with open(out_path, "wb") as f:
        f.write(data)

    print(f"\nOutput: {out_path} ({len(data)} bytes)")
    print(f"SHA256: {hashlib.sha256(data).hexdigest()}")
    print(f"\nPatch applied successfully: {PATCH_DESCRIPTION}")
    return True


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "verify":
        with open(sys.argv[2], "rb") as f:
            data = f.read()
        state = verify(data, sys.argv[2])
        sys.exit(0 if state else 1)

    elif cmd == "apply":
        if len(sys.argv) < 4:
            print("Usage: patch_sd_cid_bypass.py apply <input.elf> <output.elf>")
            sys.exit(1)
        ok = apply_patch(sys.argv[2], sys.argv[3])
        sys.exit(0 if ok else 1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
