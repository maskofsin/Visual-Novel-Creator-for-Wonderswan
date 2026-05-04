#!/usr/bin/env python3
"""
wsc_image_prep.py — prepare WonderSwan Color friendly PNG assets.

What it does:
- Backgrounds -> exact 224x144, opaque, indexed, up to 16 colors.
- Characters  -> exact 96x128, transparent padding, indexed, up to 16 colors
                (transparent pixels are preserved as index 0 when possible).

This script is intended to be used BEFORE importing assets into WSC VN Studio.
The studio/converter can then pack the image into 4bpp tiles without needing
to resize or "fix" the source art again.

Usage:
  python wsc_image_prep.py bg   input.png output.png
  python wsc_image_prep.py char input.png output.png

Optional flags:
  --resample nearest|bilinear|bicubic|lanczos   (default: lanczos)
  --bg-fill #RRGGBB                              (default: #000000)
  --colors 16                                    (default: 16)

Notes:
- For best results, feed in art that is already close to pixel-art limits.
- If the source already is indexed PNG and matches the target size, the script
  keeps it as-is when possible.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageOps

BG_SIZE = (224, 144)
CHAR_SIZE = (96, 128)


def parse_hex_color(value: str) -> Tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise argparse.ArgumentTypeError("color must be in #RRGGBB format")
    try:
        return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid hex color") from exc


def get_resample(name: str) -> int:
    name = name.lower()
    mapping = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    if name not in mapping:
        raise argparse.ArgumentTypeError(f"invalid resample mode: {name}")
    return mapping[name]


def fit_cover(img: Image.Image, size: Tuple[int, int], resample: int) -> Image.Image:
    """Crop to cover the full target size."""
    return ImageOps.fit(img, size, method=resample, centering=(0.5, 0.5))


def fit_contain(img: Image.Image, size: Tuple[int, int], resample: int, fill: Tuple[int, int, int, int]) -> Image.Image:
    """Pad to the full target size while preserving aspect ratio."""
    return ImageOps.pad(img, size, method=resample, color=fill, centering=(0.5, 0.5))


def quantize_to_indexed(img: Image.Image, colors: int, dither: Image.Dither = Image.Dither.NONE) -> Image.Image:
    """Quantize to indexed PNG, preserving alpha if present."""
    if img.mode not in ("RGB", "RGBA"):
        # normalize to a mode Pillow can quantize well
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    return img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=dither)


def prepare_bg(src: Path, dst: Path, colors: int, resample: int, bg_fill: Tuple[int, int, int]) -> None:
    img = Image.open(src)
    # Opaque, exact size. We crop to fill the frame, then flatten onto bg_fill.
    rgba = img.convert("RGBA")
    fitted = fit_cover(rgba, BG_SIZE, resample)
    canvas = Image.new("RGBA", BG_SIZE, bg_fill + (255,))
    flattened = Image.alpha_composite(canvas, fitted).convert("RGB")

    # Keep already-indexed exact-size images when possible.
    if img.mode == "P" and img.size == BG_SIZE and img.info.get("transparency") is None:
        # If it already has <= colors used, trust it.
        used = len(set(img.getdata()))
        if used <= colors:
            img.save(dst)
            return

    q = quantize_to_indexed(flattened, colors=colors, dither=Image.Dither.NONE)
    q.save(dst)


def prepare_char(src: Path, dst: Path, colors: int, resample: int) -> None:
    img = Image.open(src)
    rgba = img.convert("RGBA")
    fitted = fit_contain(rgba, CHAR_SIZE, resample, (0, 0, 0, 0))

    # If already indexed, exact size, and alpha is already in place, keep when possible.
    if img.mode == "P" and img.size == CHAR_SIZE and img.info.get("transparency") is not None:
        used = len(set(img.getdata()))
        if used <= colors:
            img.save(dst)
            return

    q = quantize_to_indexed(fitted, colors=colors, dither=Image.Dither.NONE)
    q.save(dst)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare WonderSwan Color friendly PNG assets.")
    ap.add_argument("mode", choices=("bg", "char"), help="asset type")
    ap.add_argument("input", type=Path, help="source image")
    ap.add_argument("output", type=Path, help="output PNG")
    ap.add_argument("--colors", type=int, default=16, help="max palette colors (default: 16)")
    ap.add_argument("--resample", default="lanczos", help="nearest|bilinear|bicubic|lanczos")
    ap.add_argument("--bg-fill", default="#000000", type=parse_hex_color, help="background fill color for BGs")
    args = ap.parse_args()

    if not args.input.exists():
        raise SystemExit(f"input not found: {args.input}")

    resample = get_resample(args.resample)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "bg":
        prepare_bg(args.input, args.output, args.colors, resample, args.bg_fill)
    else:
        prepare_char(args.input, args.output, args.colors, resample)

    out_img = Image.open(args.output)
    print(
        f"[ok] {args.mode}: {args.input.name} -> {args.output.name} "
        f"({out_img.mode}, {out_img.size})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
