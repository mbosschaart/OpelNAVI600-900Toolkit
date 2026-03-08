#!/usr/bin/env python3
"""
Patch ProcHMI.elf for MFi authentication retry with crash-safe coordinator
reset (v3.1).

Problem:
  When an iPhone's MFi authentication fails, the firmware gives up without
  retrying. Additionally, the error handler's epilog accesses device object
  pointers that become stale if the cable is unplugged, crashing the head unit.

Fix (v3.1 — coordinator reset + retry):
  Redirects both auth failure handlers (event=1 "Auth CP Error" and event=2
  "Authentication Failed") in iPodCtrlCoordinator::onMediaDeviceCallback to a
  code cave that:

  Retry path (counter < 3):
    1. Increments a retry counter at coordinator+0x5CE
    2. Calls function_4ec608 (the firmware's own coordinator reset function,
       used during detach/undervoltage recovery) — properly clears state,
       connected flag, deck status, and all internal fields
    3. Clears the global "already initialized" flag (g1215 at 0x089985F0)
    4. Jumps directly to the function epilog at 0x4f0b9c

    The coordinator is now in a clean idle state (state=0, connected=0).
    The firmware's timer chain (Timer 2 @ 2s / Timer 1 @ 5s) re-detects
    the still-connected USB device, triggers fresh initialization through
    iPodCtrlCoordinator::initialize, and attempts MFi auth again.

  Give-up path (counter >= 3):
    1. Resets the retry counter (ready for next device session)
    2. Sets coordinator state to 0x13 (error/terminal)
    3. Jumps directly to the function epilog

  All paths bypass CALLBACK_EXIT entirely — no device pointer is ever
  accessed, preventing the stale-pointer crash on cable disconnect.

Patch Sites:
  0x004f0714: event=1 handler -> jump to code cave (replaces 3 instructions)
  0x004f077c: event=2 handler -> jump to code cave (replaces 3 instructions)
  0x009a87a0: code cave (16 instructions / 64 bytes in .fini/.rodata gap)

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
COORD_RESET_FN  = 0x004EC608  # firmware's own coordinator reset function
INIT_FLAG_ADDR  = 0x089985F0  # "already initialized" global flag (g1215)
RETRY_OFFSET    = 0x5CE       # unused byte in coordinator object
ERROR_STATE     = 0x13        # coordinator state machine error state
MAX_RETRIES     = 3           # auth attempts before giving up

ORIGINAL_PATCH1 = bytes.fromhex("6000228e1300422805004050")
ORIGINAL_PATCH2 = bytes.fromhex("6000228e13004228f4ff4050")


def build_cave() -> bytes:
    """Build the v3.1 code cave — coordinator reset + retry.

    Layout (16 instructions, 64 bytes):
      [0-3]   Retry check: load counter, compare < MAX_RETRIES, branch
      [4-10]  Retry path:  increment counter, call coordinator reset,
              clear global init flag, jump to epilog
      [11-15] Give-up path: reset counter, set state=19, jump to epilog

    Calls the firmware's own coordinator reset function (0x4ec608) which
    properly clears all internal fields (state, connected flag, deck status,
    etc.) — the same function used during iPod detach and undervoltage recovery.

    Does NOT call iPod_cmd_disconnect — avoids tearing down the device object.
    Bypasses CALLBACK_EXIT entirely — no code touches the device pointer.
    """
    cave = bytearray()

    hi = (INIT_FLAG_ADDR >> 16) & 0xFFFF
    lo = INIT_FLAG_ADDR & 0xFFFF
    if lo >= 0x8000:
        hi = (hi + 1) & 0xFFFF
        lo = lo - 0x10000

    # --- Retry check (instructions 0-3) ---
    cave += LBU("v0", RETRY_OFFSET, "s1")     # 0: load retry counter
    cave += SLTIU("v1", "v0", MAX_RETRIES)    # 1: v1 = (counter < 3)
    cave += BEQZ("v1", 8)                     # 2: if >= 3 → give_up @11
    cave += ADDIU("v0", "v0", 1)              # 3: increment (delay slot)

    # --- Retry path (instructions 4-10) ---
    cave += SB("v0", RETRY_OFFSET, "s1")      # 4: save incremented counter
    cave += JAL(COORD_RESET_FN)                # 5: call firmware's coordinator reset
    cave += MOVE("a0", "s1")                   # 6: (delay slot) coordinator ptr as arg
    cave += LUI("v0", hi)                     # 7: upper addr of init flag
    cave += SB("zero", lo, "v0")              # 8: clear g1215 → allows fresh init
    cave += J(EPILOG_RETURN)                   # 9: jump to register restore + return
    cave += ADDIU("v0", "zero", 1)            # 10: delay slot: return value = 1

    # --- Give-up path (instructions 11-15) ---
    cave += SB("zero", RETRY_OFFSET, "s1")    # 11: reset counter for next session
    cave += ADDIU("v0", "zero", ERROR_STATE)  # 12: v0 = 19
    cave += SW("v0", 0x60, "s1")              # 13: coordinator state = error
    cave += J(EPILOG_RETURN)                   # 14: jump to register restore + return
    cave += ADDIU("v0", "zero", 1)            # 15: delay slot: return value = 1

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
