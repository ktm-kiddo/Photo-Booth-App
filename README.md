# Raspberry Pi Photo Booth

A photo booth that runs on a Raspberry Pi 5 with a USB webcam. An iPad is the
touchscreen: it shows the booth in Safari over Wi-Fi.

**What guests do:**
1. The start screen shows the camera, full screen. They touch anywhere to start.
2. They have **10 seconds** to choose a filter (11 to choose from) and a frame, seeing the result live.
   They can tap **Go** to start early.
3. Three photos in a row, **3 seconds apart**: countdown, flash, countdown, flash, countdown, flash.
4. The strip "prints out" on screen. From there they can:
   - **Customize** it: change any photo's filter or frame, pick a strip design, or type their own text
   - **Email** it to themselves or friends (the strip plus all three photos). They type an address and they're done:
     the Pi sends it in the background, so they can add more addresses or walk away straight away.
   - **Print** it, or **Retake** it
5. **Finish** returns to the start screen. If they haven't emailed or printed anything, they're asked first.
   The screen also returns to the start after 60 s without a touch. Emails keep sending while the next guests use the booth.

---

## Everyday use (from the Mac)

```bash
cd ~/Desktop/"Photo Booth App"
./deploy.sh                # push your changes to the Pi and restart (a few seconds)
./deploy.sh config         # edit the settings on the Pi; saving restarts the booth
./deploy.sh logs           # watch what the booth is doing (Ctrl+C to stop)
./deploy.sh restart        # restart the booth
./deploy.sh ssh            # open a terminal on the Pi
./deploy.sh test-email you@example.com   # check that email works
./deploy.sh admin          # show the admin page address and password
./deploy.sh wifi           # see and add Wi-Fi networks the Pi joins (e.g. the Jetpack)
```

- The **first** `./deploy.sh` sets everything up on the Pi and takes a few minutes: Python packages, settings file and auto-start.
- The first run also asks for the Pi's password once, to set up password-free login.
- After a deploy, the iPad reloads the page by itself, but never in the middle of someone's photos.
- The booth **starts automatically when the Pi gets power**, and restarts itself if it ever crashes.

---

## The admin page

Open **http://photobooth.local:8001** on any computer or phone on the same Wi-Fi.
- **User name:** `admin`
- **Password:** run `./deploy.sh admin` to see it. The first deploy creates a random one.

The guests' page on port 8000 has none of these controls, and guests can't reach port 8001 from the
iPad in Guided Access.

| Tab | What it's for |
|---|---|
| **Dashboard** | Live camera, strips and emails today, internet, Pi temperature, power, free space, and how often the booth has restarted |
| **Gallery** | Every strip: view, download photos, email it to someone, print, delete. Download everything as a ZIP |
| **Emails** | Emails waiting for internet, failed ones (retry or remove), and everything sent |
| **Settings** | Every setting, including the Gmail details, event title, timers and printing. Preview the four email designs and send a test. Saving restarts the booth (~5 s) |
| **Frames** | Upload frame PNGs, or make one with AI; reorder, rename, hide or delete them. "No frame" can be moved or hidden too, and guests start with the first frame in the list |
| **Strip designs** | Edit any design (colours, fonts, sizes, spacing, decoration) with a live preview; create, duplicate, reorder, hide; reset built-in ones; make new ones with AI |
| **System** | Pi health, live log, printer status and test print, restart the booth, **reboot or shut down the Pi** |

Frames uploaded here are stored on the Pi (`data/overlays/`), so deploying from the Mac never removes them.

**Making a frame or design with AI:** on the Frames or Strip designs tab, press **Make one with AI**.
Describe what you want (e.g. "gold art deco corners for a 1920s gala"), press **Copy prompt**,
and paste it into a new chat with any AI model (Claude, ChatGPT, Gemini...). Then paste its whole
reply back into the box. A frame shows a preview before you add it. A design opens in the editor, so
you can tweak it before saving. Ask for several designs at once and they're all added.
AI designs come with their own artwork (patterns, illustrations, borders, stickers over the photo
corners...), not just the built-in decorations. The editor can switch the artwork off.

> Always use **Shut down Pi** before unplugging it. Pulling the power while it's
> running can damage the SD card.

---

## Internet at events (Verizon Jetpack)

The booth brings its own Wi-Fi: the Pi and the iPad both join the Jetpack, so nothing needs
setting up at the venue. With no signal the booth still works fully; emails wait and go out later.

**Once, at home:**
1. Find the Jetpack's Wi-Fi name and password on its screen.
2. In its settings page (**http://my.jetpack**, or the address on its label), check that:
   - you're using the main Wi-Fi, not "Guest Wi-Fi"
   - "privacy separator" / "AP isolation" is off
   - Wi-Fi standby / auto-off is set to never
3. Save it on the Pi:
   ```bash
   ./deploy.sh wifi
   ```
   It lists the networks the Pi already knows and can see, then asks for the Jetpack's name and password.
   The Pi **stays on your home Wi-Fi** for now, and joins the Jetpack by itself wherever your home
   Wi-Fi isn't around.
   - Answer **y** to "Prefer" only if the Pi should choose the Jetpack even at home. Then deploying
     from the Mac needs the Mac on the Jetpack too.
4. Join the iPad to the Jetpack's Wi-Fi. The booth is still at **http://photobooth.local:8000**.

**At each event:** switch on the Jetpack and wait for signal, then the Pi, then open the booth on the iPad.

- Power the Jetpack from its own charger or a power bank, never from the Pi.
- Email uses about 2 MB per guest. The camera preview doesn't use mobile data.
- Forget a network with `./deploy.sh wifi remove "Name"`.

---

## Email setup (one time, about 2 minutes)

The booth sends email from a Gmail account using an **app password**. That's a
16-letter password Google makes just for the booth. It can only send mail, can't
be used to log in to your account, and can be revoked at any time.

You might want a dedicated Gmail account for the booth (e.g.
`ourphotobooth@gmail.com`), so guests don't see your personal address.

1. **Turn on 2-Step Verification** for the account, if it isn't already. Go to
   <https://myaccount.google.com/security> → **2-Step Verification**. App
   passwords only exist for accounts with it switched on.
2. **Create the app password:** go to <https://myaccount.google.com/apppasswords>.
   Type `Photo Booth` as the name and click **Create**. Copy the 16 letters it shows.
3. **Add it to the booth:** run `./deploy.sh config` and fill in:
   ```
   GMAIL_ADDRESS=ourphotobooth@gmail.com
   GMAIL_APP_PASSWORD=abcd efgh ijkl mnop


   ```
   Save with **Ctrl+O**, **Enter**, then **Ctrl+X**. The booth restarts.
4. **Test it:** `./deploy.sh test-email you@example.com`, or use the admin page (Settings → Send a test email).

There are four email designs: **Classic card**, **Minimal**, **Bold** and **Elegant**. Each one shows the strip inside the
email and attaches the full-size strip and photos. Preview them and choose one on the admin page, or set
`EMAIL_TEMPLATE` in `.env`.

Until both settings are filled in, the Email button is hidden.

> **Google Workspace account** (e.g. a work address)? Your admin may have
> switched app passwords off. A free @gmail.com account always works.

**No internet at an event?** Emails are queued on the Pi and sent
automatically when the internet comes back, even after a restart. Guests see
"We'll email you as soon as we're back online."

Gmail allows about 500 emails a day, which is plenty for an event.

---

## Printing (optional)

Printing goes through CUPS, the standard Linux printing system. Dye-sublimation
printers such as the Canon SELPHY CP1500 are popular for photo booths. Any printer
that works with CUPS will do.

1. Open a terminal on the Pi with `./deploy.sh ssh`. Then:
   ```bash
   sudo apt install -y cups printer-driver-gutenprint
   sudo usermod -aG lpadmin pi        # let the pi user manage printers
   sudo cupsctl --remote-admin        # allow the CUPS web page from other computers
   sudo reboot
   ```
2. Connect the printer by USB, or put it on the same Wi-Fi.
3. On the Mac, open <http://photobooth.local:631>. Go to **Administration → Add Printer**,
   log in as `pi`, pick your printer and driver, and choose 4x6 / postcard paper.
4. Find the printer's name and test it. Run these on the Pi (via `./deploy.sh ssh`):
   ```bash
   lpstat -p                                     # e.g. "printer Canon_SELPHY_CP1500 is idle"
   lp -d Canon_SELPHY_CP1500 -o media=4x6 -o fit-to-page ~/photobooth/data/prints/*.jpg
   ```
5. Run `./deploy.sh config` and set:
   ```
   PRINT_ENABLED=true
   PRINTER_NAME=Canon_SELPHY_CP1500
   ```

Each print is a 4×6 sheet with **two copies of the strip side by side**. Cut it
down the middle (there are small marks at the top and bottom).

---

## Setting up the iPad

1. **Open the booth:** in Safari, go to **http://photobooth.local:8000**.
   - If that doesn't load, run `hostname -I` on the Pi and use that address, e.g. `http://192.168.1.42:8000`.
2. **Make it full screen:** tap Share → **Add to Home Screen**. Open the booth from the new
   icon. It runs without Safari's address bar.
3. **Never sleep:** go to Settings → Display & Brightness → **Auto-Lock → Never**.
4. **Lock it to the booth with Guided Access:**
   - Go to Settings → Accessibility → **Guided Access**. Turn it on and set a passcode.
   - Open the booth, then **triple-click the top (or home) button** and tap **Start**.
   - Guests can't leave the app. To exit, triple-click and enter the passcode.
5. Mount the webcam just above the iPad, so people look at the camera while watching the screen.
6. Turn the volume up for the countdown beeps and shutter sound.

---

## Making it yours

| What | How |
|---|---|
| Text on the strip | `STRIP_TITLE=Sam & Alex`, plus `STRIP_DATE_FORMAT`. Guests can type their own unless `ALLOW_CUSTOM_CAPTION=false` |
| Strip designs | On the admin page (Strip designs tab). `STRIP_THEME` sets the starting one |
| Timers | `STYLE_TIMEOUT_SECONDS` (time to choose a look), `COUNTDOWN_SECONDS` (before the first photo), `BETWEEN_SHOTS_SECONDS`, `REVIEW_TIMEOUT_SECONDS` |
| Email text | `EMAIL_SUBJECT`, `EMAIL_MESSAGE`, `EMAIL_FROM_NAME` (the name guests see as the sender) |
| Mirror the preview | `MIRROR=true` (off by default, so text reads the right way) |
| **Your own frames** | Upload transparent PNGs (ideally **1080 × 960 px**) on the admin page. Or put them in `overlays/` on the Mac and `./deploy.sh`: name them `06-my_frame.png` to control the order, and the button says "My Frame". |
| New filters | Add a function to `filters.py` and list it in `FILTERS` at the bottom |

Every setting is explained in [.env.example](.env.example).

---

## Where the photos go

On the Pi, in `~/photobooth/data/`, all with timestamped names:

| Folder | Contents |
|---|---|
| `originals/` | untouched camera frames, 1920×1080 |
| `photos/` | finished photos (cropped, filter, frame), 1215×1080 |
| `strips/` | the 2×6 strips, 600×1800 at 300 DPI |
| `prints/` | the 4×6 print sheets |
| `queue/` | emails waiting for internet (`failed/` = gave up) |

To copy everything to the Mac:
`rsync -av pi@photobooth.local:photobooth/data/ ~/Desktop/booth-photos/`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| iPad says "Can't reach the photo booth" | Is the Pi on? Is the iPad on the same Wi-Fi? Try the IP address instead of `photobooth.local`. |
| "Camera not connected" | Check the USB cable. It reconnects by itself within ~5 s. |
| No Email button | `GMAIL_ADDRESS` or `GMAIL_APP_PASSWORD` is empty: `./deploy.sh config`. |
| Emails don't arrive | Run `./deploy.sh test-email you@example.com`. It says exactly what Gmail objected to. Check the guest's spam folder too. |
| "We'll email you as soon as we're back online" | The Pi has no internet. Emails are queued and will go out later. |
| Preview is jerky | Lower `PREVIEW_WIDTH` (e.g. `640`) or `PREVIEW_FPS` (e.g. `15`). Check `vcgencmd get_throttled` shows `0x0`. |
| Forgot the admin password | `./deploy.sh admin` shows it. |
| Anything else | The admin page's **System** tab (or `./deploy.sh logs`) shows what the booth is doing, including error details. |

---

## Project layout

```
Photo Booth App/
├── app.py              The booth web server (port 8000): pages, live stream, API
├── admin.py            The admin page server (port 8001)
├── ai_prompts.py       Prompts for making frames and strip designs with an AI model
├── booth.py            Sessions: capture, restyle, strip design, email, print
├── camera.py           Reads the webcam in the background, reconnects if unplugged
├── filters.py          The colour filters (same code for preview and photos)
├── imaging.py          Cropping, frames, strip and print layout
├── jobs.py             Background queue: emails wait and retry when offline
├── mailer.py           Sends email through Gmail (app password)
├── emails.py           The four email designs
├── printer.py          Printing through CUPS
├── config.py           Loads every setting from .env
├── .env.example        All settings, explained
├── requirements.txt    Python packages
├── deploy.sh           Run on the Mac: deploy, logs, config, test-email...
├── deploy/
│   ├── pi_setup.sh         Run on the Pi by deploy.sh: installs and restarts
│   ├── wifi.sh             Run on the Pi by ./deploy.sh wifi: saved Wi-Fi networks
│   └── photobooth.service  Starts the booth at power-on (systemd)
├── templates/          index.html (the iPad) and admin.html (the admin page)
├── static/             CSS, JavaScript and fonts (all free licences, see static/fonts)
├── overlays/           Frame PNGs (make more with tools/make_sample_overlays.py)
└── tools/              Helper scripts
```
