#!/usr/bin/env python3
"""
Patch ProcHMI.elf to prevent head unit crash on MFi authentication failure (v4).

Problem:
  When an iPhone's MFi authentication fails, the firmware's error handler
  falls through to CALLBACK_EXIT (0x4f0aa0) which accesses the device object
  pointer at 0x10($s1). If the user then unplugs the cable, the USB removal
  handler fires while the coordinator holds stale device state, causing a
  null/stale pointer dereference that reboots the head unit.

  Previous attempts (v3.1, v3.2) tried calling firmware functions from within
  the callback (coordinator reset, DataPool writes). This created inconsistent
  state between the coordinator (reset to idle) and the USB layer (device still
  connected), causing a DIFFERENT crash on cable pull.

Fix (v4 — minimal safe cave, zero function calls):
  Redirects both auth failure handlers to a 6-instruction code cave that:
    1. Sets coordinator state to 0x13 (error/terminal) — a known state that
       the firmware's detach handler handles correctly
    2. Clears the global "already initialized" flag (g1215 at 0x089985F0)
       so the next plug-in gets a fresh initialization
    3. Jumps directly to the function epilog at 0x4f0b9c

  This approach makes ZERO function calls from callback context. The
  coordinator stays in a consistent state: connected=1, valid IAP/device
  pointers, state=error. When the cable is pulled, the detach handler sees
  a connected coordinator in error state with valid pointers and cleans up
  normally.

  What was learned from v3.1/v3.2:
    - function_4ec608 (coordinator reset) clears coordinator+5 (connected
      flag) which prevents Timer 2 from re-triggering init — retry never fires
    - Calling functions from callback context risks leaving the coordinator
      in a state inconsistent with the USB layer, causing crashes on unplug
    - The safest approach is to modify only state fields and return

Patch Sites:
  0x004f0714: event=1 handler -> jump to code cave (replaces 3 instructions)
  0x004f077c: event=2 handler -> jump to code cave (replaces 3 instructions)
  0x009a87a0: code cave (6 instructions / 24 bytes in .fini/.rodata gap)

Usage:
  python3 patch_ipod_auth_retry.py <input.elf> <output.elf>
  python3 patch_ipod_auth_retry.py --verify <patched.elf>
"""

from __future__ import annotations
import argparse
import struct
from pathlib import Path


R = {
    "zero": 0, "v0": 2, "v1": 3, "a0": 4, "a1": 5, "a2": 6, "a3": 7,
    "s0": 16, "s1": 17, "s2": 18, "s3": 19, "s4": 20, "sp": 29, "ra": 31,
}


def _i(op, rs, rt, imm):
    return struct.pack("<I", (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF))

def _j(op, target):
    return struct.pack("<I", (op << 26) | ((target >> 2) & 0x03FFFFFF))

def _r(op, rs, rt, rd, sa, func):
    return struct.pack("<I", (op << 26) | (rs << 21) | (rt << 16) | (rd << 11) | (sa << 5) | func)

def LBU(rt, off, base):    return _i(0x24, R[base], R[rt], off)
def SB(rt, off, base):     return _i(0x28, R[base], R[rt], off)
def SW(rt, off, base):     return _i(0x2B, R[base], R[rt], off)
def LW(rt, off, base):     return _i(0x23, R[base], R[rt], off)
def ADDIU(rt, rs, imm):    return _i(0x09, R[rs], R[rt], imm)
def SLTIU(rt, rs, imm):    return _i(0x0B, R[rs], R[rt], imm)
def LUI(rt, imm):          return _i(0x0F, 0, R[rt], imm)
def BEQZ(rs, offset):      return _i(0x04, R[rs], 0, offset)
def BNEZ(rs, offset):      return _i(0x05, R[rs], 0, offset)
def J(target):             return _j(0x02, target)
def JAL(target):           return _j(0x03, target)
def NOP():                 return struct.pack("<I", 0)
def MOVE(rd, rs):          return _r(0, R[rs], 0, R[rd], 0, 0x21)


CODE_CAVE       = 0x009A87A0
PATCH1_ADDR     = 0x004F0714
PATCH2_ADDR     = 0x004F077C
EPILOG_RETURN   = 0x004F0B9C  # register restore + jr $ra + sp cleanup
INIT_FLAG_ADDR  = 0x089985F0  # "already initialized" global flag (g1215)
ERROR_STATE     = 0x13        # coordinator state machine error state

ORIGINAL_PATCH1 = bytes.fromhex("6000228e1300422805004050")
ORIGINAL_PATCH2 = bytes.fromhex("6000228e13004228f4ff4050")


def build_cave() -> bytes:
    """Build the v4 code cave — minimal safe error handling, zero function calls.

    Layout (6 instructions, 24 bytes):
      [0] Set v0 = 0x13 (error state)
      [1] Store to coordinator+0x60 (state field)
      [2] Load g1215 address high half
      [3] Clear g1215 (allows fresh init on next plug-in)
      [4] Jump to epilog (register restore + jr $ra)
      [5] Set return value = 1 (delay slot)

    Makes ZERO function calls. The coordinator stays in a consistent state:
    coordinator+5 (connected) is still 1, IAP/device pointers are still valid,
    state = 0x13. When the cable is pulled, the detach handler sees a connected
    coordinator in error state with valid pointers and cleans up normally.
    """
    cave = bytearray()

    hi = (INIT_FLAG_ADDR >> 16) & 0xFFFF
    lo = INIT_FLAG_ADDR & 0xFFFF
    if lo >= 0x8000:
        hi = (hi + 1) & 0xFFFF
        lo = lo - 0x10000

    cave += ADDIU("v0", "zero", ERROR_STATE)  # 0: v0 = 19 (error state)
    cave += SW("v0", 0x60, "s1")              # 1: coordinator state = error
    cave += LUI("v0", hi)                     # 2: g1215 address high
    cave += SB("zero", lo, "v0")              # 3: clear g1215 for next session
    cave += J(EPILOG_RETURN)                   # 4: jump to register restore + return
    cave += ADDIU("v0", "zero", 1)            # 5: delay slot: return value = 1

    return bytes(cave)


def build_jump_patch() -> bytes:
    return J(CODE_CAVE) + NOP() + NOP()


def apply_patch(data: bytes) -> bytes:
    out = bytearray(data)

    if data[PATCH1_ADDR:PATCH1_ADDR + 12] != ORIGINAL_PATCH1:
        raise ValueError(
            f"Patch site 1 (0x{PATCH1_ADDR:08x}) doesn't match expected bytes. "
            "File may already be patched or is the wrong version."
        )
    if data[PATCH2_ADDR:PATCH2_ADDR + 12] != ORIGINAL_PATCH2:
        raise ValueError(
            f"Patch site 2 (0x{PATCH2_ADDR:08x}) doesn't match expected bytes. "
            "File may already be patched or is the wrong version."
        )

    cave_size = len(build_cave())
    cave_region = data[CODE_CAVE:CODE_CAVE + cave_size]
    if any(b != 0 for b in cave_region):
        raise ValueError(
            f"Code cave region (0x{CODE_CAVE:08x}) is not empty. "
            "File may already be patched."
        )

    cave = build_cave()
    jump = build_jump_patch()

    out[CODE_CAVE:CODE_CAVE + len(cave)] = cave
    out[PATCH1_ADDR:PATCH1_ADDR + len(jump)] = jump
    out[PATCH2_ADDR:PATCH2_ADDR + len(jump)] = jump

    return bytes(out)


def verify_patch(data: bytes) -> bool:
    ok = True
    jump = build_jump_patch()
    cave = build_cave()

    if data[PATCH1_ADDR:PATCH1_ADDR + len(jump)] != jump:
        print(f"FAIL: Patch site 1 (0x{PATCH1_ADDR:08x}) not patched correctly")
        ok = False
    else:
        print(f"OK: Patch site 1 (0x{PATCH1_ADDR:08x})")

    if data[PATCH2_ADDR:PATCH2_ADDR + len(jump)] != jump:
        print(f"FAIL: Patch site 2 (0x{PATCH2_ADDR:08x}) not patched correctly")
        ok = False
    else:
        print(f"OK: Patch site 2 (0x{PATCH2_ADDR:08x})")

    if data[CODE_CAVE:CODE_CAVE + len(cave)] != cave:
        print(f"FAIL: Code cave (0x{CODE_CAVE:08x}) not written correctly")
        ok = False
    else:
        print(f"OK: Code cave (0x{CODE_CAVE:08x}) - {len(cave)} bytes")

    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="Input ProcHMI.elf (decompressed)")
    ap.add_argument("output", type=Path, nargs="?", help="Output patched ELF")
    ap.add_argument("--verify", action="store_true", help="Verify an already-patched file")
    args = ap.parse_args()

    data = args.input.read_bytes()

    if args.verify:
        ok = verify_patch(data)
        raise SystemExit(0 if ok else 1)

    if not args.output:
        ap.error("output path required (unless --verify)")

    patched = apply_patch(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)

    diff = sum(1 for a, b in zip(data, patched) if a != b)
    print(f"Patched {diff} bytes, written to {args.output}")
    print(f"  Site 1: 0x{PATCH1_ADDR:08x} (auth CP error -> code cave)")
    print(f"  Site 2: 0x{PATCH2_ADDR:08x} (auth failed -> code cave)")
    print(f"  Cave:   0x{CODE_CAVE:08x} ({len(build_cave())} bytes)")


if __name__ == "__main__":
    main()
