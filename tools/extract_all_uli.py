#!/usr/bin/env python3
"""Batch-extract all .uli files from the firmware directory."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import uli_tool

FW_DIR = REPO_DIR / "firmware" / "dnl" / "bin" / "system" / "adit" / "g__eeu10"
if not FW_DIR.exists():
    FW_DIR = Path("/Users/martijn/Downloads/OpelFirmware/dnl/bin/system/adit/g__eeu10")

OUT_DIR = REPO_DIR / "assets_extracted"

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    uli_files = sorted(FW_DIR.glob("*.uli"))
    print(f"Found {len(uli_files)} ULI files in {FW_DIR}\n")
    
    for uf in uli_files:
        dest = OUT_DIR / uf.stem
        if dest.exists() and (dest / "_uli_manifest.json").exists():
            print(f"[SKIP] {uf.name} -> already extracted")
            continue
        print(f"[EXTRACT] {uf.name} -> {dest.relative_to(REPO_DIR)}")
        try:
            uli_tool.extract(uf, dest)
        except Exception as e:
            print(f"  ERROR: {e}")
    
    print(f"\nDone. All assets in {OUT_DIR}")
    
    total_files = 0
    for d in sorted(OUT_DIR.iterdir()):
        if d.is_dir():
            files = list(d.rglob("*"))
            files = [f for f in files if f.is_file() and f.name != "_uli_manifest.json"]
            total_files += len(files)
            print(f"  {d.name}: {len(files)} files")
    print(f"  TOTAL: {total_files} asset files")

if __name__ == "__main__":
    main()
