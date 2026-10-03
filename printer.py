"""
printer.py: sends images to a printer through CUPS, the Linux printing system.

We use CUPS's standard `lp` command, the same one you'd type in a terminal:

    lp -d My_Printer -o media=4x6 -o fit-to-page photo.jpg

so if printing works from the terminal, it works from the booth.
"""

import logging
import shlex
import subprocess

log = logging.getLogger(__name__)


def print_file(path, printer_name="", options="", copies=1):
    """Send a file to the printer. Returns (ok, message)."""
    command = ["lp", "-n", str(copies)]
    if printer_name:
        command += ["-d", printer_name]
    command += shlex.split(options)
    command.append(str(path))

    log.info("Printing: %s", " ".join(shlex.quote(part) for part in command))
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, "Printing isn't set up on the Pi (the 'lp' command is missing)."
    except subprocess.TimeoutExpired:
        return False, "The printer didn't respond."

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        log.error("Printing failed: %s", detail)
        return False, f"The printer reported a problem: {detail}"

    log.info("Print job sent: %s", result.stdout.strip())   # "request id is Printer-12"
    return True, "Sent to the printer"
