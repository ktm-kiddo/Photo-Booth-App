#!/usr/bin/env bash
# =============================================================================
#  Push the photo booth to the Pi.  Run from Terminal on the Mac:
#
#      ./deploy.sh            copy changes to the Pi and restart the app
#      ./deploy.sh logs       watch the app's live log (Ctrl+C to quit)
#      ./deploy.sh config     edit the settings (.env) on the Pi, then restart
#      ./deploy.sh restart    just restart the app
#      ./deploy.sh ssh        open a terminal on the Pi
#      ./deploy.sh test-email you@example.com   check that email works
#      ./deploy.sh admin      show the admin page address and password
#      ./deploy.sh wifi       see and add Wi-Fi networks the Pi joins (e.g. the Jetpack)
#      ./deploy.sh wifi remove "Name"   forget a Wi-Fi network
#
#  The first deploy sets up everything on the Pi (Python packages, settings
#  file, auto-start on boot). Later deploys only copy what changed and take
#  a few seconds. The iPad reloads the page by itself afterwards.
#
#  These stay untouched on the Pi:  .env (settings and the Gmail app
#  password), venv/, data/ (photos)
#
#  Different Pi address?   PI=pi@192.168.1.50 ./deploy.sh
# =============================================================================
set -euo pipefail

PI="${PI:-pi@photobooth.local}"
DEST="photobooth"                     # folder in the Pi user's home: ~/photobooth
HOST="${PI#*@}"                       # just the address part, e.g. photobooth.local

cd "$(dirname "$0")"                  # run from the project folder

# Reuse one SSH connection for every step below, so it's fast and you're
# asked for a password at most once (and never, after the first run).
SSH_OPTS=(
  -o ControlMaster=auto
  -o "ControlPath=/tmp/photobooth-ssh-%C"
  -o ControlPersist=60
  -o ConnectTimeout=10
  -o StrictHostKeyChecking=accept-new
)
pi() { ssh "${SSH_OPTS[@]}" "$PI" "$@"; }       # run a command on the Pi
pi_tty() { ssh -t "${SSH_OPTS[@]}" "$PI" "$@"; } # same, for interactive commands

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- Make sure we can log in to the Pi without a password --------------------
ensure_ssh() {
  local output
  if output="$(ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$PI" true 2>&1)"; then
    return 0   # key login works
  fi

  if [[ "$output" == *"Permission denied"* ]]; then
    say "Setting up password-free login to the Pi (one time only)"
    if [ ! -f "$HOME/.ssh/id_ed25519.pub" ]; then
      ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -q
    fi
    echo "Enter the Pi's password one last time:"
    ssh-copy-id -i "$HOME/.ssh/id_ed25519.pub" -o StrictHostKeyChecking=accept-new "$PI"
    return 0
  fi

  echo
  echo "Can't reach the Pi at $PI:"
  echo "  $output"
  echo
  echo "Check that the Pi is switched on and on the same Wi-Fi as this Mac."
  echo "If photobooth.local doesn't work, use its IP address:  PI=pi@192.168.x.x ./deploy.sh"
  exit 1
}

# --- Read the app's port from the Pi's .env (for printing the URL) -----------
pi_port() {
  local port
  port="$(pi "grep -E '^PORT=' $DEST/.env 2>/dev/null | tail -n1 | cut -d= -f2 | sed 's/#.*//'" | tr -d '[:space:]' || true)"
  echo "${port:-8000}"
}

pi_admin_port() {
  local port
  port="$(pi "grep -E '^ADMIN_PORT=' $DEST/.env 2>/dev/null | tail -n1 | cut -d= -f2 | sed 's/#.*//'" | tr -d '[:space:]' || true)"
  echo "${port:-8001}"
}

show_admin() {
  local password
  password="$(pi "grep -E '^ADMIN_PASSWORD=' $DEST/.env | tail -n1 | cut -d= -f2-" | tr -d '"' || true)"
  echo "Admin page:  http://$HOST:$(pi_admin_port)"
  echo "User name:   admin"
  echo "Password:    ${password:-(not set yet; run ./deploy.sh)}"
}

# --- Wi-Fi networks the Pi remembers -------------------------------------------
# Networks are saved, not joined straight away, so the Pi stays on the current
# Wi-Fi and this Mac keeps its connection. It joins a saved network by itself
# whenever that one is in range (see deploy/wifi.sh).
wifi() {
  ensure_ssh
  # Make sure the Pi has the latest helper script, even before a full deploy.
  pi "mkdir -p $DEST/deploy"
  scp -q "${SSH_OPTS[@]}" deploy/wifi.sh "$PI:$DEST/deploy/wifi.sh"

  if [ "${1:-}" = "remove" ]; then
    local ssid="${2:-}"
    [ -n "$ssid" ] || read -r -p "Name of the network to forget: " ssid
    printf '%s\n' "$ssid" | pi "bash $DEST/deploy/wifi.sh remove"
    return
  fi

  pi "bash $DEST/deploy/wifi.sh list"
  echo
  local ssid password prefer
  read -r -p "Network to add (type its name exactly, or press Enter to stop): " ssid
  [ -n "$ssid" ] || return 0
  read -r -s -p "Password for \"$ssid\" (hidden; press Enter if it has none): " password
  echo
  echo "If the Pi can see this network and another saved one at the same time, which should it use?"
  read -r -p "Prefer \"$ssid\"? [y/N] " prefer
  prefer="$(printf '%s' "$prefer" | tr '[:upper:]' '[:lower:]' | cut -c1)"
  # The password goes to the Pi through the SSH connection's input, so it
  # never appears in a command line.
  printf '%s\n%s\n%s\n' "$ssid" "$password" "$prefer" | pi "bash $DEST/deploy/wifi.sh add"
}

# --- Commands ----------------------------------------------------------------
deploy() {
  ensure_ssh

  say "Copying files to $PI:~/$DEST"
  # --delete removes files from the Pi that you deleted on the Mac, so the
  # Pi always matches this folder exactly. Excluded paths are never deleted.
  rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude '.env' \
    --exclude 'venv/' \
    --exclude 'data/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    ./ "$PI:$DEST/"

  # Install/update anything needed and restart the app (see deploy/pi_setup.sh).
  pi_tty "bash $DEST/deploy/pi_setup.sh"

  say "Done!"
  echo "  Booth (iPad):  http://$HOST:$(pi_port)"
  echo "  Admin page:    http://$HOST:$(pi_admin_port)   (./deploy.sh admin shows the password)"
}

case "${1:-deploy}" in
  deploy)  deploy ;;
  logs)    ensure_ssh; pi_tty "journalctl -u photobooth -f -n 50" ;;
  config)  ensure_ssh; pi_tty "nano $DEST/.env && sudo systemctl restart photobooth && echo 'Settings saved, photo booth restarted.'" ;;
  restart) ensure_ssh; pi "sudo systemctl restart photobooth" && echo "Photo booth restarted." ;;
  ssh)     ensure_ssh; pi_tty "cd $DEST; exec bash -l" ;;
  admin)   ensure_ssh; show_admin ;;
  wifi)    wifi "${2:-}" "${3:-}" ;;
  test-email)
    [ -n "${2:-}" ] || { echo "Usage: ./deploy.sh test-email you@example.com"; exit 1; }
    ensure_ssh; pi "cd $DEST && venv/bin/python mailer.py '${2//\'/}'" ;;
  *)       sed -n '3,13p' "$0"; exit 1 ;;   # print the usage lines from the top of this file
esac
