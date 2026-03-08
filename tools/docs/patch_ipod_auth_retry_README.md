# patch_ipod_auth_retry.py — iPod/iPhone MFi Auth Graceful Failure Patch (v3)

Binary patch for `ProcHMI.elf` that prevents head unit crashes when iPhone
MFi authentication fails on the Opel Navi 600/900 (firmware v2.08).

## Background

The Navi 600/900 uses iAP1 (iPod Accessory Protocol version 1) with an
MFi 1.0 authentication chip (CP20) to communicate with iPhones over USB.
When MFi authentication fails (event 1 "Auth CP Error" or event 2
"Authentication Failed"), the firmware's error handler goes through a
complex epilog path (`CALLBACK_EXIT` at `0x4F0AA0`) that accesses the
device object pointer at `0x10($s1)`. If the user then physically
disconnects the USB cable, the USB removal handler fires while the
coordinator is in a partially-torn-down state, causing a null/stale pointer
crash that reboots the head unit.

### iPhone compatibility

The firmware only speaks iAP1. Newer iPhones have progressively reduced
or dropped iAP1 support:

| Device | Protocol Support | Expected Behavior |
|--------|-----------------|-------------------|
| iPhone 7 and earlier (Lightning) | iAP1 supported | Works normally |
| iPhone 13 era (Lightning) | iAP1 reduced | Intermittent connection |
| iPhone 15+ (USB-C) | iAP1 likely dropped | "Accessory not compatible" |

This patch cannot fix the protocol incompatibility with newer iPhones — it
can only ensure the head unit doesn't crash when auth fails.

## What the Patch Does (v3)

Injects a minimal 6-instruction (24-byte) code cave into an unused region
of `ProcHMI.elf` and redirects both auth failure handlers to it. The code cave:

1. Clears the "already initialized" flag at `0x089985F0` so a future
   re-plug starts a fresh init sequence
2. Sets the coordinator state machine to the error state (0x13)
3. Jumps **directly** to the function epilog (register restore + return
   at `0x4F0B9C`), bypassing `CALLBACK_EXIT` entirely

This approach leaves the device object completely untouched — the USB
removal handler can safely clean it up when the cable is disconnected.

### Version history

**v3** (current) — Graceful failure, crash fix:
- Removed `iPod_cmd_disconnect` call (was the root cause of the crash —
  tearing down the device object left stale pointers for the USB removal
  handler)
- Removed retry counter logic (retries cannot help when the iPhone doesn't
  support iAP1 at all)
- Removed stack safety flag mechanism (unnecessary — we bypass
  `CALLBACK_EXIT` entirely by jumping to the direct epilog)
- Jump target changed from `CALLBACK_EXIT` (`0x4F0AA0`) to the register
  restore epilog (`0x4F0B9C`), avoiding all code that touches the device
  pointer

**v2** — Disconnect-only with stack safety:
- Removed `iPod_cmd_connect` and busy-wait from v1
- Added stack safety flag `0x20($sp)` to skip dangerous `0x4e6ee4` call
- Still called `iPod_cmd_disconnect` — which caused the crash

**v1** — Initial retry patch:
- Called `iPod_cmd_connect` from within callback (stale state crash)
- Used ~150ms busy-wait loop (blocked event processing)
- Missing safety flags on give-up path

## Patch Sites

| Address | What | Bytes Changed |
|---------|------|---------------|
| `0x004F0714` | Event 1 handler: 3 instructions replaced with `j 0x9A87A0` + 2x `nop` | 10 bytes |
| `0x004F077C` | Event 2 handler: 3 instructions replaced with `j 0x9A87A0` + 2x `nop` | 11 bytes |
| `0x009A87A0` | Code cave: 6 MIPS instructions (was all zeros) | 21 bytes |

Total: 42 byte differences at the ELF level.

## Key Addresses

| Symbol | Address | Purpose |
|--------|---------|---------|
| `INIT_FLAG_ADDR` | `0x089985F0` | Global "already initialized" flag — cleared to allow reinit |
| `EPILOG_RETURN` | `0x004F0B9C` | Register restore + `jr $ra` + stack cleanup |
| `ERROR_STATE` | `0x13` | Coordinator state machine error/terminal state |

## Usage

### Apply the patch

```bash
python3 tools/patch_ipod_auth_retry.py ProcHMI.elf ProcHMI_patched.elf
```

The tool verifies expected bytes at both patch sites and confirms the code cave
region is empty before writing. It will refuse to patch if:
- The bytes at the patch sites don't match v2.08 (wrong firmware version)
- The code cave region is not all zeros (already patched)

### Verify a patched file

```bash
python3 tools/patch_ipod_auth_retry.py --verify ProcHMI_patched.elf
```

Checks that the jump instructions and code cave are present and correct.
Returns exit code 0 on success, 1 on failure.

## End-to-End Workflow

```bash
# 1. Decompress the XOZL module
python3 tools/xozl_tool.py extract ProcHMI.out ProcHMI.elf

# 2. Apply patch
python3 tools/patch_ipod_auth_retry.py ProcHMI.elf ProcHMI_patched.elf

# 3. Verify
python3 tools/patch_ipod_auth_retry.py --verify ProcHMI_patched.elf

# 4. Repack into XOZL (recomputes both content CRC and whole-file CRC)
python3 tools/xozl_tool.py pack ProcHMI_patched.elf ProcHMI_patched.out --ref ProcHMI.out

# 5. Validate the XOZL output
python3 tools/validate_xozl.py ProcHMI_patched.out --elf ProcHMI_patched.elf --ref ProcHMI.out
```

## Dependencies

- Python 3.10+
- No third-party packages required (uses only `struct`, `argparse`, `pathlib`)

## Compatibility

- Firmware v2.08 (`GM10.8V208`) only
- Applies identically to both Navi 600 (`g__eeu10`) and Navi 900 (`g_mpeu10`)
  — the `ProcHMI.out` modules are byte-identical across variants

## MIPS Code Cave Listing (v3)

The 6 instructions injected at `0x009A87A0`:

```
0x9A87A0: lui   $v0, 0x089A           ; INIT_FLAG_ADDR high (0x089985F0)
0x9A87A4: sb    $zero, -0x7A10($v0)   ; clear init flag → allows reinit on replug
0x9A87A8: addiu $v0, $zero, 0x13      ; v0 = 19 (error state)
0x9A87AC: sw    $v0, 0x60($s1)        ; coordinator state = 19
0x9A87B0: j     0x4F0B9C             ; jump to function epilog (register restore)
0x9A87B4: addiu $v0, $zero, 1         ; delay slot: return value = 1
```

### Why bypass CALLBACK_EXIT?

The exit path at `0x4F0AA0` (CALLBACK_EXIT) accesses the device pointer:
```
0x4F0AA0: lbu  $v0, 0x20($sp)        ; load skip flag
0x4F0AA4: bnel $v0, $zero, 0x4F0AD8  ; if set → skip 0x4e6ee4 call
  ...
0x4F0AB8: jal  0x4e6ee4              ; reads 0x6e0($a0) — crashes if $a0 is stale
0x4F0ABC: lw   $a0, 0x10($s1)        ; device pointer (delay slot)
```

Even when the `0x20($sp)` flag is set to skip `0x4e6ee4`, the epilog code
after `0x4F0AD8` still accesses `0x10($s1)` and other coordinator fields.
By jumping directly to the register restore at `0x4F0B9C`, we avoid all of
this — no device pointer is accessed, no coordinator fields are read, and
the function returns cleanly with all saved registers restored from the stack.
