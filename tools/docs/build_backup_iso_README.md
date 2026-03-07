# build_backup_iso.py — NAND Backup ISO Builder

Creates a minimal bootable ISO that copies all known NAND flash contents
from the Opel Navi 600/900 head unit to an inserted USB stick.

## How It Works

The head unit's bootloader can execute batch scripts from an inserted CD.
These scripts support `copy` with absolute source and destination paths,
enabling NAND-to-USB file transfers:

```
copy /dev/nand0/ProcHMI.out /dev/uda/nand_backup/ProcHMI.out
```

This tool generates custom batch scripts that perform only `copy`, `mkdir`,
`echo`, `eject`, and `endfile` commands — **no writes to NAND or NOR flash**.

## ISO Contents

The generated ISO contains only batch scripts — no firmware binaries:

```
dnl/bin/system/adit/g__eeu10/
    sys_dnl.bat       Backup script (NAND → USB copy operations)
    force.sys         Same content (forced execution entry point)
    sys_toc.cfg       Same content (alternate entry point)
    pre_dnl.bat       No-op (prevents NOR flash programming)
dnl/bin/system/adit/g_mpeu10/
    (same 4 files)
```

Both variants (`g__eeu10` for Navi 600, `g_mpeu10` for Navi 900) contain
identical scripts so the ISO works on either model.

### Why pre_dnl.bat is a no-op

The factory `pre_dnl.bat` contains `erase` and `program` commands that
reprogram NOR flash. During a backup, this would be destructive and
unnecessary. The backup ISO replaces it with a safe no-op:

```bat
rem *** NAND Backup Tool - pre_dnl no-op ***
echo BACKUP MODE - SKIPPING PRE-DOWNLOAD
endfile
```

## What Gets Backed Up

The script copies **119 individual files** from `/dev/nand0/` to USB,
organized into categories:

| Category | Count | Examples |
|----------|-------|---------|
| Firmware modules (.out) | 13 | ProcHMI.out, ProcNav.out, sysprogosalio.out |
| Registry files (.reg) | 11 | base.reg, hmiapp.reg, navapp.reg |
| Configuration files | 11 | config.bin, NavMediumAccess.cfg, sys_toc.cfg |
| Runtime data files | 38 | Clock_LMM.dat, trip.bin, replay.cfg |
| UAM detail files | 26 | UAM_ROWDETAIL1.dat ... UAM_ROWDETAIL13BKUP.dat |
| Subdirectory contents | 20 | vdsensor/gpsdata.bin, datapool/DP_NAV, lid/ |

### What is NOT backed up

The batch interpreter has no recursive copy or wildcard support. Files
unpacked from ULI archives at install time (hundreds of individual image
and font files in `images/` and `fonts/` subdirectories) cannot be
enumerated. These assets are regenerable from a standard firmware update
ISO and are not critical for backup.

## Usage

```bash
# Basic: build backup ISO with default settings
python3 tools/build_backup_iso.py --output nand_backup.iso

# Use alternate USB device path
python3 tools/build_backup_iso.py --output nand_backup.iso --usb-path /dev/udb

# Custom backup directory name on USB
python3 tools/build_backup_iso.py --output nand_backup.iso --backup-dir my_nand_dump

# Preview the generated batch script without building an ISO
python3 tools/build_backup_iso.py --print-script

# Keep the staging directory for inspection
python3 tools/build_backup_iso.py --output nand_backup.iso --keep-staging
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--output` | (required) | Output ISO file path |
| `--usb-path` | `/dev/uda` | USB device path on the head unit |
| `--backup-dir` | `nand_backup` | Directory name on USB for backup files |
| `--keep-staging` | off | Preserve the staging directory after build |
| `--print-script` | off | Print generated batch script and exit |

## ISO Parameters

The ISO is built with parameters matching factory firmware ISOs:

- **Volume ID**: `CDROM`
- **System ID**: `Win32`
- **Extensions**: ISO 9660 + Joliet + Rock Ridge
- **Builder**: `mkisofs` (from `cdrtools`)
- **Typical size**: ~450 KB

## Head Unit Procedure

1. Format a USB stick as **FAT32**
2. Burn the ISO to a **CD-R**
3. Insert the USB stick into the head unit
4. Insert the CD into the head unit
5. Trigger the firmware update process (the head unit reads the batch scripts)
6. Wait for the CD to eject (indicates completion)
7. Remove both CD and USB stick
8. Files will be in `<usb>/nand_backup/` (or your custom `--backup-dir`)

## Safety

The backup ISO is designed to be non-destructive:

- `pre_dnl.bat` is a no-op — prevents NOR flash erase/program operations
- `verify off` at the start — prevents abort on missing files on CD
- **No** `delete`, `rmdir`, `erase`, `load_bin`, or `program` commands
- Individual copy failures (file doesn't exist) are silently skipped
- The script only reads from NAND and writes to USB

## Device Paths

| Path | Device |
|------|--------|
| `/dev/nand0` | Internal NAND flash (firmware storage) |
| `/dev/uda` | First USB mass storage device |
| `/dev/udb` | Second USB mass storage device |
| `/dev/ffs/` | Internal flash filesystem |

## Dependencies

- **Python 3.10+**
- **mkisofs** (from `cdrtools`): `brew install cdrtools`
  - Alternatively: `genisoimage` on Linux

## Batch Interpreter Commands Used

| Command | Purpose |
|---------|---------|
| `rem` | Comments |
| `verify off` | Disable abort-on-error |
| `echo` | Progress messages |
| `mkdir` | Create directories on USB |
| `copy` | Copy individual files from NAND to USB |
| `eject` | Eject the CD when done |
| `endfile` | Mark end of script |
