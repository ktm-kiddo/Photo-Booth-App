#!/usr/bin/env bash
# =============================================================================
#  Runs ON THE PI. Manages the Wi-Fi networks the Pi remembers.
#  Don't run it by hand: use ./deploy.sh wifi on the Mac.
#
#      bash wifi.sh list      saved networks, the current one, and nearby ones
#      bash wifi.sh add       reads 3 lines from the Mac: name, password, prefer (y/n)
#      bash wifi.sh remove    reads 1 line: the network name
#
#  Networks are *saved*, not joined straight away: the Pi stays on its
#  current Wi-Fi (so the Mac doesn't lose it) and joins a saved network by
#  itself whenever it's in range, e.g. the Jetpack at an event.
# =============================================================================
set -euo pipefail

# Saved Wi-Fi connections as "connection-name<TAB>network-name<TAB>priority".
saved_networks() {
  nmcli -t -f NAME,TYPE connection show | while IFS=: read -r name type; do
    [ "$type" = "802-11-wireless" ] || continue
    ssid="$(nmcli -g 802-11-wireless.ssid connection show "$name" 2>/dev/null || true)"
    priority="$(nmcli -g connection.autoconnect-priority connection show "$name" 2>/dev/null || echo 0)"
    printf '%s\t%s\t%s\n' "$name" "${ssid:-$name}" "${priority:-0}"
  done
}

current_network() {
  nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | awk -F: '$1 == "yes" { print $2; exit }'
}

# The saved connection for a network name, if there is one.
connection_for() {
  saved_networks | awk -F'\t' -v ssid="$1" '$2 == ssid { print $1; exit }'
}

case "${1:-list}" in
  list)
    echo "Saved Wi-Fi networks (the Pi joins whichever one is in range):"
    found=0
    while IFS=$'\t' read -r _ ssid priority; do
      found=1
      if [ "${priority:-0}" -gt 0 ]; then echo "  • $ssid   (preferred)"; else echo "  • $ssid"; fi
    done < <(saved_networks)
    [ "$found" = 1 ] || echo "  (none)"
    echo "Connected now: $(current_network || true)"
    echo
    echo "Networks the Pi can see right now:"
    sudo nmcli -t -f SSID,SIGNAL dev wifi list --rescan yes 2>/dev/null \
      | awk -F: '$1 != "" && !seen[$1]++ { printf "  %-34s signal %s%%\n", $1, $2 }' | head -15
    ;;

  add)
    IFS= read -r ssid || true
    IFS= read -r password || true
    IFS= read -r prefer || true
    [ -n "$ssid" ] || { echo "No network name given."; exit 1; }
    priority=0
    [ "$prefer" = "y" ] && priority=10

    existing="$(connection_for "$ssid")"
    if [ -n "$existing" ]; then
      # Already saved: update it in place (safe even if the Pi is using it now).
      if [ -n "$password" ]; then
        sudo nmcli connection modify "$existing" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$password"
      fi
      sudo nmcli connection modify "$existing" connection.autoconnect yes connection.autoconnect-priority "$priority"
      echo "Updated \"$ssid\"."
    elif [ -n "$password" ]; then
      sudo nmcli connection add type wifi con-name "$ssid" ifname wlan0 ssid "$ssid" \
        wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$password" \
        connection.autoconnect yes connection.autoconnect-priority "$priority" >/dev/null
      echo "Saved \"$ssid\"."
    else
      sudo nmcli connection add type wifi con-name "$ssid" ifname wlan0 ssid "$ssid" \
        connection.autoconnect yes connection.autoconnect-priority "$priority" >/dev/null
      echo "Saved \"$ssid\" (no password)."
    fi
    echo "The Pi will join it by itself whenever it's in range and its current Wi-Fi isn't."
    ;;

  remove)
    IFS= read -r ssid || true
    existing="$(connection_for "$ssid")"
    [ -n "$existing" ] || { echo "\"$ssid\" isn't a saved network."; exit 1; }
    if [ "$(current_network || true)" = "$ssid" ]; then
      echo "The Pi is using \"$ssid\" right now, so removing it would cut this connection."
      echo "Remove it while the Pi is on a different network."
      exit 1
    fi
    sudo nmcli connection delete "$existing" >/dev/null
    echo "Removed \"$ssid\"."
    ;;

  *)
    echo "Usage: wifi.sh list | add | remove"
    exit 1
    ;;
esac
