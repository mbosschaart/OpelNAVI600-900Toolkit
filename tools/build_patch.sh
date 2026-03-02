#!/bin/bash
#
# End-to-end build: original ProcHMI.out → patched ProcHMI.out
#
# Applies the iPod MFi auth retry patch to ProcHMI for Navi 600 (g__eeu10).
# Requires: python3, python-lzo (pip install python-lzo)
#
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ORIG_OUT="${1:-$REPO_DIR/firmware/dnl/bin/system/adit/g__eeu10/ProcHMI.out}"
BUILD_DIR="$REPO_DIR/build"

echo "=== iPod Auth Retry Patch Builder ==="
echo "Source: $ORIG_OUT"
echo "Build:  $BUILD_DIR"
echo

mkdir -p "$BUILD_DIR"

echo "--- Step 1: Extract (XOZL → ELF) ---"
python3 "$SCRIPT_DIR/xozl_tool.py" extract "$ORIG_OUT" "$BUILD_DIR/ProcHMI.elf"
echo

echo "--- Step 2: Patch (ELF → patched ELF) ---"
python3 "$SCRIPT_DIR/patch_ipod_auth_retry.py" "$BUILD_DIR/ProcHMI.elf" "$BUILD_DIR/ProcHMI_patched.elf"
echo

echo "--- Step 3: Verify patch ---"
python3 "$SCRIPT_DIR/patch_ipod_auth_retry.py" --verify "$BUILD_DIR/ProcHMI_patched.elf"
echo

echo "--- Step 4: Pack (patched ELF → XOZL) ---"
python3 "$SCRIPT_DIR/xozl_tool.py" pack "$BUILD_DIR/ProcHMI_patched.elf" "$BUILD_DIR/ProcHMI_patched.out" --ref "$ORIG_OUT"
echo

echo "--- Step 5: Validate ---"
python3 "$SCRIPT_DIR/validate_xozl.py" "$BUILD_DIR/ProcHMI_patched.out" --elf "$BUILD_DIR/ProcHMI_patched.elf" --ref "$ORIG_OUT"

echo "=== Done ==="
echo "Deploy: copy $BUILD_DIR/ProcHMI_patched.out as ProcHMI.out onto the update media"
