#!/usr/bin/env python3
"""
convert_json.py — WSC VN Studio JSON → C source for the WSC runtime.

Usage: python tools/convert_json.py <project.wscvn.json> <output_dir>

Emits: <output_dir>/game_data.c and <output_dir>/game_data.h

Tile format: 4BPP PACKED for WonderSwan Color.
32 bytes/tile, each byte = 2 pixels, high nibble = left pixel,
low nibble = right pixel. This matches main.c's pack_font_tile()
and the video mode set by hw_init() (I/O port 0x60 = 0xE0).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import wave
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps

# ── Hardware limits ──
MAX_TEXT_PER_BOX = 100
MAX_TITLE = 24
MAX_CHOICES = 4
MAX_BG_W = 224
MAX_BG_H = 144
MAX_CHAR_W = 96
MAX_CHAR_H = 128
MAX_FG_W = 224
MAX_FG_H = 144
MAX_FG_DRAW_H = 104
MAX_FG_PALETTES = 8

MAX_BG_TILES = 511
MAX_CHAR_TILES = 192
MAX_FG_TILES = 28 * 13

SFX_TARGET_RATE = 4000
SFX_MAX_SECONDS = 6.0
SFX_MAX_BYTES = SFX_TARGET_RATE * SFX_MAX_SECONDS  # 8-bit mono

# ── JSON → C mappings ──
NODE_TYPE = {
    'title': 'NODE_TITLE',
    'chapter': 'NODE_CHAPTER',
    'scene': 'NODE_SCENE',
    'choice': 'NODE_CHOICE',
    'branch': 'NODE_BRANCH',
    'investigation': 'NODE_INVESTIGATION',
    'end': 'NODE_END',
}
OP_C = {'add': 'OP_ADD', 'sub': 'OP_SUB', 'set': 'OP_SET', 'toggle': 'OP_TOGGLE'}
COND_C = {
    '==': 'COND_EQ', '!=': 'COND_NEQ',
    '>': 'COND_GT', '>=': 'COND_GTE',
    '<': 'COND_LT', '<=': 'COND_LTE',
}
SPEED_C = {'slow': 'SPEED_SLOW', 'normal': 'SPEED_NORMAL',
           'fast': 'SPEED_FAST', 'instant': 'SPEED_INSTANT'}
MUSIC_C = {'keep': 'MUSIC_KEEP', 'change': 'MUSIC_CHANGE',
           'stop': 'MUSIC_STOP', 'fade-out': 'MUSIC_FADE_OUT'}
SFXACT_C = {'keep': 'SFX_KEEP', 'change': 'SFX_CHANGE', 'stop': 'SFX_STOP'}
WAVE_C = {
    'square': 'WAVE_SQUARE',
    'triangle': 'WAVE_TRIANGLE',
    'sawtooth': 'WAVE_SAWTOOTH',
    'sine': 'WAVE_SINE',
    # Editor may label CH4 as "noise". Runtime currently treats all 4 channels
    # as wavetable for simplicity, so map noise to a safe wave.
    'noise': 'WAVE_SQUARE',
}
POS_C = {'none': 'POS_NONE', 'left': 'POS_LEFT',
         'center':'POS_CENTER', 'right': 'POS_RIGHT'}
TB_STYLE_C = {
    'dark':'0','glass':'1','classic':'2','light':'3','none':'4','fancy':'5',
    'frame':'6','double':'7','sidebars':'8','ocean':'9','royal':'10'
}
BG_PRESET_C = {
    'room':'0','school':'1','park':'2','cafe':'3','beach':'4','night':'5',
    'sky':'6','city':'7','forest':'8','space':'9','temple':'10','snow':'11',
}
PARTICLE_C = {'none':'0','sakura':'1','snow':'2','rain':'3','stars':'4',
              'leaves':'5','bubbles':'6','sparks':'7','dust':'8'}
SCREENFX_C = {'none':'0','vhs':'1','blur':'2','darker':'3','scanline':'4',
              'sepia':'5','invert':'6'}
TRANSITION_C = {'none':'0','fade':'1','wipe-r':'2','wipe-d':'3','circle':'4',
                'dissolve':'5','flash':'6','pixel':'7'}
CHARANIM_C = {'none':'0','slide-up':'1','slide-in':'2','fade':'3','pop':'4','blink':'5','talking':'6','talk-blink':'7'}


def esc(s: Any) -> str:
    """Escape a string for safe inclusion inside a C string literal."""
    if not s:
        return ''
    r = ''
    for ch in str(s):
        if ch == '\\': r += '\\\\'
        elif ch == '"': r += '\\"'
        elif ch == '\n': r += '\\n'
        elif ch == '\t': r += '\\t'
        elif ch == '\r': pass
        elif ord(ch) < 32 or ord(ch) > 126:
            r += '?'
        else:
            r += ch
    return r


def rewrite_inline_cmds(text: str, track2idx: dict[str, int], sfx2idx: dict[str, int]) -> str:
    """Rewrite editor-friendly inline commands into runtime-friendly ones.

    Supported:
      {pause} / {wait}
      {sfx:<assetId>} -> {sfx:<index>}
      {music:<trackId>} -> {music:<index>}
      {music:stop}
      {speed:slow|normal|fast|instant}
    """
    if not text:
        return ''

    def repl_sfx(m: re.Match[str]) -> str:
        key = (m.group(1) or '').strip()
        if not key:
            return ''
        idx = sfx2idx.get(key)
        return f'{{sfx:{idx}}}' if idx is not None else ''

    def repl_music(m: re.Match[str]) -> str:
        key = (m.group(1) or '').strip()
        if not key:
            return ''
        if key.lower() == 'stop':
            return '{music:stop}'
        idx = track2idx.get(key)
        return f'{{music:{idx}}}' if idx is not None else ''

    out = text
    out = re.sub(r'\{sfx:([^}]+)\}', repl_sfx, out)
    out = re.sub(r'\{music:([^}]+)\}', repl_music, out)
    # Normalize {wait} to {pause} for now (runtime treats both as a page break).
    out = out.replace('{wait}', '{pause}')
    return out


def hexcol(s: Any) -> int:
    try:
        return int((s or '#000000').lstrip('#'), 16)
    except Exception:
        return 0


def to_wsc_12bit(rgb):
    """Convert an (R,G,B) 24-bit tuple to WSC 12-bit packed color.

    WSC palette format (per WSMan spec):
      bits 11-8 = Red, bits 7-4 = Green, bits 3-0 = Blue  → RGB444

    Conversion: round(x / 17) maps 0..255 correctly to 0..15
    because 255 / 15 = 17. The old >> 4 (x//16) truncated down,
    causing darker/desaturated colors.
    """
    r, g, b = rgb[:3]
    r4 = min(15, round(r / 17))
    g4 = min(15, round(g / 17))
    b4 = min(15, round(b / 17))
    return (r4 << 8) | (g4 << 4) | b4


NOTE_RE = re.compile(r'^([A-G])([#b]?)(-?\d+)$')
NOTE_STEP_RE = re.compile(r'^([A-G])([#b]?)(-?\d+)_([0-9]+)$')
NOTE_SEMI = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def note_to_midi(note: str) -> int | None:
    m = NOTE_RE.match(note.strip())
    if not m:
        return None
    n, acc, oct_s = m.group(1), m.group(2), m.group(3)
    if n not in NOTE_SEMI:
        return None
    semi = NOTE_SEMI[n]
    if acc == '#':
        semi += 1
    elif acc == 'b':
        semi -= 1
    try:
        octave = int(oct_s)
    except Exception:
        return None
    # MIDI convention: C-1 = 0, C4 = 60.
    return (octave + 1) * 12 + (semi % 12)


def midi_to_hz(midi: int) -> int:
    # A4 (69) = 440 Hz.
    return max(1, int(round(440.0 * (2.0 ** ((midi - 69) / 12.0)))))


def ws_wave_hz_to_freq_div(hz: int, length: int = 32, system_clock_hz: int = 3072000) -> int:
    # Mirrors ws/sound.h + ws/util.h (WS_SOUND_WAVE_HZ_TO_FREQ / WS_HZ_TO_DIVIDER).
    clock = system_clock_hz // length
    divider = (clock + (((hz + 1) >> 1))) // hz
    return (-divider) & 0xFFFF


def wav_to_u8_pcm(raw_wav: bytes) -> bytes:
    """Read a WAV file as unsigned 8-bit mono PCM at the target rate.

    Note: We intentionally require an exact format (no MP3 decoding or resampling)
    to keep the converter dependency-free on MSYS/UCRT Python builds.
    """
    with wave.open(io.BytesIO(raw_wav), 'rb') as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()   # bytes
        rate = w.getframerate()
        nframes = w.getnframes()
        pcm = w.readframes(nframes)

    if ch != 1 or sw != 1 or rate != SFX_TARGET_RATE:
        raise ValueError(
            f'unsupported SFX WAV format: {ch}ch, {sw*8}bit, {rate}Hz '
            f'(expected 1ch, 8bit, {SFX_TARGET_RATE}Hz)'
        )

    if len(pcm) > int(SFX_MAX_BYTES):
        raise ValueError(f'sfx too long: {len(pcm)} bytes (~{len(pcm)/SFX_TARGET_RATE:.2f}s), max {SFX_MAX_SECONDS:.1f}s')

    return pcm


def pad_to_tiles(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Downscale if needed and pad width/height to multiples of 8."""
    w, h = img.size
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.Resampling.NEAREST)
    w, h = img.size
    pad_w = (8 - (w % 8)) % 8
    pad_h = (8 - (h % 8)) % 8
    if pad_w or pad_h:
        mode_fill = (0, 0, 0, 0) if 'A' in img.mode else (0, 0, 0)
        padded = Image.new(img.mode, (w + pad_w, h + pad_h), mode_fill)
        padded.paste(img, (0, 0))
        img = padded
    return img


def fit_cover_exact(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop to exactly fill the target size."""
    if img.size == (target_w, target_h):
        return img
    return ImageOps.fit(img, (target_w, target_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

def fit_contain_exact(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Preserve aspect ratio and pad to the exact target size with transparency."""
    rgba = img.convert('RGBA')
    if rgba.size == (target_w, target_h):
        return rgba
    return ImageOps.pad(rgba, (target_w, target_h), method=Image.Resampling.LANCZOS,
                        color=(0, 0, 0, 0), centering=(0.5, 0.5))

def _p_image_to_tiles(img: Image.Image, allow_transparency: bool, max_colors: int):
    """Fast path for already-indexed images that match the target size."""
    w, h = img.size
    raw = list(img.getdata())
    tile_bytes = bytearray()

    # Convert palette -> WSC 12-bit palette
    pal_raw = img.getpalette() or []
    palette = []
    for i in range(16):
        off = i * 3
        if off + 2 < len(pal_raw):
            palette.append(to_wsc_12bit(pal_raw[off:off + 3]))
        else:
            palette.append(0)

    if allow_transparency:
        trans = img.info.get('transparency')
        # Only trust the fast path when transparency is present and the image uses <= max_colors entries.
        if trans is None:
            return None
        used = set(raw)
        if len(used) > max_colors:
            return None
        indexed = list(raw)
    else:
        if img.info.get('transparency') is not None:
            return None
        used = set(raw)
        if len(used) > max_colors:
            return None
        indexed = list(raw)

    # pack tiles
    w_tiles, h_tiles = w // 8, h // 8
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            pixels = []
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    pixels.append(indexed[row + tx * 8 + xx])
            tile_bytes.extend(pack_tile_packed_4bpp(pixels))

    # normalize palette length to 16
    while len(palette) < 16:
        palette.append(0)
    return w_tiles, h_tiles, palette[:16], bytes(tile_bytes)


def decode_data_url(url: str) -> bytes:
    if not url:
        return b''
    if url.startswith('data:'):
        _header, b64 = url.split(',', 1)
        return base64.b64decode(b64)
    with open(url, 'rb') as f:
        return f.read()


def write_generated_fur_assets(root_dir: Path, music_assets: list[dict[str, Any]]) -> None:
    """Write imported Furnace songs to deterministic build inputs.

    The Makefile converts music/cyg_song_N.fur into symbols named cyg_song_N,
    which are referenced from the generated C data.
    """
    music_dir = root_dir / 'music'
    music_dir.mkdir(parents=True, exist_ok=True)
    for old in music_dir.glob('cyg_song_*.fur'):
        old.unlink()
    for old in (root_dir / 'sfx').glob('cyg_sfx_*.fur') if (root_dir / 'sfx').exists() else []:
        old.unlink()

    for i, asset in enumerate(music_assets):
        raw = decode_data_url(asset.get('dataUrl', ''))
        if not raw:
            raise ValueError(f'Furnace music "{asset.get("name", "?")}" has no .fur data')
        (music_dir / f'cyg_song_{i}.fur').write_bytes(raw)


def quantize_rgb(img: Image.Image, colors: int):
    """Quantize an RGB image down to <= colors colors. Returns
    (palettized_image, [wsc_12bit_colors]).

    FIXED: Uses FASTOCTREE instead of MEDIANCUT for better preservation
    of saturated and vibrant colors in small palettes.
    """
    rgb = img.convert('RGB')
    q = rgb.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                     dither=Image.Dither.NONE)
    pal_raw = q.getpalette()
    entries = []
    for i in range(colors):
        off = i * 3
        if off + 2 < len(pal_raw):
            entries.append(to_wsc_12bit(pal_raw[off:off + 3]))
        else:
            entries.append(0)
    return q, entries

def quantize_rgb_entries_from_pixels(pixels: list[tuple[int, int, int]], colors: int):
    if not pixels:
        return [], []
    sample = Image.new('RGB', (len(pixels), 1))
    sample.putdata(pixels)
    q = sample.quantize(colors=min(colors, max(1, len(set(pixels)))),
                        method=Image.Quantize.FASTOCTREE,
                        dither=Image.Dither.NONE)
    pal_raw = q.getpalette() or []
    rgb_entries = []
    wsc_entries = []
    for i in range(colors):
        off = i * 3
        if off + 2 < len(pal_raw):
            rgb = tuple(pal_raw[off:off + 3])
            rgb_entries.append(rgb)
            wsc_entries.append(to_wsc_12bit(rgb))
        else:
            rgb_entries.append((0, 0, 0))
            wsc_entries.append(0)
    return rgb_entries, wsc_entries

def nearest_palette_index(rgb: tuple[int, int, int], palette: list[tuple[int, int, int]]) -> int:
    best_i = 0
    best_d = 1 << 62
    r, g, b = rgb
    for i, (pr, pg, pb) in enumerate(palette):
        d = (r - pr) * (r - pr) + (g - pg) * (g - pg) + (b - pb) * (b - pb)
        if d < best_d:
            best_d = d
            best_i = i
    return min(best_i + 1, 15)

def image_to_char_tiles_dual(img: Image.Image, mode: str = 'top-bottom'):
    """Character conversion using two per-tile palettes.

    WSC tilemaps can choose a palette per tile. We split the sprite into top and
    bottom regions, giving face/hair-heavy tiles and body/clothes-heavy tiles
    separate 15-color palettes without extra tile VRAM.
    """
    rgba = fit_contain_exact(img, MAX_CHAR_W, MAX_CHAR_H).convert('RGBA')
    w, h = rgba.size
    w_tiles, h_tiles = w // 8, h // 8
    pix = list(rgba.getdata())
    mode = mode if mode in ('top-bottom', 'left-right', 'auto-tile') else 'top-bottom'
    split_y = h // 2
    split_x = w // 2
    tile_groups = []

    if mode == 'auto-tile':
        tile_avgs = []
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                total = [0, 0, 0]
                count = 0
                for yy in range(8):
                    row = (ty * 8 + yy) * w
                    for xx in range(8):
                        r, g, b, a = pix[row + tx * 8 + xx]
                        if a >= 128:
                            total[0] += r
                            total[1] += g
                            total[2] += b
                            count += 1
                if count:
                    tile_avgs.append((total[0] // count, total[1] // count, total[2] // count))
                else:
                    tile_avgs.append((0, 0, 0))
        c0 = min(tile_avgs, key=lambda c: c[0] + c[1] + c[2]) if tile_avgs else (0, 0, 0)
        c1 = max(tile_avgs, key=lambda c: c[0] + c[1] + c[2]) if tile_avgs else (255, 255, 255)
        for _ in range(6):
            buckets = [[], []]
            for c in tile_avgs:
                d0 = sum((c[i] - c0[i]) * (c[i] - c0[i]) for i in range(3))
                d1 = sum((c[i] - c1[i]) * (c[i] - c1[i]) for i in range(3))
                buckets[1 if d1 < d0 else 0].append(c)
            if buckets[0]:
                c0 = tuple(sum(c[i] for c in buckets[0]) // len(buckets[0]) for i in range(3))
            if buckets[1]:
                c1 = tuple(sum(c[i] for c in buckets[1]) // len(buckets[1]) for i in range(3))
        for c in tile_avgs:
            d0 = sum((c[i] - c0[i]) * (c[i] - c0[i]) for i in range(3))
            d1 = sum((c[i] - c1[i]) * (c[i] - c1[i]) for i in range(3))
            tile_groups.append(1 if d1 < d0 else 0)
    else:
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                if mode == 'left-right':
                    tile_groups.append(0 if (tx * 8 + 4) < split_x else 1)
                else:
                    tile_groups.append(0 if (ty * 8 + 4) < split_y else 1)

    groups = [[], []]
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            group = tile_groups[ty * w_tiles + tx]
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    r, g, b, a = pix[row + tx * 8 + xx]
                    if a >= 128:
                        groups[group].append((r, g, b))
    pal_rgb0, pal0 = quantize_rgb_entries_from_pixels(groups[0], 15)
    pal_rgb1, pal1 = quantize_rgb_entries_from_pixels(groups[1], 15)
    if not pal_rgb0:
        pal_rgb0, pal0 = pal_rgb1[:], pal1[:]
    if not pal_rgb1:
        pal_rgb1, pal1 = pal_rgb0[:], pal0[:]
    while len(pal0) < 15:
        pal0.append(0)
        pal_rgb0.append((0, 0, 0))
    while len(pal1) < 15:
        pal1.append(0)
        pal_rgb1.append((0, 0, 0))
    palette = [0] + pal0[:15]
    palette2 = [0] + pal1[:15]
    tile_bytes = bytearray()
    tile_pals = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            tile_group = tile_groups[ty * w_tiles + tx]
            pal_rgb = pal_rgb0 if tile_group == 0 else pal_rgb1
            tile_pals.append(tile_group)
            pixels = []
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    r, g, b, a = pix[row + tx * 8 + xx]
                    pixels.append(0 if a < 128 else nearest_palette_index((r, g, b), pal_rgb))
            tile_bytes.extend(pack_tile_packed_4bpp(pixels))
    return w_tiles, h_tiles, palette[:16], palette2[:16], bytes(tile_pals), bytes(tile_bytes)

def _char_tile_groups_for_image(rgba: Image.Image, mode: str):
    w, h = rgba.size
    w_tiles, h_tiles = w // 8, h // 8
    pix = list(rgba.getdata())
    mode = mode if mode in ('top-bottom', 'left-right', 'auto-tile') else 'top-bottom'
    split_y = h // 2
    split_x = w // 2
    tile_groups = []

    if mode == 'auto-tile':
        tile_avgs = []
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                total = [0, 0, 0]
                count = 0
                for yy in range(8):
                    row = (ty * 8 + yy) * w
                    for xx in range(8):
                        r, g, b, a = pix[row + tx * 8 + xx]
                        if a >= 128:
                            total[0] += r
                            total[1] += g
                            total[2] += b
                            count += 1
                tile_avgs.append((total[0] // count, total[1] // count, total[2] // count) if count else (0, 0, 0))
        c0 = min(tile_avgs, key=lambda c: c[0] + c[1] + c[2]) if tile_avgs else (0, 0, 0)
        c1 = max(tile_avgs, key=lambda c: c[0] + c[1] + c[2]) if tile_avgs else (255, 255, 255)
        for _ in range(6):
            buckets = [[], []]
            for c in tile_avgs:
                d0 = sum((c[i] - c0[i]) * (c[i] - c0[i]) for i in range(3))
                d1 = sum((c[i] - c1[i]) * (c[i] - c1[i]) for i in range(3))
                buckets[1 if d1 < d0 else 0].append(c)
            if buckets[0]:
                c0 = tuple(sum(c[i] for c in buckets[0]) // len(buckets[0]) for i in range(3))
            if buckets[1]:
                c1 = tuple(sum(c[i] for c in buckets[1]) // len(buckets[1]) for i in range(3))
        for c in tile_avgs:
            d0 = sum((c[i] - c0[i]) * (c[i] - c0[i]) for i in range(3))
            d1 = sum((c[i] - c1[i]) * (c[i] - c1[i]) for i in range(3))
            tile_groups.append(1 if d1 < d0 else 0)
    else:
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                if mode == 'left-right':
                    tile_groups.append(0 if (tx * 8 + 4) < split_x else 1)
                else:
                    tile_groups.append(0 if (ty * 8 + 4) < split_y else 1)
    return tile_groups

def image_to_char_tiles_dual_shared(img: Image.Image, mode: str, group_imgs: list[Image.Image]):
    """Character conversion with palettes shared by animation-linked frames.

    Animation frames must swap without palette drift. This builds the two
    palettes from every frame in the group while keeping the base frame's tile
    palette assignment, so talk/blink frames do not remap hair/body colors.
    """
    fitted = [fit_contain_exact(frame, MAX_CHAR_W, MAX_CHAR_H).convert('RGBA') for frame in group_imgs]
    rgba = fit_contain_exact(img, MAX_CHAR_W, MAX_CHAR_H).convert('RGBA')
    base = fitted[0] if fitted else rgba
    w, h = rgba.size
    w_tiles, h_tiles = w // 8, h // 8
    tile_groups = _char_tile_groups_for_image(base, mode)

    groups = [[], []]
    for frame in fitted:
        pix = list(frame.getdata())
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                group = tile_groups[ty * w_tiles + tx]
                for yy in range(8):
                    row = (ty * 8 + yy) * w
                    for xx in range(8):
                        r, g, b, a = pix[row + tx * 8 + xx]
                        if a >= 128:
                            groups[group].append((r, g, b))

    pal_rgb0, pal0 = quantize_rgb_entries_from_pixels(groups[0], 15)
    pal_rgb1, pal1 = quantize_rgb_entries_from_pixels(groups[1], 15)
    if not pal_rgb0:
        pal_rgb0, pal0 = pal_rgb1[:], pal1[:]
    if not pal_rgb1:
        pal_rgb1, pal1 = pal_rgb0[:], pal0[:]
    while len(pal0) < 15:
        pal0.append(0)
        pal_rgb0.append((0, 0, 0))
    while len(pal1) < 15:
        pal1.append(0)
        pal_rgb1.append((0, 0, 0))

    palette = [0] + pal0[:15]
    palette2 = [0] + pal1[:15]
    pix = list(rgba.getdata())
    tile_bytes = bytearray()
    tile_pals = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            tile_group = tile_groups[ty * w_tiles + tx]
            pal_rgb = pal_rgb0 if tile_group == 0 else pal_rgb1
            tile_pals.append(tile_group)
            pixels = []
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    r, g, b, a = pix[row + tx * 8 + xx]
                    pixels.append(0 if a < 128 else nearest_palette_index((r, g, b), pal_rgb))
            tile_bytes.extend(pack_tile_packed_4bpp(pixels))
    return w_tiles, h_tiles, palette[:16], palette2[:16], bytes(tile_pals), bytes(tile_bytes)

def image_to_bg_tiles_dual(img: Image.Image, mode: str = 'top-bottom'):
    rgb = fit_cover_exact(img, MAX_BG_W, MAX_BG_H).convert('RGB')
    w, h = rgb.size
    w_tiles, h_tiles = w // 8, h // 8
    pix = list(rgb.getdata())
    mode = mode if mode in ('top-bottom', 'left-right', 'auto-tile') else 'top-bottom'
    split_y = h // 2
    split_x = w // 2
    tile_groups = []
    if mode == 'auto-tile':
        tile_avgs = []
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                total = [0, 0, 0]
                for yy in range(8):
                    row = (ty * 8 + yy) * w
                    for xx in range(8):
                        r, g, b = pix[row + tx * 8 + xx]
                        total[0] += r
                        total[1] += g
                        total[2] += b
                tile_avgs.append((total[0] // 64, total[1] // 64, total[2] // 64))
        c0 = min(tile_avgs, key=lambda c: c[0] + c[1] + c[2]) if tile_avgs else (0, 0, 0)
        c1 = max(tile_avgs, key=lambda c: c[0] + c[1] + c[2]) if tile_avgs else (255, 255, 255)
        for _ in range(6):
            buckets = [[], []]
            for c in tile_avgs:
                d0 = sum((c[i] - c0[i]) * (c[i] - c0[i]) for i in range(3))
                d1 = sum((c[i] - c1[i]) * (c[i] - c1[i]) for i in range(3))
                buckets[1 if d1 < d0 else 0].append(c)
            if buckets[0]:
                c0 = tuple(sum(c[i] for c in buckets[0]) // len(buckets[0]) for i in range(3))
            if buckets[1]:
                c1 = tuple(sum(c[i] for c in buckets[1]) // len(buckets[1]) for i in range(3))
        for c in tile_avgs:
            d0 = sum((c[i] - c0[i]) * (c[i] - c0[i]) for i in range(3))
            d1 = sum((c[i] - c1[i]) * (c[i] - c1[i]) for i in range(3))
            tile_groups.append(1 if d1 < d0 else 0)
    else:
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                if mode == 'left-right':
                    tile_groups.append(0 if (tx * 8 + 4) < split_x else 1)
                else:
                    tile_groups.append(0 if (ty * 8 + 4) < split_y else 1)
    groups = [[], []]
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            group = tile_groups[ty * w_tiles + tx]
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    groups[group].append(pix[row + tx * 8 + xx])
    pal_rgb0, pal0 = quantize_rgb_entries_from_pixels(groups[0], 16)
    pal_rgb1, pal1 = quantize_rgb_entries_from_pixels(groups[1], 16)
    if not pal_rgb0:
        pal_rgb0, pal0 = pal_rgb1[:], pal1[:]
    if not pal_rgb1:
        pal_rgb1, pal1 = pal_rgb0[:], pal0[:]
    while len(pal0) < 16:
        pal0.append(0)
        pal_rgb0.append((0, 0, 0))
    while len(pal1) < 16:
        pal1.append(0)
        pal_rgb1.append((0, 0, 0))
    tile_bytes = bytearray()
    tile_pals = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            tile_group = tile_groups[ty * w_tiles + tx]
            pal_rgb = pal_rgb0 if tile_group == 0 else pal_rgb1
            tile_pals.append(tile_group)
            pixels = []
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    pixels.append(nearest_palette_index(pix[row + tx * 8 + xx], pal_rgb) - 1)
            tile_bytes.extend(pack_tile_packed_4bpp(pixels))
    return w_tiles, h_tiles, pal0[:16], pal1[:16], bytes(tile_pals), bytes(tile_bytes)

def _cluster_tile_averages(tile_avgs: list[tuple[int, int, int]], k: int) -> list[int]:
    if not tile_avgs:
        return []
    k = max(1, min(k, len(tile_avgs)))
    ordered = sorted(tile_avgs, key=lambda c: c[0] + c[1] + c[2])
    centers = [ordered[(i * (len(ordered) - 1)) // max(1, k - 1)] for i in range(k)]
    groups = [0] * len(tile_avgs)
    for _ in range(8):
        buckets = [[] for _ in range(k)]
        for idx, c in enumerate(tile_avgs):
            best = min(range(k), key=lambda i: sum((c[j] - centers[i][j]) * (c[j] - centers[i][j]) for j in range(3)))
            groups[idx] = best
            buckets[best].append(c)
        for i, bucket in enumerate(buckets):
            if bucket:
                centers[i] = tuple(sum(c[j] for c in bucket) // len(bucket) for j in range(3))
    return groups

def image_to_fg_tiles_multi(img: Image.Image, mode: str = 'auto-tile', group_imgs: list[Image.Image] | None = None):
    """Transparent foreground/group-shot layer using up to 8 per-tile palettes.

    Foregrounds replace live character sprites, so the runtime can borrow the
    otherwise-unused character palette slots plus two free palette slots. Each
    8x8 tile chooses one 15-color palette + transparency, giving group shots
    far better color while keeping the textbox palettes untouched.
    """
    rgba_full = fit_contain_exact(img, MAX_FG_W, MAX_FG_H).convert('RGBA')
    rgba = rgba_full.crop((0, 0, MAX_FG_W, MAX_FG_DRAW_H))
    fitted_group = []
    if group_imgs:
        for frame in group_imgs:
            fitted_group.append(fit_contain_exact(frame, MAX_FG_W, MAX_FG_H).convert('RGBA').crop((0, 0, MAX_FG_W, MAX_FG_DRAW_H)))
    if not fitted_group:
        fitted_group = [rgba]
    base_rgba = fitted_group[0]
    w, h = rgba.size
    w_tiles, h_tiles = w // 8, h // 8
    pix = list(rgba.getdata())
    base_pix = list(base_rgba.getdata())
    mode = mode if mode in ('top-bottom', 'left-right', 'auto-tile') else 'auto-tile'
    if mode == 'top-bottom':
        tile_groups = [min(MAX_FG_PALETTES - 1, (ty * MAX_FG_PALETTES) // max(1, h_tiles)) for ty in range(h_tiles) for _tx in range(w_tiles)]
    elif mode == 'left-right':
        tile_groups = [min(MAX_FG_PALETTES - 1, (tx * MAX_FG_PALETTES) // max(1, w_tiles)) for _ty in range(h_tiles) for tx in range(w_tiles)]
    else:
        tile_avgs = []
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                total = [0, 0, 0]
                count = 0
                for yy in range(8):
                    row = (ty * 8 + yy) * w
                    for xx in range(8):
                        r, g, b, a = base_pix[row + tx * 8 + xx]
                        if a >= 128:
                            total[0] += r
                            total[1] += g
                            total[2] += b
                            count += 1
                tile_avgs.append((total[0] // count, total[1] // count, total[2] // count) if count else (0, 0, 0))
        tile_groups = _cluster_tile_averages(tile_avgs, MAX_FG_PALETTES)

    groups = [[] for _ in range(MAX_FG_PALETTES)]
    for frame in fitted_group:
        frame_pix = list(frame.getdata())
        for ty in range(h_tiles):
            for tx in range(w_tiles):
                group = tile_groups[ty * w_tiles + tx]
                for yy in range(8):
                    row = (ty * 8 + yy) * w
                    for xx in range(8):
                        r, g, b, a = frame_pix[row + tx * 8 + xx]
                        if a >= 128:
                            groups[group].append((r, g, b))
    palettes_rgb = []
    palettes = []
    fallback_rgb = [(0, 0, 0)] * 15
    fallback_pal = [0] * 15
    for group_pixels in groups:
        pal_rgb, pal = quantize_rgb_entries_from_pixels(group_pixels, 15)
        if not pal_rgb:
            pal_rgb, pal = fallback_rgb[:], fallback_pal[:]
        else:
            fallback_rgb, fallback_pal = pal_rgb[:], pal[:]
        while len(pal) < 15:
            pal.append(0)
            pal_rgb.append((0, 0, 0))
        palettes_rgb.append(pal_rgb[:15])
        palettes.append([0] + pal[:15])
    if all(not g for g in groups):
        palettes = [[0] * 16 for _ in range(MAX_FG_PALETTES)]
        palettes_rgb = [[(0, 0, 0)] * 15 for _ in range(MAX_FG_PALETTES)]
    tile_bytes = bytearray()
    tile_pals = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            tile_group = tile_groups[ty * w_tiles + tx]
            pal_rgb = palettes_rgb[tile_group]
            tile_pals.append(tile_group)
            pixels = []
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    r, g, b, a = pix[row + tx * 8 + xx]
                    pixels.append(0 if a < 128 else nearest_palette_index((r, g, b), pal_rgb))
            tile_bytes.extend(pack_tile_packed_4bpp(pixels))
    return w_tiles, h_tiles, palettes[0][:16], palettes[1][:16], bytes(tile_pals), bytes(tile_bytes), palettes[:MAX_FG_PALETTES]


def pack_tile_packed_4bpp(pix: list) -> bytes:
    """Pack 64 4-bit indices (8x8) into 32 bytes, WSC 4bpp PACKED format.
    Each byte = 2 pixels: high nibble = left, low nibble = right."""
    out = bytearray(32)
    for y in range(8):
        for x in range(4):
            p_left  = pix[y * 8 + x * 2 + 0] & 0x0F
            p_right = pix[y * 8 + x * 2 + 1] & 0x0F
            out[y * 4 + x] = (p_left << 4) | p_right
    return bytes(out)



def image_to_tiles(img: Image.Image, kind: str, palette_mode: str = 'top-bottom'):
    """Returns (w_tiles, h_tiles, palette[16], palette2/None, tile_pals/None, tile_bytes[, palettes])."""
    if kind == 'bg':
        img = fit_cover_exact(img, MAX_BG_W, MAX_BG_H)
        if palette_mode in ('top-bottom', 'left-right', 'auto-tile'):
            return image_to_bg_tiles_dual(img, palette_mode)
        # Fast path: already indexed and exact size
        if img.mode == 'P':
            fast = _p_image_to_tiles(img, allow_transparency=False, max_colors=16)
            if fast is not None:
                w_tiles, h_tiles, palette, tiles = fast
                return w_tiles, h_tiles, palette, None, None, tiles

        rgb = img.convert('RGB')
        q, pal = quantize_rgb(rgb, 16)
        indexed = list(q.getdata())
        palette = pal[:16]

        w, h = rgb.size
        w_tiles, h_tiles = w // 8, h // 8

    elif kind == 'fg':
        return image_to_fg_tiles_multi(img, palette_mode)

    else:
        return image_to_char_tiles_dual(img, palette_mode)
        img = fit_contain_exact(img, MAX_CHAR_W, MAX_CHAR_H)
        # Fast path: already indexed, exact size and transparent
        if img.mode == 'P':
            fast = _p_image_to_tiles(img, allow_transparency=True, max_colors=16)
            if fast is not None:
                return fast

        rgba = img.convert('RGBA')
        alpha = list(rgba.getchannel('A').getdata())
        q, pal = quantize_rgb(rgba, 15)
        pix_q = list(q.getdata())
        # Index 0 = transparent; shift opaque indices by +1, clamp to 15
        indexed = [0 if alpha[i] < 128 else min(int(pix_q[i]) + 1, 15)
                   for i in range(len(pix_q))]
        palette = [0] + pal[:15]

        w, h = rgba.size
        w_tiles, h_tiles = w // 8, h // 8

    tile_bytes = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            pixels = []
            for yy in range(8):
                row = (ty * 8 + yy) * w
                for xx in range(8):
                    pixels.append(indexed[row + tx * 8 + xx])
            tile_bytes.extend(pack_tile_packed_4bpp(pixels))

    while len(palette) < 16:
        palette.append(0)
    return w_tiles, h_tiles, palette[:16], None, None, bytes(tile_bytes)

@dataclass
class AssetPack:
    name: str
    w_tiles: int
    h_tiles: int
    tile_count: int
    palette: list
    palette2: list | None
    tile_pals: bytes | None
    tiles: bytes
    palettes: list[list] | None = None



def convert_asset(asset: dict, kind: str, shared_groups: dict | None = None) -> AssetPack:
    raw = decode_data_url(asset.get('dataUrl', ''))
    if not raw:
        raise ValueError(f'asset "{asset.get("name", "?")}" has no image data')
    img = Image.open(io.BytesIO(raw))

    if kind == 'bg':
        w_tiles, h_tiles, palette, palette2, tile_pals, tiles = image_to_tiles(img, 'bg', asset.get('paletteMode', 'top-bottom'))
        tile_count = w_tiles * h_tiles
        if tile_count > MAX_BG_TILES:
            raise ValueError(
                f'background "{asset.get("name", "?")}" is too large '
                f'({tile_count} tiles, max {MAX_BG_TILES})')
    elif kind == 'fg':
        shared_assets = (shared_groups or {}).get(asset.get('id'))
        group_imgs = []
        if shared_assets:
            for group_asset in shared_assets:
                group_raw = decode_data_url(group_asset.get('dataUrl', ''))
                if group_raw:
                    group_imgs.append(Image.open(io.BytesIO(group_raw)))
        fg_pack = image_to_fg_tiles_multi(img, asset.get('paletteMode', 'auto-tile'), group_imgs or None)
        w_tiles, h_tiles, palette, palette2, tile_pals, tiles = fg_pack[:6]
        palettes = fg_pack[6] if len(fg_pack) > 6 else None
        tile_count = w_tiles * h_tiles
        if tile_count > MAX_FG_TILES:
            raise ValueError(
                f'foreground "{asset.get("name", "?")}" is too large '
                f'({tile_count} tiles, max {MAX_FG_TILES})')
        return AssetPack(asset.get('id', ''), w_tiles, h_tiles,
                         tile_count, palette, palette2, tile_pals, tiles, palettes)
    else:
        shared_assets = (shared_groups or {}).get(asset.get('id'))
        if shared_assets:
            group_imgs = []
            for group_asset in shared_assets:
                group_raw = decode_data_url(group_asset.get('dataUrl', ''))
                if group_raw:
                    group_imgs.append(Image.open(io.BytesIO(group_raw)))
            w_tiles, h_tiles, palette, palette2, tile_pals, tiles = image_to_char_tiles_dual_shared(
                img, asset.get('paletteMode', 'top-bottom'), group_imgs)
        else:
            w_tiles, h_tiles, palette, palette2, tile_pals, tiles = image_to_tiles(img, 'char', asset.get('paletteMode', 'top-bottom'))
        tile_count = w_tiles * h_tiles
        if tile_count > MAX_CHAR_TILES:
            raise ValueError(
                f'character "{asset.get("name", "?")}" is too large '
                f'({tile_count} tiles, max {MAX_CHAR_TILES})')

    return AssetPack(asset.get('id', ''), w_tiles, h_tiles,
                     tile_count, palette, palette2, tile_pals, tiles)

def validate(project):
    errs, warns = [], []
    flag_names = {f['name'] for f in project.get('flags', []) if f.get('name')}
    bg_ids   = {a.get('id') for a in project.get('assets', {}).get('backgrounds', [])}
    fg_ids   = {a.get('id') for a in project.get('assets', {}).get('foregrounds', [])}
    char_ids = {a.get('id') for a in project.get('assets', {}).get('characters', [])}
    audio_backend = str(project.get('audioBackend', 'legacy') or 'legacy').lower()
    if audio_backend not in ('legacy', 'cygnals'):
        audio_backend = 'legacy'
    track_ids_legacy = {t.get('id') for t in project.get('tracks', []) if t.get('id')}
    track_ids_cygnals = {t.get('id') for t in project.get('assets', {}).get('musicFur', []) if t.get('id')}
    sfx_ids_legacy = {a.get('id') for a in project.get('assets', {}).get('sfx', [])}
    if audio_backend == 'cygnals' and project.get('assets', {}).get('sfxFur'):
        warns.append('Cygnals .fur SFX are not exported yet; use PCM SFX for now.')

    for n in project['nodes']:
        nm, t = n.get('name', '?'), n.get('type', '')

        if t == 'scene':
            dlg = n.get('dialogue', '') or ''
            for i, b in enumerate(dlg.split('{pause}')):
                if len(b) > MAX_TEXT_PER_BOX:
                    errs.append(
                        f'"{nm}" text block {i+1}: {len(b)}/{MAX_TEXT_PER_BOX} chars '
                        f'(use {{pause}} to split, or shorten)')

            if n.get('musicAction') == 'change':
                tid = n.get('musicTrack') or ''
                if tid:
                    track_ids = track_ids_cygnals if audio_backend == 'cygnals' else track_ids_legacy
                    backend_label = 'Cygnals .fur music' if audio_backend == 'cygnals' else 'legacy tracker'
                    if tid not in track_ids:
                        warns.append(f'"{nm}" musicTrack "{tid}" is not a {backend_label} track id')

            if n.get('sfx'):
                sid = n.get('sfx')
                if sid not in sfx_ids_legacy:
                    warns.append(f'"{nm}" sfx "{sid}" is not in assets.sfx (PCM SFX backend)')

        if t == 'choice':
            nc = len(n.get('choices') or [])
            if nc == 0:
                warns.append(f'"{nm}" has no choices')
            if nc > MAX_CHOICES:
                errs.append(f'"{nm}" has {nc} choices (max {MAX_CHOICES})')
            for c in (n.get('choices') or []):
                for op in (c.get('flagOps') or []):
                    nm_f = op.get('name')
                    if nm_f and nm_f not in flag_names:
                        errs.append(f'"{nm}" choice uses undefined flag "{nm_f}"')

        if t == 'branch':
            for b in (n.get('branches') or []):
                if b.get('flag') and b['flag'] not in flag_names:
                    errs.append(f'"{nm}" branch uses undefined flag "{b["flag"]}"')

        if n.get('bgImageId') and n['bgImageId'] not in bg_ids:
            errs.append(f'"{nm}" references missing background asset id "{n["bgImageId"]}"')
        if n.get('fgImageId') and n['fgImageId'] not in fg_ids:
            errs.append(f'"{nm}" references missing foreground asset id "{n["fgImageId"]}"')
        if n.get('fgTalkImageId') and n['fgTalkImageId'] not in fg_ids:
            errs.append(f'"{nm}" references missing foreground talk asset id "{n["fgTalkImageId"]}"')
        if n.get('fgBlinkImageId') and n['fgBlinkImageId'] not in fg_ids:
            errs.append(f'"{nm}" references missing foreground blink asset id "{n["fgBlinkImageId"]}"')

        for key in ('charId', 'char2Id', 'char3Id'):
            if n.get(key) and n[key] not in char_ids:
                errs.append(f'"{nm}" references missing character asset id "{n[key]}"')

    return errs, warns


def emit(project, out_dir):
    nodes      = project['nodes']
    flags      = project.get('flags', [])
    tracks     = project.get('tracks', [])
    assets     = project.get('assets', {}) or {}
    bg_assets  = assets.get('backgrounds', []) or []
    fg_assets  = assets.get('foregrounds', []) or []
    char_assets = assets.get('characters', []) or []
    sfx_assets = assets.get('sfx', []) or []

    # Audio backend
    # -------------
    # Legacy uses the built-in tracker + PCM SFX. Cygnals uses imported Furnace
    # .fur files for music, while SFX stay on the known-stable PCM path.
    audio_backend = str(project.get('audioBackend', 'legacy') or 'legacy').lower()
    if audio_backend not in ('legacy', 'cygnals'):
        print(f'[!] Unknown audio backend "{audio_backend}"; exporting as "legacy".')
        audio_backend = 'legacy'
    font_style_map = {'classic': 0}
    font_style = font_style_map.get(str(project.get('fontStyle', 'classic') or 'classic').lower(), 0)

    music_fur_assets = assets.get('musicFur', []) or []
    sfx_fur_assets = []
    use_cygnals = (audio_backend == 'cygnals' and len(music_fur_assets) > 0)
    if audio_backend == 'cygnals' and not use_cygnals:
        print('[!] Cygnals backend selected but no .fur music assets exist; exporting legacy music.')
    if audio_backend == 'cygnals' and (assets.get('sfxFur', []) or []):
        print('[!] Cygnals .fur SFX are ignored for now; PCM SFX remain enabled.')

    root_dir = Path(out_dir).resolve().parent
    write_generated_fur_assets(root_dir, music_fur_assets if use_cygnals else [])

    tracks_emit = [] if use_cygnals else tracks
    sfx_assets_emit = sfx_assets
    music_track_assets = music_fur_assets if use_cygnals else tracks
    sfx_id_assets = sfx_assets

    # ── Node ordering for runtime ──
    #
    # The engine advances sequentially by node index when a scene/choice/branch
    # does not explicitly jump elsewhere. If a choice target node is emitted
    # *before* the choice node, picking that route will jump "backwards" and
    # then the engine will continue forward into previously played content
    # (appearing like it "goes back" to earlier scenes).
    #
    # To make route flow sane without requiring the author to manually reorder
    # nodes, we reorder nodes with a stable topological sort that enforces:
    # - Title node is always index 0 (so New Game advances correctly)
    # - parent → child (chapters before their contents)
    # - explicit jumps (scene.next / choice.target / branch targets) require
    #   source → target in the emitted order
    def reorder_nodes_for_runtime(node_list):
        if not node_list:
            return node_list

        # Preserve original relative order as tie-breaker.
        orig_idx = {n.get('id'): i for i, n in enumerate(node_list) if n.get('id')}
        ids = [n.get('id') for n in node_list if n.get('id')]
        if len(ids) != len(node_list):
            return node_list

        id2node = {n.get('id'): n for n in node_list}
        edges = set()

        def add_edge(aid, bid):
            if not aid or not bid or aid == bid:
                return
            if aid not in orig_idx or bid not in orig_idx:
                return
            edges.add((aid, bid))

        for n in node_list:
            nid = n.get('id')
            if not nid:
                continue
            pid = n.get('parent')
            if pid:
                add_edge(pid, nid)

            t = n.get('type')
            if t in ('scene', 'title'):
                add_edge(nid, n.get('next'))
            elif t == 'choice':
                for ch in (n.get('choices') or []):
                    add_edge(nid, ch.get('target'))
            elif t == 'branch':
                for br in (n.get('branches') or []):
                    add_edge(nid, br.get('target'))
                add_edge(nid, n.get('defaultTarget'))
            elif t == 'investigation':
                add_edge(nid, n.get('next') or n.get('defaultTarget'))
                for hs in (n.get('hotspots') or []):
                    add_edge(nid, hs.get('target'))

        indeg = {nid: 0 for nid in ids}
        adj = {nid: [] for nid in ids}
        for a, b in edges:
            adj[a].append(b)
            indeg[b] += 1

        import heapq
        heap = []
        for nid in ids:
            if indeg[nid] == 0:
                heapq.heappush(heap, (orig_idx[nid], nid))

        out = []
        while heap:
            _, nid = heapq.heappop(heap)
            out.append(id2node[nid])
            for b in adj[nid]:
                indeg[b] -= 1
                if indeg[b] == 0:
                    heapq.heappush(heap, (orig_idx[b], b))

        if len(out) != len(node_list):
            placed = {n.get('id') for n in out}
            for nid in ids:
                if nid not in placed:
                    out.append(id2node[nid])
        return out

    # Keep title as node 0 so run_title() can advance to "next in order".
    title_idx = next((i for i, n in enumerate(nodes) if n.get('type') == 'title'), None)
    if title_idx is not None:
        title_node = nodes[title_idx]
        rest = [n for i, n in enumerate(nodes) if i != title_idx]
        rest = reorder_nodes_for_runtime(rest)
        nodes = [title_node] + rest
    else:
        nodes = reorder_nodes_for_runtime(nodes)

    id2idx    = {n['id']: i for i, n in enumerate(nodes)}
    flag2idx  = {f['name']: i for i, f in enumerate(flags) if f.get('name')}
    track2idx = {t['id']: i for i, t in enumerate(music_track_assets) if t.get('id')}
    bg2idx    = {a['id']: i for i, a in enumerate(bg_assets)  if a.get('id')}
    fg2idx    = {a['id']: i for i, a in enumerate(fg_assets)  if a.get('id')}
    ch2idx    = {a['id']: i for i, a in enumerate(char_assets) if a.get('id')}
    sfx2idx   = {a['id']: i for i, a in enumerate(sfx_id_assets) if a.get('id')}
    char_by_id = {a.get('id'): a for a in char_assets if a.get('id')}
    fg_by_id = {a.get('id'): a for a in fg_assets if a.get('id')}
    shared_char_groups = {}
    shared_fg_groups = {}

    def add_shared_char_group(ids):
        clean = []
        for asset_id in ids:
            if asset_id and asset_id in char_by_id and asset_id not in clean:
                clean.append(asset_id)
        if len(clean) < 2:
            return
        group_assets = [char_by_id[asset_id] for asset_id in clean]
        for asset_id in clean:
            shared_char_groups[asset_id] = group_assets

    for n in nodes:
        if n.get('char2Pos') == 'none' and n.get('charAnim') in ('blink', 'talking', 'talk-blink'):
            add_shared_char_group([n.get('charId'), n.get('char2Id'), n.get('char3Id')])
        if n.get('fgImageId') and n.get('charAnim') in ('blink', 'talking', 'talk-blink'):
            clean = []
            for asset_id in (n.get('fgImageId'), n.get('fgTalkImageId'), n.get('fgBlinkImageId')):
                if asset_id and asset_id in fg_by_id and asset_id not in clean:
                    clean.append(asset_id)
            if len(clean) >= 2:
                group_assets = [fg_by_id[asset_id] for asset_id in clean]
                for asset_id in clean:
                    shared_fg_groups[asset_id] = group_assets

    ui_sfx_text = sfx2idx.get(project.get('uiSfxText', ''), 0xFF)
    ui_sfx_cursor = sfx2idx.get(project.get('uiSfxCursor', ''), 0xFF)
    ui_sfx_confirm = sfx2idx.get(project.get('uiSfxConfirm', ''), 0xFF)

    def compute_build_id_16():
        # Simple deterministic 16-bit hash over the ordered node IDs.
        h = 0
        for n in nodes:
            nid = str(n.get('id', '') or '')
            for b in nid.encode('utf-8', errors='ignore'):
                h = (h * 31 + b) & 0xFFFF
        return h or 1

    def resolve(tid):
        if not tid:
            return 0xFFFF
        return id2idx.get(tid, 0xFFFF)

    lines = []
    W = lines.append
    W('/* game_data.c — AUTO-GENERATED. DO NOT EDIT. */')
    W('#include <stddef.h>')
    W('#include <wonderful.h>')
    W('#include "game_types.h"')
    W('#include "game_data.h"')
    W('')

    # ── Flag initial values ──
    if flags:
        W('const int16_t FLAG_INITIAL_VALUES[NUM_FLAGS] = {')
        for f in flags:
            W(f'  {int(f.get("initial", 0))},  /* {f["name"]} */')
        W('};'  )
    else:
        W('const int16_t FLAG_INITIAL_VALUES[1] = {0};')
    W('')

    # ── String pool (with interning) ──
    str_map = {}
    str_ctr = [0]
    W('static const char __far empty_str[] = { 0x00 };')

    def emit_far_c_string(name, s):
        data = (str(s) if s else '').encode('utf-8')
        bytes_hex = ', '.join(f'0x{b:02X}' for b in data + b'\x00')
        W(f'static const char __far {name}[] = {{ {bytes_hex} }};')

    def intern_str(s):
        s = str(s) if s else ''
        if not s:
            return 'empty_str'
        if s in str_map:
            return str_map[s]
        name = f's{str_ctr[0]}'
        str_ctr[0] += 1
        emit_far_c_string(name, s)
        str_map[s] = name
        return name

    for n in nodes:
        intern_str(n.get('speaker', ''))
        intern_str(n.get('dialogue', ''))
        intern_str(n.get('titleMain', ''))
        intern_str(n.get('titleSub', ''))
        intern_str(n.get('prompt', ''))
        for item in (n.get('titleMenu', '') or '').split('|'):
            intern_str(item.strip())
        for ch in (n.get('choices') or []):
            intern_str(ch.get('text', ''))
        for hs in (n.get('hotspots') or []):
            intern_str(hs.get('text', ''))
    W('')

    # ── Asset emission ──
    # ── Runtime labels (save slots, gallery) ──
    node_name_symbols = []
    for i, n in enumerate(nodes):
        nm = f'node_name_{i}'
        emit_far_c_string(nm, n.get('name', '') or f'Node {i+1}')
        node_name_symbols.append(nm)
    W(f'const char __far * const __far NODE_NAMES[{len(node_name_symbols) if node_name_symbols else 1}] = {{')
    if node_name_symbols:
        for nm in node_name_symbols:
            W(f'  {nm},')
    else:
        W('  empty_str,')
    W('};')
    W('')

    if bg_assets:
        bg_name_symbols = []
        for i, a in enumerate(bg_assets):
            nm = f'bg_name_{i}'
            emit_far_c_string(nm, a.get('name', '') or a.get('id', '') or f'CG {i+1}')
            bg_name_symbols.append(nm)
        W(f'const char __far * const __far BG_ASSET_NAMES[{len(bg_name_symbols)}] = {{')
        for nm in bg_name_symbols:
            W(f'  {nm},')
        W('};')
    else:
        W('const char __far * const __far BG_ASSET_NAMES[1] = { empty_str };')
    W('')

    def emit_assets(arr, prefix, allow_transparency):
        if not arr:
            W(f'const image_asset_t __far {prefix}[1] = '
              f'{{ {{ 0, 0, 0, NULL, NULL, NULL, NULL, NULL, 0 }} }};')
            W('')
            return

        packs = []
        for a in arr:
            kind = 'char' if allow_transparency else ('fg' if prefix == 'FG_ASSETS' else 'bg')
            shared_groups = shared_char_groups if kind == 'char' else (shared_fg_groups if kind == 'fg' else None)
            pack = convert_asset(a, kind,
                                 shared_groups)
            packs.append(pack)

        for i, pack in enumerate(packs):
            pal_name   = f'{prefix.lower()}_pal_{i}'
            pal2_name  = f'{prefix.lower()}_pal2_{i}'
            pals_name  = f'{prefix.lower()}_tile_pals_{i}'
            tiles_name = f'{prefix.lower()}_tiles_{i}'
            multi_names = []
            W(f'static const uint16_t __far {pal_name}[16] = {{')
            for c in pack.palette:
                W(f'  0x{c:04X},')
            W('};'  )
            if pack.palette2:
                W(f'static const uint16_t __far {pal2_name}[16] = {{')
                for c in pack.palette2:
                    W(f'  0x{c:04X},')
                W('};'  )
            if pack.tile_pals:
                W(f'static const uint8_t __far {pals_name}[] = {{')
                for j in range(0, len(pack.tile_pals), 16):
                    chunk = pack.tile_pals[j:j + 16]
                    W('  ' + ', '.join(f'0x{b:02X}' for b in chunk) + ',')
                W('};'  )
            if pack.palettes:
                for pi, pal in enumerate(pack.palettes):
                    mpal_name = f'{prefix.lower()}_mpal_{i}_{pi}'
                    multi_names.append(mpal_name)
                    W(f'static const uint16_t __far {mpal_name}[16] = {{')
                    for c in pal:
                        W(f'  0x{c:04X},')
                    W('};'  )
                table_name = f'{prefix.lower()}_mpals_{i}'
                W(f'static const uint16_t __far * const __far {table_name}[] = {{')
                for mpal_name in multi_names:
                    W(f'  {mpal_name},')
                W('};'  )
            W(f'static const uint8_t __far {tiles_name}[] = {{')
            for j in range(0, len(pack.tiles), 16):
                chunk = pack.tiles[j:j + 16]
                W('  ' + ', '.join(f'0x{b:02X}' for b in chunk) + ',')
            W('};'  )
            W('')

        W(f'const image_asset_t __far {prefix}[{len(packs)}] = {{')
        for i, pack in enumerate(packs):
            pal_name   = f'{prefix.lower()}_pal_{i}'
            pal2_name  = f'{prefix.lower()}_pal2_{i}' if pack.palette2 else 'NULL'
            pals_name  = f'{prefix.lower()}_tile_pals_{i}' if pack.tile_pals else 'NULL'
            tiles_name = f'{prefix.lower()}_tiles_{i}'
            mpals_name = f'{prefix.lower()}_mpals_{i}' if pack.palettes else 'NULL'
            mpals_count = len(pack.palettes) if pack.palettes else 0
            W(f'  {{ {pack.w_tiles}, {pack.h_tiles}, {pack.tile_count}, '
              f'{pal_name}, {pal2_name}, {pals_name}, {tiles_name}, {mpals_name}, {mpals_count} }},')
        W('};'  )
        W('')
        return packs

    bg_packs = emit_assets(bg_assets,   'BG_ASSETS',   False) or []
    fg_packs = emit_assets(fg_assets,   'FG_ASSETS',   False) or []
    char_packs = emit_assets(char_assets, 'CHAR_ASSETS',  True) or []

    fg_patch_map: dict[tuple[int, int], int] = {}
    fg_patch_defs: list[tuple[int, int, list[tuple[int, int, bytes, int, bytes]]]] = []
    for n in nodes:
        base_id = fg2idx.get(n.get('fgImageId')) if n.get('fgImageId') else None
        if base_id is None:
            continue
        for key in ('fgTalkImageId', 'fgBlinkImageId'):
            alt_id = fg2idx.get(n.get(key)) if n.get(key) else None
            if alt_id is None:
                continue
            pair = (base_id, alt_id)
            if pair in fg_patch_map:
                continue
            if base_id >= len(fg_packs) or alt_id >= len(fg_packs):
                continue
            base = fg_packs[base_id]
            alt = fg_packs[alt_id]
            if base.w_tiles != alt.w_tiles or base.h_tiles != alt.h_tiles or base.tile_count != alt.tile_count:
                continue
            changes = []
            for ti in range(base.tile_count):
                off = ti * 32
                base_tile = base.tiles[off:off + 32]
                alt_tile = alt.tiles[off:off + 32]
                base_pal = base.tile_pals[ti] if base.tile_pals and ti < len(base.tile_pals) else 0
                alt_pal = alt.tile_pals[ti] if alt.tile_pals and ti < len(alt.tile_pals) else 0
                if base_tile != alt_tile or base_pal != alt_pal:
                    changes.append((ti, alt_pal, alt_tile, base_pal, base_tile))
            patch_idx = len(fg_patch_defs)
            fg_patch_map[pair] = patch_idx
            fg_patch_defs.append((base_id, alt_id, changes))

    if fg_patch_defs:
        for pi, (_base_id, _alt_id, changes) in enumerate(fg_patch_defs):
            if changes:
                for ci, (_ti, _ap, alt_tile, _bp, base_tile) in enumerate(changes):
                    W(f'static const uint8_t __far fg_patch_{pi}_alt_{ci}[32] = {{')
                    W('  ' + ', '.join(f'0x{b:02X}' for b in alt_tile) + ',')
                    W('};')
                    W(f'static const uint8_t __far fg_patch_{pi}_base_{ci}[32] = {{')
                    W('  ' + ', '.join(f'0x{b:02X}' for b in base_tile) + ',')
                    W('};')
                W(f'static const fg_anim_tile_t __far fg_patch_tiles_{pi}[] = {{')
                for ci, (ti, ap, _alt_tile, bp, _base_tile) in enumerate(changes):
                    W(f'  {{ {ti}, {ap}, fg_patch_{pi}_alt_{ci}, {bp}, fg_patch_{pi}_base_{ci} }},')
                W('};')
            W('')
        W(f'const fg_anim_patch_t __far FG_ANIM_PATCHES[{len(fg_patch_defs)}] = {{')
        for pi, (_base_id, _alt_id, changes) in enumerate(fg_patch_defs):
            tiles_name = f'fg_patch_tiles_{pi}' if changes else 'NULL'
            W(f'  {{ {len(changes)}, {tiles_name} }},')
        W('};')
    else:
        W('const fg_anim_patch_t __far FG_ANIM_PATCHES[1] = { { 0, NULL } };')
    W('')

    # ── Cygnals (Furnace) assets ──
    if use_cygnals and (music_fur_assets or sfx_fur_assets):
        for i in range(len(music_fur_assets)):
            W(f'extern const unsigned char __wf_rom cyg_song_{i}[];')
        for i in range(len(sfx_fur_assets)):
            W(f'extern const unsigned char __wf_rom cyg_sfx_{i}[];')
        W('')

        if music_fur_assets:
            W(f'const unsigned char __wf_rom * const __far CYG_SONGS[{len(music_fur_assets)}] = {{')
            for i in range(len(music_fur_assets)):
                W(f'  cyg_song_{i},')
            W('};')
        else:
            W('const unsigned char __wf_rom * const __far CYG_SONGS[1] = { NULL };')
        W('')

        if sfx_fur_assets:
            W(f'const unsigned char __wf_rom * const __far CYG_SFX[{len(sfx_fur_assets)}] = {{')
            for i in range(len(sfx_fur_assets)):
                W(f'  cyg_sfx_{i},')
            W('};')
        else:
            W('const unsigned char __wf_rom * const __far CYG_SFX[1] = { NULL };')
        W('')
    else:
        W('const unsigned char __wf_rom * const __far CYG_SONGS[1] = { NULL };')
        W('const unsigned char __wf_rom * const __far CYG_SFX[1] = { NULL };')
        W('')

    # ── SFX assets (unsigned 8-bit PCM @ 12 kHz, for sound DMA) ──
    if sfx_assets_emit:
        for i, a in enumerate(sfx_assets_emit):
            raw = decode_data_url(a.get('dataUrl', ''))
            if not raw:
                raise ValueError(f'sfx "{a.get("name", "?")}" has no audio data')
            pcm = wav_to_u8_pcm(raw)
            W(f'static const uint8_t __far sfx_data_{i}[] = {{')
            for j in range(0, len(pcm), 16):
                chunk = pcm[j:j + 16]
                W('  ' + ', '.join(f'0x{b:02X}' for b in chunk) + ',')
            W('};')
            W('')
        W(f'const sfx_asset_t __far SFX_ASSETS[{len(sfx_assets_emit)}] = {{')
        for i, a in enumerate(sfx_assets_emit):
            raw = decode_data_url(a.get('dataUrl', ''))
            pcm = wav_to_u8_pcm(raw)
            W(f'  {{ {len(pcm)}UL, sfx_data_{i} }}, /* {esc(a.get("id",""))} */')
        W('};')
    else:
        W('const sfx_asset_t __far SFX_ASSETS[1] = { { 0UL, NULL } };')
    W('')

    # ── Music track emission (tracker grid) ──
    def parse_track_channel(ch: dict[str, Any] | None):
        wave = 'WAVE_SQUARE'
        base_vol = 0
        freq = [0] * 32
        vol = [0] * 32
        if not ch:
            return wave, base_vol, freq, vol

        wave = WAVE_C.get(str(ch.get('wave', 'square')), 'WAVE_SQUARE')
        try:
            base_vol = int(ch.get('vol', 0))
        except Exception:
            base_vol = 0
        base_vol = max(0, min(15, base_vol))

        # New format (from editor v4+): ch.pattern[step] = { note:'C4', len:1 }
        pattern = ch.get('pattern')
        if isinstance(pattern, list):
            for step, ev in enumerate(pattern[:32]):
                if not isinstance(ev, dict):
                    continue
                note = str(ev.get('note', '') or '').strip()
                if not note:
                    continue
                try:
                    dur = int(ev.get('len', 1))
                except Exception:
                    dur = 1
                dur = max(1, min(32, dur))

                midi = note_to_midi(note)
                if midi is None:
                    continue
                hz = midi_to_hz(midi)
                fdiv = ws_wave_hz_to_freq_div(hz, 32)
                for s in range(step, min(32, step + dur)):
                    freq[s] = fdiv
                    vol[s] = base_vol
        else:
            # Legacy format: ch.notes['C4_12'] = len
            notes = ch.get('notes') or {}
            for k, dur_v in (notes.items() if isinstance(notes, dict) else []):
                m = NOTE_STEP_RE.match(str(k))
                if not m:
                    continue
                note = f'{m.group(1)}{m.group(2)}{m.group(3)}'
                try:
                    step = int(m.group(4))
                except Exception:
                    continue
                if step < 0 or step >= 32:
                    continue
                try:
                    dur = int(dur_v)
                except Exception:
                    dur = 1
                dur = max(1, min(32, dur))

                midi = note_to_midi(note)
                if midi is None:
                    continue
                hz = midi_to_hz(midi)
                fdiv = ws_wave_hz_to_freq_div(hz, 32)
                for s in range(step, min(32, step + dur)):
                    freq[s] = fdiv
                    vol[s] = base_vol

        return wave, base_vol, freq, vol

    if tracks_emit:
        W(f'const music_track_t __far TRACKS[{len(tracks_emit)}] = {{')
        for tr in tracks_emit:
            try:
                bpm = int(tr.get('bpm', 120))
            except Exception:
                bpm = 120
            bpm = max(30, min(300, bpm))
            channels = tr.get('channels') or []
            if not isinstance(channels, list):
                channels = []
            ch_count = max(0, min(4, len(channels)))
            W('  {')
            W(f'    {bpm}, 32, {ch_count}, {{')
            for ci in range(4):
                wave, base_vol, fq, vv = parse_track_channel(channels[ci] if ci < len(channels) else None)
                W('      {')
                W(f'        {wave}, {base_vol},')
                W('        { ' + ', '.join(f'0x{x:04X}' for x in fq) + ' },')
                W('        { ' + ', '.join(str(int(x)) for x in vv) + ' },')
                W('      },')
            W('    }')
            W('  },')
        W('};')
    else:
        W('const music_track_t __far TRACKS[1] = { { 120, 32, 0, { 0 } } };')
    W('')

    # ── Built-in Save/Load background (optional) ──
    sl_path = Path(__file__).with_name('saveload_bg.png')
    if sl_path.is_file():
        img = Image.open(sl_path)
        w_tiles, h_tiles, palette, _palette2, _tile_pals, tiles = image_to_tiles(img, 'bg')
        tile_count = w_tiles * h_tiles
        if tile_count > MAX_BG_TILES:
            raise ValueError(
                f'saveload_bg.png is too large ({tile_count} tiles, max {MAX_BG_TILES})')

        W('static const uint16_t __far saveload_bg_pal[16] = {')
        for c in palette[:16]:
            W(f'  0x{c:04X},')
        W('};')
        W('static const uint8_t __far saveload_bg_tiles[] = {')
        for j in range(0, len(tiles), 16):
            chunk = tiles[j:j + 16]
            W('  ' + ', '.join(f'0x{b:02X}' for b in chunk) + ',')
        W('};')
        W('const image_asset_t __far SAVELOAD_BG = {')
        W(f'  {w_tiles}, {h_tiles}, {tile_count}, saveload_bg_pal, NULL, NULL, saveload_bg_tiles, NULL, 0')
        W('};')
    else:
        W('const image_asset_t __far SAVELOAD_BG = { 0, 0, 0, NULL, NULL, NULL, NULL, NULL, 0 };')
    W('')

    # ── Flag op arrays (interned / deduplicated) ──
    fo_map = {}
    fo_ctr = [0]

    def emit_fo(ops):
        if not ops:
            return ('NULL', 0)
        key = tuple((o.get('name', ''), o.get('op', 'add'), int(o.get('value', 0)))
                    for o in ops)
        if key in fo_map:
            return fo_map[key]
        name = f'fo{fo_ctr[0]}'
        fo_ctr[0] += 1
        W(f'static const flag_op_t __far {name}[] = {{')
        for o in ops:
            fi  = flag2idx.get(o.get('name', ''), 0xFF)
            opc = OP_C.get(o.get('op', 'add'), 'OP_ADD')
            val = int(o.get('value', 0))
            W(f'  {{ {fi}, {opc}, {val} }},')
        W('};'  )
        W('')
        fo_map[key] = (name, len(ops))
        return fo_map[key]

    # ── Per-node supporting tables ──
    node_meta = []
    for i, n in enumerate(nodes):
        t    = n.get('type', '')
        meta = {}

        if t == 'title':
            menu_items = [m.strip() for m in (n.get('titleMenu', '') or '').split('|')
                          if m.strip()]
            meta['menu'] = menu_items
            if menu_items:
                W(f'static const char __far * const __far nm{i}[] = {{')
                for m in menu_items:
                    W(f'  {intern_str(m)},')
                W('};'  )
                W('')

        if t == 'scene':
            fo, fc = emit_fo(n.get('sceneFlagOps') or [])
            meta['fo'], meta['fc'] = fo, fc

        if t == 'choice':
            choices = n.get('choices') or []
            for ch in choices:
                fo, fc = emit_fo(ch.get('flagOps') or [])
                ch['_fo'], ch['_fc'] = fo, fc
                ch['_hc'] = False
                cstr = (ch.get('condition') or '').strip()
                if cstr:
                    m = re.match(r'^\s*(\w+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+)\s*$',
                                 cstr)
                    if m:
                        fn, cop, cv = m.groups()
                        if fn in flag2idx and cop in COND_C:
                            ch['_hc'] = True
                            ch['_cf'] = flag2idx[fn]
                            ch['_co'] = COND_C[cop]
                            ch['_cv'] = int(cv)
            if choices:
                W(f'static const choice_opt_t __far nc{i}[] = {{')
                for ch in choices:
                    tgt = resolve(ch.get('target'))
                    hc  = 'true' if ch['_hc'] else 'false'
                    cf  = ch.get('_cf', 0)
                    co  = ch.get('_co', 'COND_EQ')
                    cv  = ch.get('_cv', 0)
                    W(f'  {{ {intern_str(ch.get("text", ""))}, {tgt}, '
                      f'{ch["_fc"]}, {ch["_fo"]}, {hc}, {cf}, {co}, {cv} }},')
                W('};'  )
                W('')
            meta['choices'] = choices

        if t == 'branch':
            branches = n.get('branches') or []
            if branches:
                W(f'static const branch_cond_t __far nb{i}[] = {{')
                for b in branches:
                    fi  = flag2idx.get(b.get('flag', ''), 0xFF)
                    opc = COND_C.get(b.get('op', '=='), 'COND_EQ')
                    val = int(b.get('value', 0))
                    tgt = resolve(b.get('target'))
                    W(f'  {{ {fi}, {opc}, {val}, {tgt} }},')
                W('};'  )
                W('')

        if t == 'investigation':
            hotspots = n.get('hotspots') or []
            for hs in hotspots:
                fo, fc = emit_fo(hs.get('flagOps') or [])
                hs['_fo'], hs['_fc'] = fo, fc
            if hotspots:
                W(f'static const hotspot_t __far nh{i}[] = {{')
                for hs in hotspots:
                    x = max(0, min(223, int(hs.get('x', 0) or 0)))
                    y = max(0, min(143, int(hs.get('y', 0) or 0)))
                    w = max(1, min(224 - x, int(hs.get('w', 16) or 16)))
                    h = max(1, min(144 - y, int(hs.get('h', 16) or 16)))
                    txt = intern_str(hs.get('text', ''))
                    req = 1 if bool(hs.get('required', True)) else 0
                    tgt = resolve(hs.get('target'))
                    W(f'  {{ {x}, {y}, {w}, {h}, {txt}, {req}, {hs["_fc"]}, {hs["_fo"]}, {tgt} }},')
                W('};')
                W('')
            meta['hotspots'] = hotspots

        node_meta.append(meta)

    # ── Helpers to emit full scene struct ──
    def emit_empty_scene():
        W('  empty_str, empty_str, empty_str, empty_str,')
        W('  0, NULL, SPEED_NORMAL,')
        W('  0UL, 0UL, 0UL,')
        W('  0, 0, 0, 0, 0, 0,')
        W('  0xFF, 0xFF, 0xFF, 0xFF, 0xFF, POS_NONE, 0xFF, POS_NONE, 0xFF,')
        W('  MUSIC_KEEP, 0xFF, 1, 0xFF,')
        W('  SFX_KEEP, 0, 0, 0, 0, 0,')
        W('  0, NULL, 0xFFFF')

    # ── Main NODES table ──
    # Keep each node in its own far object. A complete VN can exceed the
    # IA-16 single-object segment limit if emitted as one giant node_t array.
    for i, n in enumerate(nodes):
        t   = n.get('type', '')
        ntc = NODE_TYPE.get(t, 'NODE_END')
        meta = node_meta[i]

        if t in ('scene', 'title'):
            spk  = intern_str(n.get('speaker', ''))
            dlg  = intern_str(rewrite_inline_cmds(str(n.get('dialogue', '') or ''), track2idx, sfx2idx))
            tmn  = intern_str(n.get('titleMain', ''))
            tsb  = intern_str(n.get('titleSub', ''))
            menu_items = meta.get('menu', [])
            mnam = f'nm{i}' if menu_items else 'NULL'
            mcnt = len(menu_items)
            spd  = SPEED_C.get(n.get('textSpeed', 'normal'), 'SPEED_NORMAL')
            bg1  = hexcol(n.get('bgColor',      '#000000'))
            bg2  = hexcol(n.get('bgColor2',     '#000000'))
            spc  = hexcol(n.get('speakerColor', '#ff3366'))
            tb   = TB_STYLE_C.get(n.get('tbStyle',    'dark'), '0')
            bgp  = BG_PRESET_C.get(n.get('bgPreset',  'room'), '0')
            par  = PARTICLE_C.get(n.get('particles',  'none'), '0')
            sfx  = SCREENFX_C.get(n.get('screenFx',  'none'), '0')
            trn  = TRANSITION_C.get(n.get('transition', 'fade'), '1')
            cam  = CHARANIM_C.get(n.get('charAnim',  'slide-up'), '1')
            cpos = POS_C.get(n.get('charPos',  'none'), 'POS_NONE')
            c2p  = POS_C.get(n.get('char2Pos', 'none'), 'POS_NONE')
            mact = MUSIC_C.get(n.get('musicAction', 'keep'), 'MUSIC_KEEP')
            mtrk = track2idx.get(n.get('musicTrack', ''), 0xFF)
            mloop = 1 if bool(n.get('musicLoop', True)) else 0
            sfx_id = sfx2idx.get(n.get('sfx', ''), 0xFF)
            sfx_act = SFXACT_C.get(n.get('sfxAction', 'keep'), 'SFX_KEEP')
            sfx_loop = 1 if bool(n.get('sfxLoop', False)) else 0
            pce = 1 if bool(n.get('palCycleEnable', False)) else 0
            try:
                pcs = int(n.get('palCycleStart', 0))
            except Exception:
                pcs = 0
            try:
                pcl = int(n.get('palCycleLen', 2))
            except Exception:
                pcl = 2
            try:
                pcspd = int(n.get('palCycleSpeed', 8))
            except Exception:
                pcspd = 8
            pcs = max(0, min(15, pcs))
            pcl = max(2, min(16, pcl))
            pcspd = max(1, min(255, pcspd))
            if pcs + pcl > 16:
                pcl = max(2, 16 - pcs)
            if not pce:
                pcs = 0
                pcl = 0
                pcspd = 0
            bg_id    = bg2idx.get(n.get('bgImageId'), 0xFF) if n.get('bgImageId')  else 0xFF
            fg_id    = fg2idx.get(n.get('fgImageId'), 0xFF) if n.get('fgImageId')  else 0xFF
            fgt_id   = fg2idx.get(n.get('fgTalkImageId'), 0xFF) if n.get('fgTalkImageId') else 0xFF
            fgb_id   = fg2idx.get(n.get('fgBlinkImageId'), 0xFF) if n.get('fgBlinkImageId') else 0xFF
            fgtp_id  = fg_patch_map.get((fg_id, fgt_id), 0xFF) if fg_id != 0xFF and fgt_id != 0xFF else 0xFF
            fgbp_id  = fg_patch_map.get((fg_id, fgb_id), 0xFF) if fg_id != 0xFF and fgb_id != 0xFF else 0xFF
            char_id  = ch2idx.get(n.get('charId'),    0xFF) if n.get('charId')     else 0xFF
            char2_id = ch2idx.get(n.get('char2Id'),   0xFF) if n.get('char2Id')    else 0xFF
            char3_id = ch2idx.get(n.get('char3Id'),   0xFF) if n.get('char3Id')    else 0xFF
            fo = meta.get('fo', 'NULL')
            fc = meta.get('fc', 0)
            nxt = resolve(n.get('next'))
            W(f'static const node_t __far NODE_{i} = {{ {ntc}, {{ .scene = {{')
            W(f'    {spk}, {dlg}, {tmn}, {tsb},')
            W(f'    {mcnt}, {mnam}, {spd},')
            W(f'    0x{bg1:06X}UL, 0x{bg2:06X}UL, 0x{spc:06X}UL,')
            W(f'    {tb}, {bgp}, {par}, {sfx}, {trn}, {cam},')
            W(f'    {bg_id}, {fg_id}, {fgtp_id}, {fgbp_id}, {char_id}, {cpos}, {char2_id}, {c2p}, {char3_id},')
            W(f'    {mact}, {mtrk}, {mloop}, {sfx_id},')
            W(f'    {sfx_act}, {sfx_loop}, {pce}, {pcs}, {pcl}, {pcspd},')
            W(f'    {fc}, {fo}, {nxt}')
            W('  } } };')
            W('')

        elif t == 'investigation':
            spk  = intern_str(n.get('speaker', ''))
            dlg  = intern_str(rewrite_inline_cmds(str(n.get('dialogue', '') or ''), track2idx, sfx2idx))
            spd  = SPEED_C.get(n.get('textSpeed', 'normal'), 'SPEED_NORMAL')
            bg1  = hexcol(n.get('bgColor',      '#000000'))
            bg2  = hexcol(n.get('bgColor2',     '#000000'))
            spc  = hexcol(n.get('speakerColor', '#ff3366'))
            tb   = TB_STYLE_C.get(n.get('tbStyle',    'dark'), '0')
            bgp  = BG_PRESET_C.get(n.get('bgPreset',  'room'), '0')
            par  = PARTICLE_C.get(n.get('particles',  'none'), '0')
            sfx  = SCREENFX_C.get(n.get('screenFx',  'none'), '0')
            trn  = TRANSITION_C.get(n.get('transition', 'fade'), '1')
            cam  = CHARANIM_C.get(n.get('charAnim',  'slide-up'), '1')
            cpos = POS_C.get(n.get('charPos',  'none'), 'POS_NONE')
            c2p  = POS_C.get(n.get('char2Pos', 'none'), 'POS_NONE')
            mact = MUSIC_C.get(n.get('musicAction', 'keep'), 'MUSIC_KEEP')
            mtrk = track2idx.get(n.get('musicTrack', ''), 0xFF)
            mloop = 1 if bool(n.get('musicLoop', True)) else 0
            sfx_id = sfx2idx.get(n.get('sfx', ''), 0xFF)
            sfx_act = SFXACT_C.get(n.get('sfxAction', 'keep'), 'SFX_KEEP')
            sfx_loop = 1 if bool(n.get('sfxLoop', False)) else 0
            pce = 1 if bool(n.get('palCycleEnable', False)) else 0
            pcs = max(0, min(15, int(n.get('palCycleStart', 0) or 0)))
            pcl = max(2, min(16, int(n.get('palCycleLen', 2) or 2)))
            pcspd = max(1, min(255, int(n.get('palCycleSpeed', 8) or 8)))
            if pcs + pcl > 16:
                pcl = max(2, 16 - pcs)
            if not pce:
                pcs = 0; pcl = 0; pcspd = 0
            bg_id    = bg2idx.get(n.get('bgImageId'), 0xFF) if n.get('bgImageId')  else 0xFF
            fg_id    = fg2idx.get(n.get('fgImageId'), 0xFF) if n.get('fgImageId')  else 0xFF
            fgt_id   = fg2idx.get(n.get('fgTalkImageId'), 0xFF) if n.get('fgTalkImageId') else 0xFF
            fgb_id   = fg2idx.get(n.get('fgBlinkImageId'), 0xFF) if n.get('fgBlinkImageId') else 0xFF
            fgtp_id  = fg_patch_map.get((fg_id, fgt_id), 0xFF) if fg_id != 0xFF and fgt_id != 0xFF else 0xFF
            fgbp_id  = fg_patch_map.get((fg_id, fgb_id), 0xFF) if fg_id != 0xFF and fgb_id != 0xFF else 0xFF
            char_id  = ch2idx.get(n.get('charId'),    0xFF) if n.get('charId')     else 0xFF
            char2_id = ch2idx.get(n.get('char2Id'),   0xFF) if n.get('char2Id')    else 0xFF
            char3_id = ch2idx.get(n.get('char3Id'),   0xFF) if n.get('char3Id')    else 0xFF
            hotspots = meta.get('hotspots', [])
            hnam = f'nh{i}' if hotspots else 'NULL'
            hcnt = len(hotspots)
            dtgt = resolve(n.get('defaultTarget') or n.get('next'))
            W(f'static const node_t __far NODE_{i} = {{ {ntc}, {{ .investigation = {{')
            W('  {')
            W(f'    {spk}, {dlg}, empty_str, empty_str,')
            W(f'    0, NULL, {spd},')
            W(f'    0x{bg1:06X}UL, 0x{bg2:06X}UL, 0x{spc:06X}UL,')
            W(f'    {tb}, {bgp}, {par}, {sfx}, {trn}, {cam},')
            W(f'    {bg_id}, {fg_id}, {fgtp_id}, {fgbp_id}, {char_id}, {cpos}, {char2_id}, {c2p}, {char3_id},')
            W(f'    {mact}, {mtrk}, {mloop}, {sfx_id},')
            W(f'    {sfx_act}, {sfx_loop}, {pce}, {pcs}, {pcl}, {pcspd},')
            W(f'    0, NULL, {dtgt}')
            W(f'  }}, {hcnt}, {hnam}, {dtgt}')
            W('  } } };')
            W('')

        elif t == 'chapter':
            W(f'static const node_t __far NODE_{i} = {{ NODE_CHAPTER, {{ .scene = {{')
            emit_empty_scene()
            W('  } } };')
            W('')

        elif t == 'choice':
            choices = n.get('choices') or []
            cnam = f'nc{i}' if choices else 'NULL'
            ccnt = len(choices)
            prom = intern_str(n.get('prompt', ''))
            dtgt = resolve(n.get('defaultTarget'))
            W(f'static const node_t __far NODE_{i} = {{ NODE_CHOICE, {{ .choice = {{')
            W(f'    {prom}, {ccnt}, {cnam}, {dtgt}')
            W('  } } };')
            W('')

        elif t == 'branch':
            branches = n.get('branches') or []
            bnam = f'nb{i}' if branches else 'NULL'
            bcnt = len(branches)
            dtgt = resolve(n.get('defaultTarget'))
            W(f'static const node_t __far NODE_{i} = {{ NODE_BRANCH, {{ .branch = {{')
            W(f'    {bcnt}, {bnam}, {dtgt}')
            W('  } } };')
            W('')

        else:
            W(f'static const node_t __far NODE_{i} = {{ NODE_END, {{ .scene = {{')
            emit_empty_scene()
            W('  } } };')
            W('')
    W('const node_t __far * const __far NODES[NUM_NODES] = {')
    for i, _n in enumerate(nodes):
        W(f'  &NODE_{i},')
    W('};'  )
    W('')

    # ── Write output files ──
    os.makedirs(out_dir, exist_ok=True)
    start_idx = id2idx.get(project.get('startNodeId') or '', 0)
    build_id = compute_build_id_16()
    hdr = [
        '/* game_data.h — AUTO-GENERATED. DO NOT EDIT. */',
        '#ifndef GAME_DATA_H',
        '#define GAME_DATA_H',
        '#include <wonderful.h>',
        '#include "game_types.h"',
        '',
        f'#define BUILD_ID        0x{build_id:04X}u',
        f'#define START_NODE_IDX  {start_idx}',
        f'#define NUM_NODES       {len(nodes)}',
        f'#define NUM_FLAGS       {len(flags)}',
        f'#define USE_CYGNALS     {1 if use_cygnals else 0}',
        f'#define NUM_CYG_SONGS   {len(music_fur_assets) if use_cygnals else 0}',
        f'#define NUM_CYG_SFX     {len(sfx_fur_assets) if use_cygnals else 0}',
        f'#define NUM_TRACKS      {len(tracks_emit)}',
        f'#define NUM_SFX         {len(sfx_assets_emit)}',
        f'#define NUM_BG_ASSETS   {len(bg_assets)}',
        f'#define NUM_FG_ASSETS   {len(fg_assets)}',
        f'#define NUM_FG_ANIM_PATCHES {max(1, len(fg_patch_defs))}',
        f'#define NUM_CHAR_ASSETS {len(char_assets)}',
        f'#define FONT_STYLE      {font_style}',
        f'#define UI_SFX_TEXT     {ui_sfx_text}',
        f'#define UI_SFX_CURSOR   {ui_sfx_cursor}',
        f'#define UI_SFX_CONFIRM  {ui_sfx_confirm}',
        '',
        'extern const node_t __far * const __far NODES[NUM_NODES];',
        'extern const int16_t        FLAG_INITIAL_VALUES[NUM_FLAGS > 0 ? NUM_FLAGS : 1];',
        'extern const char __far * const __far NODE_NAMES[NUM_NODES > 0 ? NUM_NODES : 1];',
        'extern const char __far * const __far BG_ASSET_NAMES[NUM_BG_ASSETS > 0 ? NUM_BG_ASSETS : 1];',
        'extern const unsigned char __wf_rom * const __far CYG_SONGS[NUM_CYG_SONGS > 0 ? NUM_CYG_SONGS : 1];',
        'extern const unsigned char __wf_rom * const __far CYG_SFX[NUM_CYG_SFX > 0 ? NUM_CYG_SFX : 1];',
        'extern const music_track_t  __far TRACKS[NUM_TRACKS > 0 ? NUM_TRACKS : 1];',
        'extern const sfx_asset_t    __far SFX_ASSETS[NUM_SFX > 0 ? NUM_SFX : 1];',
        'extern const image_asset_t  __far SAVELOAD_BG;',
        'extern const image_asset_t  __far BG_ASSETS  [NUM_BG_ASSETS   > 0 ? NUM_BG_ASSETS   : 1];',
        'extern const image_asset_t  __far FG_ASSETS  [NUM_FG_ASSETS   > 0 ? NUM_FG_ASSETS   : 1];',
        'extern const fg_anim_patch_t __far FG_ANIM_PATCHES[NUM_FG_ANIM_PATCHES > 0 ? NUM_FG_ANIM_PATCHES : 1];',
        'extern const image_asset_t  __far CHAR_ASSETS[NUM_CHAR_ASSETS > 0 ? NUM_CHAR_ASSETS : 1];',
        '',
        '#endif',
        '',
    ]
    (Path(out_dir) / 'game_data.h').write_text('\n'.join(hdr), encoding='utf-8')
    (Path(out_dir) / 'game_data.c').write_text('\n'.join(lines), encoding='utf-8')


def main():
    if len(sys.argv) < 3:
        print('Usage: python tools/convert_json.py <project.wscvn.json> <output_dir>')
        sys.exit(1)
    in_path, out_dir = sys.argv[1], sys.argv[2]
    if not os.path.isfile(in_path):
        print(f'Error: not found: {in_path}')
        sys.exit(1)
    with open(in_path, 'r', encoding='utf-8') as f:
        project = json.load(f)
    if 'nodes' not in project:
        print('Error: not a WSC VN Studio project file')
        sys.exit(1)

    print(f'[+] Loaded "{project.get("name", "Untitled")}" — '
          f'{len(project["nodes"])} nodes, '
          f'{len(project.get("flags", []))} flags, '
          f'{len(project.get("tracks", []))} tracks, '
          f'{len(project.get("assets", {}).get("backgrounds", []))} backgrounds, '
          f'{len(project.get("assets", {}).get("characters", []))} characters')

    errs, warns = validate(project)
    for w in warns:
        print(f'[!] WARNING: {w}')
    for e in errs:
        print(f'[X] ERROR: {e}')
    if errs:
        print(f'\n[X] {len(errs)} error(s) — fix in editor and re-export.')
        sys.exit(2)

    try:
        emit(project, out_dir)
    except ValueError as e:
        print(f'[X] ERROR (asset conversion): {e}')
        sys.exit(3)

    print(f'[+] Wrote {out_dir}/game_data.h and {out_dir}/game_data.c')
    print('[+] Ready: run "make" from the project root.')


if __name__ == '__main__':
    main()
