"""
packaging/linux/branding/make_branding.py

Generates the SAINTOS artwork from code (nothing binary is committed):

    wallpaper/   SAINTOS-<w>x<h>.png      the default Plasma wallpaper (the SAINT Halo on near-black)
    plymouth/    watermark.png            boot splash wordmark (Plymouth "spinner" theme)
    logo/        saintos-logo-<n>.png     the SAINTOS mark (SAINT.png inside the Halo ring)

    python packaging/linux/branding/make_branding.py OUTPUT_DIR [--name NAME] [--subtitle TEXT]

NAME (default SAINTOS) is the wordmark on the wallpaper and boot splash; Halo builds pass "Halo".

Colours are SAINT's own (ui/theme.py): background #0a0b0d, accent "SAINT orange" #feaa34.
Deterministic for a given font file. Needs Pillow.
"""

import argparse
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[3]
BG = (10, 11, 13)
BG_LIFT = (22, 24, 30)
ACCENT = (254, 170, 52)
TEXT = (236, 238, 242)
MUTED = (138, 143, 152)
WALLPAPER_SIZES = [(3840, 2160), (2560, 1440), (1920, 1080), (1920, 1200), (1366, 768)]

_FONTS = [  # first one found wins; Fedora, then Arch paths
    "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf",
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]


def font(size: int):
    for path in _FONTS:
        if os.path.exists(path):
            f = ImageFont.truetype(path, size)
            try:
                f.set_variation_by_axes([300])          # light weight on variable fonts
            except Exception:
                pass
            return f
    raise SystemExit("make_branding: no TrueType font found (dnf install google-noto-sans-fonts)")


def _radial(size, inner, outer, center, radius) -> Image.Image:
    """A smooth radial gradient (computed small, scaled up — fast and banding-free enough)."""
    w, h = size
    sw, sh = max(1, w // 8), max(1, h // 8)
    img = Image.new("RGB", (sw, sh))
    px = img.load()
    cx, cy, r = center[0] / 8, center[1] / 8, radius / 8
    for y in range(sh):
        for x in range(sw):
            t = min(1.0, math.hypot(x - cx, y - cy) / r)
            t = t * t * (3 - 2 * t)
            px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3))
    return img.resize(size, Image.BICUBIC)


def halo(size, center, radius, width, glow) -> Image.Image:
    """The SAINT Halo: an orange ring with a soft glow, as an RGBA layer."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]
    d.ellipse(box, outline=ACCENT + (255,), width=width)
    blurred = layer.filter(ImageFilter.GaussianBlur(glow))
    out = Image.alpha_composite(blurred, blurred)
    return Image.alpha_composite(out, layer)


def wallpaper(w: int, h: int, name: str = "SAINTOS", subtitle: str = "Fedora Remix  ·  KDE Plasma") -> Image.Image:
    """SAINT front and centre: the SAINT mark inside the Halo ring, the wordmark under it."""
    s = h / 1080
    center = (w // 2, int(h * 0.42))
    radius = int(200 * s)
    img = _radial((w, h), BG_LIFT, BG, center, int(h * 0.9)).convert("RGBA")
    img = Image.alpha_composite(img, halo((w, h), center, radius, max(2, int(5 * s)), int(28 * s)))
    mark = Image.open(ROOT / "SAINT.png").convert("RGBA").resize((int(radius * 1.25),) * 2, Image.LANCZOS)
    img.alpha_composite(mark, (center[0] - mark.width // 2, center[1] - mark.height // 2))
    d = ImageDraw.Draw(img)
    word = font(int(64 * s))
    spaced = " ".join(name)                                   # letter-spaced wordmark
    box = d.textbbox((0, 0), spaced, font=word)
    d.text((center[0] - (box[2] - box[0]) // 2, center[1] + radius + int(56 * s)), spaced, font=word,
           fill=TEXT + (240,))
    d.text((int(74 * s), h - int(66 * s)), subtitle, font=font(int(18 * s)), fill=MUTED + (200,))
    return img.convert("RGB")


def logo(n: int) -> Image.Image:
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ring = halo((n, n), (n // 2, n // 2), int(n * 0.42), max(2, n // 24), max(1, n // 40))
    img = Image.alpha_composite(img, ring)
    mark = Image.open(ROOT / "SAINT.png").convert("RGBA").resize((int(n * 0.56),) * 2, Image.LANCZOS)
    img.alpha_composite(mark, ((n - mark.width) // 2, (n - mark.height) // 2))
    return img


def watermark(text: str = "SAINTOS") -> Image.Image:
    f = font(40)
    box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=f)
    img = Image.new("RGBA", (box[2] + 8, box[3] + 12), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4, 2), text, font=f, fill=TEXT + (255,))
    return img


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Generate the OS artwork")
    ap.add_argument("out")
    ap.add_argument("--name", default="SAINTOS")
    ap.add_argument("--subtitle", default="Fedora Remix  ·  KDE Plasma")
    args = ap.parse_args(argv[1:])
    out = Path(args.out)
    for sub in ("wallpaper", "plymouth", "logo"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    for w, h in WALLPAPER_SIZES:
        wallpaper(w, h, args.name, args.subtitle).save(out / "wallpaper" / f"SAINTOS-{w}x{h}.png", optimize=True)
    for n in (16, 32, 48, 64, 128, 256, 512):
        logo(n).save(out / "logo" / f"saintos-logo-{n}.png", optimize=True)
    watermark(args.name).save(out / "plymouth" / "watermark.png", optimize=True)
    print(f"branding written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
