# patch_ipod_auth_retry.py — iPod/iPhone MFi Auth Retry Patch (v3.1)

Binary patch for `ProcHMI.elf` that adds automatic MFi authentication retry
with crash-safe coordinator reset on the Opel Navi 600/900 (firmware v2.08).

## Background

The Navi 600/900 uses iAP1 (iPod Accessory Protocol version 1) with an
MFi 1.0 authentication chip (CP20) to communicate with iPhones over USB.
When MFi authentication fails (event 1 "Auth CP Error" or event 2
"Authentication Failed"), the firmware logs the error and gives up without
retrying. Since MFi auth is timing-sensitive, a simple retry often succeeds.

Additionally, the error handler's epilog (`CALLBACK_EXIT` at `0x4F0AA0`)
accesses device object pointers. If the user physically disconnects the cable
while the coordinator is in a partially-handled state, the USB removal handler
hits stale pointers and crashes the head unit.

### iPhone compatibility

| Device | Protocol Support | Expected Behavior |
|--------|-----------------|-------------------|
| iPhone 7 and earlier (Lightning) | iAP1 supported | Retry should help |
| iPhone 13 era (Lightning) | iAP1 reduced | Retry may help |
| iPhone 15+ (USB-C) | iAP1 likely dropped | Fails gracefully after 3 attempts |

## What the Patch Does (v3.1)

Injects a 16-instruction (64-byte) code cave and redirects both auth failure
handlers to it. The code cave implements two paths:

### Retry path (counter < 3)

1. Increments a retry counter stored at coordinator+`0x5CE`
2. Calls `function_4ec608` — the firmware's **own coordinator reset function**
   (the same function used during iPod detach and undervoltage recovery). This
   properly clears all internal fields: state → 0, connected flag → 0, deck
   status flags, buffer regions, etc.
3. Clears the global "already initialized" flag (`g1215` at `0x089985F0`) so
   the init function (`0x4ec40c`) won't early-exit on the next call
4. Returns through the direct function epilog (register restore at `0x4F0B9C`)

The coordinator is now in a clean idle state. The firmware's timer chain
(Timer 2 @ 2s, Timer 1 @ 5s) re-detects the still-connected USB device,
triggers fresh initialization through `iPodCtrlCoordinator::initialize`,
and attempts MFi authentication again.

### Give-up path (counter >= 3)

1. Resets the retry counter to 0 (ready for next device session)
2. Sets coordinator state to `0x13` (error/terminal)
3. Returns through the direct function epilog

### Why this is safe

- **No `iPod_cmd_disconnect` call**: v2's crash was caused by disconnect
  tearing down the device object. v3.1 uses `function_4ec608` instead, which
  only resets internal coordinator state without touching the device/USB layer.
- **Bypasses CALLBACK_EXIT entirely**: all paths jump directly to the register
  restore epilog at `0x4F0B9C`, avoiding all code that accesses device pointers.
- **Uses firmware's own reset**: `function_4ec608` is the same function the
  firmware calls during normal iPod detach — it's proven safe.

### Version history

| Version | Approach | Issue |
|---------|----------|-------|
| **v3.1** | Coordinator reset (`4ec608`) + retry counter + direct epilog | Current |
| v3 | Graceful failure only (no retry) + direct epilog | No crash, but no retry |
| v2 | `iPod_cmd_disconnect` + retry + `CALLBACK_EXIT` with safety flag | Crash on cable pull |
| v1 | `iPod_cmd_connect` + busy-wait + `CALLBACK_EXIT` | Crash + stale state |

## Patch Sites

| Address | What | Bytes Changed |
|---------|------|---------------|
| `0x004F0714` | Event 1 handler: 3 instructions → `j 0x9A87A0` + 2x `nop` | 10 bytes |
| `0x004F077C` | Event 2 handler: 3 instructions → `j 0x9A87A0` + 2x `nop` | 11 bytes |
| `0x009A87A0` | Code cave: 16 MIPS instructions (was all zeros) | 57 bytes |

Total: 78 byte differences at the ELF level.

## Key Addresses

| Symbol | Address | Purpose |
|--------|---------|---------|
| `function_4ec608` | `0x004EC608` | Firmware's coordinator reset (clears state, flags, buffers) |
| `INIT_FLAG_ADDR` | `0x089985F0` | Global `g1215` "already initialized" flag |
| `EPILOG_RETURN` | `0x004F0B9C` | Register restore + `jr $ra` + stack cleanup |
| `RETRY_OFFSET` | `+0x5CE` | Retry counter byte in coordinator object |

## MIPS Code Cave Listing (v3.1)

```
; --- Retry check ---
0x9A87A0: lbu   $v0, 0x5CE($s1)      ; load retry counter
0x9A87A4: sltiu $v1, $v0, 3           ; v1 = (counter < 3)
0x9A87A8: beqz  $v1, give_up          ; if >= 3 → give up
0x9A87AC: addiu $v0, $v0, 1           ; increment counter (delay slot)

; --- Retry path ---
0x9A87B0: sb    $v0, 0x5CE($s1)       ; save incremented counter
0x9A87B4: jal   0x4EC608             ; call firmware's coordinator reset
0x9A87B8: move  $a0, $s1              ; (delay slot) pass coordinator as arg
0x9A87BC: lui   $v0, 0x089A           ; g1215 addr high
0x9A87C0: sb    $zero, -0x7A10($v0)   ; clear g1215 → allows fresh init
0x9A87C4: j     0x4F0B9C             ; jump to function epilog
0x9A87C8: addiu $v0, $zero, 1         ; return 1 (delay slot)

; --- Give-up path ---
give_up:
0x9A87CC: sb    $zero, 0x5CE($s1)     ; reset counter for next session
0x9A87D0: addiu $v0, $zero, 0x13      ; state = 19 (error)
0x9A87D4: sw    $v0, 0x60($s1)        ; set coordinator state
0x9A87D8: j     0x4F0B9C             ; jump to function epilog
0x9A87DC: addiu $v0, $zero, 1         ; return 1 (delay slot)
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
