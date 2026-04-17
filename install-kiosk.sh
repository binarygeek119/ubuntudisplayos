#!/usr/bin/env bash
# install-kiosk.sh - Ubuntu Server/Desktop 24.04 multi-display web kiosk.
# Usage (as root): sudo ./install-kiosk.sh
# Uses ~/.config/kiosk.json for OUTPUT_LIST, MODE, URLs per display, etc.; OUTPUT_LIST=auto uses all connected outputs.

set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Run as root: sudo $0 ..." >&2; exit 1; }

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: sudo $0" >&2
  echo "URLs and layout are read from /home/kiosk/.config/kiosk.json" >&2
  echo "Web UI: installs to /usr/lib/kiosk-webui/ if kiosk-webui.py is next to this script," >&2
  echo "  or set KIOSK_WEBUI_SRC=/path/to/kiosk-webui.py, or omit KIOSK_WEBUI_SKIP_DOWNLOAD=1 to fetch from GitHub." >&2
  echo "System upgrade: apt upgrade runs after apt update (set KIOSK_SKIP_SYSTEM_UPGRADE=1 to skip)." >&2
  echo "Rerun mode: set KIOSK_UPDATE_ONLY=1 to skip apt update/upgrade/install and only refresh kiosk config/services." >&2
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

if [[ "${KIOSK_UPDATE_ONLY:-0}" != "1" ]]; then
  apt-get update -y

  # Upgrade installed packages to latest versions in this release (no full-upgrade / dist-upgrade).
  if [[ "${KIOSK_SKIP_SYSTEM_UPGRADE:-0}" != "1" ]]; then
    export NEEDRESTART_MODE=a
    apt-get upgrade -y \
      -o Dpkg::Options::=--force-confdef \
      -o Dpkg::Options::=--force-confold
  fi

  apt-get install -y --no-install-recommends \
    xorg openbox lightdm lightdm-gtk-greeter \
    x11-xserver-utils unclutter-xfixes dbus-x11 wget curl ca-certificates \
    python3-minimal
else
  echo "KIOSK_UPDATE_ONLY=1: skipping apt update/upgrade/install; refreshing kiosk configs/services only."
fi

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

# Xorg defaults often blank after ~10 minutes; disable server timers + DPMS at config level.
install -d /etc/X11/xorg.conf.d
cat >/etc/X11/xorg.conf.d/10-kiosk-no-blanking.conf <<'EOF'
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
EOF

# Linux virtual console blanking (separate from X); harmless for pure GUI kiosk.
install -d /etc/default/grub.d
cat >/etc/default/grub.d/zz-kiosk-consoleblank.cfg <<'EOF'
# Kiosk: do not blank the text console (add-on to X blanking fixes).
GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT:+$GRUB_CMDLINE_LINUX_DEFAULT }consoleblank=0"
EOF
if command -v update-grub >/dev/null 2>&1; then
  update-grub || true
fi

# Google Chrome (stable kiosk); fall back to Chromium from apt if needed.
CHROME_BIN=""
if [[ "${KIOSK_UPDATE_ONLY:-0}" == "1" ]]; then
  if [[ -x /usr/bin/google-chrome-stable ]]; then CHROME_BIN="/usr/bin/google-chrome-stable"
  elif [[ -x /usr/bin/chromium ]]; then CHROME_BIN="/usr/bin/chromium"
  elif [[ -x /usr/bin/chromium-browser ]]; then CHROME_BIN="/usr/bin/chromium-browser"
  else
    echo "KIOSK_UPDATE_ONLY=1 but no browser binary found. Install Chrome/Chromium once, or run without KIOSK_UPDATE_ONLY." >&2
    exit 1
  fi
else
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

# Single JSON config: ~/.config/kiosk.json (replaces kiosk.env + kiosk-urls/*.txt).
python3 - "${KIOSK_HOME}" "${OUTPUT_LIST}" "${MODE}" "${SCREEN_WIDTH}" "${SCREEN_HEIGHT}" "${CHROME_BIN}" <<'KIOSKJSON'
import json, pathlib, sys

home = pathlib.Path(sys.argv[1])
ol, mode, sw, sh, cb = (sys.argv[i] if len(sys.argv) > i else "" for i in range(2, 7))
cfg = home / ".config" / "kiosk.json"
if cfg.is_file():
    sys.exit(0)

defaults_disp = [
    {"url": "http://127.0.0.1:9877", "rotation": "normal"},
    {"url": "http://127.0.0.1:9876", "rotation": "normal"},
]


def parse_env(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def read_slot_txt(path: pathlib.Path) -> dict | None:
    if not path.is_file():
        return None
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    if not lines:
        return None
    u = lines[0]
    r = lines[1].lower() if len(lines) > 1 else "normal"
    if r not in ("normal", "left", "right", "inverted"):
        r = "normal"
    return {"url": u, "rotation": r}


def displays_from_url_dir(url_dir: pathlib.Path) -> list:
    disp: list = []
    for p in sorted(url_dir.glob("[0-9][0-9].txt")):
        slot = read_slot_txt(p)
        if slot:
            disp.append(slot)
    return disp


data = {
    "output_list": ol or "auto",
    "mode": mode or "auto",
    "screen_width": sw or "1024",
    "screen_height": sh or "768",
    "chrome_bin": cb or "/usr/bin/google-chrome-stable",
    "displays": list(defaults_disp),
}

env_path = home / ".config" / "kiosk.env"
if env_path.is_file():
    e = parse_env(env_path)
    if e.get("OUTPUT_LIST"):
        data["output_list"] = e["OUTPUT_LIST"]
    elif e.get("OUTPUT_LEFT") or e.get("OUTPUT_RIGHT"):
        data["output_list"] = f'{e.get("OUTPUT_LEFT", "auto")},{e.get("OUTPUT_RIGHT", "auto")}'
    if e.get("MODE"):
        data["mode"] = e["MODE"]
    if e.get("SCREEN_WIDTH"):
        data["screen_width"] = e["SCREEN_WIDTH"]
    if e.get("SCREEN_HEIGHT"):
        data["screen_height"] = e["SCREEN_HEIGHT"]
    if e.get("CHROME_BIN"):
        data["chrome_bin"] = e["CHROME_BIN"]

url_dir = home / ".config" / "kiosk-urls"
migrated = displays_from_url_dir(url_dir)
if migrated:
    data["displays"] = migrated
else:
    legacy = home / ".config" / "kiosk-url" / "display.txt"
    slot = read_slot_txt(legacy)
    if slot:
        data["displays"] = [slot]

cfg.parent.mkdir(parents=True, exist_ok=True)
with cfg.open("w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
KIOSKJSON
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.config/kiosk.json" 2>/dev/null || true
chmod 600 "$KIOSK_HOME/.config/kiosk.json" 2>/dev/null || true

cat >"$KIOSK_HOME/.config/openbox/autostart" <<'AUTOSTART'
#!/bin/bash
set -euo pipefail

# Managed by install-kiosk.sh (kiosk.json generation). Legacy kiosk.env/URL_LEFT scripts are unsupported.
KIOSK_AUTOSTART_VERSION="kiosk-json-v2"
KIOSK_JSON="${HOME}/.config/kiosk.json"
LOG_FILE="${HOME}/.config/kiosk-autostart.log"
touch "$LOG_FILE"
exec >>"$LOG_FILE" 2>&1
echo "[$(date '+%F %T')] kiosk autostart boot (${KIOSK_AUTOSTART_VERSION})"

load_kiosk_json() {
  eval "$(python3 - "$KIOSK_JSON" <<'LOADPY'
import json, shlex, sys

path = sys.argv[1]
defaults = {
    "output_list": "auto",
    "mode": "auto",
    "screen_width": "1024",
    "screen_height": "768",
    "chrome_bin": "/usr/bin/google-chrome-stable",
    "displays": [
        {"url": "http://127.0.0.1:9877", "rotation": "normal"},
        {"url": "http://127.0.0.1:9876", "rotation": "normal"},
    ],
}
try:
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
        if not isinstance(c, dict):
            c = {}
except (OSError, json.JSONDecodeError):
    c = {}
merged = {**defaults, **{k: v for k, v in c.items() if k != "displays"}}
disp = c.get("displays")
if isinstance(disp, list) and disp:
    merged["displays"] = disp
else:
    merged["displays"] = defaults["displays"]


def q(x):
    return shlex.quote(str(x))


m = merged
print(f'OUTPUT_LIST={q(m.get("output_list", "auto"))}')
print(f'MODE={q(m.get("mode", "auto"))}')
print(f'SCREEN_WIDTH={q(m.get("screen_width", "1024"))}')
print(f'SCREEN_HEIGHT={q(m.get("screen_height", "768"))}')
print(f'CHROME_BIN={q(m.get("chrome_bin", "/usr/bin/google-chrome-stable"))}')
LOADPY
)"
}

load_kiosk_json
OUTPUT_LIST="${OUTPUT_LIST:-auto}"

KOUT=()
KU_URL=()
KU_ROT=()
KIOSK_PIDS=()
G_W=()
G_H=()
G_X=()
G_Y=()

kiosk_keep_display_on() {
  # Drivers or clients sometimes re-enable DPMS; safe to call repeatedly.
  xset s off 2>/dev/null || true
  xset s noblank 2>/dev/null || true
  xset -dpms 2>/dev/null || true
  xset dpms force on 2>/dev/null || true
}

kiosk_keep_display_on

unclutter -idle 0 -root &
xsetroot -cursor_name none 2>/dev/null || true

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

clear_chrome_singleton_locks() {
  # Stale singleton files can force "Opening in existing browser session".
  local d
  for d in "${HOME}/.config/google-chrome" "${HOME}/.config/chromium" "${HOME}"/.config/chrome-kiosk-*; do
    [[ -d "$d" ]] || continue
    rm -f "$d/SingletonLock" "$d/SingletonCookie" "$d/SingletonSocket" 2>/dev/null || true
  done
}

is_auto_list() {
  case "${1:-}" in ''|auto|detect|any) return 0 ;; *) return 1 ;; esac
}

read_url_slots() {
  KU_URL=()
  KU_ROT=()
  [[ -f "$KIOSK_JSON" ]] || return 1
  local u r
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    u="${line%%$'\t'*}"
    r="${line#*$'\t'}"
    r="${r:-normal}"
    case "$r" in normal|left|right|inverted) ;; *) r="normal" ;; esac
    KU_URL+=("$u")
    KU_ROT+=("$r")
  done < <(python3 - "$KIOSK_JSON" <<'URLPY'
import json, sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
except (OSError, json.JSONDecodeError):
    c = {}
for d in c.get("displays") or []:
    if not isinstance(d, dict):
        continue
    u = (d.get("url") or "").strip()
    if not u:
        continue
    r = (d.get("rotation") or "normal").strip().lower() or "normal"
    if r not in ("normal", "left", "right", "inverted"):
        r = "normal"
    print(u + "\t" + r)
URLPY
)
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
    local part name ok c bad=0
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
        echo "[$(date '+%F %T')] WARN: OUTPUT_LIST entry not connected on this PC: ${name}"
        bad=1
        break
      fi
      KOUT+=("$name")
    done
    # Moving disks between machines often changes connector names (HDMI-1 vs DP-1, etc).
    # Fallback to all connected outputs so kiosk still comes up on new hardware.
    if [[ "$bad" -eq 1 || ${#KOUT[@]} -lt 1 ]]; then
      echo "[$(date '+%F %T')] WARN: falling back to connected outputs (auto order)"
      KOUT=("${_conn[@]}")
    fi
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
    echo "[$(date '+%F %T')] waiting: no URLs in ${KIOSK_JSON} (displays[].url)"
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
  echo "[$(date '+%F %T')] applying xrandr: ${xr_args[*]}"
  if ! xrandr "${xr_args[@]}"; then
    echo "[$(date '+%F %T')] WARN: combined xrandr apply failed; retrying per-output"
    # Some GPU/driver stacks reject a large combined command; retry output-by-output.
    for ((i=0; i<n; i++)); do
      if [[ "$i" -eq 0 ]]; then
        if [[ "${MODE}" == "auto" || "${MODE}" == "default" ]]; then
          xrandr --output "${KOUT[i]}" --auto --rotate "${KU_ROT[i]}" --primary --pos 0x0 || true
        else
          xrandr --output "${KOUT[i]}" --mode "$MODE" --rotate "${KU_ROT[i]}" --primary --pos 0x0 || true
        fi
      else
        if [[ "${MODE}" == "auto" || "${MODE}" == "default" ]]; then
          xrandr --output "${KOUT[i]}" --auto --rotate "${KU_ROT[i]}" --right-of "${KOUT[i-1]}" || true
        else
          xrandr --output "${KOUT[i]}" --mode "$MODE" --rotate "${KU_ROT[i]}" --right-of "${KOUT[i-1]}" || true
        fi
      fi
    done
  fi
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
  clear_chrome_singleton_locks

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

kiosk_json_sig() {
  stat -c %Y "${KIOSK_JSON}" 2>/dev/null || echo 0
}

(
  LAST_STATE=""
  KIOSK_PIDS=()
  TICK=0
  while true; do
    TICK=$((TICK + 1))
    # Re-apply ~every 60s (loop sleeps 2s) so blanking stays off if something toggles DPMS.
    if (( TICK % 30 == 0 )); then
      kiosk_keep_display_on
    fi
    load_kiosk_json
    read_kiosk_state
    XR_SIG="$(xrandr --query 2>/dev/null | awk '/ connected/{printf "%s,", $1}' | sed 's/,$//')"
    CFG_SIG="$(kiosk_json_sig)"
    URL_STATE="$(state_fingerprint)"
    CURRENT_STATE="${OUTPUT_LIST:-}|${URL_STATE}|${XR_SIG}|${CFG_SIG}"
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

# Remove stale/custom startup hooks that can bypass the managed autostart script.
rm -f /etc/xdg/openbox/autostart
rm -f /etc/lightdm/lightdm.conf.d/98-kiosk-session-hook.conf
rm -f /usr/local/bin/kiosk-session-start /usr/local/bin/kiosk-openbox-session
rm -f /usr/share/xsessions/kiosk-openbox.desktop

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

# Web UI: install /usr/lib/kiosk-webui/kiosk-webui.py (local path, KIOSK_WEBUI_SRC, or GitHub download).
_THIS="${BASH_SOURCE[0]:-$0}"
if command -v readlink >/dev/null 2>&1; then
  _THIS="$(readlink -f "$_THIS" 2>/dev/null || echo "$_THIS")"
fi
SCRIPT_DIR="$(cd "$(dirname "$_THIS")" && pwd)"
WEBUI_SRC=""
CLEAN_TMP_WEBUI=""
if [[ -n "${KIOSK_WEBUI_SRC:-}" && -f "${KIOSK_WEBUI_SRC}" ]]; then
  WEBUI_SRC="${KIOSK_WEBUI_SRC}"
else
  for _cand in "$SCRIPT_DIR/kiosk-webui.py" "$SCRIPT_DIR/../kiosk-webui.py" "$(pwd)/kiosk-webui.py"; do
    [[ -f "$_cand" ]] || continue
    WEBUI_SRC="$_cand"
    break
  done
fi
if [[ ! -f "${WEBUI_SRC:-}" && "${KIOSK_WEBUI_SKIP_DOWNLOAD:-0}" != "1" ]]; then
  _url="${KIOSK_WEBUI_URL:-https://raw.githubusercontent.com/binarygeek119/ubuntudisplayos/main/kiosk-webui.py}"
  tmpdl="$(mktemp /tmp/kiosk-webui.XXXXXX)"
  if wget -qO "$tmpdl" "$_url" 2>/dev/null || curl -fsSL -o "$tmpdl" "$_url" 2>/dev/null; then
    WEBUI_SRC="$tmpdl"
    CLEAN_TMP_WEBUI=1
  fi
fi

if [[ -f "${WEBUI_SRC:-}" || -s /usr/lib/kiosk-webui/kiosk-webui.py ]]; then
  apt-get install -y --no-install-recommends xdg-utils

  rm -rf /opt/kiosk-webui
  rm -f /root/kiosk-webui-token.txt

  install -d /usr/lib/kiosk-webui /etc/systemd/system
  if [[ -f "${WEBUI_SRC:-}" ]]; then
    install -m 755 "$WEBUI_SRC" /usr/lib/kiosk-webui/kiosk-webui.py
  fi
  ln -sf /usr/lib/kiosk-webui/kiosk-webui.py /usr/bin/kiosk-webui
  [[ -s /usr/lib/kiosk-webui/kiosk-webui.py ]] || {
    echo "ERROR: /usr/lib/kiosk-webui/kiosk-webui.py is missing or empty after install." >&2
    exit 1
  }
  [[ -n "$CLEAN_TMP_WEBUI" ]] && rm -f "$WEBUI_SRC"

  umask 077
  {
    printf '%s\n' '# Listen address and port (plain http://HOST:PORT/ — no auth).'
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
ExecStart=/usr/bin/kiosk-webui
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target graphical.target
WEBUISVC

  cat >/usr/share/applications/kiosk-display-config.desktop <<'DESKTOP'
[Desktop Entry]
Version=1.0
Type=Application
Name=Kiosk display config
Comment=Open the kiosk URL and monitor settings page in your web browser
Exec=sh -c 'xdg-open "http://127.0.0.1:8780/"'
Icon=preferences-desktop-display
Categories=Settings;HardwareSettings;
Keywords=display;kiosk;URL;monitor;
Terminal=false
StartupNotify=true
DESKTOP
  chmod 644 /usr/share/applications/kiosk-display-config.desktop
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
  fi

  systemctl daemon-reload
  # Enable for both targets so the UI starts on every boot (SSH / text or full GUI).
  systemctl enable kiosk-webui.service
  systemctl restart kiosk-webui.service 2>/dev/null || true

  umask 077
  {
    printf '%s\n' "Kiosk web UI (no token in URL):"
    printf '%s\n' "  Command: kiosk-webui   (same as: python3 /usr/lib/kiosk-webui/kiosk-webui.py)"
    printf '%s\n' "  Browser: http://127.0.0.1:8780/  or from menus: “Kiosk display config”"
    printf '%s\n' "  curl:    curl -sS http://127.0.0.1:8780/api/xrandr.json"
    printf '%s\n' "Anyone on the network can change URLs if BIND=0.0.0.0. Use BIND=127.0.0.1 + SSH tunnel for a closed kiosk."
  } >/root/kiosk-webui.txt
  umask 022
  chmod 600 /root/kiosk-webui.txt
else
  echo "ERROR: kiosk-webui.py could not be resolved and no existing /usr/lib/kiosk-webui/kiosk-webui.py was found." >&2
  echo "  Fix one of: place kiosk-webui.py in ${SCRIPT_DIR}, set KIOSK_WEBUI_SRC=/path/to/kiosk-webui.py, or allow GitHub download." >&2
  echo "  Offline: copy kiosk-webui.py next to this script and re-run." >&2
  exit 1
fi

echo
echo "Done. Default boot target is now graphical (LightDM). Reboot: sudo reboot"
echo "Greeter fallback password for user '${KIOSK_USER}' (if autologin fails):"
echo "  sudo cat /root/kiosk-greeter-password.txt"
echo "Or from a text console: Ctrl+Alt+F3, login as root, then: cat /root/kiosk-greeter-password.txt"
echo "LightDM X session: ${XSESSION_ID}"
echo "Config: ${KIOSK_HOME}/.config/kiosk.json (output_list, mode, chrome_bin, screen sizes, displays[].url / rotation)."
echo "  Fewer URLs than monitors repeats the last URL. Rotations: normal|left|right|inverted."
echo "Displays: output_list=auto uses all connected outputs in xrandr order; or comma-separated names."
echo "Edit ${KIOSK_HOME}/.config/kiosk.json or use the web UI (see /root/kiosk-webui.txt)."
if [[ -f /usr/lib/kiosk-webui/kiosk-webui.py ]]; then
  echo "Web UI (display + URLs): http://<host>:8780/  — app menu: Kiosk display config — notes: sudo cat /root/kiosk-webui.txt"
fi
echo
