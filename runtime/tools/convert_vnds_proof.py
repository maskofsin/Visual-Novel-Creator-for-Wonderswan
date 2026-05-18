#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageOps


WSC_W = 224
WSC_H = 144
FG_H = 104
MAX_TEXT = 96


def slug(s: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return out or "asset"


def line_parts(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?::|\s+|$)(.*)$", raw)
    if not m:
        return raw.lower(), ""
    cmd, arg = m.group(1).lower(), m.group(2).strip()
    cmd = {"bglod": "bgload", "soind": "sound", "deelay": "delay"}.get(cmd, cmd)
    return cmd, arg


def token(arg: str) -> str:
    arg = arg.strip()
    if not arg or arg == "~":
        return ""
    return arg.split()[0].strip()


def file_index(root: Path) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file():
            idx.setdefault(p.name.lower(), p)
            try:
                idx.setdefault(str(p.relative_to(root)).replace("\\", "/").lower(), p)
            except ValueError:
                pass
    return idx


def resolve(root: Path, idx: dict[str, Path], dirs: list[str], ref: str) -> Path | None:
    if not ref:
        return None
    low = ref.replace("\\", "/").lower()
    for d in dirs:
        p = root / d / ref
        if p.is_file():
            return p
        p = root / d / low
        if p.is_file():
            return p
    return idx.get(low) or idx.get(Path(low).name)


def fit_cover(img: Image.Image, w: int = WSC_W, h: int = WSC_H) -> Image.Image:
    img = img.convert("RGBA")
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def fit_contain(img: Image.Image, w: int = WSC_W, h: int = WSC_H) -> Image.Image:
    img = img.convert("RGBA")
    iw, ih = img.size
    scale = min(w / iw, h / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(img, ((w - nw) // 2, (h - nh) // 2))
    return out


def fit_foreground(img: Image.Image) -> Image.Image:
    full = fit_contain(img, WSC_W, WSC_H)
    return full.crop((0, 0, WSC_W, FG_H))


def fullscreen_coverage(img: Image.Image) -> float:
    full = fit_contain(img, WSC_W, WSC_H).convert("RGBA")
    alpha = full.getchannel("A")
    opaque = sum(1 for a in alpha.getdata() if a >= 128)
    return opaque / float(WSC_W * WSC_H)


def clean_text(text: str) -> tuple[str, str]:
    text = text.strip()
    speaker = ""
    if text.startswith("@"):
        text = text[1:].strip()
    if len(text) > 2 and text[0] == '"' and text[-1] == '"':
        speaker = ""
    return speaker, text


def split_text(text: str) -> list[str]:
    if len(text) <= MAX_TEXT:
        return [text]
    words = text.split()
    parts: list[str] = []
    cur = ""
    for word in words:
        nxt = word if not cur else f"{cur} {word}"
        if len(nxt) > MAX_TEXT and cur:
            parts.append(cur)
            cur = word
        else:
            cur = nxt
    if cur:
        parts.append(cur)
    return parts or [text[:MAX_TEXT]]


def scene_template(node_id: str, name: str, bg_id: str | None, dialogue: str, next_id: str = "") -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "scene",
        "name": name,
        "speaker": "",
        "dialogue": dialogue,
        "textSpeed": "normal",
        "bgImageId": bg_id,
        "bgPreset": "room",
        "bgColor": "#000000",
        "bgColor2": "#000000",
        "tbStyle": "ocean",
        "speakerColor": "#66aaff",
        "charId": None,
        "charPos": "center",
        "charAnim": "none",
        "char2Id": None,
        "char2Pos": "none",
        "char3Id": None,
        "particles": "none",
        "screenFx": "none",
        "transition": "fade",
        "palCycleEnable": False,
        "palCycleStart": 0,
        "palCycleLen": 2,
        "palCycleSpeed": 8,
        "musicAction": "keep",
        "musicTrack": "",
        "musicLoop": True,
        "sfxAction": "keep",
        "sfx": "",
        "sfxLoop": False,
        "next": next_id,
        "sceneFlagOps": [],
        "titleMain": "",
        "titleSub": "",
        "titleMenu": "",
        "prompt": "",
        "choices": [],
        "branches": [],
        "hotspots": [],
        "defaultTarget": "",
    }


def prepare_frame(
    img: Image.Image,
    colors: int,
    grayscale: bool,
    contrast: float,
    brightness: float,
    dither: bool,
    saturation: float,
    gamma: float,
    highlight_knee: int,
    white_cap: int,
) -> Image.Image:
    rgb = img.convert("RGB")
    if grayscale:
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if gamma != 1.0:
        lut = [max(0, min(255, round(((i / 255.0) ** gamma) * 255.0))) for i in range(256)]
        rgb = rgb.point(lut * 3)
    if white_cap < 255 and highlight_knee < white_cap:
        knee = max(0, min(254, highlight_knee)) / 255.0
        cap = max(highlight_knee + 1, min(255, white_cap)) / 255.0
        src_span = max(0.001, 1.0 - knee)
        dst_span = max(0.001, cap - knee)
        out = []
        for r, g, b in rgb.getdata():
            y = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            if y > knee:
                t = (y - knee) / src_span
                # A soft knee keeps highlight ordering while preventing pure-white blobs.
                y2 = knee + dst_span * (t ** 0.72)
                scale = y2 / max(y, 0.001)
                r = max(0, min(255, round(r * scale)))
                g = max(0, min(255, round(g * scale)))
                b = max(0, min(255, round(b * scale)))
            out.append((r, g, b))
        mapped = Image.new("RGB", rgb.size)
        mapped.putdata(out)
        rgb = mapped
    colors = max(2, min(256, colors))
    q = rgb.quantize(
        colors=colors,
        method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE,
    )
    return q.convert("RGB")


def prepare_foreground(
    img: Image.Image,
    colors: int,
    grayscale: bool,
    contrast: float,
    brightness: float,
    dither: bool,
    saturation: float,
    gamma: float,
    highlight_knee: int,
    white_cap: int,
) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = prepare_frame(rgba, colors, grayscale, contrast, brightness, dither, saturation, gamma, highlight_knee, white_cap).convert("RGBA")
    rgb.putalpha(alpha)
    return rgb


def convert(
    root: Path,
    script_name: str,
    out_json: Path,
    asset_dir: Path,
    limit_text: int | None,
    follow_jumps: bool,
    prep_colors: int,
    grayscale: bool,
    contrast: float,
    brightness: float,
    dither: bool,
    saturation: float,
    gamma: float,
    highlight_knee: int,
    white_cap: int,
    separate_layers: bool,
    bg_palette_mode: str,
    promote_fullscreen_setimg: bool,
) -> dict[str, Any]:
    idx = file_index(root)
    first_script = resolve(root, idx, ["script"], script_name)
    if not first_script:
        raise SystemExit(f"Script not found: {script_name}")

    asset_dir.mkdir(parents=True, exist_ok=True)
    current_bg: Image.Image | None = Image.new("RGBA", (WSC_W, WSC_H), (0, 0, 0, 255))
    current_bg_id: str | None = None
    overlay_refs: list[Path] = []
    overlays: list[Image.Image] = []
    bg_assets: list[dict[str, Any]] = []
    fg_assets: list[dict[str, Any]] = []
    bg_seen: dict[str, str] = {}
    bg_source_seen: dict[str, str] = {}
    fullscreen_seen: dict[str, bool] = {}
    fg_seen: dict[tuple[str, ...], str] = {}
    nodes: list[dict[str, Any]] = []
    emitted_text = 0
    state_index = 0
    bg_index = 0
    fg_index = 0

    def emit_background_source(p: Path) -> str:
        nonlocal bg_index
        key = str(p.resolve()).lower()
        if key in bg_source_seen:
            return bg_source_seen[key]
        img = prepare_frame(fit_cover(Image.open(p)), prep_colors, grayscale, contrast, brightness, dither, saturation, gamma, highlight_knee, white_cap)
        asset_id = f"vnds_bg_{bg_index:03d}_{slug(p.stem)[:24]}"
        bg_index += 1
        path = asset_dir / f"{asset_id}.png"
        img.save(path)
        bg_assets.append({
            "id": asset_id,
            "name": p.name,
            "dataUrl": str(path.resolve()),
            "paletteMode": bg_palette_mode,
        })
        bg_source_seen[key] = asset_id
        return asset_id

    def is_fullscreen_setimg(p: Path) -> bool:
        key = str(p.resolve()).lower()
        if key not in fullscreen_seen:
            try:
                fullscreen_seen[key] = fullscreen_coverage(Image.open(p)) >= 0.68
            except Exception:
                fullscreen_seen[key] = False
        return fullscreen_seen[key]

    def emit_foreground_stack() -> str | None:
        nonlocal fg_index
        if not overlay_refs:
            return None
        key = tuple(str(p.resolve()).lower() for p in overlay_refs)
        if key in fg_seen:
            return fg_seen[key]
        canvas = Image.new("RGBA", (WSC_W, FG_H), (0, 0, 0, 0))
        for p in overlay_refs:
            canvas.alpha_composite(fit_foreground(Image.open(p)))
        canvas = prepare_foreground(canvas, prep_colors, grayscale, contrast, brightness, dither, saturation, gamma, highlight_knee, white_cap)
        asset_id = f"vnds_fg_{fg_index:03d}"
        fg_index += 1
        path = asset_dir / f"{asset_id}.png"
        canvas.save(path)
        fg_assets.append({
            "id": asset_id,
            "name": " + ".join(p.name for p in overlay_refs[:4])[:80] or asset_id,
            "dataUrl": str(path.resolve()),
            "paletteMode": "auto-tile",
        })
        fg_seen[key] = asset_id
        return asset_id

    def emit_composite() -> str:
        nonlocal state_index
        canvas = (current_bg or Image.new("RGBA", (WSC_W, WSC_H), (0, 0, 0, 255))).copy()
        for layer in overlays:
            canvas.alpha_composite(layer)
        canvas = prepare_frame(canvas, prep_colors, grayscale, contrast, brightness, dither, saturation, gamma, highlight_knee, white_cap)
        key = canvas.tobytes()
        key_s = str(hash(key))
        if key_s in bg_seen:
            return bg_seen[key_s]
        asset_id = f"vnds_bg_{state_index:03d}"
        state_index += 1
        path = asset_dir / f"{asset_id}.png"
        canvas.save(path)
        bg_assets.append({
            "id": asset_id,
            "name": asset_id,
            "dataUrl": str(path.resolve()),
            "paletteMode": bg_palette_mode,
        })
        bg_seen[key_s] = asset_id
        return asset_id

    def current_scene_assets() -> tuple[str | None, str | None]:
        if separate_layers:
            return current_bg_id, emit_foreground_stack()
        return emit_composite(), None

    script = first_script
    seen_scripts: set[str] = set()
    while script:
        script_key = script.name.lower()
        if script_key in seen_scripts:
            break
        seen_scripts.add(script_key)
        next_script: Path | None = None
        stop_all = False
        for line in script.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = line_parts(line)
            if not parsed:
                continue
            cmd, arg = parsed
            if cmd == "bgload":
                ref = token(arg)
                if ref:
                    p = resolve(root, idx, ["background", "foreground"], ref)
                    if p:
                        current_bg = fit_cover(Image.open(p))
                        current_bg_id = emit_background_source(p) if separate_layers else None
                        overlay_refs = []
                        overlays = []
            elif cmd == "setimg":
                ref = token(arg)
                if ref:
                    p = resolve(root, idx, ["foreground", "background"], ref)
                    if p:
                        if separate_layers and promote_fullscreen_setimg and is_fullscreen_setimg(p):
                            current_bg = fit_cover(Image.open(p))
                            current_bg_id = emit_background_source(p)
                            overlay_refs = []
                            overlays = []
                        else:
                            overlay_refs.append(p)
                            overlays.append(fit_contain(Image.open(p)))
            elif cmd == "text":
                if arg.strip() == "~":
                    continue
                for part in split_text(clean_text(arg)[1]):
                    if limit_text is not None and emitted_text >= limit_text:
                        stop_all = True
                        break
                    bg_id, fg_id = current_scene_assets()
                    node_id = f"scene_{len(nodes) + 1:04d}"
                    node = scene_template(node_id, f"{script.stem} {len(nodes) + 1}", bg_id, part)
                    node["fgImageId"] = fg_id
                    nodes.append(node)
                    emitted_text += 1
                if stop_all:
                    break
            elif cmd == "jump":
                ref = token(arg)
                if follow_jumps and ref.lower().endswith(".scr"):
                    next_script = resolve(root, idx, ["script"], ref)
                break
        if stop_all or not follow_jumps:
            break
        script = next_script

    end_id = "end"
    for i, node in enumerate(nodes):
        node["next"] = nodes[i + 1]["id"] if i + 1 < len(nodes) else end_id

    title = scene_template("title", "Title Screen", nodes[0]["bgImageId"] if nodes else None, "")
    title.update({
        "type": "title",
        "tbStyle": "none",
        "next": nodes[0]["id"] if nodes else end_id,
        "titleMain": "Planetarian",
        "titleSub": "VNDS proof on WonderSwan Color",
        "titleMenu": "Start",
    })
    end = scene_template(end_id, "End", nodes[-1]["bgImageId"] if nodes else None, "")
    end["type"] = "end"
    end["next"] = ""
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "version": 1,
        "name": f"VNDS Proof - {root.name} - {first_script.name}",
        "created": now,
        "modified": now,
        "audioBackend": "legacy",
        "fontStyle": "standard",
        "uiSfxText": "",
        "uiSfxCursor": "",
        "uiSfxConfirm": "",
        "startNodeId": "title",
        "nodes": [title, *nodes, end],
        "flags": [],
        "tracks": [],
        "assets": {
            "backgrounds": bg_assets,
            "foregrounds": fg_assets,
            "characters": [],
            "music": [],
            "sfx": [],
            "musicFur": [],
            "sfxFur": [],
        },
        "defaultTbStyle": "ocean",
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    return project


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert a VNDS script chain into a WSC VN Studio proof project.")
    ap.add_argument("root", type=Path)
    ap.add_argument("--script", default="Opening.scr")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--asset-dir", type=Path, required=True)
    ap.add_argument("--limit-text", type=int, default=12, help="Maximum emitted text nodes. Use 0 for no limit.")
    ap.add_argument("--follow-jumps", action="store_true", help="Follow linear jump commands into the next .scr file.")
    ap.add_argument("--prep-colors", type=int, default=32, help="Pre-reduce composited frames before WSC conversion.")
    ap.add_argument("--grayscale", action="store_true", help="Use grayscale frames for a steadier WonderSwan-like look.")
    ap.add_argument("--contrast", type=float, default=1.0)
    ap.add_argument("--brightness", type=float, default=1.0)
    ap.add_argument("--saturation", type=float, default=1.0)
    ap.add_argument("--gamma", type=float, default=1.0, help="Values above 1.0 darken midtones before quantization.")
    ap.add_argument("--highlight-knee", type=int, default=255, help="Brightness where highlight compression begins, 0-255.")
    ap.add_argument("--white-cap", type=int, default=255, help="Maximum compressed highlight brightness, 0-255.")
    ap.add_argument("--dither", action="store_true")
    ap.add_argument("--separate-layers", action="store_true", help="Emit bgload as backgrounds and compose only setimg stacks as foregrounds.")
    ap.add_argument("--bg-palette-mode", choices=["auto-tile", "auto-tile-8", "top-bottom", "left-right"], default="auto-tile")
    ap.add_argument("--promote-fullscreen-setimg", action="store_true", help="Treat mostly opaque full-screen setimg frames as scene backgrounds in separated-layer imports.")
    args = ap.parse_args()
    limit_text = None if args.limit_text == 0 else args.limit_text
    project = convert(
        args.root,
        args.script,
        args.out,
        args.asset_dir,
        limit_text,
        args.follow_jumps,
        args.prep_colors,
        args.grayscale,
        args.contrast,
        args.brightness,
        args.dither,
        args.saturation,
        args.gamma,
        args.highlight_knee,
        args.white_cap,
        args.separate_layers,
        args.bg_palette_mode,
        args.promote_fullscreen_setimg,
    )
    print(f"[+] Wrote {args.out}")
    print(
        f"[+] Nodes: {len(project['nodes'])}, "
        f"backgrounds: {len(project['assets']['backgrounds'])}, "
        f"foregrounds: {len(project['assets']['foregrounds'])}"
    )


if __name__ == "__main__":
    main()
