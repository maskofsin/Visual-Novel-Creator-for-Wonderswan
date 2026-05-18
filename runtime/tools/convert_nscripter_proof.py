#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageOps


WSC_W = 224
WSC_H = 144
WSC_TEXTBOX_Y = 104
MAX_TEXT = 96


def slug(s: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return out or "asset"


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


def split_commands(line: str) -> list[str]:
    out: list[str] = []
    cur = []
    quote = False
    for ch in line:
        if ch == '"':
            quote = not quote
            cur.append(ch)
        elif ch == ":" and not quote:
            part = "".join(cur).strip()
            if part:
                out.append(part)
            cur = []
        else:
            cur.append(ch)
    part = "".join(cur).strip()
    if part:
        out.append(part)
    return out


def extract_quoted_path(cmd: str) -> str | None:
    m = re.search(r'"([^"]+)"', cmd)
    if not m:
        return None
    path = m.group(1)
    if ";" in path:
        path = path.rsplit(";", 1)[-1]
    return path.replace("\\", "/")


def file_index(root: Path | None) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    if not root or not root.exists():
        return idx
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace("\\", "/").lower()
        idx.setdefault(rel, p)
        idx.setdefault(p.name.lower(), p)
    return idx


def resolve_asset(asset_root: Path | None, idx: dict[str, Path], ref: str) -> Path | None:
    if not asset_root:
        return None
    ref_norm = ref.replace("\\", "/")
    direct = asset_root / ref_norm
    if direct.is_file():
        return direct
    found = idx.get(ref_norm.lower()) or idx.get(Path(ref_norm).name.lower())
    if found:
        return found
    parts = Path(ref_norm).parts
    if len(parts) >= 3 and parts[0].lower() in {"english", "agilis", "gp32", "haeleth"}:
        stem = Path(ref_norm).stem
        suffix = Path(ref_norm).suffix.lower()
        for candidate in (
            f"e/{stem}{suffix}",
            f"e/{stem}.jpg",
            f"e/{stem}.png",
            f"e/{stem}.bmp",
        ):
            found = idx.get(candidate.lower())
            if found:
                return found
    return None


def placeholder_image(label: str) -> Image.Image:
    img = Image.new("RGB", (WSC_W, WSC_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 48, WSC_W - 1, 96), outline=(70, 70, 70), fill=(8, 8, 10))
    words = [label[i:i + 24] for i in range(0, len(label), 24)][:3]
    y = 56
    for word in words:
        draw.text((8, y), word, fill=(170, 190, 210))
        y += 12
    return img


def crop_blackbar_band(img: Image.Image, threshold: int = 12, min_row_fraction: float = 0.03) -> Image.Image:
    rgb = img.convert("RGB")
    w, h = rgb.size
    pix = rgb.load()
    min_count = max(1, int(w * min_row_fraction))
    rows = []
    for y in range(h):
        count = 0
        for x in range(w):
            r, g, b = pix[x, y]
            if max(r, g, b) > threshold:
                count += 1
        if count >= min_count:
            rows.append(y)
    if not rows:
        return rgb
    top = max(0, min(rows) - 2)
    bottom = min(h, max(rows) + 3)
    if bottom - top >= h * 0.92:
        return rgb
    return rgb.crop((0, top, w, bottom))


def grade_image(
    img: Image.Image,
    grayscale: bool,
    saturation: float,
    red_scale: float,
    green_scale: float,
    blue_scale: float,
) -> Image.Image:
    rgb = img.convert("RGB")
    if grayscale:
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if red_scale != 1.0 or green_scale != 1.0 or blue_scale != 1.0:
        out = []
        for r, g, b in rgb.getdata():
            out.append((
                max(0, min(255, round(r * red_scale))),
                max(0, min(255, round(g * green_scale))),
                max(0, min(255, round(b * blue_scale))),
            ))
        balanced = Image.new("RGB", rgb.size)
        balanced.putdata(out)
        rgb = balanced
    return rgb


def prepare_background_image(
    img: Image.Image,
    crop_blackbars: bool,
    reserve_textbox: bool,
    grayscale: bool,
    saturation: float,
    red_scale: float,
    green_scale: float,
    blue_scale: float,
) -> Image.Image:
    rgb = img.convert("RGB")
    if crop_blackbars:
        rgb = crop_blackbar_band(rgb)
        target_h = WSC_TEXTBOX_Y if reserve_textbox else WSC_H
        scaled = rgb.resize((WSC_W, target_h), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (WSC_W, WSC_H), (0, 0, 0))
        out.paste(scaled, (0, 0))
        rgb = out
    return grade_image(rgb, grayscale, saturation, red_scale, green_scale, blue_scale)


def copy_or_placeholder(
    src: Path | None,
    out: Path,
    label: str,
    crop_blackbars: bool,
    reserve_textbox: bool,
    grayscale: bool,
    saturation: float,
    red_scale: float,
    green_scale: float,
    blue_scale: float,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if src and src.is_file():
        prepare_background_image(
            Image.open(src),
            crop_blackbars,
            reserve_textbox,
            grayscale,
            saturation,
            red_scale,
            green_scale,
            blue_scale,
        ).save(out)
    else:
        placeholder_image(label).save(out)


def clean_text(raw: str) -> str:
    s = raw.strip()
    if s.startswith("^"):
        s = s[1:]
    s = s.rstrip("\\")
    s = s.replace("^@^", " ")
    s = s.replace("^", "")
    s = re.sub(r"~i~|~s~|~u~|~n~", "", s)
    s = re.sub(r"~%?\d+~", "", s)
    s = re.sub(r"!s\d+|!sd|=0|=19", "", s)
    s = re.sub(r"#(?:[0-9a-fA-F]{6})", "", s)
    s = s.replace("``", '"').replace("''", '"')
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
    return parts


def label_slice(lines: list[str], label: str) -> list[str]:
    start_pat = f"*{label.lstrip('*')}"
    start = None
    for i, line in enumerate(lines):
        if line.strip() == start_pat:
            start = i + 1
            break
    if start is None:
        raise SystemExit(f"Label not found: {start_pat}")

    out: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("*") and stripped != start_pat:
            break
        out.append(line)
    return out


def convert(
    script: Path,
    out_json: Path,
    asset_dir: Path,
    label: str,
    asset_root: Path | None,
    limit_text: int | None,
    bg_palette_mode: str,
    crop_blackbars: bool,
    reserve_textbox: bool,
    grayscale: bool,
    saturation: float,
    red_scale: float,
    green_scale: float,
    blue_scale: float,
) -> dict[str, Any]:
    lines = script.read_text(encoding="utf-8", errors="replace").splitlines()
    section = label_slice(lines, label)
    idx = file_index(asset_root)

    bg_assets: list[dict[str, Any]] = []
    bg_seen: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    current_bg_id: str | None = None
    emitted_text = 0
    text_buf: list[str] = []

    def emit_bg(ref: str) -> str:
        if ref in bg_seen:
            return bg_seen[ref]
        asset_id = f"ns_bg_{len(bg_assets):03d}_{slug(Path(ref).stem)[:24]}"
        path = asset_dir / f"{asset_id}.png"
        copy_or_placeholder(
            resolve_asset(asset_root, idx, ref),
            path,
            ref,
            crop_blackbars,
            reserve_textbox,
            grayscale,
            saturation,
            red_scale,
            green_scale,
            blue_scale,
        )
        bg_assets.append({
            "id": asset_id,
            "name": ref,
            "dataUrl": str(path.resolve()),
            "paletteMode": bg_palette_mode,
        })
        bg_seen[ref] = asset_id
        return asset_id

    def flush_text() -> None:
        nonlocal emitted_text
        if not text_buf:
            return
        text = " ".join(t for t in text_buf if t).strip()
        text_buf.clear()
        if not text:
            return
        for part in split_text(text):
            if limit_text is not None and emitted_text >= limit_text:
                return
            node_id = f"scene_{len(nodes) + 1:04d}"
            nodes.append(scene_template(node_id, f"{label} {len(nodes) + 1}", current_bg_id, part))
            emitted_text += 1

    for raw in section:
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        for cmd in split_commands(line):
            if cmd.startswith(";"):
                continue
            if cmd.startswith("^"):
                text_buf.append(clean_text(cmd))
                if cmd.rstrip().endswith("\\"):
                    flush_text()
                continue
            low = cmd.lower()
            if low.startswith("bg "):
                flush_text()
                ref = extract_quoted_path(cmd)
                if ref:
                    current_bg_id = emit_bg(ref)
            elif low.startswith("br") or low.startswith("wait ") or low.startswith("!w"):
                flush_text()
            elif low.startswith("goto ") or low.startswith("tablegoto"):
                flush_text()
                break
        if limit_text is not None and emitted_text >= limit_text:
            break
    flush_text()

    end_id = "end"
    for i, node in enumerate(nodes):
        node["next"] = nodes[i + 1]["id"] if i + 1 < len(nodes) else end_id

    title = scene_template("title", "Title Screen", nodes[0]["bgImageId"] if nodes else None, "")
    title.update({
        "type": "title",
        "tbStyle": "none",
        "next": nodes[0]["id"] if nodes else end_id,
        "titleMain": "Narcissu",
        "titleSub": f"ONScripter proof: {label}",
        "titleMenu": "Start",
    })
    end = scene_template(end_id, "End", nodes[-1]["bgImageId"] if nodes else None, "")
    end["type"] = "end"
    end["next"] = ""
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "version": 1,
        "name": f"Narcissu proof - {label}",
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
            "foregrounds": [],
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
    ap = argparse.ArgumentParser(description="Convert a simple ONScripter/Narcissu label to a WSC VN proof project.")
    ap.add_argument("script", type=Path)
    ap.add_argument("--label", default="gp32_image")
    ap.add_argument("--asset-root", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--asset-dir", type=Path, required=True)
    ap.add_argument("--limit-text", type=int, default=80, help="Maximum emitted text nodes. Use 0 for no limit.")
    ap.add_argument("--bg-palette-mode", choices=["auto-tile", "auto-tile-8", "top-bottom", "left-right"], default="auto-tile")
    ap.add_argument("--crop-blackbars", action="store_true", help="Crop cinematic black bars from source backgrounds before WSC conversion.")
    ap.add_argument("--reserve-textbox", action="store_true", help="Place cropped backgrounds above the runtime textbox area.")
    ap.add_argument("--grayscale", action="store_true", help="Convert backgrounds to grayscale before WSC conversion.")
    ap.add_argument("--saturation", type=float, default=1.0)
    ap.add_argument("--red-scale", type=float, default=1.0)
    ap.add_argument("--green-scale", type=float, default=1.0)
    ap.add_argument("--blue-scale", type=float, default=1.0)
    args = ap.parse_args()
    project = convert(
        args.script,
        args.out,
        args.asset_dir,
        args.label,
        args.asset_root,
        None if args.limit_text == 0 else args.limit_text,
        args.bg_palette_mode,
        args.crop_blackbars,
        args.reserve_textbox,
        args.grayscale,
        args.saturation,
        args.red_scale,
        args.green_scale,
        args.blue_scale,
    )
    print(f"[+] Wrote {args.out}")
    print(
        f"[+] Nodes: {len(project['nodes'])}, "
        f"backgrounds: {len(project['assets']['backgrounds'])}"
    )


if __name__ == "__main__":
    main()
