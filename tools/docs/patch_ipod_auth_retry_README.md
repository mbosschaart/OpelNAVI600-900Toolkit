# patch_ipod_auth_retry.py — iPod/iPhone MFi Auth Retry Patch (v2)

Binary patch for `ProcHMI.elf` that fixes intermittent iPhone USB connectivity
on the Opel Navi 600/900 (firmware v2.08).

## Background

iPhones connected via USB to the Navi 600/900 frequently fail with "This
accessory is not supported." The root cause is in `ProcHMI`'s
`iPodCtrlCoordinator::onMediaDeviceCallback` — when the MFi authentication
handshake fails (event 1 "Auth CP Error" or event 2 "Authentication Failed"),
the firmware logs the error and gives up without retrying. Since MFi auth is
timing-sensitive, a simple reconnect often succeeds on the next attempt.

## What the Patch Does

Injects a 26-instruction (104-byte) code cave into an unused region of
`ProcHMI.elf` and redirects both auth failure handlers to it. The code cave:

1. Loads a retry counter from an unused byte in the coordinator object (`+0x5CE`)
2. If counter < 5: increments it, calls `iPod_cmd_disconnect` to cleanly tear
   down the failed session, clears the "already initialized" flag, sets the
   coordinator state machine to the error state (0x13), sets the stack safety
   flag `0x20($sp)` to prevent a dangerous call to `0x4e6ee4`, and returns
   through `CALLBACK_EXIT`
3. If counter >= 5: resets the counter, sets the same safety flags, and returns
   through `CALLBACK_EXIT` (gives up cleanly)

The firmware's USB detection layer notices the device is still physically
connected, triggers a new attach event, and starts a fresh MFi auth session
through the proper coordinator init code path (`0x4ec40c`). This avoids the
race conditions and stale-state crashes that occurred in the v1 patch.

### v2 changes (crash fix)

The v1 patch had three critical bugs:
- Called `iPod_cmd_connect` from within the callback (caused stale coordinator
  state and crash on USB disconnect)
- Used a ~150ms busy-wait loop (blocked event processing, caused queued
  disconnect events to hit stale state)
- Did not set the `0x20($sp)` safety flag on the give-up path (allowed a
  dangerous call to `0x4e6ee4` with potentially freed pointers)

v2 removes all three: disconnect-only, no busy-wait, stack safety flags on
every exit path.

## Patch Sites

| Address | What | Bytes Changed |
|---------|------|---------------|
| `0x004F0714` | Event 1 handler: 3 instructions replaced with `j 0x9A87A0` + 2x `nop` | 12 bytes |
| `0x004F077C` | Event 2 handler: 3 instructions replaced with `j 0x9A87A0` + 2x `nop` | 12 bytes |
| `0x009A87A0` | Code cave: 26 MIPS instructions (was all zeros) | 104 bytes |

Total: 96 non-zero byte changes at the ELF level.

## Key Addresses

| Symbol | Address | Purpose |
|--------|---------|---------|
| `iPod_cmd_disconnect` | `0x004F5C88` | Tears down the iAP session |
| `INIT_FLAG_ADDR` | `0x089985F0` | Global "already initialized" flag — cleared to allow reinit |
| `CALLBACK_EXIT` | `0x004F0AA0` | Exit path — checks `0x20($sp)` flag, then epilog |
| `ERROR_STATE` | `0x13` | Coordinator state machine error/recovery state |

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

Or use the all-in-one script: `bash tools/build_patch.sh /path/to/ProcHMI.out`

## Dependencies

- Python 3.10+
- No third-party packages required (uses only `struct`, `argparse`, `pathlib`)

## Compatibility

- Firmware v2.08 (`GM10.8V208`) only
- Applies identically to both Navi 600 (`g__eeu10`) and Navi 900 (`g_mpeu10`)
  — the `ProcHMI.out` modules are byte-identical across variants

## MIPS Code Cave Listing (v2)

The 26 instructions injected at `0x009A87A0`:

```
; --- Retry check ---
0x9A87A0: lbu   $v0, 0x5CE($s1)      ; load retry counter
0x9A87A4: sltiu $v1, $v0, 5           ; v1 = (counter < 5)
0x9A87A8: beqz  $v1, give_up          ; if >= 5 retries, give up
0x9A87AC: addiu $v0, $v0, 1           ; increment counter (delay slot)

; --- Retry path ---
0x9A87B0: sb    $v0, 0x5CE($s1)       ; save incremented counter
0x9A87B4: lw    $a0, 0x18($s1)        ; load IAPInterface pointer
0x9A87B8: beqz  $a0, give_up          ; if NULL, give up
0x9A87BC: nop
0x9A87C0: jal   iPod_cmd_disconnect   ; tear down failed session
0x9A87C4: nop
0x9A87C8: lui   $v0, 0x089A           ; INIT_FLAG_ADDR high (0x089985F0)
0x9A87CC: sb    $zero, -0x7A10($v0)   ; clear init flag → allows reinit
0x9A87D0: addiu $v0, $zero, 0x13      ; v0 = 19 (error state)
0x9A87D4: sw    $v0, 0x60($s1)        ; coordinator state = 19
0x9A87D8: addiu $v0, $zero, 1         ; v0 = 1
0x9A87DC: sb    $v0, 0x20($sp)        ; skip dangerous 4e6ee4 call
0x9A87E0: move  $s4, $zero            ; suppress error publication
0x9A87E4: j     CALLBACK_EXIT         ; safe return through exit path
0x9A87E8: nop

; --- Give-up path ---
give_up:
0x9A87EC: sb    $zero, 0x5CE($s1)     ; reset retry counter
0x9A87F0: addiu $v0, $zero, 0x13      ; v0 = 19 (error state)
0x9A87F4: sw    $v0, 0x60($s1)        ; coordinator state = 19
0x9A87F8: addiu $v0, $zero, 1         ; v0 = 1
0x9A87FC: sb    $v0, 0x20($sp)        ; skip 4e6ee4 even on give-up
0x9A8800: j     CALLBACK_EXIT         ; normal exit (4e6ee4 skipped)
0x9A8804: move  $s4, $zero            ; suppress publication (delay slot)
```

### CALLBACK_EXIT safety mechanism

The exit path at `0x4F0AA0` contains:
```
0x4F0AA0: lbu  $v0, 0x20($sp)        ; load skip flag
0x4F0AA4: bnel $v0, $zero, 0x4F0AD8  ; if set → skip dangerous call
0x4F0AB8: jal  0x4e6ee4              ; called with 0x10($s1) — CRASHES if stale
```

Setting `0x20($sp) = 1` on all code cave exit paths prevents the `4e6ee4`
call from executing with potentially freed or stale pointers, which was the
root cause of the crash on USB disconnect.
