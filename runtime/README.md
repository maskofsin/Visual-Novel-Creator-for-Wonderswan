# WSC VN Runtime

A minimal C runtime that runs Visual Novels authored in **WSC VN Studio** on
real WonderSwan Color hardware.

---

## What this does

Reads a `.wscvn.json` project exported from the WSC VN Studio editor, converts
it to C, compiles it with **Wonderful Toolchain**, and produces a `game.wsc`
ROM that boots on:

- Real WSC hardware (via flash cart: WS Flash Masta, etc.)
- Emulators: **Mesen 2**, **Mednafen**, **Ares**

### Feature support

| Feature | Status |
|---|---|
| Title screen with menu | ✅ |
| Dialogue with speaker name | ✅ |
| Text wrapping (~26 chars × 4 lines) | ✅ |
| `{pause}` to split text across multiple boxes | ✅ |
| Typewriter text speed | ✅ |
| Choices (up to 4 per scene) | ✅ |
| Flag-based branches (IF/GOTO) | ✅ |
| Flags: int16 + bool | ✅ |
| Choice conditions (show IF flag >= X) | ✅ |
| Flag ops on choices + scene end | ✅ |
| Gradient backgrounds (scene colors) | ✅ |
| Speaker color + textbox style | ✅ |
| Imported PNG backgrounds | ✅ |
| Character sprites (2 slots) | ✅ |
| Tracker music (up to 4 wavetable channels) | ✅ |
| Particle effects | ❌ planned |
| Imported audio files (WAV/MP3) | ❌ not supported |
| Save / Load | ✅ cartridge SRAM |

---

## Prerequisites (Windows 11)

1. Install **MSYS2**: https://www.msys2.org
2. Install **Wonderful Toolchain**:
   https://wonderful.asie.pl/bootstrap/wf-bootstrap-windows-x86_64.exe
3. Inside the Wonderful Toolchain Shell:
   ```bash
   wf-pacman -Syu
   wf-pacman -S target-wswan wf-tools
   pacman -S git make python python-pip
   pip install Pillow
   ```

---

## Build steps

All commands are run inside the **Wonderful Toolchain Shell**.

```bash
# 1. Go to your project folder
cd /opt/wonderful/projects/wsc-vn-runtime

# 2. Convert your exported JSON to C data
make convert JSON=/c/Users/YourName/Downloads/MyGame.wscvn.json

# 3. Build the ROM
make

# → Output: game.wsc
```

If step 2 reports errors (text too long, undefined flags, etc.), fix them in
the WSC VN Studio editor, re-export, and retry.

## Test in emulator

- **Mednafen**: `mednafen game.wsc`
- **Mesen 2**: File → Open → select `game.wsc`

---

## Controls

| WSC button | Action |
|---|---|
| **A / START** | Advance dialogue, confirm menu selection |
| **X1 (up)** | Menu: previous item |
| **X3 (down)** | Menu: next item |
| **B** | Advance dialogue (alt) |

---

## Project layout

```
wsc-vn-runtime/
├── src/
│   ├── main.c          ← engine (you don't normally edit this)
│   ├── game_types.h    ← struct definitions
│   ├── font.h          ← embedded 8×8 ASCII font
│   ├── game_data.c     ← GENERATED from your JSON (ignored by git)
│   └── game_data.h     ← GENERATED from your JSON (ignored by git)
├── tools/
│   └── convert_json.py ← JSON → C converter + validator
├── Makefile
└── README.md
```

---

## Tile format (important technical note)

This runtime uses **4bpp PACKED** tile format on the WonderSwan Color
(I/O port 0x60 = 0xE0 at startup):

- 32 bytes per tile, each byte = 2 pixels
- High nibble = left pixel, low nibble = right pixel
- 16-color palettes, color 0 = transparent (for sprites/chars)
- Tiles live at 0x4000 (bank 0, tiles 0..511) and 0x8000 (bank 1, tiles 512..1023)

Both the Python converter (`tools/convert_json.py`) and the font packer in
`main.c` emit this exact format. Do not change one without the other.

### Tile map allocation

| Bank 0 range | Used for |
|---|---|
| 0–95 | ASCII font glyphs (space..~) |
| 96 | `TILE_BLANK` (all index 0) |
| 97 | `TILE_SOLID` (all index 1) |
| 128–319 | Character 1 sprite (192 tiles max) |
| 320–511 | Character 2 sprite (192 tiles max) |

| Bank 1 range | Used for |
|---|---|
| 0 | Solid fill for gradient backgrounds |
| 1–511 | Imported background image (up to 511 tiles) |

---

## Memory layout

Runtime engine uses ~8 KB of code + ~2 KB of RAM (IRAM). Everything else in
the ROM is:

- **Flag state** in IRAM: `2 × NUM_FLAGS bytes`
- **Scene data** in ROM (far pointers): all strings, node table, flag ops,
  choices
- **Font** in ROM: 768 bytes (96 glyphs × 8 bytes)
- **Tile/screen RAM** in WSC internal VRAM (4bpp tiles: 0x4000–0xBFFF;
  screens: 0x3000/0x3800)

For a typical 30-scene VN with 10 flags, total ROM is ~20–40 KB.

---

## Extending the runtime

1. **Music**: Add panning, noise channel patterns, or tempo/pattern changes (multiple patterns per track).
2. **Particle effects**: Add sprite recycling for rain, snow, sakura, or
   similar scene dressing.
3. **Save/Load**: Saves use cartridge SRAM with three slots and checksum validation.

---

## License

MIT. Font data is public domain.
