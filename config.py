"""
config.py: every setting for the photo booth, in one place.

Settings are read from the `.env` file in the project folder (on the Pi:
`./deploy.sh config` from the Mac opens it for editing). Anything missing
from `.env` falls back to the default given below.

Use the settings elsewhere like this:

    import config
    print(config.COUNTDOWN_SECONDS)

Secrets (the Gmail app password) have no default here. They must only ever
live in `.env`, which stays on the Pi: deploy.sh never copies or overwrites it.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# The folder this file lives in, i.e. the project folder.
BASE_DIR = Path(__file__).resolve().parent

# Load .env into the environment. Existing environment variables win, which
# lets you override a setting for a single run, e.g.  PORT=9000 python app.py
load_dotenv(BASE_DIR / ".env")


# --- Helpers that read one setting and convert it to the right type ----------
# A bad value (e.g. PORT=abc) stops the app at startup with a clear message,
# instead of causing a confusing error later.

def _get(name, default):
    """Return the raw text of a setting, or `default` if it's missing/blank."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _str(name, default=""):
    return str(_get(name, default))


def _int(name, default, minimum=None, maximum=None):
    value = _get(name, default)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"Config error: {name} must be a whole number (got {value!r}).")
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _float(name, default, minimum=0.0):
    value = _get(name, default)
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        raise SystemExit(f"Config error: {name} must be a number (got {value!r}).")


def _bool(name, default):
    value = _get(name, None)
    if value is None:
        return default
    if value.lower() in ("1", "true", "yes", "on"):
        return True
    if value.lower() in ("0", "false", "no", "off"):
        return False
    raise SystemExit(f"Config error: {name} must be true or false (got {value!r}).")


def _path(name, default):
    """A folder/file path. Relative paths are relative to the project folder."""
    path = Path(_str(name, default)).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


# --- Web server ---------------------------------------------------------------
HOST = _str("HOST", "0.0.0.0")
PORT = _int("PORT", 8000)              # the booth (iPad)
ADMIN_PORT = _int("ADMIN_PORT", 8001)  # the admin page
ADMIN_PASSWORD = _str("ADMIN_PASSWORD", "")   # secret: .env only
LOG_LEVEL = _str("LOG_LEVEL", "INFO").upper()

# --- Camera -------------------------------------------------------------------
CAMERA_DEVICE = _str("CAMERA_DEVICE", "auto")
CAMERA_WIDTH = _int("CAMERA_WIDTH", 1920)
CAMERA_HEIGHT = _int("CAMERA_HEIGHT", 1080)
CAMERA_FPS = _int("CAMERA_FPS", 24)
# Flip left-to-right like a mirror. Off = text reads the right way round.
# Applies to the preview AND the photos, so they always match.
MIRROR = _bool("MIRROR", False)

# --- Live preview -------------------------------------------------------------
PREVIEW_WIDTH = _int("PREVIEW_WIDTH", 800, minimum=200)
PREVIEW_FPS = _int("PREVIEW_FPS", 20, minimum=1, maximum=30)
PREVIEW_JPEG_QUALITY = _int("PREVIEW_JPEG_QUALITY", 75, minimum=1, maximum=100)

# --- Timers (seconds) -----------------------------------------------------------
COUNTDOWN_SECONDS = _int("COUNTDOWN_SECONDS", 3, minimum=1, maximum=10)
# Time to pick a filter and frame; then the photos start anyway.
STYLE_TIMEOUT_SECONDS = _int("STYLE_TIMEOUT_SECONDS", 10, minimum=3)
# Countdown before the 2nd and 3rd photos, i.e. the time between photos.
BETWEEN_SHOTS_SECONDS = _int("BETWEEN_SHOTS_SECONDS", 3, minimum=1, maximum=10)
REVIEW_TIMEOUT_SECONDS = _int("REVIEW_TIMEOUT_SECONDS", 60, minimum=10)
EMAIL_TIMEOUT_SECONDS = _int("EMAIL_TIMEOUT_SECONDS", 90, minimum=15)

# --- Sound --------------------------------------------------------------------
SOUND_ENABLED = _bool("SOUND_ENABLED", True)

# --- Photo strip --------------------------------------------------------------
STRIP_TITLE = _str("STRIP_TITLE", "Photo Booth")
# Date line under the title, in Python strftime format. Blank = no date.
STRIP_DATE_FORMAT = os.getenv("STRIP_DATE_FORMAT", "%d %B %Y").strip()
# Starting design for every strip (guests can change it): classic, midnight,
# film, blush, elegant, retro, party, doodle, or one made on the admin page.
STRIP_THEME = _str("STRIP_THEME", "classic")
# Let guests type their own text instead of STRIP_TITLE.
ALLOW_CUSTOM_CAPTION = _bool("ALLOW_CUSTOM_CAPTION", True)

# --- Printing -----------------------------------------------------------------
PRINT_ENABLED = _bool("PRINT_ENABLED", False)
PRINTER_NAME = _str("PRINTER_NAME", "")             # blank = CUPS default printer
PRINT_OPTIONS = _str("PRINT_OPTIONS", "-o media=4x6 -o fit-to-page")
PRINT_MAX_PER_SESSION = _int("PRINT_MAX_PER_SESSION", 2, minimum=1)

# --- Email (Gmail with an app password) ---------------------------------------
EMAIL_ENABLED = _bool("EMAIL_ENABLED", True)
GMAIL_ADDRESS = _str("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = _str("GMAIL_APP_PASSWORD", "")      # secret: .env only
EMAIL_TEMPLATE = _str("EMAIL_TEMPLATE", "minimal")   # see emails.py
EMAIL_FROM_NAME = _str("EMAIL_FROM_NAME", "Photo Booth")
EMAIL_SUBJECT = _str("EMAIL_SUBJECT", "Your photos from {title}")
EMAIL_MESSAGE = _str("EMAIL_MESSAGE", "Thank you for visiting the TBS photo booth!")

# --- Storage ------------------------------------------------------------------
DATA_DIR = _path("DATA_DIR", "data")
