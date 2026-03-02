# `uli_tool.py` — ULI extractor / repacker

This repository contains firmware images where many assets/configs are stored in proprietary `.uli` containers (e.g. `registries.uli`, `imagesXX.uli`, `fonts.uli`).

`uli_tool.py` is a minimal, research-oriented tool to:

- **extract** a `.uli` into a directory tree (payloads only)
- **repack** that extracted directory back into a `.uli` while preserving container metadata

It was designed for *safe-ish* modding workflows where you want to edit files like `*.reg` inside `registries.uli` and then rebuild an update image.

## What it understands (format model)

Inferred layout (little-endian):

- File header begins with magic `ULI `.
- Table contains `count` entries at offset `0x0c`.
- Each entry points to a “chunk”:
  - first `0x100` bytes: chunk header containing a path-like name (C-string + padding)
  - remaining bytes: **payload**
- Some `.uli` files contain **trailing bytes after the last chunk**; the repacker preserves these.

The extractor writes:

- extracted payload files into an output directory, using the chunk header name as path
- `_uli_manifest.json` containing the parsed table + per-entry metadata needed for repacking

## Requirements

- Python 3
- No third-party dependencies

If you use a local virtualenv in the repo, use:

- `./.venv/bin/python`

## Usage

### Extract

```bash
python3 ./tools/uli_tool.py \
  extract "/path/to/registries.uli" "/path/to/out_dir"
```

This creates:

- extracted files under `/path/to/out_dir/...`
- `/path/to/out_dir/_uli_manifest.json`

### Repack

```bash
python3 ./tools/uli_tool.py \
  repack "/path/to/out_dir" "/path/to/new_registries.uli"
```

Optional strict mode:

```bash
python3 ./tools/uli_tool.py \
  repack "/path/to/out_dir" "/path/to/new_registries.uli" \
  --require-same-sizes
```

`--require-same-sizes` is useful if you only want to allow “same-size” edits (often safer for embedded update systems).

## Typical workflow (registry patch)

1. Extract:
   - `extract registries.uli extracted_reg/`
2. Edit one or more extracted `*.reg` files in `extracted_reg/` (keep encoding in mind; see notes below).
3. Repack:
   - `repack extracted_reg/ registries.patched.uli`
4. Replace `registries.uli` in your firmware tree with the repacked output.
5. Build ISO / USB update media and flash using the unit’s standard update procedure.

## Notes / gotchas

- **Manifest is required for repack**: repacking uses `_uli_manifest.json` to preserve:
  - entry ordering
  - original per-entry metadata fields
  - the original 0x100-byte chunk header bytes
  - any trailing bytes after the last referenced chunk

- **Directories vs files**: some entries are `mkdir ...` pseudo-entries. The extractor creates directories for those, and the repacker writes empty payloads back for them.

- **Encoding**: registry payloads are often best treated as `latin1`/byte-preserving text. If you edit with an editor that rewrites encoding/line endings, that can change payload bytes.

- **Container acceptance**: even if a repack “looks correct”, the target bootloader/updater may reject modified containers (integrity checks, signatures, version gating, etc.). This tool only preserves structural metadata; it does not bypass security.

- **Not a general ULI SDK**: this is intentionally minimal and based on observed firmware samples. If you find a ULI variant that does not match this model, the tool may fail or produce unusable output.

## Output structure example

After extracting `registries.uli`:

```text
extracted_reg/
  _uli_manifest.json
  base.reg
  conf.reg
  hmiapp.reg
  map.reg
  mmapp.reg
  MWapp.reg
  mp1app.reg
  navapp.reg
  sdsapp.reg
  videoapp.reg
```

## Troubleshooting

- **“Manifest not found”**: you’re pointing `repack` at the wrong directory; it must contain `_uli_manifest.json`.
- **“Missing extracted payload”**: you deleted/moved a file that exists in the manifest. Restore it or re-extract.
- **Repacked file different size**: expected; payload sizes often change if you edit text. Use `--require-same-sizes` if you want to forbid that.

## Related tool: `cco_tool.py` (decode `*.cco.bin`)

If you extracted `*.cco.bin` blobs from `dialogs.sdp` (Speech Dialog Protocol) and they start with `ULI `, use:

```bash
python3 ./tools/cco_tool.py info "/path/to/01_englo401.cco.bin"
python3 ./tools/cco_tool.py decode "/path/to/01_englo401.cco.bin" "/path/to/01_englo401.cco.decomp.bin"
```

Full format + reverse-engineering notes are in:

- `tools/cco_tool_README.md`
