# build_iso.py — Firmware ISO Builder

Builds a firmware update ISO for the Opel Navi 600/900 from a source directory.
The output is structurally identical to the factory original: same volume/system
IDs, same filesystem extensions (ISO 9660 + Joliet + Rock Ridge), same variant
layout, and same file set per variant.

## What It Does

1. **Stages** the firmware tree — copies only the correct variants (`g__eeu10`,
   `g_mpeu10`) from the source `dnl` directory, excluding `.DS_Store`, `_elf`,
   and other non-firmware artifacts
2. **Substitutes** patched `.out` files (when `--replace` is given) into all
   variants
3. **Validates** the staging directory — checks XOZL file integrity and
   compares the file list against the original ISO (when `--original-iso` is
   given)
4. **Builds** the ISO using `mkisofs` with the exact same parameters as the
   factory original
5. **Verifies** the ISO metadata (volume ID, system ID, Joliet, Rock Ridge)
6. Optionally runs the full `verify_patched_iso.py` verification suite

## Usage

### Build a patched ISO

```bash
python3 tools/build_iso.py \
    --source /path/to/dnl \
    --output "Patched Firmware Isos/NAVI600_900 v2.08_patched.iso" \
    --replace ProcHMI.out=ProcHMI_patched.out \
    --replace sysprogosalio.out=sysprogosalio_patched.out
```

### Build with verification against original

```bash
python3 tools/build_iso.py \
    --source /path/to/dnl \
    --output "Patched Firmware Isos/NAVI600_900 v2.08_patched.iso" \
    --replace ProcHMI.out=ProcHMI_patched.out \
    --replace sysprogosalio.out=sysprogosalio_patched.out \
    --original-iso "Original Firmware Isos/NAVI600_900 v2.08.iso" \
    --verify
```

### Build an unmodified ISO (for testing)

```bash
python3 tools/build_iso.py \
    --source /path/to/dnl \
    --output test.iso \
    --original-iso "Original Firmware Isos/NAVI600_900 v2.08.iso"
```

## Options

| Flag | Required | Description |
|------|----------|-------------|
| `--source PATH` | Yes | Path to the `dnl` firmware directory (must contain `bin/system/adit/`) |
| `--output PATH` | Yes | Output ISO file path |
| `--replace NAME=PATH` | No | Replace a file in all variants. Can be specified multiple times. |
| `--variants V1 V2 ...` | No | Override which variants to include (default: `g__eeu10 g_mpeu10`) |
| `--original-iso PATH` | No | Original ISO for file list comparison during staging |
| `--verify` | No | Run `verify_patched_iso.py` after building (requires `--original-iso`) |
| `--keep-staging` | No | Don't delete the staging directory after building |

## ISO Format

The following parameters are hardcoded to match the factory original:

| Parameter | Value | Notes |
|-----------|-------|-------|
| Filesystem | ISO 9660 | Standard CD-ROM filesystem |
| Volume ID | `CDROM` | Checked by some Bosch bootloaders |
| System ID | `Win32` | Matches original (built on Windows) |
| Joliet | UCS Level 3 | Long filename support |
| Rock Ridge | RRIP_1991A | Unix permission/ownership extensions |
| Block size | 2048 bytes | Standard |

## Staging Validation

When `--original-iso` is provided, the tool compares the file list in each
variant directory against the original ISO and reports:

- **Missing files**: present in original but not in staging (likely an error)
- **Extra files**: present in staging but not in original (may be intentional)

The tool also performs basic XOZL integrity checks on all `.out` files:
verifies the XOZL magic bytes and confirms the compressed payload size is
consistent with the file size.

## Dependencies

- Python 3.10+
- `mkisofs` (from cdrtools: `brew install cdrtools` on macOS)
- `isoinfo` (from cdrtools, used for metadata verification — optional)
- `hdiutil` (macOS built-in, used for original ISO comparison — optional)

## Output Example

```
######################################################################
  Opel NAVI600/900 Firmware ISO Builder
######################################################################
  Source:   dnl
  Output:   Patched Firmware Isos/NAVI600_900 v2.08_patched.iso
  Variants: g__eeu10, g_mpeu10
  Replacements:
    ProcHMI.out <- ProcHMI_patched.out
    sysprogosalio.out <- sysprogosalio_patched.out

--- Step 1: Build staging directory ---
  Staged 2 variant(s), 112 files (4 replaced), 108.3 MB total

--- Step 2: Validate staging ---
  Staging validation: PASS

--- Step 3: Build ISO ---
  ISO created: NAVI600_900 v2.08_patched.iso (108.8 MB)

--- Step 4: ISO metadata verification ---
  [PASS] System id: Win32
  [PASS] Volume id: CDROM
  [PASS] Logical block size is: 2048
  [PASS] Joliet with UCS level 3 found.
  [PASS] Rock Ridge signatures version 1 found

######################################################################
  BUILD COMPLETE: NAVI600_900 v2.08_patched.iso
######################################################################
```
