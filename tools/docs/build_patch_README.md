# build_patch.sh — End-to-End Patch Builder

Shell script that automates the full iPod MFi authentication retry patch
pipeline: extract, patch, verify, repack, and validate — in a single command.

## What It Does

```
ProcHMI.out (original XOZL)
    │
    ▼  Step 1: xozl_tool.py extract
ProcHMI.elf (decompressed)
    │
    ▼  Step 2: patch_ipod_auth_retry.py (apply)
ProcHMI_patched.elf
    │
    ▼  Step 3: patch_ipod_auth_retry.py --verify
    │  (confirms patch bytes are correct)
    │
    ▼  Step 4: xozl_tool.py pack --ref
ProcHMI_patched.out (repackaged XOZL)
    │
    ▼  Step 5: validate_xozl.py --elf --ref
    │  (full XOZL validation suite)
    │
    Done → build/ProcHMI_patched.out
```

## Usage

```bash
# From the repository root
bash tools/build_patch.sh /path/to/original/ProcHMI.out
```

If no argument is given, defaults to
`firmware/dnl/bin/system/adit/g__eeu10/ProcHMI.out` relative to the
repository root.

### Output

All intermediate and final files are placed in `build/`:

```
build/
├── ProcHMI.elf                Decompressed original
├── ProcHMI_patched.elf        Patched ELF (verified)
└── ProcHMI_patched.out        Final XOZL (ready to deploy)
```

## Deployment

After the build completes:

1. Copy `build/ProcHMI_patched.out` as `ProcHMI.out` into both variant
   directories (`g__eeu10` and `g_mpeu10`) in your firmware update tree
2. Build the ISO: `mkisofs -o patched.iso -V CDROM -sysid Win32 -J -R -l staging/`
3. Optionally run `verify_patched_iso.py` for full ISO-level verification
4. Burn or mount the ISO and update the head unit via USB

## Dependencies

- Python 3.10+
- `python-lzo` (`pip install python-lzo`)
- All toolkit scripts in the `tools/` directory

## Error Handling

The script uses `set -eu` — it will abort immediately on any error:
- If the source `.out` file doesn't exist
- If XOZL extraction fails (corrupted file, wrong format)
- If the patch tool detects wrong firmware version
- If the code cave is already occupied (already patched)
- If XOZL validation fails after repacking

Each step prints its own diagnostic output, so the failure point is clear.
