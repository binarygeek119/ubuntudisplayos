#!/usr/bin/env bash
# setup-dual-kiosk.sh - Ubuntu Server/Desktop 24.04 multi-display web kiosk.
# Usage (as root): sudo ./setup-dual-kiosk.sh
# Uses one URL file per display: kiosk-urls/01.txt … 99.txt; OUTPUT_LIST=auto uses all connected outputs.

set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo $0 ..." >&2; exit 1; }

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: sudo $0" >&2
  echo "URLs are read from /home/kiosk/.config/kiosk-urls/01.txt, 02.txt, … (one per display)" >&2
  exit 1
fi

KIOSK_USER="${KIOSK_USER:-kiosk}"
KIOSK_HOME="/home/${KIOSK_USER}"

# Defaults: all connected outputs in xrandr order (OUTPUT_LIST=auto); MODE=auto per panel.
OUTPUT_LIST="${OUTPUT_LIST:-auto}"
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

# Migrate old URL layouts → numbered slots 01.txt, 02.txt
if [[ -s "$KIOSK_HOME/.config/kiosk-url/display.txt" ]] && [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/01.txt" ]]; then
  head -n 2 "$KIOSK_HOME/.config/kiosk-url/display.txt" >"$KIOSK_HOME/.config/kiosk-urls/01.txt" || true
fi
if [[ -s "$KIOSK_HOME/.config/kiosk-urls/left.txt" ]] && [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/01.txt" ]]; then
  cp -a "$KIOSK_HOME/.config/kiosk-urls/left.txt" "$KIOSK_HOME/.config/kiosk-urls/01.txt" || true
fi
if [[ -s "$KIOSK_HOME/.config/kiosk-urls/right.txt" ]] && [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/02.txt" ]]; then
  cp -a "$KIOSK_HOME/.config/kiosk-urls/right.txt" "$KIOSK_HOME/.config/kiosk-urls/02.txt" || true
fi
if [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/01.txt" ]]; then
  {
    printf '%s\n' "http://127.0.0.1:9877"
    printf '%s\n' "normal"
  } >"$KIOSK_HOME/.config/kiosk-urls/01.txt"
fi
if [[ ! -s "$KIOSK_HOME/.config/kiosk-urls/02.txt" ]]; then
  {
    printf '%s\n' "http://127.0.0.1:9876"
    printf '%s\n' "normal"
  } >"$KIOSK_HOME/.config/kiosk-urls/02.txt"
fi
chown -R "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config/kiosk-urls"
chmod 600 "$KIOSK_HOME"/.config/kiosk-urls/*.txt 2>/dev/null || true

{
  printf '# KIOSK_URL_DIR: one file per display: 01.txt, 02.txt, … (line1=URL, line2=rotation).\n'
  printf '# OUTPUT_LIST: comma-separated xrandr output names, or "auto" for all connected (in probe order).\n'
  printf 'KIOSK_URL_DIR=%q\n' "$KIOSK_HOME/.config/kiosk-urls"
  printf 'OUTPUT_LIST=%q\n' "$OUTPUT_LIST"
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

# Legacy kiosk.env used OUTPUT_LEFT/OUTPUT_RIGHT instead of OUTPUT_LIST.
if [[ -z "${OUTPUT_LIST:-}" && ( -n "${OUTPUT_LEFT:-}" || -n "${OUTPUT_RIGHT:-}" ) ]]; then
  OUTPUT_LIST="${OUTPUT_LEFT:-auto},${OUTPUT_RIGHT:-auto}"
fi
OUTPUT_LIST="${OUTPUT_LIST:-auto}"

KIOSK_URL_DIR="${KIOSK_URL_DIR:-${HOME}/.config/kiosk-urls}"
KOUT=()
KU_URL=()
KU_ROT=()
KIOSK_PIDS=()
G_W=()
G_H=()
G_X=()
G_Y=()

xset s off
xset -dpms
xset s noblank

unclutter -idle 0 -root &
xsetroot -cursor_name none 2>/dev/null || true

LOG_FILE="${HOME}/.config/kiosk-autostart.log"
touch "$LOG_FILE"
exec >>"$LOG_FILE" 2>&1
echo "[$(date '+%F %T')] kiosk autostart started (multi-display)"

CHROME_FLAGS=(
  --kiosk --noerrdialogs --disable-infobars --no-first-run
  --disable-session-crashed-bubble --disable-restore-session-state
  --disable-features=TranslateUI
  --new-window
)

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

is_auto_list() {
  case "${1:-}" in ''|auto|detect|any) return 0 ;; *) return 1 ;; esac
}

read_url_slots() {
  KU_URL=()
  KU_ROT=()
  local f u r
  while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    u="$(awk 'NF {gsub(/\r/, ""); print; exit}' "$f")"
    r="$(awk 'NF {c++; if (c==2) {gsub(/\r/, ""); print tolower($0); exit}}' "$f")"
    r="${r:-normal}"
    case "$r" in normal|left|right|inverted) ;; *) r="normal" ;; esac
    KU_URL+=("$u")
    KU_ROT+=("$r")
  done < <(find "${KIOSK_URL_DIR}" -maxdepth 1 -name '[0-9][0-9].txt' -type f | LC_ALL=C sort)
}

read_kiosk_state() {
  read_url_slots
}

build_kout_array() {
  mapfile -t _conn < <(xrandr --query | awk '/ connected/{print $1}')
  local nc="${#_conn[@]}"
  KOUT=()
  if [[ "$nc" -lt 1 ]]; then
    echo "[$(date '+%F %T')] ERROR: no connected displays"
    return 1
  fi
  if is_auto_list "${OUTPUT_LIST:-}"; then
    KOUT=("${_conn[@]}")
  else
    local part name ok c
    local _parts
    IFS=',' read -ra _parts <<< "$OUTPUT_LIST"
    for part in "${_parts[@]}"; do
      name="${part//[[:space:]]/}"
      [[ -z "$name" ]] && continue
      ok=0
      for c in "${_conn[@]}"; do
        if [[ "$c" == "$name" ]]; then ok=1; break; fi
      done
      if [[ "$ok" -eq 0 ]]; then
        echo "[$(date '+%F %T')] ERROR: OUTPUT_LIST entry not connected: ${name}"
        return 1
      fi
      KOUT+=("$name")
    done
  fi
  if [[ ${#KOUT[@]} -lt 1 ]]; then
    echo "[$(date '+%F %T')] ERROR: empty OUTPUT_LIST"
    return 1
  fi
  echo "[$(date '+%F %T')] displays (${#KOUT[@]}): $(printf '%s ' "${KOUT[@]}")"
  return 0
}

pad_urls_to_outputs() {
  local n="$1" lastu lastr
  if [[ ${#KU_URL[@]} -lt 1 ]]; then
    echo "[$(date '+%F %T')] waiting: no URL slot files (need ${KIOSK_URL_DIR}/01.txt …)"
    return 1
  fi
  while [[ ${#KU_URL[@]} -lt "$n" ]]; do
    lastu="${KU_URL[-1]}"
    lastr="${KU_ROT[-1]}"
    KU_URL+=("$lastu")
    KU_ROT+=("$lastr")
  done
  while [[ ${#KU_URL[@]} -gt "$n" ]]; do
    unset 'KU_URL[-1]'
    unset 'KU_ROT[-1]'
  done
}

# WxH+X+Y token from xrandr line for output $1 → GEO_W GEO_H GEO_X GEO_Y
parse_output_geom() {
  local out="$1"
  local tok rest rest2
  tok="$(xrandr --query | awk -v o="$out" '$1==o && /connected/ { for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+x[0-9]+\*?\+[0-9]+\+[0-9]+$/) { print $i; exit } }')"
  [[ -z "$tok" ]] && return 1
  tok="${tok//\*}"
  GEO_W="${tok%%x*}"
  rest="${tok#*x}"
  GEO_H="${rest%%+*}"
  rest2="${rest#*+}"
  GEO_X="${rest2%%+*}"
  GEO_Y="${rest2#*+}"
  return 0
}

launch_kiosk() {
  read_url_slots
  if ! resolve_browser_bin; then
    return 1
  fi
  if ! build_kout_array; then
    return 1
  fi
  local n="${#KOUT[@]}"
  if ! pad_urls_to_outputs "$n"; then
    return 1
  fi

  local i prev xr_args=()
  for ((i=0; i<n; i++)); do
    if [[ "$i" -eq 0 ]]; then
      if [[ "${MODE}" == "auto" || "${MODE}" == "default" ]]; then
        xr_args+=(--output "${KOUT[i]}" --auto --rotate "${KU_ROT[i]}" --primary --pos 0x0)
      else
        xr_args+=(--output "${KOUT[i]}" --mode "$MODE" --rotate "${KU_ROT[i]}" --primary --pos 0x0)
      fi
      prev="${KOUT[i]}"
    else
      if [[ "${MODE}" == "auto" || "${MODE}" == "default" ]]; then
        xr_args+=(--output "${KOUT[i]}" --auto --rotate "${KU_ROT[i]}" --right-of "$prev")
      else
        xr_args+=(--output "${KOUT[i]}" --mode "$MODE" --rotate "${KU_ROT[i]}" --right-of "$prev")
      fi
      prev="${KOUT[i]}"
    fi
  done
  xrandr "${xr_args[@]}" || true
  sleep 1

  G_W=(); G_H=(); G_X=(); G_Y=()
  for ((i=0; i<n; i++)); do
    if parse_output_geom "${KOUT[i]}"; then
      G_W+=("$GEO_W")
      G_H+=("$GEO_H")
      G_X+=("$GEO_X")
      G_Y+=("$GEO_Y")
    else
      G_W+=("${SCREEN_WIDTH}")
      G_H+=("${SCREEN_HEIGHT}")
      G_X+=(0)
      G_Y+=(0)
    fi
  done
  echo "[$(date '+%F %T')] geometry: $(for ((i=0;i<n;i++)); do printf '%s=%sx%s+%s+%s ' "${KOUT[i]}" "${G_W[i]}" "${G_H[i]}" "${G_X[i]}" "${G_Y[i]}"; done)"

  pkill -9 -f 'google-chrome|chromium' 2>/dev/null || true
  sleep 1

  KIOSK_PIDS=()
  local prof
  for ((i=0; i<n; i++)); do
    prof="${HOME}/.config/chrome-kiosk-$(printf '%02d' "$i")"
    mkdir -p "$prof"
    u="${KU_URL[i]}"
    [[ -z "$u" ]] && u="about:blank"
    "$CHROME_BIN" "${CHROME_FLAGS[@]}" \
      --user-data-dir="$prof" \
      --window-position="${G_X[i]},${G_Y[i]}" \
      --window-size="${G_W[i]},${G_H[i]}" \
      "$u" &
    KIOSK_PIDS+=($!)
  done
  echo "[$(date '+%F %T')] launched ${#KIOSK_PIDS[@]} kiosk window(s)"
  return 0
}

stop_kiosk() {
  local pid
  for pid in "${KIOSK_PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  for pid in "${KIOSK_PIDS[@]:-}"; do
    [[ -n "$pid" ]] && wait "$pid" 2>/dev/null || true
  done
  KIOSK_PIDS=()
}

state_fingerprint() {
  read_url_slots
  local out="" i
  for ((i=0; i<${#KU_URL[@]}; i++)); do
    out+="${KU_URL[i]}#${KU_ROT[i]}|"
  done
  printf '%s' "$out"
}

url_dir_sig() {
  find "${KIOSK_URL_DIR}" -maxdepth 1 -name '[0-9][0-9].txt' -type f -printf '%f %T@\n' 2>/dev/null | LC_ALL=C sort | tr '\n' '|'
}

(
  LAST_STATE=""
  KIOSK_PIDS=()
  while true; do
    read_kiosk_state
    XR_SIG="$(xrandr --query 2>/dev/null | awk '/ connected/{printf "%s,", $1}' | sed 's/,$//')"
    ENV_SIG="$(stat -c %Y "${HOME}/.config/kiosk.env" 2>/dev/null || echo 0)"
    UF_SIG="$(url_dir_sig)"
    URL_STATE="$(state_fingerprint)"
    CURRENT_STATE="${OUTPUT_LIST:-}|${URL_STATE}|${XR_SIG}|${ENV_SIG}|${UF_SIG}"
    ANY_DEAD=0
    for pid in "${KIOSK_PIDS[@]:-}"; do
      if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then ANY_DEAD=1; break; fi
    done
    if [[ "$CURRENT_STATE" != "$LAST_STATE" || "$ANY_DEAD" -eq 1 ]]; then
      if [[ "$ANY_DEAD" -eq 1 ]]; then
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
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kiosk
Group=kiosk
EnvironmentFile=/etc/kiosk-webui.env
Environment=KIOSK_USER=kiosk
ExecStart=/usr/bin/python3 /opt/kiosk-webui/kiosk-webui.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target graphical.target
WEBUISVC

  systemctl daemon-reload
  # Enable for both targets so the UI starts on every boot (SSH / text or full GUI).
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
echo "URLs: one file per display — ${KIOSK_HOME}/.config/kiosk-urls/01.txt … 99.txt"
echo "  (line1=URL, line2=rotation: normal|left|right|inverted). Fewer files than displays repeats the last URL."
echo "Displays: OUTPUT_LIST=auto uses all connected outputs in xrandr order; or comma-separated names."
echo "Edit ${KIOSK_HOME}/.config/kiosk.env or use the web UI (see /root/kiosk-webui-token.txt)."
if [[ -f /opt/kiosk-webui/kiosk-webui.py ]]; then
  echo "Web UI (display + URLs): http://<host>:8780/?token=...  — token: sudo cat /root/kiosk-webui-token.txt"
fi
echo
