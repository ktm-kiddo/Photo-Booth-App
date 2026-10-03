"""
imaging.py: turns camera frames into finished photos, strips and prints.

Sizes (all at 300 DPI)
----------------------
  photo  9:8 shape, cut from the middle of the camera frame
         (1215 x 1080 from a 1920 x 1080 camera)
  strip  600 x 1800 px  = 2 x 6 inches: three photos and a title, in one
         of several designs (see "Strip designs" below)
  print  1200 x 1800 px = 4 x 6 inches: two strips side by side, cut in half

The live preview on the iPad uses the same 9:8 shape, so guests see exactly
what will end up on the strip.
"""

import hashlib
import io
import json
import logging
import random
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

import config
import filters

log = logging.getLogger(__name__)

# --- Layout -------------------------------------------------------------------
PHOTO_ASPECT = 9 / 8                 # width / height of every photo
STRIP_SIZE = (600, 1800)             # 2 x 6 in at 300 DPI
PRINT_SIZE = (1200, 1800)            # 4 x 6 in at 300 DPI
DPI = (300, 300)
STRIP_MARGIN = 30                    # outside edge around the photos
STRIP_GAP = 24                       # space between photos
STRIP_PHOTO_SIZE = (540, 480)        # each photo on the strip (9:8)
STRIP_FOOTER_TOP = STRIP_MARGIN + 3 * STRIP_PHOTO_SIZE[1] + 2 * STRIP_GAP + STRIP_GAP

FONTS_DIR = config.BASE_DIR / "static" / "fonts"
OVERLAYS_DIR = config.BASE_DIR / "overlays"


# --- Camera frame -> photo ----------------------------------------------------

def crop_to_photo(frame):
    """Cut the largest 9:8 rectangle out of the middle of a camera frame.

    `frame` is an OpenCV image (numpy array). Returns a view, not a copy.
    """
    height, width = frame.shape[:2]
    if width / height > PHOTO_ASPECT:          # frame is wider: trim the sides
        new_width = round(height * PHOTO_ASPECT)
        left = (width - new_width) // 2
        return frame[:, left:left + new_width]
    new_height = round(width / PHOTO_ASPECT)   # frame is taller: trim top/bottom
    top = (height - new_height) // 2
    return frame[top:top + new_height, :]


def frame_to_image(frame, width=None, crop=True):
    """Crop a camera frame to 9:8, optionally shrink it, and return a Pillow RGB image.

    crop=False keeps the camera's full picture (used for the full-screen
    start screen).
    """
    if crop:
        frame = crop_to_photo(frame)
    if width and frame.shape[1] > width:
        height = round(width * frame.shape[0] / frame.shape[1])
        # INTER_AREA is sharpest for big reductions (thumbnails); INTER_LINEAR is
        # much cheaper and looks the same at preview size.
        method = cv2.INTER_AREA if width < frame.shape[1] / 2 else cv2.INTER_LINEAR
        frame = cv2.resize(frame, (width, height), interpolation=method)
    if config.MIRROR:
        frame = cv2.flip(frame, 1)
    # OpenCV stores colours as BGR, Pillow expects RGB.
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def to_jpeg(img, quality=90):
    """Encode a Pillow image as JPEG bytes."""
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


# --- Overlays -----------------------------------------------------------------
# An overlay is a transparent PNG laid over each photo (a frame, confetti...).
# They come from two folders:
#   overlays/        the ones that ship with the app (edit these on the Mac)
#   data/overlays/   ones uploaded on the admin page (live only on the Pi, so
#                    deploying from the Mac never removes them)
# Best size: 1080 x 960 px (the 9:8 photo shape). Other sizes get stretched.
#
# Their order, names and which are hidden are set on the admin page and kept
# in data/overlays.json. Without a name there, the button label comes from
# the file name: "02-gold_stars.png" -> "Gold Stars".
#
# "No frame" is in the same list (id "none"), so the admin can move or hide
# it too. Guests start with whichever frame is first.

UPLOADED_OVERLAYS_DIR = config.DATA_DIR / "overlays"
OVERLAY_SETTINGS_FILE = config.DATA_DIR / "overlays.json"
_OLD_HIDDEN_FILE = config.DATA_DIR / "overlays_hidden.json"     # from an earlier version
NO_FRAME = "none"


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    """Write JSON safely: to a temporary file first, then swap it in."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(path)


def _overlay_files():
    """{id: path} of every overlay PNG, built-in first."""
    files = {}
    for folder in (OVERLAYS_DIR, UPLOADED_OVERLAYS_DIR):
        for path in sorted(folder.glob("*.png")) if folder.is_dir() else []:
            if path.stem != NO_FRAME:
                files.setdefault(path.stem, path)
    return files


def overlay_settings():
    """{"order": [ids], "names": {id: name}, "hidden": [ids]}"""
    data = _read_json(OVERLAY_SETTINGS_FILE, {})
    if "hidden" not in data:
        data["hidden"] = _read_json(_OLD_HIDDEN_FILE, [])
    data.setdefault("order", [])
    data.setdefault("names", {})
    return data


def save_overlay_settings(data):
    _write_json(OVERLAY_SETTINGS_FILE, data)


def overlay_label(overlay_id):
    if overlay_id == NO_FRAME:
        return "No frame"
    label = re.sub(r"^\d+[-_ ]*", "", overlay_id).replace("_", " ").replace("-", " ")
    return label.title() or overlay_id


def list_overlays(include_hidden=False):
    """Return [{id, name, uploaded, hidden, none}] in the order set on the admin page."""
    files = _overlay_files()
    settings = overlay_settings()
    hidden = set(settings["hidden"])
    ids = [NO_FRAME] + list(files)
    order = settings["order"]
    if order and NO_FRAME not in order:
        order = [NO_FRAME] + order          # saved before "No frame" could be moved: keep it first
    ordered = [i for i in order if i in ids] + [i for i in ids if i not in order]
    result = []
    for overlay_id in ordered:
        if overlay_id in hidden and not include_hidden:
            continue
        result.append({"id": overlay_id,
                       "name": settings["names"].get(overlay_id) or overlay_label(overlay_id),
                       "uploaded": overlay_id in files and files[overlay_id].parent == UPLOADED_OVERLAYS_DIR,
                       "hidden": overlay_id in hidden,
                       "none": overlay_id == NO_FRAME})
    return result


def default_overlay():
    """The frame guests start with: the first one shown, or no frame."""
    shown = list_overlays()
    return shown[0]["id"] if shown else NO_FRAME


def overlay_path(overlay_id):
    """Path of an overlay PNG, or None. Only known ids work, so '../x' can't."""
    if not overlay_id or overlay_id == NO_FRAME:
        return None
    return _overlay_files().get(overlay_id)


_overlay_cache = {}


def _overlay_image(path, size):
    """Load an overlay resized to `size`, cached (reloaded if the file changes)."""
    key = (str(path), path.stat().st_mtime, size)
    img = _overlay_cache.get(key)
    if img is None:
        img = Image.open(path).convert("RGBA").resize(size, Image.LANCZOS)
        _overlay_cache.clear()      # keep memory use small: only the latest one
        _overlay_cache[key] = img
    return img


def apply_overlay(img, overlay_id):
    path = overlay_path(overlay_id)
    if path is None:
        return img
    base = img.convert("RGBA")
    base.alpha_composite(_overlay_image(path, img.size))
    return base.convert("RGB")


def finish_photo(frame, filter_id, overlay_id):
    """Camera frame -> finished full-resolution photo (cropped, filtered, overlaid)."""
    img = frame_to_image(frame)
    img = filters.apply(filter_id, img)
    return apply_overlay(img, overlay_id)


# --- Fonts --------------------------------------------------------------------

@lru_cache(maxsize=64)
def _font(filename, size, variation=None):
    """Load a font from static/fonts, falling back to Pillow's built-in font."""
    try:
        font = ImageFont.truetype(str(FONTS_DIR / filename), size)
        if variation:
            font.set_variation_by_name(variation)
        return font
    except (OSError, ValueError) as error:
        log.warning("Font %s unavailable (%s); using the built-in font", filename, error)
        return ImageFont.load_default(size)


def _text_width(text, font, tracking=0):
    """Width of `text` in pixels, with `tracking` extra pixels between letters."""
    if not tracking:
        return font.getlength(text)
    return sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def _draw_centered(draw, centre, text, font, fill, tracking=0):
    """Draw text centred on `centre`, optionally letter-spaced."""
    if not tracking:
        draw.text(centre, text, font=font, fill=fill, anchor="mm")
        return
    x = centre[0] - _text_width(text, font, tracking) / 2
    for ch in text:
        draw.text((x, centre[1]), ch, font=font, fill=fill, anchor="lm")
        x += font.getlength(ch) + tracking


def _fit_font(text, spec, max_width):
    """Largest size (up to the spec's size) at which `text` fits max_width."""
    filename, variation, size, tracking = spec
    while True:
        font = _font(filename, size, variation)
        if _text_width(text, font, tracking) <= max_width or size <= 24:
            return font
        size -= 4


# --- Strip designs -------------------------------------------------------------
# A design sets the colours, fonts and decoration of the whole strip. The
# photos are always in the same place, so any design works with any photos.
#
# The built-in designs are below. On the admin page they can be edited,
# hidden, reordered or reset, and new ones added; those changes are kept in
# data/strip_themes.json and take priority over the built-in versions.

# Fonts a design can use: key -> (file, variation, CSS family, CSS weight, label).
# The CSS family/weight are for the buttons on screen (fonts in style.css).
FONTS = {
    "pacifico":         ("Pacifico-Regular.ttf", None, "Pacifico", 400, "Pacifico (script)"),
    "bebas":            ("BebasNeue-Regular.ttf", None, "Bebas Neue", 400, "Bebas Neue (bold capitals)"),
    "playfair":         ("PlayfairDisplay.ttf", "Regular", "Playfair Display", 400, "Playfair (serif)"),
    "playfair-bold":    ("PlayfairDisplay.ttf", "Bold", "Playfair Display", 700, "Playfair bold (serif)"),
    "special-elite":    ("SpecialElite-Regular.ttf", None, "Special Elite", 400, "Special Elite (typewriter)"),
    "caveat":           ("Caveat.ttf", "Regular", "Caveat", 400, "Caveat (handwriting)"),
    "caveat-bold":      ("Caveat.ttf", "Bold", "Caveat", 700, "Caveat bold (handwriting)"),
    "fredoka":          ("Fredoka.ttf", "Medium", "Fredoka", 500, "Fredoka (rounded)"),
    "fredoka-semibold": ("Fredoka.ttf", "SemiBold", "Fredoka", 600, "Fredoka semibold (rounded)"),
}

DECORATIONS = {
    "none": "None",
    "sprockets": "Film holes down the sides",
    "gold_border": "Thin double border",
    "lines": "Notebook lines",
    "confetti": "Confetti",
    "hearts": "Hearts",
}


def _design(name, background, text, accent, title_font, title_size, title_spacing,
            date_font, date_size, date_spacing, upper=False, decor="none", date_color="accent",
            art_back="", art_front=""):
    return {"name": name, "background": background, "text": text, "accent": accent,
            "title_font": title_font, "title_size": title_size, "title_spacing": title_spacing,
            "date_font": date_font, "date_size": date_size, "date_spacing": date_spacing,
            "upper": upper, "decor": decor, "date_color": date_color,
            "art_back": art_back, "art_front": art_front}


BUILTIN_THEMES = {
    "classic":  _design("Classic", "#ffffff", "#1d1d1f", "#ff4f6d", "pacifico", 76, 0, "fredoka", 30, 3),
    "midnight": _design("Midnight", "#141118", "#f6f1ea", "#d9b26f", "bebas", 104, 6, "fredoka", 26, 5, upper=True),
    "film":     _design("Film", "#0c0c0c", "#f2f2f2", "#ffc23d", "bebas", 96, 4, "special-elite", 26, 2,
                        upper=True, decor="sprockets"),
    "blush":    _design("Blush", "#f9e1e5", "#7d2e43", "#c2566f", "playfair-bold", 68, 0, "playfair", 28, 2,
                        decor="hearts"),
    "elegant":  _design("Elegant", "#fbf7ef", "#4f412b", "#b8955a", "playfair", 66, 1, "playfair", 24, 6,
                        decor="gold_border"),
    "retro":    _design("Retro", "#e8d9bd", "#3a2c1f", "#b5562f", "special-elite", 60, 1, "special-elite", 26, 2),
    "party":    _design("Party", "#ffffff", "#ff4f6d", "#8f5cff", "pacifico", 74, 0, "fredoka-semibold", 28, 3,
                        decor="confetti"),
    "doodle":   _design("Doodle", "#fffdf4", "#2b4a8b", "#e2574c", "caveat-bold", 92, 0, "caveat", 36, 0,
                        decor="lines", date_color="text"),
}

THEMES_FILE = config.DATA_DIR / "strip_themes.json"

# A design can also have artwork: full-strip transparent PNGs (600 x 1800),
# usually drawn by an AI model. "art_back" goes behind the photos, "art_front"
# on top of them (stickers over the corners...). The title and date are
# always written last, on top of everything. Each picture is saved once under
# a name made from its contents, so duplicated designs share the same file.
STRIP_ART_DIR = config.DATA_DIR / "strip_art"
ART_ID = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_THEME = "classic"
CAPTION_MAX_LENGTH = 28


def _theme_settings():
    """{"themes": {id: design}, "order": [ids], "hidden": [ids]}"""
    data = _read_json(THEMES_FILE, {})
    data.setdefault("themes", {})
    data.setdefault("order", [])
    data.setdefault("hidden", [])
    return data


def get_theme(theme_id):
    """The design with this id (the admin's edited version if there is one), or None."""
    saved = _theme_settings()["themes"].get(theme_id)
    if saved:
        return {**BUILTIN_THEMES.get(theme_id, {}), **saved}
    return BUILTIN_THEMES.get(theme_id)


def all_themes(include_hidden=False):
    """Every design in display order, with flags for the admin page."""
    settings = _theme_settings()
    ids = list(BUILTIN_THEMES) + [i for i in settings["themes"] if i not in BUILTIN_THEMES]
    ordered = [i for i in settings["order"] if i in ids] + [i for i in ids if i not in settings["order"]]
    result = []
    for theme_id in ordered:
        hidden = theme_id in settings["hidden"]
        if hidden and not include_hidden:
            continue
        result.append({"id": theme_id, **get_theme(theme_id), "builtin": theme_id in BUILTIN_THEMES,
                       "edited": theme_id in BUILTIN_THEMES and theme_id in settings["themes"],
                       "hidden": hidden})
    return result


def theme_choices():
    """The designs guests can pick, with what the buttons need to look right."""
    return [{"id": t["id"], "name": t["name"], "background": t["background"], "text": t["text"],
             "accent": t["accent"], "font": FONTS[t["title_font"]][2], "weight": FONTS[t["title_font"]][3],
             "upper": t["upper"], "art": t.get("art_back") or ""} for t in all_themes()]


def clean_theme(theme_id):
    """A design id that exists and isn't hidden (falls back to the first one)."""
    visible = [t["id"] for t in all_themes()]
    if theme_id in visible:
        return theme_id
    return DEFAULT_THEME if DEFAULT_THEME in visible else (visible[0] if visible else DEFAULT_THEME)


def validate_theme(data):
    """Check a design sent from the admin page. Returns a clean copy or raises ValueError."""
    def colour(key):
        value = str(data.get(key, "")).strip()
        if re.match(r"^#[0-9a-fA-F]{3}$", value):
            value = "#" + "".join(ch * 2 for ch in value[1:])
        if not re.match(r"^#[0-9a-fA-F]{6}$", value):
            raise ValueError(f"“{key}” must be a colour like #1d1d1f.")
        return value.lower()

    def number(key, low, high):
        try:
            value = int(float(data.get(key)))
        except (TypeError, ValueError):
            raise ValueError(f"“{key}” must be a number.")
        return max(low, min(high, value))

    def choice(key, options):
        value = data.get(key)
        if value not in options:
            raise ValueError(f"Unknown {key}: {value!r}.")
        return value

    def art(key):
        value = str(data.get(key) or "")
        return value if strip_art_path(value) else ""

    name = " ".join(str(data.get("name", "")).split())[:30]
    if not name:
        raise ValueError("Give the design a name.")
    return _design(
        name, colour("background"), colour("text"), colour("accent"),
        choice("title_font", FONTS), number("title_size", 24, 160), number("title_spacing", 0, 30),
        choice("date_font", FONTS), number("date_size", 12, 70), number("date_spacing", 0, 30),
        upper=bool(data.get("upper")), decor=choice("decor", DECORATIONS),
        date_color=choice("date_color", ("accent", "text")),
        art_back=art("art_back"), art_front=art("art_front"),
    )


def save_theme(theme_id, data):
    """Save a design (theme_id=None makes a new one). Returns its id."""
    design = validate_theme(data)
    settings = _theme_settings()
    if not theme_id:
        base = re.sub(r"[^a-z0-9]+", "-", design["name"].lower()).strip("-") or "design"
        theme_id, n = base, 2
        while theme_id in BUILTIN_THEMES or theme_id in settings["themes"]:
            theme_id, n = f"{base}-{n}", n + 1
    settings["themes"][theme_id] = design
    _write_json(THEMES_FILE, settings)
    return theme_id


def delete_theme(theme_id):
    """Delete a design you made, or reset a built-in one to how it shipped."""
    settings = _theme_settings()
    settings["themes"].pop(theme_id, None)
    if theme_id not in BUILTIN_THEMES:
        settings["order"] = [i for i in settings["order"] if i != theme_id]
        settings["hidden"] = [i for i in settings["hidden"] if i != theme_id]
    _write_json(THEMES_FILE, settings)


def set_theme_hidden(theme_id, hidden):
    settings = _theme_settings()
    ids = set(settings["hidden"])
    ids.add(theme_id) if hidden else ids.discard(theme_id)
    settings["hidden"] = sorted(ids)
    _write_json(THEMES_FILE, settings)


def set_theme_order(ids):
    settings = _theme_settings()
    settings["order"] = [str(i) for i in ids]
    _write_json(THEMES_FILE, settings)


def strip_art_path(art_id):
    """Path of a saved artwork picture, or None. Only real ids work, so '../x' can't."""
    if not ART_ID.match(str(art_id or "")):
        return None
    path = STRIP_ART_DIR / f"{art_id}.png"
    return path if path.is_file() else None


def save_strip_art(img):
    """Save artwork for a design (any size: it's stretched to the strip). Returns its id."""
    img = img.convert("RGBA")
    if img.size != STRIP_SIZE:
        img = img.resize(STRIP_SIZE, Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, "PNG", optimize=True)
    data = buffer.getvalue()
    art_id = hashlib.sha1(data).hexdigest()
    STRIP_ART_DIR.mkdir(parents=True, exist_ok=True)
    path = STRIP_ART_DIR / f"{art_id}.png"
    if not path.exists():
        path.write_bytes(data)
    return art_id


@lru_cache(maxsize=8)
def _strip_art(art_id):
    return Image.open(STRIP_ART_DIR / f"{art_id}.png").convert("RGBA")


def _add_art(strip, art_id):
    """Lay a design's artwork over the strip (a no-op without artwork)."""
    if strip_art_path(art_id):
        strip.alpha_composite(_strip_art(art_id))


def clean_caption(caption):
    """Tidy a guest-typed caption: one line, no odd characters, limited length."""
    caption = " ".join(str(caption or "").split())          # collapse spaces/newlines
    caption = "".join(ch for ch in caption if ch.isprintable())
    return caption[:CAPTION_MAX_LENGTH]


def _photo_box(index):
    """(left, top, right, bottom) of photo `index` (0-2) on the strip."""
    top = STRIP_MARGIN + index * (STRIP_PHOTO_SIZE[1] + STRIP_GAP)
    return (STRIP_MARGIN, top, STRIP_MARGIN + STRIP_PHOTO_SIZE[0], top + STRIP_PHOTO_SIZE[1])


def _draw_decor(strip, draw, theme):
    """Extra decorations for some designs, drawn before the photos go on."""
    decor = theme.get("decor") or "none"
    w, h = STRIP_SIZE
    if decor == "sprockets":
        # Film-strip holes down both edges.
        for y in range(14, h - 20, 44):
            for x in (8, w - 22):
                draw.rounded_rectangle([x, y, x + 14, y + 22], radius=4, fill="#e9e9e9")
    elif decor == "gold_border":
        draw.rectangle([9, 9, w - 10, h - 10], outline=theme["accent"], width=2)
        draw.rectangle([16, 16, w - 17, h - 17], outline=theme["accent"], width=1)
    elif decor == "lines":
        # Notebook lines in the footer, with a red margin line.
        for y in range(STRIP_FOOTER_TOP + 30, h - 10, 38):
            draw.line([(0, y), (w, y)], fill="#cfdcf2", width=2)
        draw.line([(52, STRIP_FOOTER_TOP), (52, h)], fill="#f0b3ad", width=2)
    elif decor in ("confetti", "hearts"):
        rng = random.Random(42)          # same pattern on every strip
        colours = (["#ff4f6d", "#ffb703", "#3ec1d3", "#8f5cff", "#4cd97b"]
                   if decor == "confetti" else [theme["accent"], "#e88fa2", "#f3b8c4"])
        placed = 0
        while placed < 46:
            x, y = rng.uniform(4, w - 4), rng.uniform(STRIP_FOOTER_TOP - 10, h - 6)
            # Keep the middle of the footer clear, so the text stays readable.
            if 64 < x < w - 64 and STRIP_FOOTER_TOP + 24 < y < h - 22:
                continue
            placed += 1
            if decor == "hearts":
                _heart(draw, x, y, rng.uniform(10, 18), rng.choice(colours))
            elif rng.random() < 0.5:
                r = rng.uniform(3, 6)
                draw.ellipse([x - r, y - r, x + r, y + r], fill=rng.choice(colours))
            else:
                draw.rectangle([x, y, x + rng.uniform(5, 9), y + rng.uniform(10, 16)],
                               fill=rng.choice(colours))


def _heart(draw, cx, cy, size, colour):
    r = size / 4
    draw.ellipse([cx - 2 * r, cy - r, cx, cy + r], fill=colour)
    draw.ellipse([cx, cy - r, cx + 2 * r, cy + r], fill=colour)
    draw.polygon([(cx - 2 * r + 0.6, cy + 0.3 * r), (cx + 2 * r - 0.6, cy + 0.3 * r), (cx, cy + 2.4 * r)],
                 fill=colour)


def build_strip(photos, when=None, theme_id=DEFAULT_THEME, caption=None, theme=None):
    """Combine three finished photos into a 2 x 6 inch strip in a design.

    `caption` replaces the event title (STRIP_TITLE) when given. Pass a
    `theme` dict instead of an id to try out a design (admin page preview).
    """
    theme = theme or get_theme(theme_id) or BUILTIN_THEMES[DEFAULT_THEME]
    when = when or datetime.now()
    strip = Image.new("RGBA", STRIP_SIZE, theme["background"])
    draw = ImageDraw.Draw(strip)
    _draw_decor(strip, draw, theme)
    _add_art(strip, theme.get("art_back"))

    for index, photo in enumerate(photos):
        box = _photo_box(index)
        strip.paste(photo.convert("RGB").resize(STRIP_PHOTO_SIZE, Image.LANCZOS), box[:2])
    _add_art(strip, theme.get("art_front"))
    draw = ImageDraw.Draw(strip)

    # Footer: title and date, centred in the space under the photos.
    centre_x = STRIP_SIZE[0] // 2
    footer_height = STRIP_SIZE[1] - STRIP_FOOTER_TOP
    title = clean_caption(caption) or config.STRIP_TITLE
    date_text = when.strftime(config.STRIP_DATE_FORMAT) if config.STRIP_DATE_FORMAT else ""
    if theme["upper"]:
        title = title.upper()

    if title:
        file, variation = FONTS[theme["title_font"]][:2]
        spec = (file, variation, theme["title_size"], theme["title_spacing"])
        font = _fit_font(title, spec, STRIP_SIZE[0] - 2 * STRIP_MARGIN - 20)
        title_y = STRIP_FOOTER_TOP + footer_height * (0.40 if date_text else 0.48)
        _draw_centered(draw, (centre_x, title_y), title, font, theme["text"], theme["title_spacing"])
    if date_text:
        file, variation = FONTS[theme["date_font"]][:2]
        date_y = STRIP_FOOTER_TOP + footer_height * (0.78 if title else 0.5)
        colour = theme["accent"] if theme["date_color"] == "accent" else theme["text"]
        _draw_centered(draw, (centre_x, date_y), date_text.upper(),
                       _font(file, theme["date_size"], variation), colour, theme["date_spacing"])
    return strip.convert("RGB")


def theme_preview(theme, photos=None, width=240):
    """A small JPEG of a strip in `theme`, for the admin page."""
    if not photos:
        photos = [Image.new("RGB", STRIP_PHOTO_SIZE, shade) for shade in ("#9aa3ad", "#b7aea1", "#a3b1a0")]
    strip = build_strip(photos, theme=theme)
    return to_jpeg(strip.resize((width, width * 3), Image.LANCZOS), 85)


def build_print_sheet(strip):
    """Two copies of the strip side by side on a 4 x 6 inch page."""
    sheet = Image.new("RGB", PRINT_SIZE, "#ffffff")
    sheet.paste(strip, (0, 0))
    sheet.paste(strip, (STRIP_SIZE[0], 0))
    # Small grey marks at the top and bottom edge showing where to cut.
    draw = ImageDraw.Draw(sheet)
    middle = STRIP_SIZE[0]
    draw.line([(middle, 0), (middle, 14)], fill="#bbbbbb", width=2)
    draw.line([(middle, PRINT_SIZE[1] - 14), (middle, PRINT_SIZE[1])], fill="#bbbbbb", width=2)
    return sheet


def save_jpeg(img, path, quality=95):
    """Save as a high-quality JPEG tagged as 300 DPI (so it prints at the right size)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, dpi=DPI)
