"""
mailer.py: sends emails through Gmail, using an app password.

An *app password* is a 16-letter password Google creates for one app (here,
the booth). It works only for sending/receiving mail, never for logging in to
your Google account, and you can revoke it at any time. It lives in the Pi's
.env file (GMAIL_ADDRESS and GMAIL_APP_PASSWORD), never in the code.

Errors are translated for the job queue (jobs.py):
  RetryLater  no internet, Gmail busy, or the password was rejected
              (the email stays queued and goes out once it's fixed)
  GiveUp      Gmail refused the recipient address. Retrying won't help.

Send a test email from the Mac with:   ./deploy.sh test-email you@example.com
"""

import logging
import smtplib
import socket
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import config
from jobs import GiveUp, RetryLater

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465              # SMTP over SSL: the connection is encrypted from the start
TIMEOUT_S = 30


def is_configured():
    """True when the Gmail address and app password are set in .env."""
    return bool(config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD)


def send(to, subject, text, html=None, attachments=(), inline=None):
    """Send one email from the booth's Gmail account, with JPEG attachments.

    `inline` is {content_id: path}: images shown inside the HTML, which
    refers to them as <img src="cid:content_id">.
    """
    if not is_configured():
        raise RetryLater("Gmail isn't set up: add GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                         "with ./deploy.sh config", offline=False)

    message = EmailMessage()
    message["From"] = formataddr((config.EMAIL_FROM_NAME, config.GMAIL_ADDRESS))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
        html_part = message.get_payload()[1]
        for content_id, path in (inline or {}).items():
            if Path(path).is_file():
                html_part.add_related(Path(path).read_bytes(), maintype="image",
                                      subtype="jpeg", cid=f"<{content_id}>")
    for path in attachments:
        path = Path(path)
        if path.is_file():
            message.add_attachment(path.read_bytes(), maintype="image",
                                   subtype="jpeg", filename=path.name)

    # Google shows the app password in groups of four ("abcd efgh ijkl mnop").
    # The spaces aren't part of it.
    password = config.GMAIL_APP_PASSWORD.replace(" ", "")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_S, context=context) as smtp:
            smtp.login(config.GMAIL_ADDRESS, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise RetryLater("Gmail rejected the address/app password. Check GMAIL_ADDRESS and "
                         "GMAIL_APP_PASSWORD with ./deploy.sh config", offline=False)
    except smtplib.SMTPRecipientsRefused:
        raise GiveUp(f"Gmail refused the address {to}")
    except smtplib.SMTPResponseException as error:
        # Codes 4xx are temporary ("try later"); 5xx are permanent.
        detail = f"Gmail said {error.smtp_code}: {error.smtp_error!r}"
        if 400 <= error.smtp_code < 500:
            raise RetryLater(detail, offline=False)
        raise GiveUp(detail)
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
            socket.error, ssl.SSLError, TimeoutError) as error:
        raise RetryLater(f"No internet connection ({error.__class__.__name__})")

    log.info("Email sent to %s", to)


if __name__ == "__main__":
    # Test: python mailer.py you@example.com
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) != 2:
        sys.exit("Usage: python mailer.py you@example.com")
    try:
        send(sys.argv[1], "Photo booth test email ✔",
             "If you can read this, the photo booth can send email.")
        print(f"Test email sent from {config.GMAIL_ADDRESS} to {sys.argv[1]} ✔")
    except (RetryLater, GiveUp) as error:
        sys.exit(f"Couldn't send: {error}")
