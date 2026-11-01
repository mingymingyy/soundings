"""One-off brand assets: favicon, touch icons, manifest and OG image. Outputs are committed.

Needs Pillow and static TTFs of Playfair Display and Inter (paths below). The nightly
build does not run this, so CI needs neither.
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "site" / "assets"
SERIF = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / ".tmp" / "playfair.ttf")
SANS = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / ".tmp" / "inter.ttf")
BG = (11, 16, 23)
INK = (244, 241, 235)
BONE = (215, 210, 203)
GREY = (162, 171, 179)
BLUE = (61, 111, 184)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="6" fill="#0b1017"/>
<g fill="none" stroke="#f4f1eb" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
<path d="M16 3v18"/><path d="M4 10.500c2.900-2.800 5.900-2.800 8.800 0s5.900 2.800 8.800 0 4.700-2.800 6.400-1.400"/><path d="M4 16.500c2.900-2.800 5.900-2.800 8.800 0s5.900 2.800 8.800 0 4.700-2.800 6.400-1.400"/>
</g>
<path d="M16 21l3 4-3 4-3-4z" fill="#7ea6e6"/>
</svg>
"""


def wave(y, x0, x1, amp, wave_len, phase=0.0):
    return [(x, y + amp * math.sin((x - x0) / wave_len * 2 * math.pi + phase)) for x in range(int(x0), int(x1) + 1, 2)]


def draw_mark(size: int, pad: float, rounded: bool = True) -> Image.Image:
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.19), fill=BG + (255,))
    else:
        d.rectangle((0, 0, s, s), fill=BG + (255,))
    inner = s - 2 * pad * s
    x = lambda v: pad * s + v / 32 * inner  # noqa: E731
    w = max(2, int(inner / 32 * 1.5))
    d.line([(x(16), x(3)), (x(16), x(21))], fill=INK + (255,), width=w)
    for yy in (10.5, 16.5):
        d.line(wave(x(yy), x(4), x(28), inner / 32 * 1.6, inner / 32 * 17.6, math.pi), fill=INK + (255,), width=w, joint="curve")
    d.polygon([(x(16), x(21)), (x(19), x(25)), (x(16), x(29)), (x(13), x(25))], fill=(126, 166, 230, 255))
    return img.resize((size, size), Image.LANCZOS)


def og() -> None:
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), BG)
    glow = Image.new("RGB", (w, h), BG)
    ImageDraw.Draw(glow).ellipse((430, -130, 1270, 560), fill=(30, 62, 116))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(150)), 0.9)
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, w, 96):
        d.line([(x, 0), (x, h)], fill=(215, 210, 203, 8), width=1)

    # A generic rising curve shape, drawn as a seabed. No numbers: this image can't go stale.
    xs = [i / 10 for i in range(11)]
    ys = [0.16, 0.2, 0.26, 0.34, 0.44, 0.5, 0.58, 0.63, 0.74, 0.97, 0.94]
    left, right, base, top = 80, w - 80, 590, 300
    pts = [(left + xv * (right - left), base - yv * (base - top)) for xv, yv in zip(xs, ys)]
    d.polygon(pts + [(right, h), (left, h)], fill=(61, 111, 184, 36))
    d.line(pts, fill=INK + (235,), width=3, joint="curve")
    for x_, y_ in pts:
        d.line([(x_, y_), (x_, h)], fill=(215, 210, 203, 34), width=1)
        d.ellipse((x_ - 5, y_ - 5, x_ + 5, y_ + 5), fill=BG, outline=INK, width=2)

    mark = draw_mark(64, 0.0, rounded=True)
    img.paste(mark, (80, 70), mark)
    d = ImageDraw.Draw(img)
    word = ImageFont.truetype(SERIF, 34)
    x = 164
    for ch in "SOUNDINGS":
        d.text((x, 84), ch, font=word, fill=INK)
        x += word.getlength(ch) + 7
    d.text((80, 158), "The world's tape,", font=ImageFont.truetype(SERIF, 66), fill=INK)
    d.text((80, 232), "read from Singapore.", font=ImageFont.truetype(SERIF, 66), fill=BONE)
    img.save(ASSETS / "og.png", optimize=True)


def main() -> None:
    (ASSETS / "favicon.svg").write_text(SVG, encoding="utf-8")
    draw_mark(32, 0.0).save(ASSETS / "favicon-32.png", optimize=True)
    draw_mark(180, 0.08, rounded=False).save(ASSETS / "apple-touch-icon.png", optimize=True)
    draw_mark(192, 0.08).save(ASSETS / "icon-192.png", optimize=True)
    draw_mark(512, 0.08).save(ASSETS / "icon-512.png", optimize=True)
    og()
    manifest = {
        "name": "Soundings",
        "short_name": "Soundings",
        "description": "Live global macro dashboard, read from Singapore.",
        "start_url": "./",
        "display": "standalone",
        "background_color": "#0b1017",
        "theme_color": "#0b1017",
        "icons": [
            {"src": "assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "assets/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    (ROOT / "site" / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
