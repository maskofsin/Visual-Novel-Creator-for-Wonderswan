# Setup Guide

This guide explains how to set up WSC VN Creator from a clean GitHub checkout.
It intentionally does **not** include Wonderful Toolchain, Cygnals binaries, ROMs, or game assets.

## 1. Install required software

### Windows recommended path

1. Install MSYS2 from https://www.msys2.org/
2. Install Wonderful Toolchain from https://wonderful.asie.pl/
3. Open the **Wonderful Toolchain Shell**.
4. Install the WonderSwan target and build tools:

```bash
wf-pacman -Syu
wf-pacman -S target-wswan wf-tools
pacman -S git make python python-pip
python -m pip install Pillow
```

Wonderful's WonderSwan target documentation lists the required target package as:

```bash
wf-pacman -S target-wswan
```

## 2. Get the project

Clone or download this repository somewhere inside the Wonderful projects folder.
A convenient Windows path is:

```bash
cd /opt/wonderful/projects
git clone https://github.com/maskofsin/Visual-Novel-Creator-for-Wonderswan.git wsc-vn-creator
cd wsc-vn-creator
```

If you downloaded a ZIP instead, extract it to something like:

```text
C:\msys64\opt\wonderful\projects\wsc-vn-creator
```

## 3. Install Cygnals for Furnace music support

The runtime can build without music, but Furnace `.fur` music needs Cygnals.
Install it from its upstream repository instead of committing binaries here:

```bash
cd runtime/third_party
git clone https://github.com/joffb/cygnals.git
cd cygnals
make TARGET=wswan/medium
```

On Windows, the runtime Makefile expects:

```text
runtime/third_party/cygnals/bin/windows_amd64/fur2ws.exe
runtime/third_party/cygnals/build/wswan/medium/libcygnals.a
```

If you are on Linux/macOS, either build/provide the matching `fur2ws` binary and adjust this line in `runtime/Makefile`:

```make
FUR2WS := $(CYGNALS_DIR)/bin/windows_amd64/fur2ws.exe
```

or build with `audioBackend` set to `legacy`/no Cygnals music in your exported project.

## 4. Open the editor

Open this file in your browser:

```text
wsc-vn-studio.html
```

Recommended workflow:

1. Create or import a `.wscvn.json` project.
2. Add backgrounds, character PNGs, music, SFX, choices, flags, and scenes.
3. Use the validation panel before exporting.
4. Export the project JSON.

## 5. Build the starter ROM

From the Wonderful Toolchain Shell:

```bash
cd /opt/wonderful/projects/wsc-vn-creator/runtime
make convert JSON=/opt/wonderful/projects/wsc-vn-creator/examples/starter.wscvn.json
make NAME=starter-vn
```

Output:

```text
runtime/starter-vn.wsc
```

## 6. Build your exported game

Put your exported `.wscvn.json` somewhere convenient, then run:

```bash
cd /opt/wonderful/projects/wsc-vn-creator/runtime
make convert JSON=/c/Users/YOUR_USER/Downloads/MyGame.wscvn.json
make NAME=my-game
```

Output:

```text
runtime/my-game.wsc
```

If you changed assets or JSON, repeat both steps:

```bash
make convert JSON=/path/to/your/project.wscvn.json
make clean NAME=my-game
make NAME=my-game
```

## 7. Test the ROM

Recommended emulators:

- Mesen 2
- Ares
- Mednafen

Open the generated `.wsc` file in your emulator.
For real hardware, flash the `.wsc` file using your flash cartridge software.

## 8. Expected project layout after setup

```text
wsc-vn-creator/
├─ wsc-vn-studio.html
├─ examples/
│  └─ starter.wscvn.json
└─ runtime/
   ├─ Makefile
   ├─ wfconfig.toml
   ├─ src/
   │  ├─ main.c
   │  ├─ game_types.h
   │  └─ font.h
   ├─ tools/
   │  └─ convert_json.py
   ├─ music/
   │  └─ your .fur files copied by the converter/build process
   └─ third_party/
      └─ cygnals/   ← downloaded separately by you
```

## 9. What not to commit

Do not commit:

- `.wsc` ROM files
- private game assets
- generated `runtime/src/game_data.c`
- generated `runtime/src/game_data.h`
- `runtime/build/`
- downloaded toolchains
- built Cygnals binaries/library

The included `.gitignore` already blocks these.

## 10. Common fixes

### `Missing Pillow`

Install Pillow in the Python used by the Wonderful shell:

```bash
python -m pip install Pillow
```

### `target-wswan` missing

Install the WonderSwan target:

```bash
wf-pacman -S target-wswan
```

### Cygnals/fur2ws not found

Make sure Cygnals exists under:

```text
runtime/third_party/cygnals
```

Then build it:

```bash
cd runtime/third_party/cygnals
make TARGET=wswan/medium
```

### ROM builds but has no music

Check that your exported project uses `audioBackend: cygnals`, includes `.fur` music assets, and that Cygnals was built successfully.

### ROM is too large or allocator fails

Try reducing imported assets first:

- Use fewer unique backgrounds in one build.
- Prefer 224×144 backgrounds.
- Prefer 96×128 character sprites.
- Remove unused music/SFX/assets before export.
- Keep generated foregrounds only where they are visually worth it.
