"""
ai_prompts.py: prompts for making frames and strip designs with an AI model.

On the admin page you describe what you want, copy the prompt this file
builds, and paste it into a chat with any AI model (Claude, ChatGPT,
Gemini...). Then you paste the reply back:

  * Frames come back as SVG code. The admin page's browser draws it into a
    1080 x 960 transparent PNG and uploads it like any other frame.
  * Strip designs come back as JSON settings (colours, fonts...) plus SVG
    artwork. parse_designs() below reads and checks the settings and hands
    the artwork back to the browser, which draws it into transparent PNGs.
    Then they open in the design editor (or are all saved, if several).
"""

import json
import re

import config
import imaging

FRAME_PROMPT = """You are designing a decorative frame for a photo booth. Reply with SVG code only.

What the frame is for: {description}

How it will be used:
- It is laid on top of every photo a guest takes. Wherever the SVG is transparent, the photo shows through.
- Photos are 1080 px wide and 960 px tall. Start with exactly:
  <svg xmlns="http://www.w3.org/2000/svg" width="1080" height="960" viewBox="0 0 1080 960">
- Guests' faces are in the middle, so keep the centre clear: draw nothing inside the rectangle
  from x=170 to x=910 and y=150 to y=810. Put the decoration around the edges and in the corners.
- No background: never fill the whole canvas.

Technical rules (a web browser draws it, with no internet access):
- Plain SVG only: shapes, paths, groups, gradients, patterns, clip paths, opacity and text.
- No <image>, <foreignObject> or <script>, no links to other files, no CSS @import and no web fonts.
- For text, use font-family="Helvetica, Arial, sans-serif" or font-family="Georgia, serif".
  Keep text short and inside the edge area.
- Bold, clean shapes look best. Lines thinner than 3 px can disappear in print.

Reply with the complete SVG, from <svg to </svg>, and nothing else."""

DESIGN_PROMPT = """You are designing a photo strip for a photo booth: the strip of three photos guests take home.
Be creative and give it a strong, original look that suits the event.

What the design is for: {description}

THE LAYOUT (fixed; everything in pixels, 600 wide x 1800 tall, printed at 2 x 6 inches)
- Photo 1: x={px} to {px2}, y={y1} to {y1b}
- Photo 2: x={px} to {px2}, y={y2} to {y2b}
- Photo 3: x={px} to {px2}, y={y3} to {y3b}
- Footer: y={footer} to 1800. The booth writes two lines of text here, centred:
  a title around y={title_y} (the event name, for example "{title}", or a caption the guest types,
  up to 28 characters) and the date around y={date_y}.

YOUR REPLY has two parts.

Part 1: the settings, as a JSON object with exactly these fields:
- "name": a short name for the design, up to 30 characters
- "background": the base colour of the strip, like "#ffffff" (your artwork goes on top of it)
- "text": colour of the title
- "accent": colour of the date
- "title_font": one of {fonts}
- "title_size": 24 to 160 (70 to 100 looks best; long titles shrink to fit)
- "title_spacing": extra space between the title's letters, 0 to 30
- "upper": true to write the title in capitals, otherwise false
- "date_font": one of the same fonts
- "date_size": 12 to 70 (24 to 34 looks best)
- "date_spacing": extra space between the date's letters, 0 to 30
- "date_color": "accent" or "text" (which colour the date uses)
- "decor": "none" (you draw your own artwork instead)

Part 2: the artwork, as SVG code. This is where the design comes alive: patterns, textures,
gradients, illustrations, borders, frames around the photos, stickers, doodles, shapes, scenes,
anything that fits the theme. Use the whole strip: the edges around the photos, the gaps between
them and the footer.
- Start with exactly: <svg xmlns="http://www.w3.org/2000/svg" width="600" height="1800" viewBox="0 0 600 1800">
- It is drawn on top of the background colour and BEHIND the photos, so anything inside the photo
  areas is hidden.
- Optionally add a second SVG, starting <svg data-layer="front" xmlns="http://www.w3.org/2000/svg"
  width="600" height="1800" viewBox="0 0 600 1800">, drawn ON TOP of the photos. Use it for things
  that overlap the photos' edges and corners (stickers, tape, a frame around each photo). Never cover
  more than the outer 60 px of a photo, because guests' faces are in the middle.
- Don't write the title or the date yourself; the booth adds them on top. Keep the area around them
  calm enough that they're easy to read in your "text" and "accent" colours, between x=50 and x=550.
- Plain SVG only: shapes, paths, gradients, patterns, clip paths, masks, opacity and text.
  No <image>, <foreignObject> or <script>, no links to other files, no web fonts. For any extra text
  use font-family="Helvetica, Arial, sans-serif" or font-family="Georgia, serif".
- It is printed, so avoid lines thinner than 2 px and very pale details on white.

Format your reply as the JSON in one code block, then each SVG in its own code block. If I asked for
more than one design, repeat that for each design: its JSON, then its SVG(s), then the next design.
Reply with only the code blocks and nothing else."""


def _describe(description, fallback):
    return " ".join(str(description or "").split())[:500] or fallback


def frame_prompt(description):
    return FRAME_PROMPT.format(description=_describe(description, "a fun, general party photo booth"))


def design_prompt(description):
    fonts = ", ".join(f'"{key}" ({spec[4]})' for key, spec in imaging.FONTS.items())
    boxes = [imaging._photo_box(i) for i in range(3)]
    footer = imaging.STRIP_FOOTER_TOP
    footer_height = imaging.STRIP_SIZE[1] - footer
    return DESIGN_PROMPT.format(
        description=_describe(description, "a fun, general party photo booth"),
        title=config.STRIP_TITLE or "Our Party", fonts=fonts,
        px=boxes[0][0], px2=boxes[0][2],
        y1=boxes[0][1], y1b=boxes[0][3], y2=boxes[1][1], y2b=boxes[1][3], y3=boxes[2][1], y3b=boxes[2][3],
        footer=footer, title_y=round(footer + footer_height * 0.40), date_y=round(footer + footer_height * 0.78))


SVG_PATTERN = re.compile(r"<svg[\s>].*?</svg\s*>", re.IGNORECASE | re.DOTALL)
FRONT_LAYER = re.compile(r"""data-layer\s*=\s*["']?front""", re.IGNORECASE)


def _match(value, options, default):
    """A font or decoration by its key or its label ("Bebas Neue" -> "bebas"); unknown -> default."""
    text = str(value or "").strip().lower()
    if text in options:
        return text
    for key, label in options.items():
        if text and (text == label.lower() or text == label.lower().split(" (")[0]):
            return key
    return default


def _json_values(text):
    """Every JSON object or list in the text, as [(position, value)]."""
    decoder = json.JSONDecoder()
    values, i = [], 0
    while i < len(text):
        if text[i] in "[{":
            try:
                value, end = decoder.raw_decode(text, i)
                values.append((i, value))
                i = end
                continue
            except ValueError:
                pass
        i += 1
    return values


def parse_designs(text):
    """Read strip designs from an AI reply.

    Returns a list of checked designs. Each also has "svg_back" and
    "svg_front": the artwork's SVG code (or ""), for the browser to draw.
    Raises ValueError with a friendly message when it can't.
    """
    text = str(text or "")
    # Take the SVG artwork out first, remembering where each one was, so the
    # JSON search doesn't trip over it.
    svgs = [(m.start(), m.group(0)) for m in SVG_PATTERN.finditer(text)]
    settings_text = SVG_PATTERN.sub(lambda m: " " * len(m.group(0)), text)

    found = []          # [(position, design dict)]
    for position, value in _json_values(settings_text):
        if isinstance(value, dict) and isinstance(value.get("designs"), list):
            value = value["designs"]
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and ("background" in item or "title_font" in item or "name" in item):
                found.append((position, item))
    if not found:
        if svgs:
            raise ValueError("That reply only has artwork, without the design's settings (the part in { }). "
                             "Ask the AI to include both.")
        raise ValueError("Couldn't find any design code in that reply. Paste the AI's whole answer: "
                         "it should contain something starting with { and ending with }.")
    if len(found) > 12:
        raise ValueError("That's more than 12 designs. Ask for fewer at a time.")

    # Each SVG belongs to the design written just before it.
    art = [{"svg_back": "", "svg_front": ""} for _ in found]
    for position, svg in svgs:
        owner = max([i for i, (p, _) in enumerate(found) if p < position] or [0])
        layer = "svg_front" if FRONT_LAYER.search(svg[:svg.find(">") + 1]) else "svg_back"
        if not art[owner][layer]:
            art[owner][layer] = svg
    # Artwork can also come inside the JSON.
    for index, (_, item) in enumerate(found):
        for key, layer in (("art", "svg_back"), ("art_back", "svg_back"), ("svg", "svg_back"),
                           ("art_front", "svg_front"), ("svg_front", "svg_front")):
            value = item.get(key)
            if isinstance(value, str) and "<svg" in value and not art[index][layer]:
                art[index][layer] = value

    fonts = {key: spec[4] for key, spec in imaging.FONTS.items()}
    base = imaging.BUILTIN_THEMES["classic"]
    designs = []
    for number, (_, item) in enumerate(found, 1):
        design = {**base, "name": "AI design", **item, "art_back": "", "art_front": ""}
        for key in ("title_font", "date_font"):
            design[key] = _match(design[key], fonts, base[key])
        design["decor"] = _match(design["decor"], imaging.DECORATIONS, "none")
        if design["date_color"] not in ("accent", "text"):
            design["date_color"] = "accent"
        if isinstance(design["upper"], str):
            design["upper"] = design["upper"].strip().lower() in ("true", "yes", "1")
        try:
            checked = imaging.validate_theme(design)
        except ValueError as error:
            prefix = f"Design {number}: " if len(found) > 1 else ""
            raise ValueError(f"{prefix}{error} Ask the AI to fix it and paste the new reply.")
        designs.append({**checked, **art[number - 1]})
    return designs
