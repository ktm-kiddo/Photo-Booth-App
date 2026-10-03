"""
booth.py: the photo booth's brain.

One *session* = one group of guests = three photos and one strip. The web
page drives it step by step:

    new session (with the chosen filter and frame)
      -> capture photo 1, 2, 3     (each with its own filter and frame,
                                    each one saved right away)
      -> finish                    (build the strip)
      -> restyle any photo         (new filter/frame, strip rebuilt from the original)
      -> redesign the strip        (colours/fonts design, own caption)
      -> email / print / retake / done

Every file is saved on the Pi with a timestamped name:

    data/originals/20261002_181500_1.jpg   untouched camera frame (full size)
    data/photos/20261002_181500_1.jpg      finished photo (cropped, filter, frame)
    data/strips/20261002_181500_strip.jpg  the 2x6 strip
    data/prints/20261002_181500_print.jpg  the 4x6 print sheet (when printed)

Emails go through the job queue (jobs.py), so the screen never waits for
the internet, and emails sent while offline go out once it's back.
"""

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime

import cv2
from PIL import Image

import config
import emails
import filters
import imaging
import mailer
import printer
from jobs import JobQueue

log = logging.getLogger(__name__)

PHOTOS_PER_STRIP = 3
MAX_SESSIONS_IN_MEMORY = 30
THUMB_WIDTH = 600
LOOK_WIDTH = 240             # size of the "what would this filter look like" previews

# A sensible email check: name@domain.tld, no spaces, at most 254 characters.
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"\.[A-Za-z]{2,}$"
)


def is_valid_email(address):
    return (len(address) <= 254 and ".." not in address
            and EMAIL_PATTERN.match(address) is not None)


class BoothError(Exception):
    """A problem to show to the guest. `message` is written for them."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class Session:
    def __init__(self, session_id, filter_id, overlay_id):
        self.id = session_id
        self.filter_id = filter_id
        self.overlay_id = overlay_id
        self.created = datetime.now()
        self.photos = []          # finished photos (Pillow images) until the strip is built
        self.original_paths = []
        self.photo_paths = []
        self.styles = []          # per photo: {"filter": id, "overlay": id}
        self.thumbs = []          # small JPEGs of each finished photo
        self.look_bases = []      # small unfiltered copies, for the restyle previews
        self.theme = imaging.clean_theme(config.STRIP_THEME)   # strip design
        self.caption = ""         # guest's own text; "" = the event title
        self.strip_path = None
        self.version = 0          # goes up each time the strip is rebuilt
        self.emails = []          # [{"id", "to", "state"}]
        self.prints = 0
        self.print_state = None   # None, "printing", "done", "failed"
        self.print_message = None
        self.lock = threading.Lock()

    def public(self, features):
        """What the web page is allowed to know about this session."""
        return {
            "id": self.id,
            "photos": len(self.photo_paths),
            "styles": self.styles,
            "theme": self.theme,
            "caption": self.caption,
            "version": self.version,
            # ?v= makes the browser fetch the new strip after a restyle
            "strip_url": (f"/api/sessions/{self.id}/strip.jpg?v={self.version}"
                          if self.strip_path else None),
            "emails": self.emails,
            "print": {
                "state": self.print_state,
                "message": self.print_message,
                "remaining": max(0, config.PRINT_MAX_PER_SESSION - self.prints),
            },
            "features": features,
        }


class Booth:
    def __init__(self, camera):
        self.camera = camera
        data = config.DATA_DIR
        self.dirs = {name: data / name for name in ("originals", "photos", "strips", "prints")}
        for folder in self.dirs.values():
            folder.mkdir(parents=True, exist_ok=True)

        self.jobs = JobQueue(data / "queue", on_update=self._job_update)
        self.jobs.register("email", self._job_email)

        self.sessions = OrderedDict()
        self._lock = threading.Lock()
        self.preview_filter = "none"       # filter shown on the live preview
        self.content_version = 0           # bumped when frames change, so the iPad reloads
        self._thumb_cache = {"time": 0, "images": {}}

    def start(self):
        self.jobs.start()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def features(self):
        """Which buttons the review screen should show."""
        return {
            "email": config.EMAIL_ENABLED and mailer.is_configured(),
            "print": config.PRINT_ENABLED,
        }

    def status(self):
        return {
            "camera": self.camera.status(),
            "features": self.features(),
            "queue": {"pending": self.jobs.pending_count(), "online": self.jobs.online},
            "content_version": self.content_version,
        }

    # ------------------------------------------------------------------
    # Live preview helpers
    # ------------------------------------------------------------------

    def set_preview_filter(self, filter_id):
        self.preview_filter = filter_id if filter_id in filters.FILTERS else "none"

    def filter_thumbnail(self, filter_id):
        """Small JPEG of the current camera view with a filter, for the filter buttons.

        All thumbnails are made from the same frame and cached for 2 seconds,
        so the buttons stay in sync and the Pi isn't doing extra work.
        """
        cache = self._thumb_cache
        with self._lock:
            if time.monotonic() - cache["time"] > 2.0:
                frame = self.camera.latest_frame()
                if frame is None:
                    return None
                base = imaging.frame_to_image(frame, width=240)
                cache["images"] = {fid: imaging.to_jpeg(filters.apply(fid, base), 80)
                                   for fid in filters.FILTERS}
                cache["time"] = time.monotonic()
            return cache["images"].get(filter_id)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_look(filter_id, overlay_id):
        """Replace unknown filter ids with "none", and unknown frames with the first frame."""
        if filter_id not in filters.FILTERS:
            filter_id = "none"
        if overlay_id != imaging.NO_FRAME and imaging.overlay_path(overlay_id) is None:
            overlay_id = imaging.default_overlay()
        return filter_id, overlay_id

    def new_session(self, filter_id, overlay_id):
        filter_id, overlay_id = self._clean_look(filter_id, overlay_id)

        with self._lock:
            base = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id, n = base, 2
            while session_id in self.sessions:
                session_id, n = f"{base}_{n}", n + 1
            session = Session(session_id, filter_id, overlay_id)
            self.sessions[session_id] = session
            while len(self.sessions) > MAX_SESSIONS_IN_MEMORY:
                self.sessions.popitem(last=False)     # forget the oldest
        log.info("Session %s started (filter=%s, frame=%s)", session_id, filter_id, overlay_id)
        return session

    def get(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            raise BoothError("This photo session has expired.", 404)
        return session

    def capture(self, session_id, filter_id=None, overlay_id=None):
        """Take the next photo of the session, in the given look. Returns its number (1-3)."""
        session = self.get(session_id)
        if filter_id is not None or overlay_id is not None:
            filter_id, overlay_id = self._clean_look(filter_id, overlay_id)
        else:
            filter_id, overlay_id = session.filter_id, session.overlay_id
        frame = self.camera.latest_frame()
        if frame is None:
            raise BoothError("The camera isn't ready. Please try again.", 503)

        with session.lock:
            index = len(session.photo_paths) + 1
            if index > PHOTOS_PER_STRIP:
                raise BoothError("All three photos have already been taken.")
            name = f"{session.id}_{index}.jpg"

            # 1. The untouched original, straight from the camera. Restyling
            #    later always starts again from this.
            original = self.dirs["originals"] / name
            cv2.imwrite(str(original), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            # 2. The finished photo: cropped, filtered, framed.
            photo = imaging.finish_photo(frame, filter_id, overlay_id)
            imaging.save_jpeg(photo, self.dirs["photos"] / name, quality=92)

            session.photos.append(photo)
            session.original_paths.append(original)
            session.photo_paths.append(self.dirs["photos"] / name)
            session.styles.append({"filter": filter_id, "overlay": overlay_id})
            session.thumbs.append(_thumb_jpeg(photo))
            session.look_bases.append(imaging.frame_to_image(frame, width=LOOK_WIDTH))
        log.info("Session %s: photo %d taken", session.id, index)
        return index

    def thumbnail(self, session_id, index):
        session = self.get(session_id)
        if not 1 <= index <= len(session.thumbs):
            raise BoothError("No such photo.", 404)
        return session.thumbs[index - 1]

    def finish(self, session_id):
        """Build the strip from the three photos."""
        session = self.get(session_id)
        with session.lock:
            if session.strip_path is None:
                if len(session.photos) < PHOTOS_PER_STRIP:
                    raise BoothError("We need three photos for a strip.")
                session.strip_path = self.dirs["strips"] / f"{session.id}_strip.jpg"
                self._save_strip(session, session.photos)
                session.photos = []        # free memory: everything is on disk now
                log.info("Session %s: strip saved to %s", session.id, session.strip_path)
        return session

    # ------------------------------------------------------------------
    # Restyling a photo after the shoot
    # ------------------------------------------------------------------

    def look_preview(self, session_id, index, filter_id):
        """Small JPEG of photo `index` with a filter, for the restyle buttons."""
        session = self.get(session_id)
        if not 1 <= index <= len(session.look_bases):
            raise BoothError("No such photo.", 404)
        return imaging.to_jpeg(filters.apply(filter_id, session.look_bases[index - 1]), 80)

    def restyle(self, session_id, index, filter_id, overlay_id, apply_to_all=False):
        """Give one photo (or all of them) a new filter and frame, then rebuild the strip.

        Each photo is re-made from its untouched original, so restyling never
        loses quality, however many times it's done.
        """
        session = self.get(session_id)
        filter_id, overlay_id = self._clean_look(filter_id, overlay_id)
        look = {"filter": filter_id, "overlay": overlay_id}

        with session.lock:
            if session.strip_path is None:
                raise BoothError("The strip isn't ready yet.")
            if not 1 <= index <= len(session.photo_paths):
                raise BoothError("No such photo.", 404)

            targets = range(len(session.photo_paths)) if apply_to_all else [index - 1]
            changed = False
            for i in targets:
                if session.styles[i] == look:
                    continue
                frame = cv2.imread(str(session.original_paths[i]))
                if frame is None:
                    raise BoothError("Sorry, that photo can't be changed.", 500)
                photo = imaging.finish_photo(frame, filter_id, overlay_id)
                imaging.save_jpeg(photo, session.photo_paths[i], quality=92)
                session.styles[i] = dict(look)
                session.thumbs[i] = _thumb_jpeg(photo)
                changed = True

            if changed:
                self._save_strip(session)
                log.info("Session %s: photo %s restyled (%s, %s)", session.id,
                         "all" if apply_to_all else index, filter_id, overlay_id)
        return session

    def discard(self, session_id):
        """Retake: forget this session (its files stay on the Pi)."""
        if self.sessions.pop(session_id, None):
            log.info("Session %s discarded (retake)", session_id)

    def redesign(self, session_id, theme_id, caption):
        """Change the strip's design and/or caption, then rebuild it."""
        session = self.get(session_id)
        theme_id = imaging.clean_theme(theme_id)
        caption = imaging.clean_caption(caption) if config.ALLOW_CUSTOM_CAPTION else ""
        with session.lock:
            if session.strip_path is None:
                raise BoothError("The strip isn't ready yet.")
            if (theme_id, caption) != (session.theme, session.caption):
                session.theme, session.caption = theme_id, caption
                self._save_strip(session)
                log.info("Session %s: strip design %s, caption %r", session.id, theme_id, caption)
        return session

    def _save_strip(self, session, photos=None):
        """(Re)build the strip from the finished photos. Caller holds session.lock."""
        if photos is None:
            photos = []
            for path in session.photo_paths:
                with Image.open(path) as img:
                    photos.append(img.convert("RGB"))
        strip = imaging.build_strip(photos, session.created, session.theme, session.caption)
        imaging.save_jpeg(strip, session.strip_path)
        session.version += 1

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    def email(self, session_id, address):
        session = self.get(session_id)
        address = (address or "").strip()
        if not self.features()["email"]:
            raise BoothError("Email isn't available right now.")
        if not is_valid_email(address):
            raise BoothError("That email address doesn't look quite right. Please check it.")
        if session.strip_path is None:
            raise BoothError("The strip isn't ready yet.")

        with session.lock:
            # Emails go out in the background, one after another, so guests
            # can add several addresses and leave straight away.
            if any(e["to"].lower() == address.lower() and e["state"] != "failed"
                   for e in session.emails):
                raise BoothError("We've already sent the photos to that address.")
            attachments = [str(session.strip_path)] + [str(p) for p in session.photo_paths]
            job = self.jobs.add("email", session.id, {
                "to": address,
                "attachments": attachments,
                "date": _nice_date(session.created),
            })
            session.emails.append({"id": job["id"], "to": address, "state": "sending"})
        log.info("Session %s: email queued to %s", session.id, address)
        return session

    # ------------------------------------------------------------------
    # Printing
    # ------------------------------------------------------------------

    def print_strip(self, session_id):
        session = self.get(session_id)
        if not config.PRINT_ENABLED:
            raise BoothError("Printing is switched off.")
        with session.lock:
            if session.strip_path is None:
                raise BoothError("The strip isn't ready yet.")
            if session.print_state == "printing":
                raise BoothError("Already printing.")
            if session.prints >= config.PRINT_MAX_PER_SESSION:
                raise BoothError("That's all the prints for this strip.")
            session.prints += 1
            session.print_state = "printing"
            session.print_message = None

        def run():
            # Rebuilt every time, in case a photo was restyled since the last print.
            sheet_path = self.dirs["prints"] / f"{session.id}_print.jpg"
            with session.lock, Image.open(session.strip_path) as strip:
                sheet = imaging.build_print_sheet(strip.convert("RGB"))
            imaging.save_jpeg(sheet, sheet_path)
            ok, message = printer.print_file(sheet_path, config.PRINTER_NAME, config.PRINT_OPTIONS)
            with session.lock:
                session.print_state = "done" if ok else "failed"
                session.print_message = message
                if not ok:
                    session.prints -= 1      # a failed print doesn't count

        threading.Thread(target=run, name=f"print-{session.id}", daemon=True).start()
        return session

    # ------------------------------------------------------------------
    # Job handlers (run in the job queue's background thread)
    # ------------------------------------------------------------------

    def _job_email(self, job):
        payload = job["payload"]
        title = config.STRIP_TITLE or "Photo Booth"
        date = payload.get("date", "")
        text, body = emails.render(config.EMAIL_TEMPLATE, title, config.EMAIL_MESSAGE,
                                   date, config.EMAIL_FROM_NAME)
        attachments = payload["attachments"]
        mailer.send(payload["to"], emails.subject(config.EMAIL_SUBJECT, title, date), text, body,
                    attachments, inline={emails.STRIP_CID: attachments[0]})
        _log_email(config.DATA_DIR / "email_log.jsonl", job)
        return {}

    def _job_update(self, job, state, info):
        """Called by the job queue: reflect job progress on the session."""
        session = self.sessions.get(job["session"])
        if session is None:
            return       # e.g. a job left over from before a restart
        info = info or {}
        with session.lock:
            if job["type"] != "email":
                return
            for entry in session.emails:
                if entry["id"] != job["id"]:
                    continue
                if state == "running":
                    entry["state"] = "sending"
                elif state == "retrying":
                    # "queued" = no internet; "delayed" = some other hiccup
                    # (e.g. Gmail busy, or the app password needs fixing).
                    entry["state"] = "queued" if info.get("offline") else "delayed"
                else:
                    entry["state"] = "sent" if state == "done" else "failed"


def _thumb_jpeg(photo):
    """Small JPEG of a finished photo for the iPad."""
    return imaging.to_jpeg(photo.resize((THUMB_WIDTH, round(THUMB_WIDTH / imaging.PHOTO_ASPECT))), 85)


def _nice_date(when):
    """e.g. "2 October 2026" (no leading zero)."""
    return f"{when.day} {when.strftime('%B %Y')}"


def _log_email(path, job):
    """Remember every sent email (shown on the admin page)."""
    entry = {"time": datetime.now().isoformat(timespec="seconds"),
             "to": job["payload"]["to"], "session": job["session"]}
    with open(path, "a") as log_file:
        log_file.write(json.dumps(entry) + "\n")
