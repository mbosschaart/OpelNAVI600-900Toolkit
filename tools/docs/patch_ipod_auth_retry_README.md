# patch_ipod_auth_retry.py — iPod/iPhone MFi Auth Retry Patch

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
2. If counter < 5: increments it, calls `iPod_cmd_disconnect`, busy-waits
   ~150ms, clears the "already initialized" flag, calls `iPod_cmd_connect`,
   and returns from the callback — the new auth result arrives as a fresh callback
3. If counter >= 5: resets the counter and falls through to the original error
   handling path

## Patch Sites

| Address | What | Bytes Changed |
|---------|------|---------------|
| `0x004F0714` | Event 1 handler: 3 instructions replaced with `j 0x9A87A0` + 2x `nop` | 12 bytes |
| `0x004F077C` | Event 2 handler: 3 instructions replaced with `j 0x9A87A0` + 2x `nop` | 12 bytes |
| `0x009A87A0` | Code cave: 26 MIPS instructions (was all zeros) | 104 bytes |

Total: 93 non-zero byte changes (the `nop` instructions at the jump sites
contribute zero bytes that were previously non-zero).

## Key Addresses

| Symbol | Address | Purpose |
|--------|---------|---------|
| `iPod_cmd_disconnect` | `0x004F5C88` | Tears down the iAP session |
| `iPod_cmd_connect` | `0x004F5D34` | Starts a new iAP session with fresh MFi auth |
| `INIT_FLAG_ADDR` | `0x089985F0` | Global "already initialized" flag — cleared to allow reinit |
| `CALLBACK_EPILOG` | `0x004F0B9C` | Normal callback return path |
| `CALLBACK_EXIT` | `0x004F0AA0` | Give-up exit path (original behavior) |

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

# 4. Repack into XOZL (using original as reference for header/trailer)
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

## MIPS Code Cave Listing

The 26 instructions injected at `0x009A87A0`:

```
0x9A87A0: lbu   $v0, 0x5CE($s1)      ; load retry counter
0x9A87A4: sltiu $v1, $v0, 5           ; v1 = (counter < 5)
0x9A87A8: beqz  $v1, give_up          ; if >= 5 retries, give up
0x9A87AC: addiu $v0, $v0, 1           ; increment counter (delay slot)
0x9A87B0: sb    $v0, 0x5CE($s1)       ; save incremented counter
0x9A87B4: lw    $a0, 0x18($s1)        ; load IAPInterface pointer
0x9A87B8: beqz  $a0, give_up          ; if NULL, give up
0x9A87BC: nop
0x9A87C0: jal   iPod_cmd_disconnect   ; tear down failed session
0x9A87C4: nop
0x9A87C8: lui   $v0, 0x0100           ; delay loop: ~16M iterations
0x9A87CC: addiu $v0, $v0, -1          ; decrement
0x9A87D0: bnez  $v0, -2               ; spin wait ~100-250ms
0x9A87D4: nop
0x9A87D8: lui   $v0, 0x089A           ; load INIT_FLAG_ADDR high
0x9A87DC: sb    $zero, -0x7A10($v0)   ; clear init flag
0x9A87E0: lw    $a0, 0x18($s1)        ; reload IAPInterface pointer
0x9A87E4: beqz  $a0, skip_connect     ; safety check
0x9A87E8: nop
0x9A87EC: jal   iPod_cmd_connect      ; start fresh MFi auth session
0x9A87F0: nop
0x9A87F4: j     CALLBACK_EPILOG       ; return from callback
0x9A87F8: move  $v0, $zero            ; return 0 (delay slot)
give_up:
0x9A87FC: sb    $zero, 0x5CE($s1)     ; reset retry counter
0x9A8800: j     CALLBACK_EXIT         ; original error exit path
0x9A8804: move  $s4, $zero            ; no publish (delay slot)
```
