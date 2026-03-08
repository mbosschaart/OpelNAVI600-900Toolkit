#!/usr/bin/env python3
"""
Patch ProcHMI.elf to add automatic retry on MFi authentication failure.

Problem:
  iPhone connection to the Navi600/900 is intermittent. The MFi authentication
  handshake can fail due to timing, requiring up to 10 physical USB reconnects.
  The firmware logs the error but immediately gives up without retrying.

Fix:
  Redirects both auth failure handlers (event=1 "Auth CP Error" and event=2
  "Authentication Failed") in iPodCtrlCoordinator::onMediaDeviceCallback to a
  code cave that:
    1. Maintains a retry counter (up to 5 attempts)
    2. Calls iPod_cmd_disconnect to tear down the failed session cleanly
    3. Clears the "already initialized" flag so the firmware reinits on
       USB re-detection (the device is still physically on the bus)
    4. Transitions the state machine to the error state (0x13) to keep
       the coordinator object consistent
    5. Sets the stack safety flag 0x20($sp) to prevent a dangerous call
       to 0x4e6ee4 with stale pointers
    6. Returns through CALLBACK_EXIT; the firmware detects the still-
       connected USB device, triggers a new attach event, and starts
       a fresh MFi auth session through the proper init code path

  The reconnect is handled entirely by the firmware's own USB detection
  and coordinator init function (0x4ec40c), not by calling iPod_cmd_connect
  from within the callback. This avoids the race conditions and stale-state
  crashes that occurred in the v1 patch.

Patch Sites:
  0x004f0714: event=1 handler -> jump to code cave (replaces 3 instructions)
  0x004f077c: event=2 handler -> jump to code cave (replaces 3 instructions)
  0x009a87a0: code cave (26 instructions in .fini/.rodata gap, was all zeros)

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
RETRY_OFFSET    = 0x5CE       # unused byte in coordinator object
CALLBACK_EXIT   = 0x004F0AA0  # checks 0x20($sp) flag, then epilog
DISCONNECT_FN   = 0x004F5C88  # iPod_cmd_disconnect(IAPInterface*)
INIT_FLAG_ADDR  = 0x089985F0  # "already initialized" global flag
ERROR_STATE     = 0x13        # coordinator state machine error state

ORIGINAL_PATCH1 = bytes.fromhex("6000228e1300422805004050")
ORIGINAL_PATCH2 = bytes.fromhex("6000228e13004228f4ff4050")


def build_cave() -> bytes:
    """Build the v2 code cave — disconnect-only, no busy-wait, stack-safe.

    Layout (26 instructions, 104 bytes):
      [0-3]   Retry check: load counter, compare < 5, branch give-up
      [4-18]  Retry path:  disconnect, clear init flag, set state=19,
              set safety flags, jump CALLBACK_EXIT
      [19-25] Give-up path: reset counter, set state=19, set safety flag,
              jump CALLBACK_EXIT
    """
    cave = bytearray()

    # --- Retry check (instructions 0-3) ---
    cave += LBU("v0", RETRY_OFFSET, "s1")     # 0: load retry counter
    cave += SLTIU("v1", "v0", 5)              # 1: v1 = (counter < 5)
    cave += BEQZ("v1", 16)                    # 2: if >= 5 → give_up @19
    cave += ADDIU("v0", "v0", 1)              # 3: increment (delay slot)

    # --- Retry path (instructions 4-18) ---
    cave += SB("v0", RETRY_OFFSET, "s1")      # 4: save incremented counter
    cave += LW("a0", 0x18, "s1")              # 5: load IAPInterface ptr
    cave += BEQZ("a0", 12)                    # 6: if NULL → give_up @19
    cave += NOP()                              # 7: (delay slot)
    cave += JAL(DISCONNECT_FN)                 # 8: tear down failed session
    cave += NOP()                              # 9: (delay slot)

    hi = (INIT_FLAG_ADDR >> 16) & 0xFFFF
    lo = INIT_FLAG_ADDR & 0xFFFF
    if lo >= 0x8000:
        hi = (hi + 1) & 0xFFFF
        lo = lo - 0x10000
    cave += LUI("v0", hi)                     # 10: upper addr of init flag
    cave += SB("zero", lo, "v0")              # 11: clear init flag

    cave += ADDIU("v0", "zero", ERROR_STATE)  # 12: v0 = 19
    cave += SW("v0", 0x60, "s1")              # 13: coordinator state = 19

    cave += ADDIU("v0", "zero", 1)            # 14: v0 = 1
    cave += SB("v0", 0x20, "sp")              # 15: skip dangerous 4e6ee4 call
    cave += MOVE("s4", "zero")                # 16: suppress error publication
    cave += J(CALLBACK_EXIT)                   # 17: return through safe exit
    cave += NOP()                              # 18: (delay slot)

    # --- Give-up path (instructions 19-25) ---
    cave += SB("zero", RETRY_OFFSET, "s1")    # 19: reset retry counter
    cave += ADDIU("v0", "zero", ERROR_STATE)  # 20: v0 = 19
    cave += SW("v0", 0x60, "s1")              # 21: coordinator state = 19
    cave += ADDIU("v0", "zero", 1)            # 22: v0 = 1
    cave += SB("v0", 0x20, "sp")              # 23: skip 4e6ee4 even on give-up
    cave += J(CALLBACK_EXIT)                   # 24: normal exit path
    cave += MOVE("s4", "zero")                # 25: suppress publish (delay slot)

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

    cave_region = data[CODE_CAVE:CODE_CAVE + 128]
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
