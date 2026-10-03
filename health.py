"""
health.py: records the Pi's health every 30 seconds, to find out why it stops.

Each line in data/health.log looks like:

    2026-10-02 19:41:30  temp=71.2C  power=OK  load=1.35  mem_free=6120MB  app=412MB  threads=14  camera=30.0fps

The file is written straight to the SD card after every line, so if the Pi
suddenly switches off, the last lines show what was happening just before:
rising temperature, a power warning, memory running out...

It also decodes the Pi's own "throttled" flags (vcgencmd get_throttled),
which say whether the power supply dipped too low or the chip overheated
at any point since the Pi was switched on, and logs a warning when they appear.

View the log on the admin page (System tab), or from the Mac:
    ./deploy.sh ssh   then   tail -50 data/health.log
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("health")

INTERVAL_S = 30
LOG_FILE = config.DATA_DIR / "health.log"
MAX_LOG_BYTES = 2_000_000          # then the old part moves to health.log.1

# Meaning of each bit in vcgencmd get_throttled's answer.
THROTTLE_FLAGS = {
    0: "power too low NOW",
    1: "CPU speed capped NOW",
    2: "CPU slowed down NOW",
    3: "hot: soft temperature limit NOW",
    16: "power dipped too low since switch-on",
    17: "CPU speed was capped since switch-on",
    18: "CPU was slowed down since switch-on",
    19: "hit the soft temperature limit since switch-on",
}


def read_throttled():
    """Return (raw number, [problems]) or (None, []) if not on a Pi."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
        value = int(out.split("=")[1], 16)
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None, []
    return value, [text for bit, text in THROTTLE_FLAGS.items() if value & (1 << bit)]


def read_temperature():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def _meminfo(field):
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1]) // 1024          # kB -> MB
    except (OSError, ValueError):
        pass
    return None


def _app_memory_mb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def snapshot(camera=None):
    """Current health as a dict."""
    raw, problems = read_throttled()
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "temperature": read_temperature(),
        "throttled": raw,
        "problems": problems,
        "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        "mem_free_mb": _meminfo("MemAvailable"),
        "app_mb": _app_memory_mb(),
        "threads": threading.active_count(),
        "camera_fps": camera.status()["fps"] if camera else None,
    }


def format_line(s):
    def part(label, value, fmt="{}"):
        return f"{label}={fmt.format(value)}" if value is not None else f"{label}=?"
    power = ("OK" if not s["problems"] else "; ".join(s["problems"])) if s["throttled"] is not None else "?"
    return "  ".join([
        s["time"],
        part("temp", s["temperature"], "{:.1f}C"),
        f"power={power}",
        part("load", s["load"], "{:.2f}"),
        part("mem_free", s["mem_free_mb"], "{}MB"),
        part("app", s["app_mb"], "{}MB"),
        part("threads", s["threads"]),
        part("camera", s["camera_fps"], "{}fps"),
    ])


def _write(line):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
        LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())        # really on the SD card, even if the power goes


def last_lines(n=200):
    try:
        return LOG_FILE.read_text().splitlines()[-n:]
    except OSError:
        return []


def start(camera):
    """Start recording in the background."""
    previous = last_lines(1)
    _write(f"---- booth started {datetime.now():%Y-%m-%d %H:%M:%S}"
           + (f"  (last record before this: {previous[0][:19]})" if previous else ""))

    def run():
        reported = set()
        while True:
            try:
                s = snapshot(camera)
                _write(format_line(s))
                # Say so in the normal log the first time each problem appears.
                for problem in s["problems"]:
                    if problem not in reported:
                        log.warning("Pi health: %s (vcgencmd get_throttled = %s)", problem, hex(s["throttled"]))
                        reported.add(problem)
                if s["temperature"] and s["temperature"] >= 80:
                    log.warning("Pi health: very hot (%.1f °C). Is the cooler fitted?", s["temperature"])
            except Exception:
                log.exception("Health check failed")
            time.sleep(INTERVAL_S)

    threading.Thread(target=run, name="health", daemon=True).start()
