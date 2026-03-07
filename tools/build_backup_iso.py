#!/usr/bin/env python3
"""
Build a NAND backup ISO for the Opel Navi 600/900.

Creates a minimal ISO that, when inserted as a CD into the head unit with
a USB stick connected, copies all known NAND flash contents to the USB stick
via the bootloader's batch interpreter.

The ISO contains only batch scripts — no firmware binaries. The scripts use
absolute-path copy commands (copy /dev/nand0/... /dev/uda/...) to transfer
files from NAND to USB.

Usage:
  python3 build_backup_iso.py --output nand_backup.iso
  python3 build_backup_iso.py --output nand_backup.iso --usb-path /dev/udb
  python3 build_backup_iso.py --output nand_backup.iso --backup-dir nand_dump

Requires: mkisofs (from cdrtools: brew install cdrtools)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VOLUME_ID = "CDROM"
SYSTEM_ID = "Win32"

VARIANTS = ["g__eeu10", "g_mpeu10"]

NAND = "/dev/nand0"

FIRMWARE_MODULES = [
    "SysProg1.out",
    "SysProg2.out",
    "sysprogosalio.out",
    "sysprogcal.out",
    "sysprogperf.out",
    "ProcBase.out",
    "ProcMW.out",
    "ProcHMI.out",
    "ProcMM.out",
    "ProcMP1.out",
    "ProcMap.out",
    "ProcNav.out",
    "ProcSDS.out",
]

REGISTRY_FILES = [
    "BuildVersion.reg",
    "base.reg",
    "hmiapp.reg",
    "map.reg",
    "mmapp.reg",
    "MWapp.reg",
    "mp1app.reg",
    "conf.reg",
    "sdsapp.reg",
    "videoapp.reg",
    "navapp.reg",
]

CONFIG_FILES = [
    "config.bin",
    "resource.bin",
    "NavMediumAccess.cfg",
    "sys_toc.cfg",
    "navdata.cfg",
    "EOLUpdateTrigger.dat",
    "errmemflag.dnl",
    "rul.dnl",
    "display.cfg",
    "tr_class_cnfg.cfg",
    "tr_dev_cnfg.cfg",
]

RUNTIME_DATA_FILES = [
    "tun_LearnMem_lmm.dat",
    "TM_LMM1.dat",
    "DCM_TunerMaster_LMM_d0.dat",
    "DCM_TunerMaster_LMM_d1.dat",
    "DCM_TunerMaster_LMM_d2.dat",
    "Clock_LMM.dat",
    "DnlSrcCARD.set",
    "DnlSrcCd.set",
    "DnlSrcDVD.set",
    "DnlSrcUSB.set",
    "download.off",
    "download.on",
    "FC_SPM_BACKGROUND.dat",
    "FC_SPM_LOG.dat",
    "FC_SPM_WATCHDOG.dat",
    "FC_TmcTuner_LMM.dat",
    "gps_int1.dat",
    "HEATCTRL_LMM.DAT",
    "KBD_LMM.dat",
    "landscape_crrsw.dat",
    "Lightning_LMM.dat",
    "OffsetBase.dat",
    "OffsetRds.dat",
    "ProfileCtrl_LMM.dat",
    "personalised_crrsw.dat",
    "protected_crrsw.dat",
    "replay.bin",
    "replay.cfg",
    "resetsaveram_crrsw.dat",
    "tun_QuartHour_lmm.dat",
    "UAM_LMM.DAT",
    "unprotected_crrsw.dat",
    "VDCLK_LMM.DAT",
    "AllInOne.zip",
    "cdda_lastmode.dat",
    "MP_pers_1.ini",
    "MP_pers_hdd.dat",
    "MP_pers_dvd.dat",
    "MP_pers_usb.dat",
    "trip.bin",
    "trip.cfg",
]

UAM_DETAIL_FILES = [
    f"UAM_ROWDETAIL{i}.dat" for i in range(1, 14)
] + [
    f"UAM_ROWDETAIL{i}BKUP.dat" for i in range(1, 14)
]

SUBDIR_FILES = {
    "vdsensor": [
        "gpsdata.bin",
        "gyrodata.bin",
        "odomdata.bin",
    ],
    "datapool": [
        "DP_CONFIG",
        "DP_EOL",
        "DP_HMIINTERNAL",
        "DP_NAV",
        "DP_TUNER",
    ],
    "lid/imp_data": [
        "lid_id.dat",
    ],
    "lid/imp_data/lid_data": [
        "connect.dat",
        "lid00002.dat",
        "meta0000.dat",
    ],
    "cfg/navi": [
        "loaddec.ini",
        "sentence.snt",
        "tripfile.ini",
    ],
    "china": [
        "elftest.cfg",
    ],
    "sds/ser": [
        "dialogs.sdp",
    ],
}


def generate_backup_script(usb_path: str, backup_dir: str) -> str:
    dst = f"{usb_path}/{backup_dir}"
    lines: list[str] = []

    def emit(line: str = ""):
        lines.append(line)

    def copy(nand_file: str, usb_file: str | None = None):
        if usb_file is None:
            usb_file = nand_file
        emit(f"copy {NAND}/{nand_file} {dst}/{usb_file}")

    emit("rem *** NAND Backup Tool for Opel Navi 600/900 ***")
    emit("rem This script copies all known NAND contents to USB.")
    emit("rem It does NOT modify any data on the head unit.")
    emit("")
    emit("verify off")
    emit("echo START NAND BACKUP")
    emit("")

    emit("rem --- Create directory structure on USB ---")
    emit(f"mkdir {dst}")
    subdirs = sorted(set(
        d for d in SUBDIR_FILES.keys()
    ))
    parents_added: set[str] = set()
    for sd in subdirs:
        parts = sd.split("/")
        for i in range(len(parts)):
            parent = "/".join(parts[:i + 1])
            if parent not in parents_added:
                emit(f"mkdir {dst}/{parent}")
                parents_added.add(parent)
    emit("")

    emit("rem --- Firmware modules ---")
    emit("echo COPYING FIRMWARE MODULES")
    for f in FIRMWARE_MODULES:
        copy(f)
    emit("")

    emit("rem --- Registry files ---")
    emit("echo COPYING REGISTRY FILES")
    for f in REGISTRY_FILES:
        copy(f)
    emit("")

    emit("rem --- Configuration and system files ---")
    emit("echo COPYING CONFIG FILES")
    for f in CONFIG_FILES:
        copy(f)
    emit("")

    emit("rem --- Runtime data files ---")
    emit("echo COPYING DATA FILES")
    for f in RUNTIME_DATA_FILES:
        copy(f)
    emit("")

    emit("rem --- UAM detail files ---")
    for f in UAM_DETAIL_FILES:
        copy(f)
    emit("")

    emit("rem --- Subdirectory contents ---")
    emit("echo COPYING SUBDIRECTORY FILES")
    for sd, files in sorted(SUBDIR_FILES.items()):
        for f in files:
            copy(f"{sd}/{f}")
    emit("")

    emit("echo BACKUP COMPLETE")
    emit("eject")
    emit("echo SUCESSFULLY DONE")
    emit("endfile")
    emit("")

    return "\r\n".join(lines)


def generate_noop_pre_dnl() -> str:
    lines = [
        "rem *** NAND Backup Tool - pre_dnl no-op ***",
        "rem This replaces the NOR flash programmer to prevent damage.",
        "echo BACKUP MODE - SKIPPING PRE-DOWNLOAD",
        "endfile",
        "",
    ]
    return "\r\n".join(lines)


def find_mkisofs() -> str:
    for name in ["mkisofs", "genisoimage"]:
        path = shutil.which(name)
        if path:
            return path
    print("ERROR: mkisofs not found. Install with: brew install cdrtools",
          file=sys.stderr)
    sys.exit(1)


def build_staging(staging: Path, usb_path: str, backup_dir: str) -> dict:
    stats = {"variants": 0, "files": 0}

    for variant in VARIANTS:
        vdir = staging / "dnl" / "bin" / "system" / "adit" / variant
        vdir.mkdir(parents=True, exist_ok=True)

        script = generate_backup_script(usb_path, backup_dir)
        noop = generate_noop_pre_dnl()

        for fname in ["sys_dnl.bat", "force.sys", "sys_toc.cfg"]:
            (vdir / fname).write_text(script, encoding="latin-1")
            stats["files"] += 1

        (vdir / "pre_dnl.bat").write_text(noop, encoding="latin-1")
        stats["files"] += 1

        stats["variants"] += 1

    return stats


def build_iso(mkisofs: str, staging: Path, output: Path) -> bool:
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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--output", type=Path,
                    help="Output ISO file path (required unless --print-script)")
    ap.add_argument("--usb-path", default="/dev/uda",
                    help="USB device path (default: /dev/uda)")
    ap.add_argument("--backup-dir", default="nand_backup",
                    help="Directory name on USB for the backup (default: nand_backup)")
    ap.add_argument("--keep-staging", action="store_true",
                    help="Don't delete the staging directory")
    ap.add_argument("--print-script", action="store_true",
                    help="Print the generated batch script and exit")
    args = ap.parse_args()

    if args.print_script:
        print(generate_backup_script(args.usb_path, args.backup_dir))
        return

    if not args.output:
        ap.error("--output is required (unless --print-script)")

    mkisofs = find_mkisofs()

    print(f"\n{'#' * 70}")
    print(f"  Opel NAVI600/900 NAND Backup ISO Builder")
    print(f"{'#' * 70}")
    print(f"  Output:     {args.output}")
    print(f"  USB path:   {args.usb_path}")
    print(f"  Backup dir: {args.usb_path}/{args.backup_dir}")
    print(f"  Variants:   {', '.join(VARIANTS)}")
    print()

    staging = Path(tempfile.mkdtemp(prefix="nand_backup_iso_"))
    try:
        print("--- Step 1: Generate batch scripts ---")
        stats = build_staging(staging, args.usb_path, args.backup_dir)
        print(f"  Created {stats['files']} files across {stats['variants']} variants")

        total_copies = (
            len(FIRMWARE_MODULES) + len(REGISTRY_FILES) +
            len(CONFIG_FILES) + len(RUNTIME_DATA_FILES) +
            len(UAM_DETAIL_FILES) +
            sum(len(files) for files in SUBDIR_FILES.values())
        )
        print(f"  Batch script copies {total_copies} files from NAND to USB")
        print()

        print("--- Step 2: Build ISO ---")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not build_iso(mkisofs, staging, args.output):
            sys.exit(1)

        iso_size = args.output.stat().st_size
        print(f"  ISO created: {args.output} ({iso_size / 1024:.1f} KB)")
        print()

        print("--- Step 3: Verify ISO metadata ---")
        r = subprocess.run(["isoinfo", "-d", "-i", str(args.output)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if any(k in line for k in ["System id", "Volume id", "Joliet",
                                            "Rock Ridge", "Logical block", "Volume size"]):
                    print(f"  {line}")
        else:
            print("  (isoinfo not available for metadata check)")
        print()

        print(f"{'#' * 70}")
        print(f"  BUILD COMPLETE: {args.output}")
        print(f"{'#' * 70}")
        print()
        print("  Instructions:")
        print("    1. Burn this ISO to a CD-R")
        print("    2. Insert a FAT32-formatted USB stick into the head unit")
        print("    3. Insert the CD into the head unit")
        print("    4. Trigger the firmware update process")
        print(f"    5. Files will be copied to USB:{args.usb_path}/{args.backup_dir}/")
        print("    6. Wait for 'BACKUP COMPLETE' or CD eject")
        print()

    finally:
        if args.keep_staging:
            print(f"  Staging directory kept at: {staging}")
        else:
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
