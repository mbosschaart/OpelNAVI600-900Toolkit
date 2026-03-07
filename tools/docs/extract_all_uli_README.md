# extract_all_uli.py — Batch ULI Asset Extractor

Batch-extracts all `.uli` resource archives from the firmware directory into
individual subdirectories. A convenience wrapper around `uli_tool.py extract`.

## What It Does

Scans the Navi 600 firmware variant directory (`g__eeu10`) for all `.uli`
files, then extracts each one into a named subdirectory under
`_PATCH/assets_extracted/`. Already-extracted archives (identified by the
presence of `_uli_manifest.json`) are skipped.

## ULI Files in v2.08

| File | Contents | Approx. Files |
|------|----------|---------------|
| `fonts.uli` | TrueType and bitmap fonts | ~30 |
| `registries.uli` | System/module configuration (`.reg` files) | ~10 |
| `images01.uli` – `images24.uli` | PNG images, icons, backgrounds, animations | ~100 each |

Total: ~2,500 asset files across all ULI archives.

## Usage

```bash
python3 tools/extract_all_uli.py
```

No arguments required. The script auto-detects the firmware directory from
the repository layout.

### Output structure

```
assets_extracted/
├── fonts/
│   ├── _uli_manifest.json
│   ├── Arial_12.ttf
│   └── ...
├── registries/
│   ├── _uli_manifest.json
│   ├── base.reg
│   └── ...
├── images01/
│   ├── _uli_manifest.json
│   └── images/mid/png/...
├── images02/
│   └── ...
└── images24/
    └── ...
```

Each subdirectory contains:
- `_uli_manifest.json` — metadata for repacking (see `uli_tool.py` docs)
- Extracted asset files in their original directory hierarchy

## Firmware Directory Detection

The script looks for firmware files in this order:
1. `_PATCH/firmware/dnl/bin/system/adit/g__eeu10/` (relative to repo)
2. `/Users/martijn/Downloads/OpelFirmware/dnl/bin/system/adit/g__eeu10/` (absolute fallback)

## Dependencies

- Python 3.10+
- `uli_tool.py` (must be in the same `tools/` directory)
- No third-party packages required
