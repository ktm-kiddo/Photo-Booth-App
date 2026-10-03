"""
app.py: the photo booth web server.

On the Pi this runs automatically at power-on (see deploy/photobooth.service).
Open  http://photobooth.local:8000  in Safari on the iPad.

Pages and endpoints
-------------------
    /                                  the booth (HTML/CSS/JS from templates/ and static/)
    /stream.mjpg                       live camera preview (MJPEG), cut to the photo shape
    /stream.mjpg?shape=full            the camera's whole picture (start screen)
    /snapshot.jpg                      one full-size camera frame, for checking quality
    /overlays/<id>.png                 frame overlay images
    /strip-art/<id>.png                artwork of a strip design (for its button)

    GET  /api/status                   camera, email and queue status (polled every 2 s)
    POST /api/preview                  {"filter": id}: filter for the live preview
    GET  /api/thumbs/<filter>.jpg      small live picture with a filter, for the buttons
    GET  /api/looks                    current filters, frames and strip designs

    POST /api/sessions                 {"filter", "overlay"}: start a session
    GET  /api/sessions/<id>            session status (emails, printing)
    POST /api/sessions/<id>/capture    {"filter", "overlay"}: take the next photo in that look
    GET  /api/sessions/<id>/photos/<n>.jpg
    GET  /api/sessions/<id>/photos/<n>/looks/<filter>.jpg   restyle previews
    POST /api/sessions/<id>/photos/<n>/style  {"filter", "overlay", "all"}: restyle
    POST /api/sessions/<id>/design     {"theme", "caption"}: strip design
    POST /api/sessions/<id>/finish     build the strip
    GET  /api/sessions/<id>/strip.jpg
    POST /api/sessions/<id>/email      {"address"}
    POST /api/sessions/<id>/print
    POST /api/sessions/<id>/discard    retake: drop this session
"""

import logging
import time

import cv2
import numpy as np
from flask import Flask, Response, abort, jsonify, render_template, request, send_file, send_from_directory

import threading

from werkzeug.serving import make_server

import config
import filters
import health
import imaging
from admin import LogBuffer, create_admin_app
from booth import Booth, BoothError
from camera import Camera

# --- Logging -----------------------------------------------------------------
# Log lines go to the terminal, or the system journal when run by systemd
# (view them from the Mac with ./deploy.sh logs).
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Flask's web server logs every request, and the iPad asks for something
# several times a second, so only show its warnings and errors.
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = logging.getLogger("photobooth")
# Keep recent log lines in memory too, for the admin page's Logs view.
log_buffer = LogBuffer()
logging.getLogger().addHandler(log_buffer)

# --- App, camera and booth ---------------------------------------------------
app = Flask(__name__)
# Make browsers check for new CSS/JS each time, so updates show up at once.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# A new ID every time the app starts. The iPad notices when it changes (after
# a deploy or restart) and reloads the page to pick up the new code.
SERVER_ID = str(time.time())

camera = Camera(
    device=config.CAMERA_DEVICE,
    width=config.CAMERA_WIDTH,
    height=config.CAMERA_HEIGHT,
    fps=config.CAMERA_FPS,
)
booth = Booth(camera)


# --- Live preview stream -----------------------------------------------------

def _offline_frame():
    """A plain dark frame, sent once a second while the camera is offline.

    The page shows a friendly message on top. Sending something keeps the
    stream connection alive, so the picture returns by itself on reconnect.
    """
    height = round(config.PREVIEW_WIDTH / imaging.PHOTO_ASPECT)
    dark = np.full((height, config.PREVIEW_WIDTH, 3), 24, dtype=np.uint8)
    return cv2.imencode(".jpg", dark)[1].tobytes()


OFFLINE_JPEG = _offline_frame()


def mjpeg_stream(crop=True):
    """Yield an endless series of JPEG frames in MJPEG format.

    MJPEG is simply JPEG images sent one after another over a single HTTP
    response. Safari shows it in an ordinary <img> tag. Each frame is cut to
    the 9:8 photo shape and given the currently selected filter, so the
    preview is exactly what the photo will look like.
    """
    min_interval = 1.0 / config.PREVIEW_FPS
    last_id = None
    next_send = 0.0

    while True:
        # Limit the frame rate to PREVIEW_FPS to save CPU and Wi-Fi.
        delay = next_send - time.monotonic()
        if delay > 0:
            time.sleep(delay)

        frame_id, frame = camera.wait_for_frame(after_id=last_id, timeout=1.0)

        if frame is not None and frame_id != last_id:
            last_id = frame_id
            img = imaging.frame_to_image(frame, width=config.PREVIEW_WIDTH, crop=crop)
            img = filters.apply(booth.preview_filter, img)
            jpeg = imaging.to_jpeg(img, config.PREVIEW_JPEG_QUALITY)
            interval = min_interval
        elif not camera.status()["connected"]:
            jpeg = OFFLINE_JPEG   # camera offline: keep the connection alive
            interval = 1.0        # ...but only bother once a second
        else:
            continue              # camera is fine, just no new frame yet

        # Schedule the next frame relative to the previous slot (not "now"),
        # so time spent waiting for the camera doesn't lower the frame rate.
        next_send = max(next_send + interval, time.monotonic())
        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n"
               b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
               + jpeg + b"\r\n")


@app.route("/stream.mjpg")
def stream():
    # When the iPad closes the page, Flask stops this generator automatically.
    crop = request.args.get("shape") != "full"
    return Response(mjpeg_stream(crop), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


def jpeg_response(data, cache=False):
    headers = {} if cache else {"Cache-Control": "no-store"}
    return Response(data, mimetype="image/jpeg", headers=headers)


# --- Pages ---------------------------------------------------------------------

def looks():
    """The choices guests see. Frames and designs can change on the admin page."""
    return {
        "filters": filters.choices(),
        "overlays": imaging.list_overlays(),
        "themes": imaging.theme_choices(),
    }


@app.route("/")
def index():
    # Settings the page needs, handed over as JSON inside the HTML.
    page_config = {
        "countdown": config.COUNTDOWN_SECONDS,
        "styleTimeout": config.STYLE_TIMEOUT_SECONDS,
        "betweenShots": config.BETWEEN_SHOTS_SECONDS,
        "reviewTimeout": config.REVIEW_TIMEOUT_SECONDS,
        "emailTimeout": config.EMAIL_TIMEOUT_SECONDS,
        "sound": config.SOUND_ENABLED,
        "title": config.STRIP_TITLE or "Photo Booth",
        **looks(),
        "customCaption": config.ALLOW_CUSTOM_CAPTION,
        "captionMax": imaging.CAPTION_MAX_LENGTH,
    }
    # Never cached: after a change on the admin page, the iPad must get the new page.
    return render_template("index.html", page_config=page_config), {"Cache-Control": "no-store"}


@app.route("/api/looks")
def api_looks():
    return jsonify(looks())


@app.route("/strip-art/<art_id>.png")
def strip_art(art_id):
    """A strip design's artwork, for its button on the review screen."""
    path = imaging.strip_art_path(art_id)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", max_age=86400)


@app.route("/overlays/<overlay_id>.png")
def overlay_file(overlay_id):
    path = imaging.overlay_path(overlay_id)
    if path is None:
        abort(404)
    return send_from_directory(path.parent, path.name, max_age=3600)


@app.route("/snapshot.jpg")
def snapshot():
    """One full-resolution frame, so you can check image quality."""
    frame = camera.latest_frame()
    if frame is None:
        return "The camera is not connected right now.", 503
    return jpeg_response(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes())


# --- API: status and preview ---------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify({"server_id": SERVER_ID, **booth.status()})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    booth.set_preview_filter(_json().get("filter", "none"))
    return jsonify({"ok": True, "filter": booth.preview_filter})


@app.route("/api/thumbs/<filter_id>.jpg")
def api_thumb(filter_id):
    data = booth.filter_thumbnail(filter_id)
    if data is None:
        abort(404)
    return jpeg_response(data)


# --- API: sessions ---------------------------------------------------------------

def _json():
    return request.get_json(silent=True) or {}


def session_json(session):
    return jsonify(session.public(booth.features()))


@app.route("/api/sessions", methods=["POST"])
def api_new_session():
    body = _json()
    return session_json(booth.new_session(body.get("filter", "none"), body.get("overlay", "none")))


@app.route("/api/sessions/<session_id>")
def api_session(session_id):
    return session_json(booth.get(session_id))


@app.route("/api/sessions/<session_id>/capture", methods=["POST"])
def api_capture(session_id):
    body = _json()
    index = booth.capture(session_id, body.get("filter"), body.get("overlay"))
    return jsonify({"index": index, "thumb_url": f"/api/sessions/{session_id}/photos/{index}.jpg"})


@app.route("/api/sessions/<session_id>/photos/<int:index>.jpg")
def api_photo(session_id, index):
    return jpeg_response(booth.thumbnail(session_id, index))


@app.route("/api/sessions/<session_id>/photos/<int:index>/looks/<filter_id>.jpg")
def api_look(session_id, index, filter_id):
    return jpeg_response(booth.look_preview(session_id, index, filter_id), cache=True)


@app.route("/api/sessions/<session_id>/photos/<int:index>/style", methods=["POST"])
def api_restyle(session_id, index):
    body = _json()
    return session_json(booth.restyle(session_id, index, body.get("filter", "none"),
                                      body.get("overlay", "none"), bool(body.get("all"))))


@app.route("/api/sessions/<session_id>/finish", methods=["POST"])
def api_finish(session_id):
    return session_json(booth.finish(session_id))


@app.route("/api/sessions/<session_id>/strip.jpg")
def api_strip(session_id):
    session = booth.get(session_id)
    if session.strip_path is None:
        abort(404)
    return send_file(session.strip_path, mimetype="image/jpeg", max_age=0)


@app.route("/api/sessions/<session_id>/design", methods=["POST"])
def api_design(session_id):
    body = _json()
    return session_json(booth.redesign(session_id, body.get("theme"), body.get("caption", "")))


@app.route("/api/sessions/<session_id>/email", methods=["POST"])
def api_email(session_id):
    return session_json(booth.email(session_id, _json().get("address", "")))


@app.route("/api/sessions/<session_id>/print", methods=["POST"])
def api_print(session_id):
    return session_json(booth.print_strip(session_id))


@app.route("/api/sessions/<session_id>/discard", methods=["POST"])
def api_discard(session_id):
    booth.discard(session_id)
    return jsonify({"ok": True})


# --- Error handling ----------------------------------------------------------
# Problems are logged, and the browser gets a friendly message instead of a
# crash page. The web page itself always stays on screen.

@app.errorhandler(BoothError)
def handle_booth_error(error):
    return jsonify({"ok": False, "error": error.message}), error.status


@app.errorhandler(Exception)
def handle_error(error):
    code = getattr(error, "code", 500)
    if not isinstance(code, int):
        code = 500
    if code == 500:
        log.exception("Unhandled error on %s", request.path)
    message = "Something went wrong. Please try again." if code == 500 else str(error)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": message}), code
    return message, code


# --- Start -------------------------------------------------------------------

if __name__ == "__main__":
    camera.start()
    booth.start()
    health.start(camera)     # records temperature/power every 30 s to data/health.log
    log.info("Photo booth starting on http://%s:%d", config.HOST, config.PORT)
    log.info("Email: %s", f"sending from {config.GMAIL_ADDRESS}" if booth.features()["email"]
             else "off (set GMAIL_ADDRESS and GMAIL_APP_PASSWORD with ./deploy.sh config)")
    log.info("Printing: %s", "on" if config.PRINT_ENABLED else "off")

    # The admin page runs alongside, on its own port.
    admin_server = make_server(config.HOST, config.ADMIN_PORT,
                               create_admin_app(booth, camera, log_buffer.lines), threaded=True)
    threading.Thread(target=admin_server.serve_forever, name="admin", daemon=True).start()
    log.info("Admin page on http://%s:%d", config.HOST, config.ADMIN_PORT)

    # Flask's built-in server with threaded=True gives every connection its
    # own thread. That suits us well: the live stream holds a connection open
    # for as long as the page is showing, and there's only one iPad.
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False, use_reloader=False)
