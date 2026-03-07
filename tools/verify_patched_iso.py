#!/usr/bin/env python3
"""
Comprehensive verification of a patched NAVI600/900 firmware ISO.

Performs 7 categories of checks that mirror the head unit bootloader's
own validation sequence:

  1. ELF structural integrity (headers, segments, architecture, entry point)
  2. Binary diff audit (only intended bytes changed)
  3. XOZL header field-by-field comparison against originals
  4. 'verify all' simulation (XOZL → LZO decompress → CRC32)
  5. NAND flash capacity check
  6. Installation script dry-run (every referenced file must exist)
  7. Cross-variant consistency (Navi 600 vs 900 patched files identical)

Usage:
  python3 verify_patched_iso.py \\
      --patched-iso  <patched.iso> \\
      --original-iso <original.iso> \\
      [--nand-size 67108864]
"""

from __future__ import annotations

import argparse
import binascii
import ctypes
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"
INFO = "\033[94mINFO\033[0m"
SECT = "\033[1;96m"
RESET = "\033[0m"

VARIANTS = ["g__eeu10", "g_mpeu10"]
PATCHED_FILES = {"ProcHMI.out", "sysprogosalio.out"}
EXPECTED_DIFFS = {
    "ProcHMI.out": {"count": 93, "desc": "iPod auth retry: 2 jump sites + code cave"},
    "sysprogosalio.out": {"count": 3, "desc": "SD CID bypass: bnez→nop"},
}

failures = 0


def fail(msg: str):
    global failures
    failures += 1
    print(f"  [{FAIL}] {msg}")


def ok(msg: str):
    print(f"  [{PASS}] {msg}")


def info(msg: str):
    print(f"  [{INFO}] {msg}")


def warn(msg: str):
    print(f"  [{WARN}] {msg}")


def section(title: str):
    print(f"\n{SECT}{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}{RESET}")


# ---------------------------------------------------------------------------
# LZO helpers (via native liblzo2)
# ---------------------------------------------------------------------------

_lib = None

def _get_lzo():
    global _lib
    if _lib is None:
        for path in [
            "/opt/homebrew/lib/liblzo2.dylib",
            "/usr/local/lib/liblzo2.so",
            "/usr/lib/liblzo2.so",
            "/usr/lib/x86_64-linux-gnu/liblzo2.so.2",
        ]:
            if os.path.exists(path):
                _lib = ctypes.CDLL(path)
                break
        if _lib is None:
            try:
                _lib = ctypes.CDLL("liblzo2.dylib")
            except OSError:
                _lib = ctypes.CDLL("liblzo2.so")
    return _lib


def lzo_decompress(compressed: bytes, expected_size: int) -> bytes:
    lib = _get_lzo()
    dst = ctypes.create_string_buffer(expected_size + 256)
    dst_len = ctypes.c_size_t(expected_size + 256)
    ret = lib.lzo1x_decompress_safe(
        ctypes.c_char_p(compressed),
        ctypes.c_size_t(len(compressed)),
        dst,
        ctypes.byref(dst_len),
        None,
    )
    if ret != 0:
        raise RuntimeError(f"lzo1x_decompress_safe failed: {ret}")
    return dst.raw[: dst_len.value]


# ---------------------------------------------------------------------------
# XOZL helpers
# ---------------------------------------------------------------------------

def read_xozl(path: str) -> dict:
    with open(path, "rb") as f:
        b = f.read()
    comp_size = struct.unpack_from("<I", b, 0x1C)[0]
    return {
        "raw": b,
        "magic": b[:4],
        "reserved0": struct.unpack_from("<I", b, 0x04)[0],
        "ver_major": struct.unpack_from("<I", b, 0x08)[0],
        "ver_minor": struct.unpack_from("<I", b, 0x0C)[0],
        "reserved1": struct.unpack_from("<I", b, 0x10)[0],
        "header_len": struct.unpack_from("<I", b, 0x14)[0],
        "decomp_size": struct.unpack_from("<I", b, 0x18)[0],
        "comp_size": comp_size,
        "crc32": struct.unpack_from("<I", b, 0x20)[0],
        "payload": b[0x24 : 0x24 + comp_size],
        "trailer": b[0x24 + comp_size :],
    }


def decompress_xozl(xozl: dict) -> bytes:
    return lzo_decompress(xozl["payload"], xozl["decomp_size"])


# ---------------------------------------------------------------------------
# 1. ELF structural integrity
# ---------------------------------------------------------------------------

def check_elf_integrity(elf: bytes, label: str) -> bool:
    section(f"1. ELF Structural Integrity: {label}")
    passed = True

    if elf[:4] != b"\x7fELF":
        fail("ELF magic missing")
        return False
    ok("ELF magic: \\x7fELF")

    ei_class = elf[4]
    ei_data = elf[5]
    ei_version = elf[6]
    if ei_class == 1:
        ok("ELF class: 32-bit (ELFCLASS32)")
    else:
        fail(f"ELF class: {ei_class} (expected 1 = 32-bit)")
        passed = False

    if ei_data == 1:
        ok("ELF data: Little-endian (ELFDATA2LSB)")
    else:
        fail(f"ELF data: {ei_data} (expected 1 = LE)")
        passed = False

    if ei_version == 1:
        ok("ELF version: 1 (current)")
    else:
        warn(f"ELF version: {ei_version}")

    e_type = struct.unpack_from("<H", elf, 16)[0]
    e_machine = struct.unpack_from("<H", elf, 18)[0]
    e_entry = struct.unpack_from("<I", elf, 24)[0]
    e_phoff = struct.unpack_from("<I", elf, 28)[0]
    e_phentsize = struct.unpack_from("<H", elf, 42)[0]
    e_phnum = struct.unpack_from("<H", elf, 44)[0]
    e_shoff = struct.unpack_from("<I", elf, 32)[0]
    e_shentsize = struct.unpack_from("<H", elf, 46)[0]
    e_shnum = struct.unpack_from("<H", elf, 48)[0]

    if e_type == 2:
        ok("ELF type: ET_EXEC (executable)")
    else:
        warn(f"ELF type: {e_type} (expected 2 = ET_EXEC)")

    if e_machine == 8:
        ok("ELF machine: MIPS (EM_MIPS)")
    else:
        fail(f"ELF machine: {e_machine} (expected 8 = MIPS)")
        passed = False

    info(f"Entry point: 0x{e_entry:08X}")

    if e_phnum == 0:
        fail("No program headers (e_phnum == 0)")
        return False

    ok(f"Program headers: {e_phnum} entries at offset 0x{e_phoff:X}")

    entry_in_segment = False
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type = struct.unpack_from("<I", elf, off)[0]
        p_offset = struct.unpack_from("<I", elf, off + 4)[0]
        p_vaddr = struct.unpack_from("<I", elf, off + 8)[0]
        p_filesz = struct.unpack_from("<I", elf, off + 16)[0]
        p_memsz = struct.unpack_from("<I", elf, off + 20)[0]
        p_flags = struct.unpack_from("<I", elf, off + 24)[0]

        if p_type == 1:  # PT_LOAD
            if p_offset + p_filesz > len(elf):
                fail(f"LOAD segment {i}: extends past EOF "
                     f"(offset 0x{p_offset:X} + 0x{p_filesz:X} > 0x{len(elf):X})")
                passed = False
            else:
                ok(f"LOAD seg {i}: vaddr=0x{p_vaddr:08X} "
                   f"filesz=0x{p_filesz:X} memsz=0x{p_memsz:X} "
                   f"flags={'R' if p_flags & 4 else ''}{'W' if p_flags & 2 else ''}{'X' if p_flags & 1 else ''}")

            if p_vaddr <= e_entry < p_vaddr + p_memsz and (p_flags & 1):
                entry_in_segment = True

    if entry_in_segment:
        ok(f"Entry point 0x{e_entry:08X} is within an executable LOAD segment")
    else:
        fail(f"Entry point 0x{e_entry:08X} not within any executable LOAD segment")
        passed = False

    if e_shoff > 0 and e_shnum > 0:
        if e_shoff + e_shnum * e_shentsize <= len(elf):
            ok(f"Section headers: {e_shnum} entries at offset 0x{e_shoff:X}")
        else:
            warn(f"Section headers extend past EOF (0x{e_shoff:X} + {e_shnum}*{e_shentsize})")
    else:
        info("No section headers (stripped)")

    return passed


# ---------------------------------------------------------------------------
# 2. Binary diff audit
# ---------------------------------------------------------------------------

def check_binary_diff(orig_elf: bytes, patched_elf: bytes, filename: str) -> bool:
    section(f"2. Binary Diff Audit: {filename}")

    if len(orig_elf) != len(patched_elf):
        fail(f"Size mismatch: original={len(orig_elf):,} patched={len(patched_elf):,}")
        return False
    ok(f"Size match: {len(orig_elf):,} bytes")

    diffs = []
    for i in range(len(orig_elf)):
        if orig_elf[i] != patched_elf[i]:
            diffs.append(i)

    expected = EXPECTED_DIFFS.get(filename, {})
    exp_count = expected.get("count", "?")
    exp_desc = expected.get("desc", "")

    info(f"Expected: {exp_count} byte differences ({exp_desc})")
    info(f"Actual:   {len(diffs)} byte differences")

    if len(diffs) == exp_count:
        ok(f"Byte diff count matches expected: {len(diffs)}")
    else:
        fail(f"Byte diff count {len(diffs)} != expected {exp_count}")
        return False

    if diffs:
        first = diffs[0]
        last = diffs[-1]
        info(f"Diff range: 0x{first:08X} .. 0x{last:08X}")

        regions = []
        region_start = diffs[0]
        prev = diffs[0]
        for d in diffs[1:]:
            if d - prev > 16:
                regions.append((region_start, prev))
                region_start = d
            prev = d
        regions.append((region_start, prev))

        info(f"Contiguous diff regions: {len(regions)}")
        for start, end in regions:
            count = sum(1 for d in diffs if start <= d <= end)
            info(f"  0x{start:08X}..0x{end:08X} ({count} bytes)")

    return True


# ---------------------------------------------------------------------------
# 3. XOZL header field-by-field comparison
# ---------------------------------------------------------------------------

def check_xozl_headers(patched_xozl: dict, orig_xozl: dict, filename: str) -> bool:
    section(f"3. XOZL Header Comparison: {filename}")
    passed = True

    for field in ["magic", "ver_major", "ver_minor", "reserved0", "reserved1", "header_len"]:
        pv = patched_xozl[field]
        ov = orig_xozl[field]
        if pv == ov:
            ok(f"{field}: {pv!r}" if isinstance(pv, bytes) else f"{field}: {pv}")
        else:
            fail(f"{field}: patched={pv!r}, original={ov!r}")
            passed = False

    if patched_xozl["decomp_size"] == orig_xozl["decomp_size"]:
        ok(f"decomp_size: {patched_xozl['decomp_size']:,} (unchanged)")
    else:
        fail(f"decomp_size changed: {orig_xozl['decomp_size']:,} -> {patched_xozl['decomp_size']:,}")
        passed = False

    info(f"comp_size: original={orig_xozl['comp_size']:,} patched={patched_xozl['comp_size']:,} "
         f"(delta={patched_xozl['comp_size'] - orig_xozl['comp_size']:+,})")

    info(f"crc32: original=0x{orig_xozl['crc32']:08X} patched=0x{patched_xozl['crc32']:08X}")

    pt = patched_xozl["trailer"]
    ot = orig_xozl["trailer"]
    if pt == ot:
        ok(f"Trailer: identical ({len(pt)} bytes)")
    else:
        pt_stripped = pt.rstrip(b"\x00\x01")
        ot_stripped = ot.rstrip(b"\x00\x01")
        if pt_stripped == ot_stripped:
            ok("Trailer: content matches (padding differs)")
        else:
            fail(f"Trailer content differs")
            passed = False

    return passed


# ---------------------------------------------------------------------------
# 4. 'verify all' simulation
# ---------------------------------------------------------------------------

def check_verify_all(iso_path: str, variant: str) -> bool:
    section(f"4. 'verify all' Simulation: {variant}")
    passed = True
    vdir = os.path.join(iso_path, "dnl", "bin", "system", "adit", variant)

    out_files = sorted(f for f in os.listdir(vdir) if f.endswith(".out"))
    info(f"Found {len(out_files)} .out files to verify")

    for fname in out_files:
        fpath = os.path.join(vdir, fname)
        with open(fpath, "rb") as f:
            data = f.read()

        if data[:4] != b"XOZL":
            fail(f"{fname}: not XOZL (magic={data[:4]!r})")
            passed = False
            continue

        hdr_len = struct.unpack_from("<I", data, 0x14)[0]
        decomp_sz = struct.unpack_from("<I", data, 0x18)[0]
        comp_sz = struct.unpack_from("<I", data, 0x1C)[0]
        stored_crc = struct.unpack_from("<I", data, 0x20)[0]

        if len(data) < hdr_len + comp_sz:
            fail(f"{fname}: file truncated ({len(data)} < {hdr_len + comp_sz})")
            passed = False
            continue

        try:
            payload = data[hdr_len : hdr_len + comp_sz]
            decompressed = lzo_decompress(payload, decomp_sz)
        except Exception as e:
            fail(f"{fname}: decompression failed: {e}")
            passed = False
            continue

        if len(decompressed) != decomp_sz:
            fail(f"{fname}: size mismatch ({len(decompressed)} != {decomp_sz})")
            passed = False
            continue

        actual_crc = binascii.crc32(decompressed) & 0xFFFFFFFF
        if actual_crc != stored_crc:
            fail(f"{fname}: CRC mismatch (stored=0x{stored_crc:08X} actual=0x{actual_crc:08X})")
            passed = False
        else:
            ok(f"{fname}: decompress OK, CRC32 0x{actual_crc:08X} verified ({len(decompressed):,} bytes)")

    return passed


# ---------------------------------------------------------------------------
# 5. NAND flash capacity check
# ---------------------------------------------------------------------------

def check_nand_capacity(iso_path: str, variant: str, nand_size: int) -> bool:
    section(f"5. NAND Flash Capacity: {variant}")
    vdir = os.path.join(iso_path, "dnl", "bin", "system", "adit", variant)

    total_compressed = 0
    total_decompressed = 0

    for fname in sorted(os.listdir(vdir)):
        fpath = os.path.join(vdir, fname)
        if not os.path.isfile(fpath):
            continue
        sz = os.path.getsize(fpath)
        total_compressed += sz

        if fname.endswith(".out"):
            with open(fpath, "rb") as f:
                hdr = f.read(0x24)
            if hdr[:4] == b"XOZL":
                decomp_sz = struct.unpack_from("<I", hdr, 0x18)[0]
                total_decompressed += decomp_sz
            else:
                total_decompressed += sz
        elif fname.endswith(".uli"):
            total_decompressed += sz
        else:
            total_decompressed += sz

    info(f"Total on-disk (ISO):        {total_compressed:>12,} bytes ({total_compressed / 1024 / 1024:.1f} MB)")
    info(f"Total decompressed in RAM:  {total_decompressed:>12,} bytes ({total_decompressed / 1024 / 1024:.1f} MB)")
    info(f"NAND capacity:              {nand_size:>12,} bytes ({nand_size / 1024 / 1024:.0f} MB)")

    pct = total_compressed / nand_size * 100
    if total_compressed < nand_size:
        ok(f"Fits within NAND: {pct:.1f}% utilization")
        return True
    else:
        fail(f"Exceeds NAND capacity: {pct:.1f}%")
        return False


# ---------------------------------------------------------------------------
# 6. Installation script dry-run
# ---------------------------------------------------------------------------

def check_install_script(iso_path: str, variant: str) -> bool:
    section(f"6. Installation Script Dry-Run: {variant}")
    vdir = os.path.join(iso_path, "dnl", "bin", "system", "adit", variant)
    passed = True

    for script_name in ["sys_dnl.bat", "sys_toc.cfg", "force.sys"]:
        script_path = os.path.join(vdir, script_name)
        if not os.path.exists(script_path):
            continue

        info(f"Parsing {script_name}...")
        with open(script_path, "r", errors="replace") as f:
            lines = f.readlines()

        copy_files = set()
        unpack_files = set()
        program_files = set()

        for line in lines:
            line = line.strip()
            if line.startswith("rem") or not line:
                continue

            m = re.match(r"copy\s+(\S+)", line, re.IGNORECASE)
            if m:
                copy_files.add(m.group(1))
                continue

            m = re.match(r"unpack\s+(\S+)", line, re.IGNORECASE)
            if m:
                unpack_files.add(m.group(1))
                continue

            m = re.match(r"program\s+(\S+)", line, re.IGNORECASE)
            if m:
                program_files.add(m.group(1))

        all_refs = copy_files | unpack_files | program_files
        missing = []
        for ref in sorted(all_refs):
            ref_path = os.path.join(vdir, ref)
            if not os.path.exists(ref_path):
                missing.append(ref)

        if missing:
            for m in missing:
                fail(f"{script_name}: referenced file missing: {m}")
            passed = False
        else:
            ok(f"{script_name}: all {len(all_refs)} referenced files exist "
               f"(copy={len(copy_files)}, unpack={len(unpack_files)}, program={len(program_files)})")

    return passed


# ---------------------------------------------------------------------------
# 7. Cross-variant consistency
# ---------------------------------------------------------------------------

def check_cross_variant(iso_path: str) -> bool:
    section("7. Cross-Variant Consistency (Navi 600 vs Navi 900)")
    passed = True

    for fname in sorted(PATCHED_FILES):
        paths = []
        for v in VARIANTS:
            p = os.path.join(iso_path, "dnl", "bin", "system", "adit", v, fname)
            if os.path.exists(p):
                paths.append((v, p))

        if len(paths) < 2:
            warn(f"{fname}: only found in {len(paths)} variant(s)")
            continue

        with open(paths[0][1], "rb") as f:
            data0 = f.read()
        with open(paths[1][1], "rb") as f:
            data1 = f.read()

        if data0 == data1:
            ok(f"{fname}: byte-identical across {paths[0][0]} and {paths[1][0]} ({len(data0):,} bytes)")
        else:
            fail(f"{fname}: differs between {paths[0][0]} and {paths[1][0]}")
            passed = False

    return passed


# ---------------------------------------------------------------------------
# Helpers for ISO mounting
# ---------------------------------------------------------------------------

def mount_iso(iso_path: str) -> str:
    mnt = tempfile.mkdtemp(prefix="iso_verify_")
    r = subprocess.run(
        ["hdiutil", "attach", iso_path, "-readonly", "-mountpoint", mnt],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to mount {iso_path}: {r.stderr}")
    return mnt


def unmount_iso(mnt: str):
    subprocess.run(["hdiutil", "detach", mnt], capture_output=True)
    try:
        os.rmdir(mnt)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patched-iso", required=True, help="Path to patched ISO")
    ap.add_argument("--original-iso", required=True, help="Path to original ISO")
    ap.add_argument("--nand-size", type=int, default=64 * 1024 * 1024,
                    help="NAND flash size in bytes (default: 64 MB)")
    args = ap.parse_args()

    global failures
    failures = 0

    print(f"\n{SECT}{'#' * 70}")
    print(f"  Opel NAVI600/900 Patched Firmware ISO Verification Suite")
    print(f"{'#' * 70}{RESET}")
    info(f"Patched ISO:  {args.patched_iso}")
    info(f"Original ISO: {args.original_iso}")

    patched_mnt = mount_iso(args.patched_iso)
    original_mnt = mount_iso(args.original_iso)

    try:
        for variant in VARIANTS:
            orig_dir = os.path.join(original_mnt, "dnl", "bin", "system", "adit", variant)
            patch_dir = os.path.join(patched_mnt, "dnl", "bin", "system", "adit", variant)

            if not os.path.isdir(patch_dir):
                fail(f"Variant {variant} missing from patched ISO")
                continue

            for fname in sorted(PATCHED_FILES):
                orig_xozl = read_xozl(os.path.join(orig_dir, fname))
                patch_xozl = read_xozl(os.path.join(patch_dir, fname))

                orig_elf = decompress_xozl(orig_xozl)
                patch_elf = decompress_xozl(patch_xozl)

                # --- Test 1: ELF integrity ---
                check_elf_integrity(patch_elf, f"{variant}/{fname}")

                # --- Test 2: Binary diff ---
                check_binary_diff(orig_elf, patch_elf, fname)

                # --- Test 3: XOZL header comparison ---
                check_xozl_headers(patch_xozl, orig_xozl, f"{variant}/{fname}")

            # --- Test 4: verify all simulation ---
            check_verify_all(patched_mnt, variant)

            # --- Test 5: NAND capacity ---
            check_nand_capacity(patched_mnt, variant, args.nand_size)

            # --- Test 6: Installation script dry-run ---
            check_install_script(patched_mnt, variant)

        # --- Test 7: Cross-variant consistency ---
        check_cross_variant(patched_mnt)

    finally:
        unmount_iso(patched_mnt)
        unmount_iso(original_mnt)

    print(f"\n{SECT}{'#' * 70}")
    if failures == 0:
        print(f"  FINAL RESULT: {PASS}  (all checks passed)")
    else:
        print(f"  FINAL RESULT: {FAIL}  ({failures} failure(s))")
    print(f"{'#' * 70}{RESET}\n")

    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
