"""
make_sample_overlays.py: draws the sample frame overlays in overlays/.

Run it from the project folder (on the Mac or the Pi):

    python tools/make_sample_overlays.py

You don't need this script to make your own overlays. Any transparent PNG
dropped into overlays/ works (1080 x 960 px is the ideal size). Canva,
Photoshop or Procreate are great for designing them. This script just
gives you a few to start with.

Each overlay is drawn at double size and then shrunk, which gives smooth
anti-aliased edges.
"""

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "overlays"
W, H = 1080, 960                 # final size: the 9:8 photo shape
S = 2                            # supersampling factor
PARTY = ["#ff4f6d", "#ffb703", "#3ec1d3", "#8f5cff", "#4cd97b", "#ff8a3d"]


def canvas():
    return Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))


def save(img, name):
    img = img.resize((W, H), Image.LANCZOS)
    OUT_DIR.mkdir(exist_ok=True)
    img.save(OUT_DIR / name)
    print("wrote", OUT_DIR / name)


def near_edge(x, y, band):
    """True if the point is within `band` pixels of the image edge."""
    return min(x, y, W * S - x, H * S - y) < band


def rotated_rect(cx, cy, w, h, angle):
    corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in corners]


def heart(cx, cy, size):
    points = []
    for i in range(60):
        t = 2 * math.pi * i / 60
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        points.append((cx + x * size / 32, cy - y * size / 32))
    return points


def star(cx, cy, outer, inner, rotation=0.0):
    points = []
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        a = rotation - math.pi / 2 + i * math.pi / 5
        points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def soft_shadow(img, radius=6 * S, opacity=90):
    """Add a gentle drop shadow under everything drawn, so it pops off the photo."""
    alpha = img.getchannel("A").filter(ImageFilter.GaussianBlur(radius))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: a * opacity // 255))
    shadow = shadow.transform(img.size, Image.AFFINE, (1, 0, -3 * S, 0, 1, -4 * S))
    return Image.alpha_composite(shadow, img)


def confetti():
    rng = random.Random(7)
    img = canvas()
    draw = ImageDraw.Draw(img)
    placed = 0
    while placed < 170:
        x, y = rng.uniform(0, W * S), rng.uniform(0, H * S)
        if not near_edge(x, y, 150 * S):
            continue
        colour = rng.choice(PARTY)
        if rng.random() < 0.35:
            r = rng.uniform(7, 13) * S
            draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)
        else:
            draw.polygon(rotated_rect(x, y, rng.uniform(12, 20) * S, rng.uniform(26, 40) * S,
                                      rng.uniform(0, math.pi)), fill=colour)
        placed += 1
    save(soft_shadow(img), "01-confetti.png")


def hearts():
    rng = random.Random(3)
    img = canvas()
    draw = ImageDraw.Draw(img)
    pinks = ["#ff4f6d", "#ff7b9c", "#ffb3c6", "#e63462"]
    # Clusters in the top-left and bottom-right corners.
    for corner_x, corner_y in [(0, 0), (W * S, H * S)]:
        for _ in range(16):
            distance = rng.uniform(30, 280) * S
            angle = rng.uniform(0, math.pi / 2)
            dx, dy = distance * math.cos(angle), distance * math.sin(angle)
            x = corner_x + dx if corner_x == 0 else corner_x - dx
            y = corner_y + dy if corner_y == 0 else corner_y - dy
            size = rng.uniform(40, 110) * S * (1.2 - distance / (300 * S))
            draw.polygon(heart(x, y, size), fill=rng.choice(pinks))
    save(soft_shadow(img), "02-hearts.png")


def party():
    """Bunting flags strung across the top."""
    img = canvas()
    draw = ImageDraw.Draw(img)
    sag = 70 * S

    def string_y(x):
        return 40 * S + sag * math.sin(math.pi * x / (W * S))

    draw.line([(x, string_y(x)) for x in range(0, W * S + 1, 8)], fill="#4a4a4a", width=4 * S)
    flags = 11
    for i in range(flags):
        x1 = (i + 0.1) * W * S / flags
        x2 = (i + 0.9) * W * S / flags
        mid = (x1 + x2) / 2
        draw.polygon([(x1, string_y(x1)), (x2, string_y(x2)), (mid, string_y(mid) + 110 * S)],
                     fill=PARTY[i % len(PARTY)])
    save(soft_shadow(img), "03-party.png")


def stars():
    rng = random.Random(11)
    img = canvas()
    draw = ImageDraw.Draw(img)
    golds = ["#ffd166", "#ffc43d", "#fff1b8", "#f7b32b"]
    placed = 0
    while placed < 38:
        x, y = rng.uniform(0, W * S), rng.uniform(0, H * S)
        if not near_edge(x, y, 130 * S):
            continue
        outer = rng.uniform(14, 40) * S
        draw.polygon(star(x, y, outer, outer * 0.45, rng.uniform(0, 1)), fill=rng.choice(golds))
        placed += 1
    save(soft_shadow(img, opacity=70), "04-gold_stars.png")


def classic():
    """A clean white rounded frame, like an instant photo."""
    img = canvas()
    border = 34 * S
    frame = Image.new("L", img.size, 255)
    ImageDraw.Draw(frame).rounded_rectangle(
        [border, border, W * S - border, H * S - border], radius=28 * S, fill=0)
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img.paste(white, (0, 0), frame)
    save(img, "05-classic.png")


if __name__ == "__main__":
    random.seed(0)
    confetti()
    hearts()
    party()
    stars()
    classic()
    sys.exit(0)
