"""
emails.py: the designs for the email guests receive.

Each design turns a few details (event title, date, message) into a
plain-text version and an HTML version of the email. The strip itself is
shown inside the email: the HTML refers to it as "cid:strip@photobooth",
and mailer.py attaches the image under that name. The full-size strip and
the three photos are also attached as files.

Email apps are fussy, so the HTML uses old-fashioned tables, inline styles
and web-safe fonts. That's what makes it look right in Gmail, Apple Mail and
Outlook alike.

Pick the design with EMAIL_TEMPLATE in .env, or on the admin page, where
you can also preview them all.
"""

from html import escape

STRIP_CID = "strip@photobooth"
SANS = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
SERIF = "Georgia, 'Times New Roman', serif"


def _strip_img(width, style=""):
    return (f'<img src="cid:{STRIP_CID}" width="{width}" alt="Your photo strip" '
            f'style="display:block;width:{width}px;max-width:100%;height:auto;border:0;{style}">')


def _text_version(c):
    return (f"{c['message']}\n\n"
            "Your strip and the three photos are attached in full resolution.\n\n"
            f"{c['title']}, {c['date']}")


# --- 1. Classic card ---------------------------------------------------------

def classic(c):
    html = f"""\
<!doctype html><html><body style="margin:0;padding:0;background:#f3f1f6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f1f6;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="520" cellpadding="0" cellspacing="0"
         style="width:520px;max-width:100%;background:#ffffff;border-radius:16px;">
    <tr><td align="center" style="padding:40px 40px 8px;font-family:{SANS};">
      <div style="font-size:12px;letter-spacing:3px;text-transform:uppercase;color:#ff4f6d;font-weight:700;">{c['title']}</div>
      <h1 style="margin:14px 0 0;font-size:28px;line-height:1.25;color:#1d1d1f;font-weight:700;">Your photos are ready</h1>
      <p style="margin:14px 0 0;font-size:16px;line-height:1.55;color:#55505e;">{c['message']}</p>
    </td></tr>
    <tr><td align="center" style="padding:28px 40px;">
      {_strip_img(200, "border-radius:4px;box-shadow:0 8px 24px rgba(0,0,0,0.15);")}
    </td></tr>
    <tr><td align="center" style="padding:0 40px 36px;font-family:{SANS};font-size:14px;line-height:1.5;color:#8a8494;">
      Your strip and the three photos are attached in full resolution.
    </td></tr>
  </table>
  <p style="margin:20px 0 0;font-family:{SANS};font-size:12px;color:#a19bab;">{c['from_name']} &middot; {c['date']}</p>
</td></tr></table>
</body></html>"""
    return _text_version(c), html


# --- 2. Minimal ----------------------------------------------------------------
# Just the message and the strip: nothing else.

def minimal(c):
    html = f"""\
<!doctype html><html><body style="margin:0;padding:0;background:#ffffff;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:40px 20px;">
  <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="width:480px;max-width:100%;">
    <tr><td style="font-family:{SANS};padding-bottom:28px;">
      <p style="margin:0;font-size:18px;line-height:1.5;color:#1d1d1f;">{c['message']}</p>
    </td></tr>
    <tr><td>{_strip_img(200)}</td></tr>
  </table>
</td></tr></table>
</body></html>"""
    return c["message"], html


# --- 3. Bold -------------------------------------------------------------------

def bold(c):
    html = f"""\
<!doctype html><html><body style="margin:0;padding:0;background:#efedf3;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#efedf3;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="540" cellpadding="0" cellspacing="0"
         style="width:540px;max-width:100%;background:#ffffff;border-radius:18px;overflow:hidden;">
    <tr><td style="background:#16121f;padding:36px 40px 32px;font-family:{SANS};">
      <div style="height:4px;width:56px;background:#ff4f6d;border-radius:2px;"></div>
      <h1 style="margin:20px 0 0;font-size:32px;line-height:1.15;color:#ffffff;font-weight:800;">That's a wrap!</h1>
      <p style="margin:10px 0 0;font-size:15px;color:#c9c2d6;">{c['title']} &middot; {c['date']}</p>
    </td></tr>
    <tr><td style="padding:32px 40px 0;font-family:{SANS};font-size:16px;line-height:1.6;color:#3b3644;">
      {c['message']}
    </td></tr>
    <tr><td align="center" style="padding:28px 40px;">{_strip_img(210, "border-radius:4px;")}</td></tr>
    <tr><td style="padding:0 40px 36px;font-family:{SANS};">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:#fff3f5;border-radius:12px;">
        <tr><td style="padding:16px 20px;font-size:14px;line-height:1.5;color:#7a2d3f;">
          <strong>4 photos attached</strong>: your strip plus the three individual shots, in full resolution.
        </td></tr>
      </table>
    </td></tr>
  </table>
  <p style="margin:20px 0 0;font-family:{SANS};font-size:12px;color:#9a94a6;">Sent by {c['from_name']}</p>
</td></tr></table>
</body></html>"""
    return _text_version(c), html


# --- 4. Elegant ------------------------------------------------------------------

def elegant(c):
    html = f"""\
<!doctype html><html><body style="margin:0;padding:0;background:#f6f1e7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f1e7;">
<tr><td align="center" style="padding:36px 16px;">
  <table role="presentation" width="500" cellpadding="0" cellspacing="0"
         style="width:500px;max-width:100%;background:#fffdf8;border:1px solid #e3d6bd;">
    <tr><td align="center" style="padding:44px 44px 0;font-family:{SERIF};">
      <div style="font-size:12px;letter-spacing:4px;text-transform:uppercase;color:#b8955a;">{c['date']}</div>
      <h1 style="margin:16px 0 0;font-size:30px;font-weight:normal;font-style:italic;color:#4f412b;">{c['title']}</h1>
      <div style="width:60px;height:1px;background:#b8955a;margin:22px auto 0;"></div>
      <p style="margin:22px 0 0;font-size:17px;line-height:1.65;color:#5e5240;">{c['message']}</p>
    </td></tr>
    <tr><td align="center" style="padding:30px 44px;">{_strip_img(190, "border:6px solid #ffffff;box-shadow:0 2px 10px rgba(79,65,43,0.18);")}</td></tr>
    <tr><td align="center" style="padding:0 44px 40px;font-family:{SERIF};font-size:14px;font-style:italic;color:#8c7d63;">
      Your strip and photos are attached, with love.
    </td></tr>
  </table>
  <p style="margin:18px 0 0;font-family:{SERIF};font-size:12px;color:#a8997d;">{c['from_name']}</p>
</td></tr></table>
</body></html>"""
    return _text_version(c), html


# id -> (name, function). The order here is the order on the admin page.
TEMPLATES = {
    "classic": ("Classic card", classic),
    "minimal": ("Minimal", minimal),
    "bold": ("Bold", bold),
    "elegant": ("Elegant", elegant),
}
DEFAULT_TEMPLATE = "classic"


def choices():
    return [{"id": tid, "name": name} for tid, (name, _) in TEMPLATES.items()]


def render(template_id, title, message, date, from_name):
    """Return (plain_text, html) for the chosen design.

    The inputs are escaped here, so typed-in settings can't break the HTML.
    """
    _, function = TEMPLATES.get(template_id, TEMPLATES[DEFAULT_TEMPLATE])
    text = function({"title": title, "message": message, "date": date, "from_name": from_name})[0]
    safe = {"title": escape(title), "message": escape(message), "date": escape(date),
            "from_name": escape(from_name)}
    return text, function(safe)[1]


def subject(template, title, date):
    """Fill {title} and {date} into the EMAIL_SUBJECT setting."""
    return template.replace("{title}", title).replace("{date}", date)
