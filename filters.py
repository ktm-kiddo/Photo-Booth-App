"""
filters.py: the colour filters guests can choose from.

Every filter is a small function that takes a Pillow RGB image and returns a
new one. The *same* functions are used for the live preview (small frames,
20 times a second) and for the final photos (full resolution), so what
guests see on the iPad is exactly what they get on the strip.

To stay fast enough for live video, the filters stick to cheap operations:

  * curves  - a lookup table per colour channel ("make every 40 red into 60")
  * colourise - map grey levels onto a colour gradient (sepia)
  * saturation - blend towards / away from black & white
  * vignette - darken the corners using a pre-computed mask

Adding a filter: write a function below and add it to FILTERS at the bottom.
"""

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageOps


# --- Building blocks ---------------------------------------------------------

def curve(*points):
    """Make a 256-entry lookup table from (input, output) control points.

    curve((0, 0), (255, 255)) leaves values unchanged.
    curve((0, 40), (255, 255)) lifts the blacks (a faded look).
    curve((0, 0), (64, 40), (192, 215), (255, 255)) is an S-curve (more contrast).
    """
    xs, ys = zip(*points)
    table = np.interp(np.arange(256), xs, ys)
    return np.clip(table, 0, 255).astype(np.uint8).tolist()


IDENTITY = curve((0, 0), (255, 255))


def apply_curves(img, red=IDENTITY, green=IDENTITY, blue=IDENTITY):
    """Apply one lookup table per channel. Pillow does this very quickly."""
    return img.point(red + green + blue)


def saturation(img, amount):
    """amount < 1 = more muted, 1 = unchanged, > 1 = more colourful."""
    return ImageEnhance.Color(img).enhance(amount)


def grayscale(img, tone=IDENTITY):
    """Black & white, with an optional tone curve."""
    return ImageOps.grayscale(img).point(tone).convert("RGB")


_vignette_cache = {}


def vignette(img, strength=0.4):
    """Darken the corners. strength 0 = none, 1 = corners go black.

    The mask only depends on the image size, so it's built once per size
    and reused for every frame.
    """
    key = (img.size, strength)
    mask = _vignette_cache.get(key)
    if mask is None:
        w, h = img.size
        y, x = np.ogrid[-1:1:h * 1j, -1:1:w * 1j]
        distance = np.sqrt(x * x + y * y) / np.sqrt(2)          # 0 centre .. 1 corner
        shade = 1 - strength * np.clip((distance - 0.35) / 0.65, 0, 1) ** 1.6
        mask = Image.fromarray((shade * 255).astype(np.uint8)).convert("RGB")
        _vignette_cache[key] = mask
    return ImageChops.multiply(img, mask)


# --- Tone curves used by several filters ------------------------------------

S_CURVE = curve((0, 0), (64, 52), (192, 205), (255, 255))
STRONG_S = curve((0, 0), (40, 18), (128, 128), (215, 240), (255, 255))


# --- The filters -------------------------------------------------------------

def original(img):
    return img


def black_and_white(img):
    return grayscale(img, S_CURVE)


def sepia(img):
    gray = ImageOps.grayscale(img).point(S_CURVE)
    return ImageOps.colorize(gray, black="#2a1a0c", mid="#a07850", white="#fff3dc")


def noir(img):
    return vignette(grayscale(img, STRONG_S), 0.55)


def vintage(img):
    img = apply_curves(
        img,
        red=curve((0, 38), (128, 140), (255, 245)),
        green=curve((0, 28), (128, 128), (255, 232)),
        blue=curve((0, 55), (128, 110), (255, 200)),
    )
    return vignette(saturation(img, 0.8), 0.35)


def warm(img):
    img = apply_curves(
        img,
        red=curve((0, 8), (128, 145), (255, 255)),
        green=curve((0, 4), (128, 132), (255, 248)),
        blue=curve((0, 0), (128, 110), (255, 220)),
    )
    return saturation(img, 1.1)


def cool(img):
    return apply_curves(
        img,
        red=curve((0, 0), (128, 115), (255, 235)),
        green=curve((0, 4), (128, 128), (255, 250)),
        blue=curve((0, 18), (128, 145), (255, 255)),
    )


def vivid(img):
    return saturation(apply_curves(img, S_CURVE, S_CURVE, S_CURVE), 1.5)


def fade(img):
    matte = curve((0, 45), (128, 135), (255, 232))
    return saturation(apply_curves(img, matte, matte, matte), 0.7)


def retro(img):
    """Cross-processed look: teal shadows, golden highlights."""
    img = apply_curves(
        img,
        red=curve((0, 0), (70, 50), (190, 215), (255, 255)),
        green=curve((0, 10), (128, 135), (255, 245)),
        blue=curve((0, 50), (128, 120), (255, 185)),
    )
    return vignette(saturation(img, 1.15), 0.3)


def pop(img):
    """Bold pop-art colours with fewer, flatter tones."""
    img = ImageOps.posterize(apply_curves(img, S_CURVE, S_CURVE, S_CURVE), 3)
    return saturation(img, 1.7)


# id -> (name shown on the button, function). The order here is the order on screen.
FILTERS = {
    "none": ("Original", original),
    "bw": ("B&W", black_and_white),
    "sepia": ("Sepia", sepia),
    "noir": ("Noir", noir),
    "vintage": ("Vintage", vintage),
    "warm": ("Golden", warm),
    "cool": ("Cool", cool),
    "vivid": ("Vivid", vivid),
    "fade": ("Faded", fade),
    "retro": ("Retro", retro),
    "pop": ("Pop", pop),
}


def apply(filter_id, img):
    """Apply a filter by id. Unknown ids fall back to no filter."""
    _, function = FILTERS.get(filter_id, FILTERS["none"])
    return function(img)


def choices():
    """List of {id, name} for the web page."""
    return [{"id": fid, "name": name} for fid, (name, _) in FILTERS.items()]
