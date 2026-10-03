"""
camera.py: owns the USB webcam.

How it works
------------
One background thread does nothing but read frames from the webcam and keep
the newest one in memory. The rest of the app never talks to the webcam
directly. It just asks this class for "the latest frame". That means:

  * the live preview and photo capture (Stage 3) never fight over the camera;
  * taking a photo is instant, because a fresh full-resolution frame is
    always waiting;
  * if the webcam is unplugged or freezes, this thread notices, closes it,
    and keeps trying to reopen it every couple of seconds until it works.
    Plug the cable back in and the booth heals itself.

Frames are OpenCV images: numpy arrays of shape (height, width, 3) with the
colours in BGR order (OpenCV's habit: blue first, not red).
"""

import glob
import logging
import sys
import threading
import time

import cv2

log = logging.getLogger(__name__)

# Timings that rarely need changing, so they live here rather than in .env.
RECONNECT_DELAY_S = 2.0   # wait between attempts to (re)open the webcam
NO_FRAME_TIMEOUT_S = 5.0  # no good frame for this long = webcam is gone, reopen it
STALE_AFTER_S = 3.0       # newest frame older than this = report camera as offline


class Camera:
    def __init__(self, device="auto", width=1920, height=1080, fps=30):
        self.requested_device = device
        self.requested_size = (width, height)
        self.requested_fps = fps

        # `_cond` protects all the fields below. It also lets other threads
        # sleep until a new frame arrives (see wait_for_frame) instead of
        # repeatedly checking.
        self._cond = threading.Condition()
        self._frame = None         # newest frame (numpy array), or None if no camera
        self._frame_id = 0         # goes up by one for every new frame
        self._frame_time = 0.0     # when the newest frame arrived (time.monotonic())
        self._connected = False
        self._error = "Starting the camera…"
        self._device_label = None  # the device we actually opened
        self._fps = 0.0            # measured frames per second

        self._running = False
        self._thread = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the background thread that reads the webcam."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background thread and release the webcam."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def wait_for_frame(self, after_id=None, timeout=1.0):
        """Return `(frame_id, frame)` for the newest frame.

        If `after_id` is given, first wait (up to `timeout` seconds) for a
        frame newer than that one. This lets a caller handle each frame
        exactly once without spinning in a busy loop.

        `frame` is None when the camera has no picture right now.
        Treat the frame as read-only: copy it before drawing on it, because
        other parts of the app may be using the same frame at the same time.
        """
        with self._cond:
            if after_id is not None:
                self._cond.wait_for(lambda: self._frame_id != after_id, timeout)
            return self._frame_id, self._frame

    def latest_frame(self):
        """Return the newest frame if it's fresh, otherwise None."""
        with self._cond:
            if self._frame is None or not self._is_fresh():
                return None
            return self._frame

    def status(self):
        """Return a dict describing the camera, for the /api/status endpoint."""
        with self._cond:
            live = self._connected and self._is_fresh()
            size = None
            if self._frame is not None:
                h, w = self._frame.shape[:2]
                size = f"{w}x{h}"
            return {
                "connected": live,
                "error": None if live else (self._error or "Waiting for the camera…"),
                "device": self._device_label,
                "resolution": size,
                "fps": round(self._fps, 1) if live else 0,
            }

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self):
        """Main loop: open the webcam, read until it fails, repeat forever."""
        while self._running:
            source = self._resolve_device()
            cap = None
            try:
                cap = self._open(source)
                if cap is None:
                    self._mark_disconnected(
                        f"No camera found at {source}. Is the webcam plugged in?")
                else:
                    self._read_frames(cap, source)
            except Exception:
                # Never let an unexpected error kill this thread: log it,
                # then try again.
                log.exception("Unexpected camera error")
                self._mark_disconnected("The camera hit an unexpected error.")
            finally:
                if cap is not None:
                    cap.release()

            if self._running:
                time.sleep(RECONNECT_DELAY_S)

    def _resolve_device(self):
        """Turn the CAMERA_DEVICE setting into something OpenCV can open."""
        device = str(self.requested_device).strip()

        if device.lower() in ("", "auto"):
            # /dev/v4l/by-id/ contains stable names such as
            #   usb-046d_Logitech_Webcam_C930e_1234ABCD-video-index0
            # Unlike /dev/video0, these don't change when the webcam is
            # unplugged and plugged back in. "index0" is the real video
            # stream; the other entries are metadata we don't want.
            matches = sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
            return matches[0] if matches else 0

        if device.isdigit():
            return int(device)
        return device

    def _open(self, source):
        """Open the webcam and ask for our resolution. Returns None on failure."""
        # On Linux use V4L2 (the Linux camera system) directly. On other
        # systems (e.g. testing on a Mac), let OpenCV choose.
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        cap = cv2.VideoCapture(source, backend)
        if not cap.isOpened():
            cap.release()
            return None

        width, height = self.requested_size
        # Ask for MJPG *before* setting the size. MJPG is the compressed
        # format that lets USB webcams deliver 1080p at 30 fps. In raw YUYV
        # format your C930e only manages 5 fps at 1080p.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, self.requested_fps)
        return cap

    def _read_frames(self, cap, source):
        """Read frames until the webcam stops delivering them."""
        last_good = time.monotonic()
        announced = False

        while self._running:
            ok, frame = cap.read()
            now = time.monotonic()

            if not ok or frame is None:
                # One bad read can be a hiccup. A few seconds of them means
                # the webcam was unplugged or froze: give up so _run reopens it.
                if now - last_good > NO_FRAME_TIMEOUT_S:
                    self._mark_disconnected("The camera stopped sending pictures.")
                    return
                time.sleep(0.05)
                continue

            if not announced:
                self._log_connected(source, frame)
                announced = True

            with self._cond:
                # Smoothed frames-per-second measurement, shown in /api/status.
                if self._frame_time:
                    instant_fps = 1.0 / max(now - self._frame_time, 1e-6)
                    self._fps = instant_fps if self._fps == 0 else 0.9 * self._fps + 0.1 * instant_fps
                self._frame = frame
                self._frame_id += 1
                self._frame_time = now
                self._connected = True
                self._error = None
                self._device_label = str(source)
                self._cond.notify_all()  # wake anyone waiting in wait_for_frame()

            last_good = now

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_fresh(self):
        # Caller must hold self._cond.
        return time.monotonic() - self._frame_time < STALE_AFTER_S

    def _mark_disconnected(self, message):
        with self._cond:
            was_connected = self._connected
            changed = message != self._error
            self._connected = False
            self._frame = None
            self._fps = 0.0
            self._error = message
            self._cond.notify_all()
        # Only log when something changes, so a missing webcam doesn't fill
        # the log with the same line every two seconds.
        if was_connected or changed:
            log.warning("Camera offline: %s (retrying every %.0fs)", message, RECONNECT_DELAY_S)

    def _log_connected(self, source, frame):
        height, width = frame.shape[:2]
        log.info("Camera connected: %s at %dx%d", source, width, height)
        if (width, height) != self.requested_size:
            log.warning(
                "Camera gave %dx%d instead of the requested %dx%d. "
                "Check CAMERA_WIDTH/CAMERA_HEIGHT against what the webcam supports "
                "(run: v4l2-ctl -d /dev/video0 --list-formats-ext).",
                width, height, *self.requested_size)
