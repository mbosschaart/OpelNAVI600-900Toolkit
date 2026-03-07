# decompile_chunked.sh — Chunked C Decompiler

Shell script that decompiles `ProcHMI.elf` into pseudo-C using
[RetDec](https://github.com/avast/retdec) (Avast's open-source decompiler).
Processes the `.text` section in 512 KB chunks to stay within RetDec's memory
limits, then combines the results into a single `ProcHMI.c` file.

## Why Chunked?

`ProcHMI.elf` has a 10 MB `.text` section (~2.5 million MIPS instructions).
RetDec's whole-binary decompilation would require excessive memory and time.
By splitting into manageable 512 KB chunks and using `--select-ranges`, each
chunk completes in minutes.

## Prerequisites

- [RetDec](https://github.com/avast/retdec) installed and `retdec-decompiler`
  on `PATH`
- A decompressed `ProcHMI.elf` (from `xozl_tool.py extract`)

## Usage

```bash
# Default: expects build/ProcHMI.elf
bash tools/decompile_chunked.sh

# Custom ELF path
bash tools/decompile_chunked.sh /path/to/ProcHMI.elf
```

### What happens

1. Splits the `.text` address range (`0x1050` – `0x9A8778`) into 512 KB chunks
2. For each chunk, runs `retdec-decompiler` with `--select-ranges` and
   `--select-decode-only`
3. Skips chunks that were already decompiled (for resumability)
4. Combines all chunk outputs into a single `ProcHMI.c`, stripping
   duplicate headers and preserving function bodies
5. Cleans up individual chunk files

### Output

```
208_source/modules/ProcHMI/decompiled/ProcHMI.c
```

The output is pseudo-C — useful for understanding control flow and identifying
function boundaries, but not directly compilable.

## Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `ELF` | `build/ProcHMI.elf` | Input ELF to decompile |
| `OUT_DIR` | `208_source/modules/ProcHMI/decompiled/` | Output directory |
| `TEXT_START` | `0x1050` | Start of `.text` section |
| `TEXT_END` | `0x9A8778` | End of `.text` section |
| `CHUNK_SIZE` | `0x80000` (512 KB) | Bytes per chunk |

Adjust `TEXT_START` and `TEXT_END` if decompiling a different module.

## Limitations

- RetDec's MIPS decompilation is approximate — many constructs (especially
  C++ virtual calls, RTTI, exception handling) produce incorrect or
  unreadable output
- Branch delay slots are sometimes mishandled, producing phantom variables
- The combined output has no global type/struct definitions — each chunk
  independently infers types
- For accurate reverse engineering, use the assembly output from
  `disasm_pack.py` alongside this C output
