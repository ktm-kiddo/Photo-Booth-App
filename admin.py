"""
admin.py: the admin page, on its own port (default 8001).

    http://photobooth.local:8001      (user name: admin, password: ADMIN_PASSWORD)

It runs inside the same program as the booth (app.py starts it), so it can
see everything the booth is doing, but on a separate port with a password.
Guests on the iPad only ever see port 8000, which has none of this.

What it can do:
  Dashboard  booth and Pi health, today's numbers, a live camera snapshot
  Gallery    every strip: view, download, email, print, delete; ZIP of all
  Emails     queued / failed / sent emails; retry or remove
  Settings   every setting in .env, with an email design preview and a test
             email; saving restarts the booth
  Frames     upload frame PNGs; reorder, rename, hide or delete them
  Designs    edit, create, reorder, hide or reset the strip designs
  System     temperature, disk, logs, printer status and test print,
             restart the booth, reboot or shut down the Pi

The page itself is templates/admin.html + static/admin/; this file is the
JSON API behind it.
"""

import hmac
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from PIL import Image

import ai_prompts
import config
import emails
import health as health_module
import imaging
import mailer
import printer
from booth import _nice_date, is_valid_email
from jobs import GiveUp, RetryLater

log = logging.getLogger("admin")

ENV_FILE = config.BASE_DIR / ".env"
SESSION_ID = re.compile(r"^\d{8}_\d{6}(_\d+)?$")


# --- Settings shown on the Settings tab -----------------------------------------
# (key, label, type, help). Types: text, number, bool, select, secret, textarea.
# Select options are filled in by settings_schema().

SETTINGS = [
    ("Event", [
        ("STRIP_TITLE", "Event title", "text", "Printed on every strip and used in emails, e.g. “Sam & Alex”."),
        ("STRIP_DATE_FORMAT", "Date format", "text", "%d %B %Y → 02 October 2026. Leave blank for no date."),
        ("STRIP_THEME", "Starting strip design", "select", "Guests can still pick another on the review screen."),
        ("ALLOW_CUSTOM_CAPTION", "Guests can type their own strip text", "bool", ""),
    ]),
    ("Timers", [
        ("STYLE_TIMEOUT_SECONDS", "Time to choose a look (seconds)", "number", "After Start. When it runs out, the photos start anyway."),
        ("COUNTDOWN_SECONDS", "Countdown before the first photo (seconds)", "number", ""),
        ("BETWEEN_SHOTS_SECONDS", "Time between photos (seconds)", "number", "A countdown shows it."),
        ("REVIEW_TIMEOUT_SECONDS", "Review screen timeout (seconds)", "number", "Back to the start after this long without a touch."),
        ("EMAIL_TIMEOUT_SECONDS", "Email keyboard timeout (seconds)", "number", ""),
        ("SOUND_ENABLED", "Countdown beeps and shutter sound", "bool", ""),
    ]),
    ("Email", [
        ("EMAIL_ENABLED", "Show the Email button", "bool", "Also needs the Gmail address and app password."),
        ("GMAIL_ADDRESS", "Gmail address", "text", "The account emails are sent from."),
        ("GMAIL_APP_PASSWORD", "Gmail app password", "secret", "From myaccount.google.com/apppasswords. Leave empty to keep the current one."),
        ("EMAIL_TEMPLATE", "Email design", "select", "Preview below."),
        ("EMAIL_FROM_NAME", "Sender name", "text", "What guests see as the sender."),
        ("EMAIL_SUBJECT", "Subject", "text", "{title} = event title, {date} = the date."),
        ("EMAIL_MESSAGE", "Message", "textarea", ""),
    ]),
    ("Printing", [
        ("PRINT_ENABLED", "Show the Print button", "bool", "Set up the printer first (see README)."),
        ("PRINTER_NAME", "Printer name", "text", "As shown on the System tab. Blank = default printer."),
        ("PRINT_OPTIONS", "Print options", "text", "Passed to the lp command."),
        ("PRINT_MAX_PER_SESSION", "Prints per strip", "number", ""),
    ]),
    ("Camera & preview", [
        ("MIRROR", "Mirror like a selfie", "bool", "Off = text reads the right way round."),
        ("CAMERA_DEVICE", "Camera", "text", "auto, or a number (0, 1...)."),
        ("CAMERA_WIDTH", "Capture width", "number", ""),
        ("CAMERA_HEIGHT", "Capture height", "number", ""),
        ("CAMERA_FPS", "Camera frames per second", "number", ""),
        ("PREVIEW_WIDTH", "Preview width on the iPad", "number", "Lower = smoother on slow Wi-Fi."),
        ("PREVIEW_FPS", "Preview frames per second", "number", ""),
        ("PREVIEW_JPEG_QUALITY", "Preview quality (1-100)", "number", ""),
    ]),
    ("Admin", [
        ("ADMIN_PASSWORD", "Admin password", "secret", "Leave empty to keep the current one."),
        ("LOG_LEVEL", "Log detail", "select", ""),
    ]),
]

SELECT_OPTIONS = {
    "STRIP_THEME": lambda: [(t["id"], t["name"]) for t in imaging.theme_choices()],
    "EMAIL_TEMPLATE": lambda: [(t["id"], t["name"]) for t in emails.choices()],
    "LOG_LEVEL": lambda: [(v, v.title()) for v in ("DEBUG", "INFO", "WARNING", "ERROR")],
}


def settings_schema():
    groups = []
    for title, fields in SETTINGS:
        items = []
        for key, label, kind, help_text in fields:
            value = getattr(config, key, "")
            item = {"key": key, "label": label, "type": kind, "help": help_text}
            if kind == "secret":
                item["value"] = ""
                item["is_set"] = bool(value)
            else:
                item["value"] = value
            if kind == "select":
                item["options"] = [{"value": v, "label": n} for v, n in SELECT_OPTIONS[key]()]
            items.append(item)
        groups.append({"title": title, "fields": items})
    return groups


def _env_line(key, value):
    """KEY=value, quoted when needed so .env reads it back exactly."""
    value = str(value).replace("\r", "").replace("\n", " ")
    if re.search(r"[\s#\"'\\=]", value):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{key}={value}"


def update_env(changes):
    """Write changed settings into .env, keeping its comments and layout."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    remaining = dict(changes)
    for i, line in enumerate(lines):
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        if match and match.group(1) in remaining:
            lines[i] = _env_line(match.group(1), remaining.pop(match.group(1)))
    if remaining:
        lines += ["", "# Added from the admin page"] + [_env_line(k, v) for k, v in remaining.items()]
    temp = ENV_FILE.with_suffix(".tmp")
    temp.write_text("\n".join(lines) + "\n")
    temp.chmod(0o600)
    temp.replace(ENV_FILE)


def restart_soon(delay=1.0):
    """Stop the program shortly; systemd starts it again within seconds."""
    def stop():
        log.warning("Restarting the booth (requested from the admin page)")
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Timer(delay, stop).start()


# --- Pi health ------------------------------------------------------------------

def _run(command, timeout=5):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pi_health():
    health = {}
    try:
        health["temperature"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        health["temperature"] = None
    usage = shutil.disk_usage(config.DATA_DIR)
    health["disk_free_gb"] = round(usage.free / 1e9, 1)
    health["disk_used_pct"] = round(100 * usage.used / usage.total)
    try:
        health["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        health["uptime_s"] = None
    # How often systemd has had to restart the booth since the Pi switched on.
    # More than a few means it keeps crashing: the System tab's log says why.
    restarts = _run(["systemctl", "show", "photobooth", "-p", "NRestarts", "--value"])
    health["app_restarts"] = int(restarts) if restarts.isdigit() else None
    health["ip"] = (_run(["hostname", "-I"]).split() or [_local_ip()])[0] if sys.platform.startswith("linux") else _local_ip()
    health["hostname"] = socket.gethostname()
    raw, problems = health_module.read_throttled()
    health["power_ok"] = None if raw is None else not problems
    health["problems"] = problems
    health["load"] = round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None
    return health


def _local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# --- Gallery helpers --------------------------------------------------------------

def _strip_sessions():
    """Session ids of every saved strip, newest first."""
    folder = config.DATA_DIR / "strips"
    ids = [p.name[:-len("_strip.jpg")] for p in folder.glob("*_strip.jpg")]
    return sorted((i for i in ids if SESSION_ID.match(i)), reverse=True)


def _session_files(session_id):
    data = config.DATA_DIR
    return {
        "strip": data / "strips" / f"{session_id}_strip.jpg",
        "photos": [data / "photos" / f"{session_id}_{n}.jpg" for n in (1, 2, 3)],
        "originals": [data / "originals" / f"{session_id}_{n}.jpg" for n in (1, 2, 3)],
        "print": data / "prints" / f"{session_id}_print.jpg",
    }


def _session_time(session_id):
    return datetime.strptime(session_id[:15], "%Y%m%d_%H%M%S")


def _check_session(session_id):
    if not SESSION_ID.match(session_id) or not _session_files(session_id)["strip"].is_file():
        abort(404)


def _thumbnail(session_id):
    """Small copy of a strip for the gallery grid, cached in data/.thumbs."""
    strip = _session_files(session_id)["strip"]
    thumb = config.DATA_DIR / ".thumbs" / f"{session_id}.jpg"
    if not thumb.exists() or thumb.stat().st_mtime < strip.stat().st_mtime:
        thumb.parent.mkdir(exist_ok=True)
        with Image.open(strip) as img:
            img.convert("RGB").resize((200, 600), Image.LANCZOS).save(thumb, "JPEG", quality=82)
    return thumb


# --- The app ----------------------------------------------------------------------

def create_admin_app(booth, camera, log_lines):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024     # uploads up to 20 MB

    # --- Password check (HTTP "basic auth": the browser asks for it once) ----
    @app.before_request
    def require_password():
        if not config.ADMIN_PASSWORD:
            return Response("The admin password isn't set. Run ./deploy.sh on the Mac, "
                            "which creates one, or set ADMIN_PASSWORD with ./deploy.sh config.",
                            503, mimetype="text/plain")
        auth = request.authorization
        if not auth or not hmac.compare_digest((auth.password or "").encode(), config.ADMIN_PASSWORD.encode()):
            return Response("Password required.", 401,
                            {"WWW-Authenticate": 'Basic realm="Photo booth admin"'})

    @app.errorhandler(Exception)
    def handle_error(error):
        code = getattr(error, "code", 500)
        code = code if isinstance(code, int) else 500
        if code == 500:
            log.exception("Admin error on %s", request.path)
        return jsonify({"ok": False, "error": str(error) if code != 500 else "Something went wrong (see Logs)."}), code

    def ok(**extra):
        return jsonify({"ok": True, **extra})

    def body():
        return request.get_json(silent=True) or {}

    # --- Page -------------------------------------------------------------------
    @app.route("/")
    def page():
        return render_template("admin.html", booth_port=config.PORT)

    # --- Dashboard ----------------------------------------------------------------
    @app.route("/api/overview")
    def overview():
        sessions = _strip_sessions()
        today = datetime.now().strftime("%Y%m%d")
        sent = _sent_emails()
        return jsonify({
            "booth": booth.status(),
            "pi": pi_health(),
            "counts": {
                "strips_today": sum(1 for s in sessions if s.startswith(today)),
                "strips_total": len(sessions),
                "emails_today": sum(1 for e in sent if e["time"].startswith(datetime.now().strftime("%Y-%m-%d"))),
                "emails_total": len(sent),
                "emails_waiting": booth.jobs.pending_count(),
                "emails_failed": len(list(booth.jobs.failed_folder.glob("*.json"))),
            },
            "email": {"configured": mailer.is_configured(), "address": config.GMAIL_ADDRESS,
                      "enabled": config.EMAIL_ENABLED, "template": config.EMAIL_TEMPLATE},
            "print": {"enabled": config.PRINT_ENABLED, "printer": config.PRINTER_NAME or "(default)"},
            "event": {"title": config.STRIP_TITLE, "theme": config.STRIP_THEME},
        })

    @app.route("/api/stream.mjpg")
    def stream():
        """Live camera for the dashboard: 640 px, up to 15 fps (lighter than the iPad's)."""
        def frames():
            last_id = None
            while True:
                frame_id, frame = camera.wait_for_frame(after_id=last_id, timeout=1.0)
                if frame is None or frame_id == last_id:
                    continue                  # no new picture yet (or camera offline)
                last_id = frame_id
                jpeg = imaging.to_jpeg(imaging.frame_to_image(frame, width=640), 70)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                time.sleep(1 / 15)
        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    @app.route("/api/snapshot.jpg")
    def snapshot():
        frame = camera.latest_frame()
        if frame is None:
            abort(404)
        img = imaging.frame_to_image(frame, width=640)
        return Response(imaging.to_jpeg(img, 80), mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    # --- Gallery ------------------------------------------------------------------
    @app.route("/api/gallery")
    def gallery():
        sessions = _strip_sessions()
        offset = max(0, request.args.get("offset", 0, type=int))
        limit = min(120, max(1, request.args.get("limit", 48, type=int)))
        items = []
        for session_id in sessions[offset:offset + limit]:
            files = _session_files(session_id)
            items.append({
                "id": session_id,
                "time": _session_time(session_id).isoformat(timespec="minutes"),
                "photos": [p.name for p in files["photos"] if p.is_file()],
                "originals": [p.name for p in files["originals"] if p.is_file()],
            })
        return jsonify({"items": items, "total": len(sessions)})

    @app.route("/files/<kind>/<name>")
    def files(kind, name):
        folders = {"strips", "photos", "originals"}
        if kind not in folders or not re.match(r"^[\w.-]+\.jpg$", name):
            abort(404)
        path = config.DATA_DIR / kind / name
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/jpeg", as_attachment=request.args.get("download") == "1")

    @app.route("/thumbs/<session_id>.jpg")
    def thumb(session_id):
        _check_session(session_id)
        return send_file(_thumbnail(session_id), mimetype="image/jpeg")

    @app.route("/api/gallery/<session_id>/email", methods=["POST"])
    def gallery_email(session_id):
        _check_session(session_id)
        address = (body().get("address") or "").strip()
        if not is_valid_email(address):
            return jsonify({"ok": False, "error": "That email address doesn't look right."}), 400
        files_ = _session_files(session_id)
        attachments = [str(files_["strip"])] + [str(p) for p in files_["photos"] if p.is_file()]
        booth.jobs.add("email", session_id, {"to": address, "attachments": attachments,
                                             "date": _nice_date(_session_time(session_id))})
        return ok(message=f"Email to {address} queued.")

    @app.route("/api/gallery/<session_id>/print", methods=["POST"])
    def gallery_print(session_id):
        _check_session(session_id)
        files_ = _session_files(session_id)
        with Image.open(files_["strip"]) as strip:
            imaging.save_jpeg(imaging.build_print_sheet(strip.convert("RGB")), files_["print"])
        success, message = printer.print_file(files_["print"], config.PRINTER_NAME, config.PRINT_OPTIONS)
        return jsonify({"ok": success, "message": message, "error": None if success else message})

    @app.route("/api/gallery/<session_id>/delete", methods=["POST"])
    def gallery_delete(session_id):
        _check_session(session_id)
        files_ = _session_files(session_id)
        for path in [files_["strip"], files_["print"], *files_["photos"], *files_["originals"],
                     config.DATA_DIR / ".thumbs" / f"{session_id}.jpg"]:
            path.unlink(missing_ok=True)
        log.info("Deleted session %s from the admin page", session_id)
        return ok()

    @app.route("/api/gallery/download.zip")
    def gallery_zip():
        """ZIP of every strip (or, with ?all=1, strips + photos + originals)."""
        everything = request.args.get("all") == "1"
        temp = tempfile.NamedTemporaryFile(suffix=".zip", dir=config.DATA_DIR, delete=False)
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_STORED) as archive:   # JPEGs don't compress further
            for folder in (["strips", "photos", "originals"] if everything else ["strips"]):
                for path in sorted((config.DATA_DIR / folder).glob("*.jpg")):
                    archive.write(path, f"{folder}/{path.name}")
        temp.close()
        response = send_file(temp.name, mimetype="application/zip", as_attachment=True,
                             download_name=f"photobooth_{datetime.now():%Y-%m-%d}{'_all' if everything else ''}.zip")
        response.call_on_close(lambda: os.unlink(temp.name))
        return response

    # --- Emails --------------------------------------------------------------------
    def _sent_emails():
        path = config.DATA_DIR / "email_log.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
        return entries

    def _job_list(folder):
        jobs = []
        for path in sorted(folder.glob("*.json")):
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            jobs.append({"id": job["id"], "to": job["payload"].get("to"), "session": job["session"],
                         "attempts": job["attempts"], "error": job.get("last_error"),
                         "next_try": job.get("next_try")})
        return jobs

    @app.route("/api/emails")
    def email_overview():
        return jsonify({
            "waiting": _job_list(booth.jobs.folder),
            "failed": _job_list(booth.jobs.failed_folder),
            "sent": list(reversed(_sent_emails()))[:300],
            "online": booth.jobs.online,
        })

    @app.route("/api/emails/retry", methods=["POST"])
    def email_retry_all():
        booth.jobs.wake()
        return ok(message="Retrying waiting emails now.")

    @app.route("/api/emails/<job_id>/<action>", methods=["POST"])
    def email_job_action(job_id, action):
        if not re.match(r"^[\w-]+$", job_id):
            abort(404)
        waiting = booth.jobs.folder / f"{job_id}.json"
        failed = booth.jobs.failed_folder / f"{job_id}.json"
        if action == "delete":
            waiting.unlink(missing_ok=True)
            failed.unlink(missing_ok=True)
            return ok()
        if action == "retry" and failed.exists():
            job = json.loads(failed.read_text())
            job.update(attempts=0, next_try=0, last_error=None)
            failed.unlink()
            booth.jobs.add_existing(job)
            return ok(message="Email queued again.")
        abort(404)

    # --- Email designs ---------------------------------------------------------------
    @app.route("/api/email-preview/<template_id>")
    def email_preview(template_id):
        if template_id not in emails.TEMPLATES:
            abort(404)
        sessions = _strip_sessions()
        strip_url = f"/files/strips/{sessions[0]}_strip.jpg" if sessions else "/static/admin/sample-strip.svg"
        title = config.STRIP_TITLE or "Photo Booth"
        date = f"{datetime.now().day} {datetime.now():%B %Y}"
        _, html = emails.render(template_id, title, config.EMAIL_MESSAGE, date, config.EMAIL_FROM_NAME)
        return Response(html.replace(f"cid:{emails.STRIP_CID}", strip_url), mimetype="text/html")

    @app.route("/api/email-test", methods=["POST"])
    def email_test():
        address = (body().get("address") or "").strip()
        if not is_valid_email(address):
            return jsonify({"ok": False, "error": "That email address doesn't look right."}), 400
        sessions = _strip_sessions()
        strip = str(_session_files(sessions[0])["strip"]) if sessions else None
        title = config.STRIP_TITLE or "Photo Booth"
        date = f"{datetime.now().day} {datetime.now():%B %Y}"
        text, html = emails.render(config.EMAIL_TEMPLATE, title, config.EMAIL_MESSAGE, date, config.EMAIL_FROM_NAME)
        try:
            mailer.send(address, "[Test] " + emails.subject(config.EMAIL_SUBJECT, title, date), text, html,
                        [strip] if strip else [], inline={emails.STRIP_CID: strip} if strip else None)
        except (RetryLater, GiveUp) as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        return ok(message=f"Test email sent to {address}.")

    # --- Settings ----------------------------------------------------------------------
    @app.route("/api/settings")
    def get_settings():
        return jsonify({"groups": settings_schema()})

    @app.route("/api/settings", methods=["POST"])
    def save_settings():
        values = body().get("values", {})
        allowed = {key: kind for _, fields in SETTINGS for key, _, kind, _ in fields}
        changes = {}
        for key, value in values.items():
            kind = allowed.get(key)
            if kind is None:
                continue
            if kind == "secret" and not str(value).strip():
                continue                                  # empty = keep the current secret
            if kind == "bool":
                value = "true" if value in (True, "true", "1", 1) else "false"
            if kind == "number" and not re.match(r"^-?\d+(\.\d+)?$", str(value).strip()):
                return jsonify({"ok": False, "error": f"“{key}” must be a number."}), 400
            changes[key] = str(value).strip()
        if changes:
            update_env(changes)
            log.info("Settings changed from the admin page: %s", ", ".join(sorted(changes)))
            restart_soon()
        return ok(restarting=bool(changes))

    # --- Frames (overlays) -------------------------------------------------------------
    def _looks_changed():
        booth.content_version += 1          # the iPad picks up the new frames/designs

    @app.route("/api/frames")
    def frames():
        return jsonify({"frames": imaging.list_overlays(include_hidden=True)})

    @app.route("/api/frames", methods=["POST"])
    def upload_frame():
        upload = request.files.get("file")
        if upload is None:
            return jsonify({"ok": False, "error": "Choose a PNG file first."}), 400
        label = " ".join(request.form.get("name", "").split())[:24]
        stem = label or Path(upload.filename or "frame").stem
        name = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_") or "frame"
        if name == imaging.NO_FRAME:
            name = "frame"
        try:
            img = Image.open(upload.stream)
            img.load()
        except Exception:
            return jsonify({"ok": False, "error": "That file isn't an image Pillow can read."}), 400
        if "A" not in img.getbands():
            return jsonify({"ok": False, "error": "The image has no transparency, so it would cover the "
                                                  "whole photo. Export it as a transparent PNG."}), 400
        folder = imaging.UPLOADED_OVERLAYS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        existing = {f["id"] for f in imaging.list_overlays(include_hidden=True)}
        overlay_id, n = name, 2
        while overlay_id in existing:
            overlay_id, n = f"{name}_{n}", n + 1
        img.convert("RGBA").resize((1080, 960), Image.LANCZOS).save(folder / f"{overlay_id}.png")
        # New frames go at the end of the list.
        settings = imaging.overlay_settings()
        if settings["order"]:
            settings["order"].append(overlay_id)
        if label and label != imaging.overlay_label(overlay_id):
            settings["names"][overlay_id] = label
        imaging.save_overlay_settings(settings)
        _looks_changed()
        log.info("Frame %s uploaded from the admin page", overlay_id)
        return ok(id=overlay_id)

    @app.route("/api/frames/order", methods=["POST"])
    def frame_order():
        ids = [str(i) for i in body().get("ids", [])]
        settings = imaging.overlay_settings()
        settings["order"] = ids
        imaging.save_overlay_settings(settings)
        _looks_changed()
        return ok()

    @app.route("/api/frames/<overlay_id>/<action>", methods=["POST"])
    def frame_action(overlay_id, action):
        path = imaging.overlay_path(overlay_id)
        if path is None and overlay_id != imaging.NO_FRAME:
            abort(404)
        settings = imaging.overlay_settings()
        if action in ("hide", "show"):
            hidden = set(settings["hidden"])
            hidden.add(overlay_id) if action == "hide" else hidden.discard(overlay_id)
            settings["hidden"] = sorted(hidden)
        elif action == "rename":
            name = " ".join(str(body().get("name", "")).split())[:24]
            if name and name != imaging.overlay_label(overlay_id):
                settings["names"][overlay_id] = name
            else:
                settings["names"].pop(overlay_id, None)     # empty = back to the file name
        elif action == "delete":
            if path is None:
                return jsonify({"ok": False, "error": "“No frame” can't be deleted, only hidden."}), 400
            if path.parent != imaging.UPLOADED_OVERLAYS_DIR:
                return jsonify({"ok": False, "error": "Built-in frames can only be hidden. "
                                                      "To remove one for good, delete it from overlays/ on the Mac."}), 400
            path.unlink()
            settings["order"] = [i for i in settings["order"] if i != overlay_id]
            settings["names"].pop(overlay_id, None)
        else:
            abort(404)
        imaging.save_overlay_settings(settings)
        _looks_changed()
        return ok()

    # --- Making frames and designs with an AI model --------------------------------------
    @app.route("/api/ai-prompt", methods=["POST"])
    def ai_prompt():
        data = body()
        make = ai_prompts.frame_prompt if data.get("kind") == "frame" else ai_prompts.design_prompt
        return ok(prompt=make(data.get("description", "")))

    @app.route("/api/strip-art", methods=["POST"])
    def upload_strip_art():
        """Artwork for a design, drawn into a PNG by the browser (from the AI's SVG)."""
        upload = request.files.get("file")
        try:
            img = Image.open(upload.stream)
            img.load()
        except Exception:
            return jsonify({"ok": False, "error": "That artwork isn't a picture the booth can read."}), 400
        return ok(id=imaging.save_strip_art(img))

    @app.route("/strip-art/<art_id>.png")
    def strip_art(art_id):
        path = imaging.strip_art_path(art_id)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png", max_age=86400)

    @app.route("/api/themes/import", methods=["POST"])
    def theme_import():
        try:
            designs = ai_prompts.parse_designs(body().get("text", ""))
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        return ok(designs=designs)

    # --- Strip designs -----------------------------------------------------------------
    @app.route("/api/themes")
    def themes():
        return jsonify({
            "themes": imaging.all_themes(include_hidden=True),
            "fonts": [{"key": k, "label": v[4], "css": v[2], "weight": v[3]} for k, v in imaging.FONTS.items()],
            "decorations": [{"key": k, "label": v} for k, v in imaging.DECORATIONS.items()],
        })

    def _sample_photos():
        """The latest guests' photos, so previews look real (or None)."""
        sessions = _strip_sessions()
        if not sessions:
            return None
        photos = []
        for path in _session_files(sessions[0])["photos"]:
            if not path.is_file():
                return None
            with Image.open(path) as img:
                photos.append(img.convert("RGB").resize(imaging.STRIP_PHOTO_SIZE))
        return photos

    @app.route("/api/themes/preview", methods=["POST"])
    def theme_preview():
        try:
            design = imaging.validate_theme(body().get("theme", {}))
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        return Response(imaging.theme_preview(design, _sample_photos(), width=300), mimetype="image/jpeg")

    @app.route("/api/themes/<theme_id>/preview.jpg")
    def saved_theme_preview(theme_id):
        design = imaging.get_theme(theme_id)
        if design is None:
            abort(404)
        return Response(imaging.theme_preview(design, _sample_photos()), mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.route("/api/themes/order", methods=["POST"])
    def theme_order():
        imaging.set_theme_order(body().get("ids", []))
        _looks_changed()
        return ok()

    @app.route("/api/themes/<theme_id>", methods=["POST"])
    def theme_save(theme_id):
        try:
            saved = imaging.save_theme(None if theme_id == "new" else theme_id, body().get("theme", {}))
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        _looks_changed()
        log.info("Strip design %s saved from the admin page", saved)
        return ok(id=saved, message="Design saved")

    @app.route("/api/themes/<theme_id>/<action>", methods=["POST"])
    def theme_action(theme_id, action):
        if imaging.get_theme(theme_id) is None:
            abort(404)
        if action in ("hide", "show"):
            if action == "hide" and len(imaging.all_themes()) <= 1:
                return jsonify({"ok": False, "error": "At least one design has to stay visible."}), 400
            imaging.set_theme_hidden(theme_id, action == "hide")
        elif action == "delete":           # a built-in design is reset instead
            imaging.delete_theme(theme_id)
        else:
            abort(404)
        _looks_changed()
        return ok()

    @app.route("/overlays/<overlay_id>.png")
    def overlay_file(overlay_id):
        path = imaging.overlay_path(overlay_id)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png")

    # --- System --------------------------------------------------------------------
    @app.route("/api/system")
    def system():
        return jsonify({
            "pi": pi_health(),
            "printers": _run(["lpstat", "-p", "-d"]) or "CUPS isn't installed, or no printers are set up.",
            "python": sys.version.split()[0],
            "opencv": cv2.__version__,
        })

    @app.route("/api/health-log")
    def health_log():
        return jsonify({"lines": health_module.last_lines(int(request.args.get("n", 200)))})

    @app.route("/api/logs")
    def logs():
        return jsonify({"lines": list(log_lines)[-int(request.args.get("n", 300)):]})

    @app.route("/api/printer/test", methods=["POST"])
    def printer_test():
        sessions = _strip_sessions()
        if not sessions:
            return jsonify({"ok": False, "error": "Take a strip first, then print it."}), 400
        return gallery_print(sessions[0])

    @app.route("/api/system/<action>", methods=["POST"])
    def system_action(action):
        if action == "restart":
            restart_soon()
            return ok(message="Restarting the booth…")
        commands = {"reboot": ["sudo", "-n", "reboot"], "shutdown": ["sudo", "-n", "shutdown", "-h", "now"]}
        if action not in commands:
            abort(404)
        if not sys.platform.startswith("linux"):
            return jsonify({"ok": False, "error": "Only works on the Pi."}), 400
        log.warning("%s requested from the admin page", action.title())
        threading.Timer(1.0, lambda: subprocess.run(commands[action])).start()
        return ok(message="Rebooting…" if action == "reboot" else
                  "Shutting down. Wait for the green light to stop blinking before unplugging.")

    return app


class LogBuffer(logging.Handler):
    """Keeps the last lines of the log in memory, for the admin Logs view."""

    def __init__(self, size=1000):
        super().__init__()
        self.lines = deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass
