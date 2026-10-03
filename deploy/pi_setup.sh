#!/usr/bin/env bash
# =============================================================================
#  Runs ON THE PI. Gets everything ready and (re)starts the photo booth.
#
#  You normally never run this yourself: ./deploy.sh on the Mac runs it after
#  every copy. It's safe to run any number of times, because each step
#  checks whether it's needed and skips itself if not.
#  To run it by hand:   bash ~/photobooth/deploy/pi_setup.sh
# =============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # e.g. /home/pi/photobooth
APP_USER="$(id -un)"                          # e.g. pi
SERVICE_NAME="photobooth"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

# System packages the app needs (installed with apt).
APT_PACKAGES="python3-venv python3-pip python3-dev v4l-utils"

cd "$APP_DIR"
step() { echo "==> $*"; }

# --- 1. System packages (only installs what's missing) -----------------------
# shellcheck disable=SC2086
if ! dpkg -s $APT_PACKAGES >/dev/null 2>&1; then
  step "Installing system packages: $APT_PACKAGES"
  sudo apt-get update -q
  # shellcheck disable=SC2086
  sudo apt-get install -y -q $APT_PACKAGES
fi

# --- 2. Python virtual environment (created once) ----------------------------
if [ ! -x venv/bin/python ]; then
  step "Creating Python virtual environment"
  python3 -m venv venv
fi

# --- 3. Python packages (only when requirements.txt has changed) -------------
# We store a fingerprint (hash) of requirements.txt after installing. If the
# file's fingerprint differs next time, something changed, so install again.
REQ_HASH="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat venv/.requirements-hash 2>/dev/null || true)" != "$REQ_HASH" ]; then
  step "Installing Python packages (this can take a few minutes)"
  venv/bin/pip install --quiet --upgrade pip
  venv/bin/pip install --quiet -r requirements.txt
  echo "$REQ_HASH" > venv/.requirements-hash
fi

# --- 4. Settings file (created once, never overwritten) ----------------------
if [ ! -f .env ]; then
  step "Creating .env from .env.example (edit it with: ./deploy.sh config)"
  cp .env.example .env
fi

# The admin page needs a password. Make a random one the first time.
if ! grep -qE '^ADMIN_PASSWORD=.+' .env; then
  NEW_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
  if grep -qE '^ADMIN_PASSWORD=' .env; then
    sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$NEW_PASSWORD|" .env
  else
    printf '\n# Admin page password (user name: admin)\nADMIN_PASSWORD=%s\n' "$NEW_PASSWORD" >> .env
  fi
  step "Created an admin password: $NEW_PASSWORD   (see it again with: ./deploy.sh admin)"
fi

# New settings added in an update aren't in your existing .env. The app
# uses their defaults; list them so you know they exist.
MISSING="$(comm -23 <(grep -oE '^[A-Z_]+=' .env.example | sort -u) <(grep -oE '^[A-Z_]+=' .env | sort -u) | tr -d '=' | tr '\n' ' ')"
if [ -n "$MISSING" ]; then
  step "New settings available (using defaults): $MISSING"
  echo "    To change them: ./deploy.sh config  (copy the lines you want from .env.example)"
fi

# Keep the system log across restarts, so after an unexpected switch-off
# "journalctl -b -1" can show what happened before it.
if [ ! -d /var/log/journal ]; then
  step "Keeping the system log across restarts"
  sudo mkdir -p /var/log/journal
  sudo systemd-tmpfiles --create --prefix /var/log/journal
  sudo systemctl restart systemd-journald
fi

# --- 5. Auto-start service (installed/updated only when it changes) ----------
NEW_SERVICE="$(mktemp)"
sed -e "s|__USER__|$APP_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" \
  deploy/photobooth.service > "$NEW_SERVICE"
if ! cmp -s "$NEW_SERVICE" "$SERVICE_FILE"; then
  step "Installing auto-start service"
  sudo install -m 644 "$NEW_SERVICE" "$SERVICE_FILE"
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME" --quiet
fi
rm -f "$NEW_SERVICE"

# --- 6. Restart the app so it runs the new code ------------------------------
step "Restarting the photo booth"
sudo systemctl restart "$SERVICE_NAME"

# --- 7. Check it really started ----------------------------------------------
PORT="$(grep -E '^PORT=' .env | tail -n1 | cut -d= -f2 | sed 's/#.*//' | tr -d '[:space:]' || true)"
PORT="${PORT:-8000}"
for _ in $(seq 1 60); do   # wait up to 30 s
  if venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/status', timeout=1)" 2>/dev/null; then
    step "Photo booth is running ✔"
    exit 0
  fi
  sleep 0.5
done

echo
echo "!!! The photo booth didn't start. Last lines of its log:"
sudo journalctl -u "$SERVICE_NAME" -n 30 --no-pager
exit 1
