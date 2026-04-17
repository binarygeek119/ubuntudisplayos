#!/usr/bin/env bash
# setup-dual-kiosk.sh - Ubuntu Server/Desktop 24.04 dual-display kiosk.
# Usage (as root): sudo ./setup-dual-kiosk.sh
# By default picks any two connected outputs from xrandr (OUTPUT_LEFT/OUTPUT_RIGHT=auto).

set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo $0 ..." >&2; exit 1; }

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: sudo $0" >&2
  echo "URLs are read from /home/kiosk/.config/kiosk-urls/left.txt and right.txt" >&2
  exit 1
fi

KIOSK_USER="${KIOSK_USER:-kiosk}"
KIOSK_HOME="/home/${KIOSK_USER}"

# Defaults: first two connected outputs (auto); MODE=auto uses each panel's preferred mode.
OUTPUT_LEFT="${OUTPUT_LEFT:-auto}"
OUTPUT_RIGHT="${OUTPUT_RIGHT:-auto}"
MODE="${MODE:-auto}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1024}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-768}"

export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y --no-install-recommends \
  xorg openbox lightdm lightdm-gtk-greeter \
  x11-xserver-utils unclutter-xfixes dbus-x11 wget ca-certificates \
  python3-minimal openssl

# Keep kiosk systems always awake: disable suspend/hibernate/idle sleep globally.
install -d /etc/systemd/logind.conf.d
cat >/etc/systemd/logind.conf.d/99-kiosk-no-sleep.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
IdleAction=ignore
IdleActionSec=0
EOF
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl restart systemd-logind

# Google Chrome (stable kiosk); fall back to Chromium from apt if needed.
CHROME_BIN=""
tmpdeb="$(mktemp --suffix=.deb /tmp/chrome.XXXXXX)"
if wget -qO "$tmpdeb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; then
  apt-get install -y "$tmpdeb" || apt-get -f install -y
  rm -f "$tmpdeb"
else
  rm -f "$tmpdeb"
  apt-get install -y chromium-browser 2>/dev/null || apt-get install -y chromium
fi
if [[ -x /usr/bin/google-chrome-stable ]]; then CHROME_BIN="/usr/bin/google-chrome-stable"
elif [[ -x /usr/bin/chromium ]]; then CHROME_BIN="/usr/bin/chromium"
elif [[ -x /usr/bin/chromium-browser ]]; then CHROME_BIN="/usr/bin/chromium-browser"
else
  echo "Could not install Google Chrome or Chromium." >&2
  exit 1
fi

if ! id -u "$KIOSK_USER" &>/dev/null; then
  adduser --disabled-password --gecos "Kiosk" "$KIOSK_USER"
fi
usermod -aG video,audio "$KIOSK_USER" 2>/dev/null || usermod -aG video "$KIOSK_USER"
# Some LightDM PAM stacks require these groups for passwordless autologin.
getent group autologin >/dev/null 2>&1 || groupadd --system autologin
getent group nopasswdlogin >/dev/null 2>&1 || groupadd --system nopasswdlogin
usermod -aG autologin,nopasswdlogin "$KIOSK_USER"
# If autologin fails, --disabled-password leaves no greeter password → you are stuck. Always set a fallback password.
KIOSK_PASS="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)"
echo "${KIOSK_USER}:${KIOSK_PASS}" | chpasswd
usermod -U "$KIOSK_USER" 2>/dev/null || true
umask 077
{
  printf '%s\n' "Greeter fallback (if LightDM does not autologin):"
  printf '  user: %s\n' "$KIOSK_USER"
  printf '  password: %s\n' "$KIOSK_PASS"
  printf '%s\n' "Remove this file after autologin works: rm -f /root/kiosk-greeter-password.txt"
} >/root/kiosk-greeter-password.txt
chmod 600 /root/kiosk-greeter-password.txt
umask 022

# Session id must match basename of /usr/share/xsessions/*.desktop (usually openbox).
XSESSION_ID="openbox"
while IFS= read -r -d '' f; do
  if grep -qi 'openbox' "$f" 2>/dev/null; then
    XSESSION_ID="$(basename "$f" .desktop)"
    break
  fi
done < <(find /usr/share/xsessions -maxdepth 1 -name '*.desktop' -print0 2>/dev/null || true)

install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$KIOSK_HOME/.config/openbox"
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$KIOSK_HOME/.config/kiosk-urls"

# Migrate old single-file URL config if present.
if [[ -s "$KIOSK_HOME/.config/kiosk-url/display.txt" ]] && [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/left.txt" ]]; then
  head -n 2 "$KIOSK_HOME/.config/kiosk-url/display.txt" >"$KIOSK_HOME/.config/kiosk-urls/left.txt" || true
fi
if [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/left.txt" ]]; then
  {
    printf '%s\n' "http://127.0.0.1:9877"
    printf '%s\n' "normal"
  } >"$KIOSK_HOME/.config/kiosk-urls/left.txt"
fi
if [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/right.txt" ]]; then
  {
    printf '%s\n' "http://127.0.0.1:9876"
    printf '%s\n' "normal"
  } >"$KIOSK_HOME/.config/kiosk-urls/right.txt"
fi
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config/kiosk-urls/left.txt" "$KIOSK_HOME/.config/kiosk-urls/right.txt"
chmod 600 "$KIOSK_HOME/.config/kiosk-urls/left.txt" "$KIOSK_HOME/.config/kiosk-urls/right.txt"

{
  printf '# OUTPUT_LEFT / OUTPUT_RIGHT: output names from xrandr, or "auto" to pick any two connected screens.\n'
  printf '# URL files (line1=URL, line2=rotation normal|left|right|inverted):\n'
  printf '#   %s\n' "$KIOSK_HOME/.config/kiosk-urls/left.txt"
  printf '#   %s\n' "$KIOSK_HOME/.config/kiosk-urls/right.txt"
  printf 'URL_LEFT_FILE=%q\n' "$KIOSK_HOME/.config/kiosk-urls/left.txt"
  printf 'URL_RIGHT_FILE=%q\n' "$KIOSK_HOME/.config/kiosk-urls/right.txt"
  printf 'OUTPUT_LEFT=%q\n' "$OUTPUT_LEFT"
  printf 'OUTPUT_RIGHT=%q\n' "$OUTPUT_RIGHT"
  printf 'MODE=%q\n' "$MODE"
  printf 'SCREEN_WIDTH=%q\n' "$SCREEN_WIDTH"
  printf 'SCREEN_HEIGHT=%q\n' "$SCREEN_HEIGHT"
  printf 'CHROME_BIN=%q\n' "$CHROME_BIN"
} >"$KIOSK_HOME/.config/kiosk.env"
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config/kiosk.env"
chmod 600 "$KIOSK_HOME/.config/kiosk.env"

cat >"$KIOSK_HOME/.config/openbox/autostart" <<'AUTOSTART'
#!/bin/bash
set -euo pipefail
set -a
# shellcheck source=/dev/null
. "${HOME}/.config/kiosk.env"
set +a

xset s off
xset -dpms
xset s noblank

unclutter -idle 0 -root &
xsetroot -cursor_name none 2>/dev/null || true

LOG_FILE="${HOME}/.config/kiosk-autostart.log"
touch "$LOG_FILE"
exec >>"$LOG_FILE" 2>&1
echo "[$(date '+%F %T')] kiosk autostart started"

CHROME_FLAGS=(
  --kiosk --noerrdialogs --disable-infobars --no-first-run
  --disable-session-crashed-bubble --disable-restore-session-state
  --disable-features=TranslateUI
  --new-window
)
CHROME_LEFT_PROFILE="${HOME}/.config/chrome-kiosk-left"
CHROME_RIGHT_PROFILE="${HOME}/.config/chrome-kiosk-right"
mkdir -p "$CHROME_LEFT_PROFILE" "$CHROME_RIGHT_PROFILE"

resolve_browser_bin() {
  if [[ -n "${CHROME_BIN:-}" && -x "${CHROME_BIN}" ]]; then
    return 0
  fi
  for candidate in /usr/bin/google-chrome-stable /usr/bin/chromium /usr/bin/chromium-browser; do
    if [[ -x "$candidate" ]]; then
      CHROME_BIN="$candidate"
      echo "[$(date '+%F %T')] using browser: ${CHROME_BIN}"
      return 0
    fi
  done
  echo "[$(date '+%F %T')] ERROR: no chrome/chromium binary found"
  return 1
}

read_kiosk_state() {
  URL_LEFT="$(awk 'NF {gsub(/\r/, ""); print; exit}' "${URL_LEFT_FILE}")"
  URL_RIGHT="$(awk 'NF {gsub(/\r/, ""); print; exit}' "${URL_RIGHT_FILE}")"
  ROTATE_LEFT="$(awk 'NF {c++; if (c==2) {gsub(/\r/, ""); print tolower($0); exit}}' "${URL_LEFT_FILE}")"
  ROTATE_RIGHT="$(awk 'NF {c++; if (c==2) {gsub(/\r/, ""); print tolower($0); exit}}' "${URL_RIGHT_FILE}")"
  ROTATE_LEFT="${ROTATE_LEFT:-normal}"
  ROTATE_RIGHT="${ROTATE_RIGHT:-normal}"
  case "$ROTATE_LEFT" in normal|left|right|inverted) ;; *) ROTATE_LEFT="normal" ;; esac
  case "$ROTATE_RIGHT" in normal|left|right|inverted) ;; *) ROTATE_RIGHT="normal" ;; esac
}

is_auto_output() {
  case "${1:-}" in ''|auto|detect|any) return 0 ;; *) return 1 ;; esac
}

detect_outputs() {
  mapfile -t _xr < <(xrandr --query | awk '/ connected/{print $1}')
  local n="${#_xr[@]}"
  echo "[$(date '+%F %T')] connected outputs (${n}): $(printf '%s ' "${_xr[@]}")"
  if [[ "$n" -lt 2 ]]; then
    echo "[$(date '+%F %T')] ERROR: need 2 connected displays (found ${n})"
    return 1
  fi
  if is_auto_output "${OUTPUT_LEFT}"; then
    if is_auto_output "${OUTPUT_RIGHT}"; then
      OUTPUT_LEFT="${_xr[0]}"
    else
      OUTPUT_LEFT=""
      for c in "${_xr[@]}"; do
        if [[ "$c" != "${OUTPUT_RIGHT}" ]]; then
          OUTPUT_LEFT="$c"
          break
        fi
      done
      [[ -z "${OUTPUT_LEFT}" ]] && OUTPUT_LEFT="${_xr[0]}"
    fi
  fi
  if is_auto_output "${OUTPUT_RIGHT}"; then
    OUTPUT_RIGHT=""
    for c in "${_xr[@]}"; do
      if [[ "$c" != "${OUTPUT_LEFT}" ]]; then
        OUTPUT_RIGHT="$c"
        break
      fi
    done
    [[ -z "${OUTPUT_RIGHT}" ]] && OUTPUT_RIGHT="${_xr[1]}"
  fi
  echo "[$(date '+%F %T')] using: left=${OUTPUT_LEFT} right=${OUTPUT_RIGHT}"
  return 0
}

# Parse WxH+X+Y from `xrandr --query` line for a connected output (e.g. 1440x900+0+0).
parse_output_geom() {
  local out="$1"
  local tok
  tok="$(xrandr --query | awk -v o="$out" '$1==o && /connected/ { for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+x[0-9]+\*?\+[0-9]+\+[0-9]+$/) { print $i; exit } }')"
  if [[ -z "$tok" ]]; then
    return 1
  fi
  tok="${tok//\*}"
  GEO_W="${tok%%x*}"
  local rest="${tok#*x}"
  GEO_H="${rest%%+*}"
  local rest2="${rest#*+}"
  GEO_X="${rest2%%+*}"
  GEO_Y="${rest2#*+}"
  return 0
}

refresh_chrome_geometry() {
  GEO_L_X=0 GEO_L_Y=0 GEO_L_W="${SCREEN_WIDTH}" GEO_L_H="${SCREEN_HEIGHT}"
  GEO_R_X="${SCREEN_WIDTH}" GEO_R_Y=0 GEO_R_W="${SCREEN_WIDTH}" GEO_R_H="${SCREEN_HEIGHT}"
  case "$ROTATE_LEFT" in left|right) GEO_L_W="${SCREEN_HEIGHT}"; GEO_L_H="${SCREEN_WIDTH}" ;; esac
  case "$ROTATE_RIGHT" in left|right) GEO_R_W="${SCREEN_HEIGHT}"; GEO_R_H="${SCREEN_WIDTH}" ;; esac

  if parse_output_geom "${OUTPUT_LEFT}"; then
    GEO_L_W="$GEO_W"
    GEO_L_H="$GEO_H"
    GEO_L_X="$GEO_X"
    GEO_L_Y="$GEO_Y"
  fi
  if parse_output_geom "${OUTPUT_RIGHT}"; then
    GEO_R_W="$GEO_W"
    GEO_R_H="$GEO_H"
    GEO_R_X="$GEO_X"
    GEO_R_Y="$GEO_Y"
  fi
  echo "[$(date '+%F %T')] layout ${OUTPUT_LEFT}=${GEO_L_W}x${GEO_L_H}+${GEO_L_X}+${GEO_L_Y} ${OUTPUT_RIGHT}=${GEO_R_W}x${GEO_R_H}+${GEO_R_X}+${GEO_R_Y}"
}

launch_kiosk() {
  if [[ -z "${URL_LEFT}" || -z "${URL_RIGHT}" ]]; then
    echo "[$(date '+%F %T')] waiting: URL file empty"
    return 1
  fi
  if ! resolve_browser_bin; then
    return 1
  fi
  if ! detect_outputs; then
    return 1
  fi

  if [[ -n "${OUTPUT_LEFT:-}" && -n "${OUTPUT_RIGHT:-}" ]]; then
    # --right-of avoids mirrored +0+0 when MODE=auto picks different sizes than SCREEN_*.
    if [[ "${MODE}" == "auto" || "${MODE}" == "default" ]]; then
      xrandr --output "$OUTPUT_LEFT" --auto --rotate "$ROTATE_LEFT" --primary --pos 0x0 \
             --output "$OUTPUT_RIGHT" --auto --rotate "$ROTATE_RIGHT" --right-of "$OUTPUT_LEFT" || true
    else
      xrandr --output "$OUTPUT_LEFT" --mode "$MODE" --rotate "$ROTATE_LEFT" --primary --pos 0x0 \
             --output "$OUTPUT_RIGHT" --mode "$MODE" --rotate "$ROTATE_RIGHT" --right-of "$OUTPUT_LEFT" || true
    fi
    sleep 1
    refresh_chrome_geometry
  else
    refresh_chrome_geometry
  fi

  # Avoid "Opening in existing browser session": kill stale browser processes.
  pkill -9 -f 'google-chrome|chromium' 2>/dev/null || true
  sleep 1

  "$CHROME_BIN" "${CHROME_FLAGS[@]}" \
    --user-data-dir="${CHROME_LEFT_PROFILE}" \
    --window-position="${GEO_L_X},${GEO_L_Y}" \
    --window-size="${GEO_L_W},${GEO_L_H}" \
    "$URL_LEFT" &
  LEFT_PID=$!

  "$CHROME_BIN" "${CHROME_FLAGS[@]}" \
    --user-data-dir="${CHROME_RIGHT_PROFILE}" \
    --window-position="${GEO_R_X},${GEO_R_Y}" \
    --window-size="${GEO_R_W},${GEO_R_H}" \
    "$URL_RIGHT" &
  RIGHT_PID=$!
  echo "[$(date '+%F %T')] launched left=${URL_LEFT} right=${URL_RIGHT} rotate=(${ROTATE_LEFT},${ROTATE_RIGHT})"
  return 0
}

stop_kiosk() {
  if [[ -n "${LEFT_PID:-}" ]]; then kill "$LEFT_PID" 2>/dev/null || true; fi
  if [[ -n "${RIGHT_PID:-}" ]]; then kill "$RIGHT_PID" 2>/dev/null || true; fi
  wait "${LEFT_PID:-}" 2>/dev/null || true
  wait "${RIGHT_PID:-}" 2>/dev/null || true
  LEFT_PID=""
  RIGHT_PID=""
}

(
  LAST_STATE=""
  LEFT_PID=""
  RIGHT_PID=""
  while true; do
    read_kiosk_state
    XR_SIG="$(xrandr --query 2>/dev/null | awk '/ connected/{printf "%s,", $1}' | sed 's/,$//')"
    ENV_SIG="$(stat -c %Y "${HOME}/.config/kiosk.env" 2>/dev/null || echo 0)"
    CURRENT_STATE="${URL_LEFT}|${URL_RIGHT}|${ROTATE_LEFT}|${ROTATE_RIGHT}|${XR_SIG}|${ENV_SIG}"
    LEFT_DEAD=0
    RIGHT_DEAD=0
    if [[ -n "${LEFT_PID}" ]] && ! kill -0 "${LEFT_PID}" 2>/dev/null; then LEFT_DEAD=1; fi
    if [[ -n "${RIGHT_PID}" ]] && ! kill -0 "${RIGHT_PID}" 2>/dev/null; then RIGHT_DEAD=1; fi
    if [[ "$CURRENT_STATE" != "$LAST_STATE" || "$LEFT_DEAD" -eq 1 || "$RIGHT_DEAD" -eq 1 ]]; then
      if [[ "$LEFT_DEAD" -eq 1 || "$RIGHT_DEAD" -eq 1 ]]; then
        echo "[$(date '+%F %T')] detected browser exit; relaunching"
      fi
      stop_kiosk
      if launch_kiosk; then
        LAST_STATE="$CURRENT_STATE"
      fi
    fi
    sleep 2
  done
) &

exit 0
AUTOSTART
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config/openbox/autostart"
chmod 755 "$KIOSK_HOME/.config/openbox/autostart"
chown -R "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config"

install -d /etc/lightdm/lightdm.conf.d
rm -f /etc/lightdm/lightdm.conf.d/50-kiosk-autologin.conf
# 99-* loads late so other snippets do not override autologin.
# Some hardware only honors seat0; duplicate both.
cat >/etc/lightdm/lightdm.conf.d/99-kiosk-autologin.conf <<EOF
[Seat:*]
autologin-user=${KIOSK_USER}
autologin-user-timeout=0
autologin-session=${XSESSION_ID}
user-session=${XSESSION_ID}
autologin-guest=false
greeter-hide-users=false

[Seat:seat0]
autologin-user=${KIOSK_USER}
autologin-user-timeout=0
autologin-session=${XSESSION_ID}
user-session=${XSESSION_ID}
autologin-guest=false
greeter-hide-users=false
EOF

# Server images default to multi-user.target → text login on tty; kiosk needs graphical boot.
systemctl set-default graphical.target

# Ensure LightDM owns the GUI (not GDM/SDDM from other metapackages).
printf '%s\n' '/usr/sbin/lightdm' >/etc/X11/default-display-manager
systemctl disable gdm3 2>/dev/null || true
systemctl disable sddm 2>/dev/null || true
systemctl enable lightdm
systemctl restart lightdm 2>/dev/null || true

# Optional web UI to edit display + URLs (token in /etc/kiosk-webui.env).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEBUI_SRC="${SCRIPT_DIR}/kiosk-webui.py"
install -d /opt/kiosk-webui
if [[ -f "$WEBUI_SRC" ]]; then
  install -m 755 "$WEBUI_SRC" /opt/kiosk-webui/kiosk-webui.py
  WEBUI_TOKEN="$(openssl rand -hex 24)"
  umask 077
  {
    printf 'TOKEN=%s\n' "$WEBUI_TOKEN"
    printf '%s\n' 'BIND=0.0.0.0'
    printf '%s\n' 'PORT=8780'
  } >/etc/kiosk-webui.env
  umask 022
  chown root:kiosk /etc/kiosk-webui.env 2>/dev/null || chown root:root /etc/kiosk-webui.env
  chmod 640 /etc/kiosk-webui.env

  cat >/etc/systemd/system/kiosk-webui.service <<'WEBUISVC'
[Unit]
Description=Kiosk display and URL web config
After=lightdm.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kiosk
Group=kiosk
EnvironmentFile=/etc/kiosk-webui.env
Environment=KIOSK_USER=kiosk
ExecStart=/usr/bin/python3 /opt/kiosk-webui/kiosk-webui.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical.target
WEBUISVC

  systemctl daemon-reload
  systemctl enable kiosk-webui.service
  systemctl restart kiosk-webui.service 2>/dev/null || true

  umask 077
  {
    printf '%s\n' "Kiosk web UI token (also in /etc/kiosk-webui.env):"
    printf '%s\n' "$WEBUI_TOKEN"
    printf '%s\n' "Open: http://THIS_HOST:8780/?token=PASTE_TOKEN"
    printf '%s\n' "Or: curl -H \"X-Kiosk-Token: $WEBUI_TOKEN\" http://127.0.0.1:8780/api/xrandr.json"
  } >/root/kiosk-webui-token.txt
  umask 022
  chmod 600 /root/kiosk-webui-token.txt
else
  echo "Note: kiosk-webui.py not found beside $0 — skipped web UI install." >&2
fi

echo
echo "Done. Default boot target is now graphical (LightDM). Reboot: sudo reboot"
echo "Greeter fallback password for user '${KIOSK_USER}' (if autologin fails):"
echo "  sudo cat /root/kiosk-greeter-password.txt"
echo "Or from a text console: Ctrl+Alt+F3, login as root, then: cat /root/kiosk-greeter-password.txt"
echo "LightDM X session: ${XSESSION_ID}"
echo "URLs are loaded from:"
echo "  ${KIOSK_HOME}/.config/kiosk-urls/left.txt  (first detected / left screen)"
echo "  ${KIOSK_HOME}/.config/kiosk-urls/right.txt (second detected / right screen)"
echo "Format each file: line1=URL, line2=rotation (normal|left|right|inverted)"
echo "Displays: set OUTPUT_LEFT/OUTPUT_RIGHT to xrandr names, or leave as auto to use any two connected outputs."
echo "If a screen is wrong or black, edit ${KIOSK_HOME}/.config/kiosk.env"
echo "  (OUTPUT_LEFT, OUTPUT_RIGHT, MODE=auto|WxH, SCREEN_WIDTH/HEIGHT for window sizing fallback)."
if [[ -f /opt/kiosk-webui/kiosk-webui.py ]]; then
  echo "Web UI (display + URLs): http://<host>:8780/?token=...  — token: sudo cat /root/kiosk-webui-token.txt"
fi
echo
