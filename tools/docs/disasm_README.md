# Disassembly Tools

Two tools for turning the Navi 600/900 MIPS firmware binaries into readable
assembly. Both use [Capstone](http://www.capstone-engine.org/) for disassembly
and work directly on the ELF files produced by `xozl_tool.py extract`.

## Dependencies

```
pip install capstone python-lzo
```

- **capstone** — Multi-architecture disassembly framework (we use MIPS32/MIPS64)
- **python-lzo** — Required by `disasm_pack.py` to decompress XOZL modules

---

## mips_disasm.py — Interactive Region Disassembler

Point-and-shoot disassembler for examining specific address ranges in any MIPS
binary (decompressed ELF or raw blob like `dragon.bin`).

### Usage

```bash
# Disassemble 256 bytes starting at offset 0x4f0700 in ProcHMI.elf
python3 mips_disasm.py build/ProcHMI.elf --offset 0x4f0700 --size 0x100

# Same region with raw hex shown alongside
python3 mips_disasm.py build/ProcHMI.elf --offset 0x4f0700 --size 0x100 --hex

# dragon.bin needs MIPS64 mode
python3 mips_disasm.py ../dnl/bin/system/adit/g__eeu10/dragon.bin \
    --offset 0x4ab50 --size 0x80 --mips64

# Try to resolve string references in operands
python3 mips_disasm.py build/ProcHMI.elf --offset 0x4f0700 --size 0x40 --strings
```

### Options

| Flag | Description |
|------|-------------|
| `--offset HEX` | File offset to start disassembly (hex or decimal) |
| `--size HEX` | Number of bytes to decode (default: 0x100 = 256) |
| `--base HEX` | Override display base address (default: 0, so offset = address) |
| `--mips64` | MIPS64 mode for `dragon.bin` |
| `--strings` | Attempt to resolve string pointers in immediate operands |
| `--hex` | Show raw 32-bit hex word alongside each instruction |

### How it works

Unlike Capstone's default `disasm()` which stops at the first unrecognized
sequence, this tool decodes **one 4-byte instruction at a time**. MIPS
instructions are always 4 bytes, so if one word doesn't decode (data embedded
in a code section, compressed tables, etc.), it prints `.word 0xXXXXXXXX` and
continues to the next word. This means you always get complete coverage of the
requested range.

### Example output

```
0x009a87a0:  922205ce  lbu      $v0, 0x5ce($s1)
0x009a87a4:  2c430005  sltiu    $v1, $v0, 5
0x009a87a8:  10600014  beqz     $v1, 0x9a87fc
0x009a87ac:  24420001  addiu    $v0, $v0, 1
0x009a87b0:  a22205ce  sb       $v0, 0x5ce($s1)
```

### When to use

- Examining a specific function or patch site
- Verifying binary patches are encoded correctly
- Tracing control flow around a known address
- Quick look at bootloader dispatch logic in `dragon.bin`

---

## disasm_pack.py — Batch Module Disassembler

Processes all `.out` firmware modules in a firmware update tree. For each XOZL
file it decompresses the ELF, parses headers, and disassembles the `.text`
section. Also disassembles `dragon.bin`.

### Usage

```bash
# Default: all modules from dnl/bin/system/adit/g__eeu10/
python3 disasm_pack.py

# Custom paths
python3 disasm_pack.py --adit-dir /path/to/g__eeu10 --out-dir ./my_disasm

# Single module only (faster for targeted work)
python3 disasm_pack.py --module ProcHMI.out

# Skip bootloader
python3 disasm_pack.py --skip-dragon

# Full executable segment instead of just .text
python3 disasm_pack.py --full
```

### Options

| Flag | Description |
|------|-------------|
| `--adit-dir PATH` | Firmware variant directory with `.out` files and `dragon.bin` |
| `--out-dir PATH` | Output directory (default: `_PATCH/disasm_output/`) |
| `--module NAME` | Process single module only (e.g. `ProcHMI.out`) |
| `--full` | Disassemble entire executable segment, not just `.text` |
| `--skip-dragon` | Don't disassemble `dragon.bin` |

### Output structure

```
disasm_output/
├── index.json                  Master index (metadata for all modules)
├── modules/
│   ├── ProcHMI/
│   │   ├── elf_meta.json       ELF headers, sections, segments
│   │   └── text.asm            .text disassembly (2.5M instructions)
│   ├── ProcBase/
│   │   ├── elf_meta.json
│   │   └── text.asm
│   ├── ProcMM/
│   │   └── ...
│   └── ...
└── dragon/
    └── dragon.asm              Full bootloader disassembly (490K instructions)
```

### How it works

1. For each `.out` file, reads the XOZL header and decompresses the LZO payload
   (using the same corrected offset as `xozl_tool.py`)
2. Parses the ELF32 headers to find the `.text` section (or executable PT_LOAD
   segment if `--full` is used)
3. Disassembles instruction-at-a-time with Capstone MIPS32 LE
4. Writes the `.asm` listing and `elf_meta.json` per module
5. Disassembles `dragon.bin` as a raw MIPS64 blob (file offset = address)
6. Writes `index.json` with metadata for all processed modules

### elf_meta.json fields

```json
{
  "class": 32,
  "machine": 8,           // MIPS
  "entry": 4416,          // ELF entry point
  "phoff": 52,            // program header offset
  "shoff": 15626808,      // section header offset
  "flags": 815276033,     // MIPS ABI flags
  "shnum": 17,            // number of sections
  "shstrndx": 16,
  "text_offset": 4176,    // .text file offset (= vaddr for these ELFs)
  "text_vaddr": 4176,     // .text virtual address
  "text_size": 10123048,  // .text size in bytes
  "sections": [...],      // all section headers
  "segments": [...]       // all program headers
}
```

### Module sizes (Navi 600 v2.08)

| Module | ELF size | .text instructions |
|--------|----------|--------------------|
| ProcHMI.out | 15.6 MB | ~2.5M |
| ProcNav.out | 16.5 MB | ~2.8M |
| ProcSDS.out | 6.3 MB | ~1.1M |
| ProcBase.out | 5.6 MB | ~900K |
| ProcMW.out | 4.8 MB | ~790K |
| ProcMM.out | 4.0 MB | ~650K |
| ProcMap.out | 2.4 MB | ~420K |
| ProcMP1.out | 1.6 MB | ~296K |
| dragon.bin | 1.9 MB | ~490K |

### Searching the output

The `.asm` files are plain text, one instruction per line, prefixed with the
address. Use `grep` / `rg` to search:

```bash
# Find all calls to iPod_cmd_disconnect (0x4f5c88)
rg "0x4f5c88" disasm_output/modules/ProcHMI/text.asm

# Find all LUI loading high part of a specific address
rg "lui.*0x89a" disasm_output/modules/ProcHMI/text.asm

# Find all jump targets in the bootloader's module loader region
rg "^0x0004a" disasm_output/dragon/dragon.asm | rg "jal|j "
```

---

## Which tool when?

| Task | Tool |
|------|------|
| "What's at address X?" | `mips_disasm.py --offset X --size 0x100` |
| "Disassemble everything for searching" | `disasm_pack.py` |
| "Just one module" | `disasm_pack.py --module ProcHMI.out` |
| "Verify my patch" | `mips_disasm.py --offset 0x9a87a0 --size 0x70 --hex` |
| "Bootloader analysis" | `mips_disasm.py dragon.bin --mips64 --offset X` |

For **decompilation to pseudo-C** (higher-level than assembly), you need
[Ghidra](https://ghidra-sre.org/) — a free reverse engineering tool from the
NSA. Load the decompressed ELF as `MIPS:LE:32:default` and Ghidra will produce
C-like output with function boundaries, control flow, and inferred types.
