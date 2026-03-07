#!/usr/bin/env python3
"""
Build a firmware update ISO for the Opel Navi 600/900 from a source directory.

Creates an ISO 9660 image that is structurally identical to the factory
original: same volume/system IDs, same extensions (Joliet + Rock Ridge),
same variant layout, and same file set per variant. Optionally substitutes
patched .out files before building.

Usage:
  # Basic: build ISO from the dnl firmware tree
  python3 build_iso.py --source /path/to/dnl --output patched.iso

  # With patched file substitution
  python3 build_iso.py --source /path/to/dnl --output patched.iso \
      --replace ProcHMI.out=ProcHMI_patched.out \
      --replace sysprogosalio.out=sysprogosalio_patched.out

  # With automatic verification against the original
  python3 build_iso.py --source /path/to/dnl --output patched.iso \
      --replace ProcHMI.out=ProcHMI_patched.out \
      --original-iso original.iso --verify

Requires: mkisofs (from cdrtools: brew install cdrtools)
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

VOLUME_ID = "CDROM"
SYSTEM_ID = "Win32"

ISO_VARIANTS = ["g__eeu10", "g_mpeu10"]

EXCLUDED_NAMES = {".DS_Store", "._.DS_Store", "Thumbs.db", "desktop.ini"}
EXCLUDED_DIRS = {"_elf", "__MACOSX", ".git"}


def find_mkisofs() -> str:
    for name in ["mkisofs", "genisoimage"]:
        path = shutil.which(name)
        if path:
            return path
    print("ERROR: mkisofs not found. Install with: brew install cdrtools", file=sys.stderr)
    sys.exit(1)


def get_variant_files(variant_dir: Path) -> list[str]:
    """Return the sorted list of regular files in a variant directory,
    excluding hidden/system files and subdirectories like _elf."""
    files = []
    for entry in sorted(variant_dir.iterdir()):
        if entry.name in EXCLUDED_NAMES:
            continue
        if entry.name in EXCLUDED_DIRS:
            continue
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            files.append(entry.name)
    return files


def build_staging(source_dnl: Path, staging: Path,
                  replacements: dict[str, Path],
                  variants: list[str]) -> dict:
    """Build the staging directory tree for ISO creation.

    Returns a dict with statistics about the build.
    """
    stats = {"variants": 0, "files_copied": 0, "files_replaced": 0, "total_bytes": 0}

    adit_src = source_dnl / "bin" / "system" / "adit"
    if not adit_src.is_dir():
        print(f"ERROR: Source directory not found: {adit_src}", file=sys.stderr)
        sys.exit(1)

    adit_dst = staging / "dnl" / "bin" / "system" / "adit"

    for variant in variants:
        vsrc = adit_src / variant
        if not vsrc.is_dir():
            print(f"WARNING: Variant {variant} not found in source, skipping")
            continue

        vdst = adit_dst / variant
        vdst.mkdir(parents=True, exist_ok=True)

        files = get_variant_files(vsrc)
        stats["variants"] += 1

        for fname in files:
            src_file = vsrc / fname

            if fname in replacements:
                rep_path = replacements[fname]
                if not rep_path.is_file():
                    print(f"ERROR: Replacement file not found: {rep_path}", file=sys.stderr)
                    sys.exit(1)
                shutil.copy2(rep_path, vdst / fname)
                stats["files_replaced"] += 1
            else:
                shutil.copy2(src_file, vdst / fname)

            stats["files_copied"] += 1
            stats["total_bytes"] += (vdst / fname).stat().st_size

    return stats


def verify_staging(staging: Path, original_iso: Path | None) -> bool:
    """Verify the staging directory matches expected structure."""
    ok = True

    adit = staging / "dnl" / "bin" / "system" / "adit"
    for variant in ISO_VARIANTS:
        vdir = adit / variant
        if not vdir.is_dir():
            print(f"  WARN: Variant {variant} missing from staging")
            continue

        for entry in vdir.iterdir():
            if entry.name in EXCLUDED_NAMES or entry.name.startswith("."):
                print(f"  FAIL: Unwanted file in staging: {variant}/{entry.name}")
                ok = False
            if entry.is_dir():
                print(f"  FAIL: Subdirectory in staging: {variant}/{entry.name}")
                ok = False

        out_files = [f for f in vdir.iterdir() if f.suffix == ".out"]
        for out_file in out_files:
            with open(out_file, "rb") as f:
                magic = f.read(4)
            if magic == b"XOZL":
                with open(out_file, "rb") as f:
                    hdr = f.read(0x24)
                comp_size = struct.unpack_from("<I", hdr, 0x1C)[0]
                hdr_len = struct.unpack_from("<I", hdr, 0x14)[0]
                file_size = out_file.stat().st_size
                if file_size < hdr_len + comp_size:
                    print(f"  FAIL: {variant}/{out_file.name} truncated "
                          f"({file_size} < {hdr_len + comp_size})")
                    ok = False
            elif magic == b"\x7fELF":
                pass
            else:
                pass

    if original_iso and original_iso.exists():
        mnt = tempfile.mkdtemp(prefix="iso_ref_")
        try:
            r = subprocess.run(
                ["hdiutil", "attach", str(original_iso), "-readonly", "-mountpoint", mnt],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                for variant in ISO_VARIANTS:
                    orig_dir = Path(mnt) / "dnl" / "bin" / "system" / "adit" / variant
                    stage_dir = adit / variant
                    if not orig_dir.is_dir() or not stage_dir.is_dir():
                        continue

                    orig_files = set(f.name for f in orig_dir.iterdir() if f.is_file())
                    stage_files = set(f.name for f in stage_dir.iterdir() if f.is_file())

                    missing = orig_files - stage_files
                    extra = stage_files - orig_files

                    if missing:
                        print(f"  FAIL: {variant}: files in original but not in staging: {missing}")
                        ok = False
                    if extra:
                        print(f"  WARN: {variant}: extra files not in original: {extra}")
            else:
                print(f"  WARN: Could not mount original ISO for comparison")
        finally:
            subprocess.run(["hdiutil", "detach", mnt], capture_output=True)
            try:
                os.rmdir(mnt)
            except OSError:
                pass

    return ok


def build_iso(mkisofs: str, staging: Path, output: Path) -> bool:
    """Run mkisofs to create the ISO."""
    cmd = [
        mkisofs,
        "-o", str(output),
        "-V", VOLUME_ID,
        "-sysid", SYSTEM_ID,
        "-J",
        "-R",
        "-l",
        "-quiet",
        str(staging),
    ]

    print(f"  Running: {' '.join(cmd[:8])} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)

    if r.returncode != 0:
        print(f"  ERROR: mkisofs failed (exit {r.returncode})")
        if r.stderr:
            for line in r.stderr.strip().split("\n")[:10]:
                print(f"    {line}")
        return False

    return True


def run_verify(script_dir: Path, output_iso: Path, original_iso: Path) -> bool:
    """Run verify_patched_iso.py if available."""
    verify_script = script_dir / "verify_patched_iso.py"
    if not verify_script.exists():
        print("  WARN: verify_patched_iso.py not found, skipping verification")
        return True

    print(f"\n{'=' * 70}")
    print("  Running full ISO verification suite...")
    print(f"{'=' * 70}")

    r = subprocess.run(
        [sys.executable, str(verify_script),
         "--patched-iso", str(output_iso),
         "--original-iso", str(original_iso)],
    )
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--source", required=True, type=Path,
                    help="Path to the dnl firmware directory (contains bin/system/adit/)")
    ap.add_argument("--output", required=True, type=Path,
                    help="Output ISO file path")
    ap.add_argument("--replace", action="append", default=[], metavar="NAME=PATH",
                    help="Replace a file in all variants: e.g. ProcHMI.out=/path/to/patched.out "
                         "(can be specified multiple times)")
    ap.add_argument("--variants", nargs="+", default=ISO_VARIANTS,
                    help=f"Variant directories to include (default: {' '.join(ISO_VARIANTS)})")
    ap.add_argument("--original-iso", type=Path, default=None,
                    help="Original ISO for file list comparison during staging validation")
    ap.add_argument("--verify", action="store_true",
                    help="Run verify_patched_iso.py after building (requires --original-iso)")
    ap.add_argument("--keep-staging", action="store_true",
                    help="Don't delete the staging directory after building")
    args = ap.parse_args()

    replacements: dict[str, Path] = {}
    for spec in args.replace:
        if "=" not in spec:
            ap.error(f"Invalid --replace format: {spec} (expected NAME=PATH)")
        name, path_str = spec.split("=", 1)
        replacements[name] = Path(path_str)

    if args.verify and not args.original_iso:
        ap.error("--verify requires --original-iso")

    mkisofs = find_mkisofs()

    print(f"\n{'#' * 70}")
    print(f"  Opel NAVI600/900 Firmware ISO Builder")
    print(f"{'#' * 70}")
    print(f"  Source:   {args.source}")
    print(f"  Output:   {args.output}")
    print(f"  Variants: {', '.join(args.variants)}")
    if replacements:
        print(f"  Replacements:")
        for name, path in sorted(replacements.items()):
            print(f"    {name} <- {path}")
    print()

    staging = Path(tempfile.mkdtemp(prefix="iso_build_"))
    try:
        print("--- Step 1: Build staging directory ---")
        stats = build_staging(args.source, staging, replacements, args.variants)
        print(f"  Staged {stats['variants']} variant(s), "
              f"{stats['files_copied']} files "
              f"({stats['files_replaced']} replaced), "
              f"{stats['total_bytes'] / 1024 / 1024:.1f} MB total")
        print()

        print("--- Step 2: Validate staging ---")
        staging_ok = verify_staging(staging, args.original_iso)
        if staging_ok:
            print("  Staging validation: PASS")
        else:
            print("  Staging validation: FAIL (see warnings above)")
            print("  Proceeding anyway — review the warnings.")
        print()

        print("--- Step 3: Build ISO ---")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not build_iso(mkisofs, staging, args.output):
            sys.exit(1)

        iso_size = args.output.stat().st_size
        print(f"  ISO created: {args.output} ({iso_size / 1024 / 1024:.1f} MB)")
        print()

        print("--- Step 4: ISO metadata verification ---")
        r = subprocess.run(["isoinfo", "-d", "-i", str(args.output)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if any(k in line for k in ["System id", "Volume id", "Joliet", "Rock Ridge",
                                            "Logical block", "Volume size"]):
                    check = "PASS" if any([
                        "System id: Win32" in line,
                        "Volume id: CDROM" in line,
                        "Joliet" in line,
                        "Rock Ridge" in line,
                        "Logical block size is: 2048" in line,
                        "Volume size" in line,
                    ]) else "INFO"
                    print(f"  [{check}] {line}")
        else:
            print("  WARN: isoinfo not available for metadata check")
        print()

        if args.verify:
            print("--- Step 5: Full verification ---")
            if not run_verify(Path(__file__).resolve().parent, args.output, args.original_iso):
                print("\nVerification FAILED.")
                sys.exit(1)
            print()

        print(f"{'#' * 70}")
        print(f"  BUILD COMPLETE: {args.output}")
        print(f"{'#' * 70}\n")

    finally:
        if args.keep_staging:
            print(f"  Staging directory kept at: {staging}")
        else:
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
