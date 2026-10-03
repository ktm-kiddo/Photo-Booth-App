"""
jobs.py: a small background to-do list for slow internet tasks.

Sending an email can take a few seconds, and sometimes there's no
internet at all. So instead of doing them while the
guest waits, the app adds a *job* to this queue and carries on. A single
background thread works through the jobs one at a time.

  * Every job is saved as a small JSON file in data/queue/, so nothing is
    lost if the Pi is switched off. Jobs left over from before a restart
    simply continue.
  * If a job fails because the internet is down, it's retried later, with
    longer waits each time (5 s, 15 s, 30 s, 1 min, 2 min, then every 5 min).
  * If a job can never succeed (e.g. Gmail rejects the email address), it's
    moved to data/queue/failed/ so you can look at it later.

Job handlers (in booth.py) signal what went wrong by raising RetryLater
or GiveUp.
"""

import json
import logging
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

RETRY_DELAYS_S = [5, 15, 30, 60, 120, 300]


class RetryLater(Exception):
    """Temporary problem (no internet, Gmail busy...). Try the job again later.

    offline=True means "the internet seems to be down", which the review
    screen uses to show a friendly "we'll do it later" message.
    """

    def __init__(self, message, offline=True):
        super().__init__(message)
        self.offline = offline


class GiveUp(Exception):
    """Permanent problem. Retrying won't help."""


class JobQueue:
    def __init__(self, folder, on_update=None):
        """
        folder     where job files are kept (e.g. data/queue)
        on_update  function(job, state, info) called whenever a job changes
                   state: "running", "retrying", "done" or "failed"
        """
        self.folder = Path(folder)
        self.failed_folder = self.folder / "failed"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.failed_folder.mkdir(exist_ok=True)

        self.on_update = on_update or (lambda job, state, info: None)
        self.handlers = {}          # job type -> function(job) -> result dict
        self.online = None          # True/False once we've tried something; None = unknown
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancelled = set()
        self._thread = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, job_type, handler):
        self.handlers[job_type] = handler

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        pending = len(list(self.folder.glob("*.json")))
        if pending:
            log.info("Resuming %d queued job(s) from before the restart", pending)
        self._thread = threading.Thread(target=self._run, name="jobs", daemon=True)
        self._thread.start()

    def add(self, job_type, session_id, payload):
        """Queue a new job and wake the worker. Returns the job dict."""
        job = {
            "id": f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            "type": job_type,
            "session": session_id,
            "payload": payload,
            "attempts": 0,
            "next_try": 0,
            "created": time.time(),
            "last_error": None,
        }
        with self._lock:
            self._save(job)
        self._wake.set()
        return job

    def add_existing(self, job):
        """Put a job dict back in the queue (e.g. retrying a failed email)."""
        with self._lock:
            self._save(job)
        self._wake.set()

    def cancel_session(self, session_id, job_types):
        """Remove queued jobs of the given types for a session (e.g. on Retake).

        A job that is already running finishes, but its result is ignored.
        """
        with self._lock:
            for job in self._load_all():
                if job["session"] == session_id and job["type"] in job_types:
                    self._cancelled.add(job["id"])
                    self._path(job).unlink(missing_ok=True)

    def wake(self):
        """Retry waiting jobs right away (e.g. after fixing the Gmail password)."""
        with self._lock:
            for job in self._load_all():
                job["next_try"] = 0
                self._save(job)
        self._wake.set()

    def pending_count(self):
        return len(list(self.folder.glob("*.json")))

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self):
        while True:
            job = self._next_due_job()
            if job is None:
                # Nothing due: sleep until the next job is due, or until
                # add() wakes us up (at most 30 s, to be safe).
                self._wake.wait(timeout=self._seconds_until_next_job())
                self._wake.clear()
                continue
            try:
                self._process(job)
            except Exception:
                # A bug in a handler must never stop the queue.
                log.exception("Job %s crashed; it will be retried", job["id"])
                self._reschedule(job, "Unexpected error", offline=False)

    def _process(self, job):
        handler = self.handlers.get(job["type"])
        if handler is None:
            self._fail(job, f"No handler for job type {job['type']!r}")
            return

        job["attempts"] += 1
        self.on_update(job, "running", None)
        try:
            result = handler(job)
        except RetryLater as error:
            if error.offline:
                self.online = False
            self._reschedule(job, str(error), error.offline)
            return
        except GiveUp as error:
            self.online = True
            self._fail(job, str(error))
            return

        self.online = True
        with self._lock:
            self._path(job).unlink(missing_ok=True)
            cancelled = job["id"] in self._cancelled
            self._cancelled.discard(job["id"])
        if not cancelled:
            log.info("Job %s (%s) done", job["id"], job["type"])
            self.on_update(job, "done", result)

    def _reschedule(self, job, message, offline):
        delay = RETRY_DELAYS_S[min(job["attempts"], len(RETRY_DELAYS_S)) - 1]
        job["next_try"] = time.time() + delay
        job["last_error"] = message
        with self._lock:
            if job["id"] in self._cancelled:
                self._cancelled.discard(job["id"])
                return
            self._save(job)
        log.warning("Job %s (%s) will retry in %ds: %s", job["id"], job["type"], delay, message)
        self.on_update(job, "retrying", {"error": message, "offline": offline})

    def _fail(self, job, message):
        job["last_error"] = message
        with self._lock:
            self._path(job).unlink(missing_ok=True)
            (self.failed_folder / f"{job['id']}.json").write_text(json.dumps(job, indent=2))
        log.error("Job %s (%s) failed for good: %s", job["id"], job["type"], message)
        self.on_update(job, "failed", {"error": message})

    # ------------------------------------------------------------------
    # Job files
    # ------------------------------------------------------------------

    def _path(self, job):
        return self.folder / f"{job['id']}.json"

    def _save(self, job):
        # Write to a temporary file then rename, so a power cut mid-write
        # can never leave a half-written job file.
        temp = self._path(job).with_suffix(".tmp")
        temp.write_text(json.dumps(job, indent=2))
        temp.replace(self._path(job))

    def _load_all(self):
        jobs = []
        for path in self.folder.glob("*.json"):
            try:
                jobs.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                log.error("Unreadable job file %s; moving it aside", path.name)
                path.replace(self.failed_folder / path.name)
        return sorted(jobs, key=lambda j: j["created"])   # oldest first

    def _next_due_job(self):
        now = time.time()
        with self._lock:
            for job in self._load_all():
                if job["next_try"] <= now:
                    return job
        return None

    def _seconds_until_next_job(self):
        with self._lock:
            jobs = self._load_all()
        if not jobs:
            return 30
        return max(0.5, min(30, min(j["next_try"] for j in jobs) - time.time()))
