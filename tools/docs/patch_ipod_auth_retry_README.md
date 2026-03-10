# patch_ipod_auth_retry.py — iPod/iPhone MFi Auth Crash Prevention (v4)

Binary patch for `ProcHMI.elf` that prevents head unit crashes when MFi
authentication fails on the Opel Navi 600/900 (firmware v2.08).

## Background

The Navi 600/900 uses iAP1 (iPod Accessory Protocol version 1) with an
MFi 1.0 authentication chip (CP20) to communicate with iPhones over USB.
When MFi authentication fails (event 1 "Auth CP Error" or event 2
"Authentication Failed"), the firmware's error handler falls through to
`CALLBACK_EXIT` at `0x4F0AA0`, which accesses device object pointers at
`0x10($s1)`. If the user physically disconnects the cable while the
coordinator holds this stale device state, the USB removal handler hits
stale/null pointers and **crashes the head unit**.

### iPhone compatibility

| Device | Protocol Support | Expected Behavior |
|--------|-----------------|-------------------|
| iPhone 7 and earlier (Lightning) | iAP1 supported | Auth may succeed on first or second plug |
| iPhone 13 era (Lightning) | iAP1 reduced | Auth less likely to succeed |
| iPhone 15+ (USB-C) | iAP1 likely dropped | Auth will fail, unit will not crash |

## What the Patch Does (v4)

Injects a **6-instruction (24-byte)** code cave and redirects both auth
failure handlers to it. The cave:

1. Sets coordinator state to `0x13` (error/terminal) — a known state that
   the firmware's detach handler handles correctly
2. Clears the global "already initialized" flag (`g1215` at `0x089985F0`)
   so the next plug-in gets a fresh initialization
3. Jumps directly to the function epilog at `0x4F0B9C` (register restore
   + `jr $ra` + stack cleanup)

This approach makes **zero function calls** from callback context. The
coordinator stays in a consistent state: `coordinator+5` (connected) is
still 1, IAP/device pointers are still valid, `state` = error. When the
cable is pulled, the detach handler sees a connected coordinator in error
state with valid pointers and cleans up normally.

### What was learned from v3.1/v3.2

| Version | Approach | Why it failed |
|---------|----------|--------------|
| v3.2 | Coordinator reset + DataPool writes + retry | `function_4ec608` clears `coordinator+5` (connected=0) but leaves IAP/device pointers set, creating inconsistent state. USB removal handler crashes. Timer 2 re-init skipped because connected=0. Retry never fires. |
| v3.1 | Coordinator reset + retry (no HMI) | Same root cause as v3.2 — coordinator reset from callback context is unsafe |
| v2 | `iPod_cmd_disconnect` + retry + `CALLBACK_EXIT` | Disconnect tears down device object → stale pointer in `CALLBACK_EXIT` |
| v1 | `iPod_cmd_connect` + busy-wait + `CALLBACK_EXIT` | Crash + stale state |

**Key insight**: calling any firmware function that modifies coordinator/device
state from within `onMediaDeviceCallback` leaves the coordinator inconsistent
with the USB layer. The only safe approach is to modify state fields directly
(no function calls) and return immediately.

### Version history

| Version | Approach | Status |
|---------|----------|--------|
| **v4** | Error state + init flag clear + direct epilog (zero function calls) | Current |
| v3.2 | Coordinator reset + retry + HMI display + direct epilog | Crash on cable pull |
| v3.1 | Coordinator reset + retry + direct epilog | Crash on cable pull |
| v3 | Graceful failure only + direct epilog | Never tested in isolation |
| v2 | `iPod_cmd_disconnect` + retry + `CALLBACK_EXIT` with safety flag | Crash on cable pull |
| v1 | `iPod_cmd_connect` + busy-wait + `CALLBACK_EXIT` | Crash + stale state |

## Patch Sites

| Address | What | Bytes Changed |
|---------|------|---------------|
| `0x004F0714` | Event 1 handler: 3 instructions -> `j 0x9A87A0` + 2x `nop` | 10 bytes |
| `0x004F077C` | Event 2 handler: 3 instructions -> `j 0x9A87A0` + 2x `nop` | 11 bytes |
| `0x009A87A0` | Code cave: 6 MIPS instructions (was all zeros) | 21 bytes |

Total: 42 byte differences at the ELF level.

## Key Addresses

| Symbol | Address | Purpose |
|--------|---------|---------|
| `INIT_FLAG_ADDR` | `0x089985F0` | Global `g1215` "already initialized" flag |
| `EPILOG_RETURN` | `0x004F0B9C` | Register restore + `jr $ra` + stack cleanup |
| `ERROR_STATE` | `0x13` | Coordinator state machine error state |

## MIPS Code Cave Listing (v4)

```
0x9A87A0: addiu $v0, $zero, 0x13      ; v0 = 19 (error state)
0x9A87A4: sw    $v0, 0x60($s1)        ; coordinator state = error
0x9A87A8: lui   $v0, 0x089A           ; g1215 address high half
0x9A87AC: sb    $zero, -0x7A10($v0)   ; clear g1215 for next session
0x9A87B0: j     0x4F0B9C             ; jump to register restore + return
0x9A87B4: addiu $v0, $zero, 1         ; return 1 (delay slot)
```

## Usage

### Apply the patch

```bash
python3 tools/patch_ipod_auth_retry.py ProcHMI.elf ProcHMI_patched.elf
```

### Verify a patched file

```bash
python3 tools/patch_ipod_auth_retry.py --verify ProcHMI_patched.elf
```

## Dependencies

- Python 3.10+
- No third-party packages required

## Compatibility

- Firmware v2.08 (`GM10.8V208`) only
- Applies identically to both Navi 600 (`g__eeu10`) and Navi 900 (`g_mpeu10`)
