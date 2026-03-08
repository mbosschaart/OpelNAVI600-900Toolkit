#!/usr/bin/env python3
"""
Patch: SD Card CID Bypass for Opel Navi 600/900 (sysprogosalio.out)

Patches fd_crypt_verify_signaturefile() in sysprogosalio.elf to always
take the "Crypt disabled - signature verification always success" code
path, bypassing all SD card CID and cryptographic signature checks.

Two patch sites (v2):

  Site 1 — Force "marker not found" path:
    0x00192520: beqz $v0, 0x192580   (branch if cryptmarker.dat not found)
    becomes:
    0x00192520: beqz $zero, 0x192580  (always branch — skip CID verify at 0x19318c)

    When cryptmarker.dat exists on NAND (normal case after first SD card use),
    the original code takes the "marker found" path which calls the actual CID
    verification function at 0x19318c. This patch forces the code to always
    take the "marker not found" path instead, reaching site 2.

  Site 2 — Bypass crypt-enabled check:
    0x001925BC: bnez $v0, 0x192604   (branch to verification if crypt enabled)
    becomes:
    0x001925BC: nop                   (always fall through to "crypt disabled" path)

    This leverages the firmware's built-in bypass mechanism. The code falls
    through to the path that logs "Crypt disabled - signature verification
    always success" and returns success without any CID or signature checks.

Usage:
  python3 patch_sd_cid_bypass.py apply  <input.elf> <output.elf>
  python3 patch_sd_cid_bypass.py verify <file.elf>
"""

import sys
import struct
import hashlib

ELF_TEXT_VMA = 0x00100000
ELF_TEXT_FILE_OFF = 0x1000

PATCHES = [
    {
        "name": "Site 1: force marker-not-found path",
        "vma": 0x00192520,
        "old": bytes.fromhex("17004010"),  # beqz $v0, 0x192580
        "new": bytes.fromhex("17000010"),  # beqz $zero, 0x192580 (always branches)
        "ctx_before": bytes.fromhex("cc4a060c8b0040a2"),  # jal 0x192b30 / sb $zero,0x8b($s2)
        "ctx_after":  bytes.fromhex("1f00103c782b0726"),  # lui $s0,0x1f / addiu $a3,$s0,0x2b78
        "desc_old": "beqz $v0, 0x192580",
        "desc_new": "beqz $zero, 0x192580 (always branch)",
    },
    {
        "name": "Site 2: bypass crypt-enabled check",
        "vma": 0x001925BC,
        "old": bytes.fromhex("11004014"),  # bnez $v0, 0x192604
        "new": bytes.fromhex("00000000"),  # nop
        "ctx_before": bytes.fromhex("2000023c4462428c"),  # lui $v0,0x20 / lw $v0,0x6244($v0)
        "ctx_after":  bytes.fromhex("0700103c1f00073c"),  # lui $s0,7 / lui $a3,0x1f
        "desc_old": "bnez $v0, 0x192604",
        "desc_new": "nop",
    },
]


def file_offset(vma):
    return ELF_TEXT_FILE_OFF + (vma - ELF_TEXT_VMA)


def verify(data, label=""):
    all_ok = True
    all_patched = True
    all_unpatched = True

    for p in PATCHES:
        off = file_offset(p["vma"])
        current = data[off:off+4]
        ctx_before = data[off-8:off]
        ctx_after = data[off+4:off+12]

        if ctx_before != p["ctx_before"]:
            print(f"ERROR: Context before {p['name']} does not match. Wrong binary?")
            print(f"  Expected: {p['ctx_before'].hex()}")
            print(f"  Found:    {ctx_before.hex()}")
            return None

        if ctx_after != p["ctx_after"]:
            print(f"ERROR: Context after {p['name']} does not match. Wrong binary?")
            print(f"  Expected: {p['ctx_after'].hex()}")
            print(f"  Found:    {ctx_after.hex()}")
            return None

        if current == p["old"]:
            print(f"[{label}] {p['name']}: UNPATCHED — {p['desc_old']}")
            print(f"  VMA 0x{p['vma']:08X}, file offset 0x{off:06X}")
            all_patched = False
        elif current == p["new"]:
            print(f"[{label}] {p['name']}: PATCHED — {p['desc_new']}")
            print(f"  VMA 0x{p['vma']:08X}, file offset 0x{off:06X}")
            all_unpatched = False
        else:
            print(f"ERROR: Unexpected bytes at {p['name']}")
            print(f"  Expected original: {p['old'].hex()}")
            print(f"  Expected patched:  {p['new'].hex()}")
            print(f"  Found:             {current.hex()}")
            return None

    if all_patched:
        return "patched"
    if all_unpatched:
        return "unpatched"
    return "partial"


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
    if state == "partial":
        print("\nERROR: Partially patched — cannot apply cleanly.")
        return False

    for p in PATCHES:
        off = file_offset(p["vma"])
        data[off:off+len(p["new"])] = p["new"]

    print()
    state = verify(data, "post-patch")
    if state != "patched":
        print("ERROR: Verification after patch failed!")
        return False

    with open(out_path, "wb") as f:
        f.write(data)

    diff = sum(1 for a, b in zip(open(in_path, "rb").read(), data) if a != b)
    print(f"\nOutput: {out_path} ({len(data)} bytes)")
    print(f"SHA256: {hashlib.sha256(data).hexdigest()}")
    print(f"Changed {diff} bytes across {len(PATCHES)} patch sites")
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
