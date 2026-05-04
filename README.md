# Wonderswan Color Visual Novel Creator
A browser-based visual novel editor plus WonderSwan Color runtime.

This clean export intentionally does not include game assets, ROM files, local builds, or toolchains.

## Features
Standalone browser-based visual novel editor, no install needed for editing.

Exports .wscvn.json projects for WonderSwan Color builds.

WonderSwan Color runtime in C for real hardware and emulators.

Dialogue, speaker names, choices, branching, flags, save/load, backlog, title menu, and return-to-title endings.

Imported PNG backgrounds, character sprites, foreground layers, textbox styles, speaker-colored decorations, and talk/blink animation.

Supports Furnace .fur music through Cygnals, plus simple SFX/dialogue blips.

ADV/investigation scenes with cursor-controlled hotspots.

Hardware-aware validation for text, assets, ROM budget, palettes, and node links.

Includes an asset-free starter project and clean setup workflow.

## Contents
- `wsc-vn-studio.html`  the standalone editor.
- `runtime/`  WonderSwan Color C runtime and JSON converter.
- `examples/`  tiny asset-free starter project.

Users should install Wonderful Toolchain, MSYS2/Python/Pillow, and Cygnals from official sources rather than committing them here.

## Setup
Follow [`SETUP.md`](SETUP.md) for the full step-by-step installation and ROM build workflow.

## Credits
This would not be possible without:

Wonderful Toolchain

https://wonderful.asie.pl/


Cygnals

https://github.com/joffb/cygnals

## Closing
This was made so I could finish a project that started back in 2021. 

It was the first (and last) story that I've written, turned into a visual novel relased by 2022 called Lost Sea.

Later that year I recruited some volunteers to create an improved version.

That didn't work out in the end (my fault) but I never gave up the idea of giving it closure.

It's now playable with some issues to iron out - mostly script adaptation to the Wonderswan.

It's my attempt to talk about a serious and sensitive topic - mental health.

If you are struggling, please know you're not alone.

Seek professional help. I will leave my e-mail at the end if you want to say something.

I'll try my best to answer everyone - joexkurtz at gmail.

This editor and Lost;Sea are both dedicated to 三浦隆一, who passed away 3 years ago.


