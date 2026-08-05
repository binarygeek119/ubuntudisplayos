#!/usr/bin/env python3
"""
Minimal kiosk display / URL config web UI (Python 3 stdlib only).
Server config: /etc/kiosk-webui.env (BIND=0.0.0.0, PORT=8780)
Kiosk config: ~/.config/kiosk.json (output_list, mode, displays[].url / rotation, …)
"""
from __future__ import annotations

import html
import json
import os
import pwd
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_SLOTS = 24
ROT_OK = frozenset({"normal", "left", "right", "inverted"})
WEBUI_VERSION = "v1.3.1"
DISCORD_INVITE_URL = "https://discord.gg/vftKQvpT"
DEFAULT_VERSION_URL = "https://raw.githubusercontent.com/binarygeek119/ubuntudisplayos/main/kiosk-webui.py"
_VERSION_CACHE_TTL_SEC = 300
_version_cache_ts = 0.0
_version_cache_payload: dict[str, str] | None = None
UPDATE_STATUS_FILE = "/tmp/kiosk-self-update.status"
UPDATE_LOG_FILE = "/tmp/kiosk-self-update.log"
POSTERRX_UPDATE_STATUS_FILE = "/tmp/kiosk-posterrx-update.status"
POSTERRX_UPDATE_LOG_FILE = "/tmp/kiosk-posterrx-update.log"
POSTERRX_UPDATE_HELPER = "/usr/local/bin/kiosk-posterrx-update"


def _conf() -> dict[str, str]:
    path = os.environ.get("KIOSK_WEBUI_CONF", "/etc/kiosk-webui.env")
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _kiosk_paths() -> tuple[str, str, str]:
    user = os.environ.get("KIOSK_USER", "kiosk")
    home = f"/home/{user}"
    return user, home, f"{home}/.config/kiosk.json"


def _load_kiosk_raw(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def _default_kiosk() -> dict:
    return {
        "output_list": "auto",
        "mode": "auto",
        "screen_width": "1024",
        "screen_height": "768",
        "chrome_bin": "/usr/bin/google-chrome-stable",
        "startup_delay_sec": "0",
        "displays": [
            {"url": "http://127.0.0.1:9877", "rotation": "normal", "mode": ""},
            {"url": "http://127.0.0.1:9876", "rotation": "normal", "mode": ""},
        ],
    }


def _canonical_keys(raw: dict) -> dict:
    """Accept legacy UPPER_SNAKE keys from older hand-edited JSON."""
    out = dict(raw)
    upl = {
        "OUTPUT_LIST": "output_list",
        "MODE": "mode",
        "SCREEN_WIDTH": "screen_width",
        "SCREEN_HEIGHT": "screen_height",
        "CHROME_BIN": "chrome_bin",
    }
    for a, b in upl.items():
        if a in out and b not in out:
            out[b] = out.pop(a)
    # Backward compatibility: old docker-only delay key.
    if "docker_startup_delay_sec" in out and "startup_delay_sec" not in out:
        out["startup_delay_sec"] = out.pop("docker_startup_delay_sec")
    return out


def _merge_kiosk(raw: dict) -> dict:
    raw = _canonical_keys(raw)
    base = _default_kiosk()
    for k, v in raw.items():
        if k != "displays":
            base[k] = v
    disp = raw.get("displays")
    if isinstance(disp, list) and disp:
        base["displays"] = disp
    return base


def _form_env_map(data: dict) -> dict[str, str]:
    """Keys matching HTML form / legacy UPPER names for labels."""
    return {
        "OUTPUT_LIST": str(data.get("output_list", "auto")),
        "MODE": str(data.get("mode", "auto")),
        "SCREEN_WIDTH": str(data.get("screen_width", "1024")),
        "SCREEN_HEIGHT": str(data.get("screen_height", "768")),
        "CHROME_BIN": str(data.get("chrome_bin", "/usr/bin/google-chrome-stable")),
        "STARTUP_DELAY_SEC": str(data.get("startup_delay_sec", "0")),
    }


def _load_slots(data: dict) -> list[tuple[str, str, str, str]]:
    """(slot, url, rot, mode) for 01..MAX_SLOTS from displays array."""
    disp = data.get("displays")
    if not isinstance(disp, list):
        disp = []
    rows: list[tuple[str, str, str, str]] = []
    for i in range(1, MAX_SLOTS + 1):
        slot = f"{i:02d}"
        u, r, m = "", "normal", ""
        if i - 1 < len(disp) and isinstance(disp[i - 1], dict):
            u = str(disp[i - 1].get("url", "")).strip()
            r = str(disp[i - 1].get("rotation", "normal")).strip().lower() or "normal"
            if r not in ROT_OK:
                r = "normal"
            m = str(disp[i - 1].get("mode", "")).strip()
        rows.append((slot, u, r, m))
    return rows


def _write_kiosk(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _trigger_kiosk_reload(user: str, home: str) -> None:
    """Terminate browser windows and ensure kiosk autostart loop is running."""
    for pat in ("google-chrome", "chromium", "chromium-browser"):
        try:
            subprocess.run(["pkill", "-f", pat], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    # Fallback: if watcher is not running, bootstrap autostart once.
    autostart = f"{home}/.config/openbox/autostart"
    cmd = (
        f'sleep 4; '
        f'if ! pgrep -f "google-chrome|chromium|chromium-browser" >/dev/null 2>&1; then '
        f'nohup "{autostart}" >/dev/null 2>&1 & '
        f"fi"
    )
    try:
        subprocess.Popen(["/bin/bash", "-lc", cmd], start_new_session=True)  # noqa: S603,S607
    except OSError:
        pass


def _fix_chrome_layout(user: str, home: str) -> None:
    """Try to guarantee one kiosk browser window per connected display."""
    _trigger_kiosk_reload(user, home)
    autostart = f"{home}/.config/openbox/autostart"
    # If tools are available, move/resize windows to match each connected output.
    cmd = (
        "sleep 8; "
        "mapfile -t geoms < <(xrandr --query 2>/dev/null | awk '/ connected/ {for(i=1;i<=NF;i++) "
        "if($i ~ /^[0-9]+x[0-9]+\\+[0-9]+\\+[0-9]+$/){print $i; break}}'); "
        "outputs=${#geoms[@]}; "
        "wins=$(pgrep -fc 'google-chrome|chromium|chromium-browser'); "
        f'if [ "$wins" -lt "$outputs" ]; then nohup "{autostart}" >/dev/null 2>&1 & sleep 4; fi; '
        "command -v wmctrl >/dev/null 2>&1 || exit 0; "
        "mapfile -t wids < <(wmctrl -lx 2>/dev/null | awk 'tolower($3) ~ /(google-chrome|chromium)/ {print $1}'); "
        "limit=${#wids[@]}; [ $outputs -lt $limit ] && limit=$outputs; "
        "for ((i=0;i<limit;i++)); do "
        "  g=${geoms[$i]}; "
        "  w=${g%%x*}; r=${g#*x}; h=${r%%+*}; r=${r#*+}; x=${r%%+*}; y=${r#*+}; "
        "  wmctrl -ir \"${wids[$i]}\" -e \"0,$x,$y,$w,$h\" >/dev/null 2>&1 || true; "
        "done"
    )
    try:
        subprocess.Popen(["/bin/bash", "-lc", cmd], start_new_session=True)  # noqa: S603,S607
    except OSError:
        pass


def _xrandr_json(user: str, home: str) -> str:
    env = os.environ.copy()
    env["DISPLAY"] = ":0"
    env["XAUTHORITY"] = f"{home}/.Xauthority"
    try:
        p = subprocess.run(
            ["xrandr", "--query"],
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
        )
        text = p.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return json.dumps({"ok": False, "error": str(e), "outputs": []})

    outputs: list[dict[str, object]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "connected":
            name = parts[0]
            outputs.append({"name": name, "primary": "primary" in parts})
    return json.dumps({"ok": True, "outputs": outputs})


def _connected_output_names(user: str, home: str) -> list[str]:
    raw = _xrandr_json(user, home)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not data.get("ok"):
        return []
    return [str(o["name"]) for o in data.get("outputs", [])]


def _xrandr_output_modes(user: str, home: str) -> dict[str, list[str]]:
    env = os.environ.copy()
    env["DISPLAY"] = ":0"
    env["XAUTHORITY"] = f"{home}/.Xauthority"
    try:
        p = subprocess.run(
            ["xrandr", "--query"],
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
        )
        text = p.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return {}

    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t")):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "connected":
                current = parts[0]
                out.setdefault(current, [])
            else:
                current = None
            continue
        if current is None:
            continue
        m = re.match(r"^\s*([0-9]+x[0-9]+)\b", line)
        if not m:
            continue
        mode = m.group(1)
        if mode not in out[current]:
            out[current].append(mode)
    return out


def _display_row_meta(env_map: dict[str, str], user: str, home: str) -> list[tuple[str, str]]:
    connected = _connected_output_names(user, home)
    ol = (env_map.get("OUTPUT_LIST") or "auto").strip().lower()
    if ol in ("", "auto", "detect", "any"):
        names = connected
    else:
        configured = [x.strip() for x in (env_map.get("OUTPUT_LIST") or "").split(",") if x.strip()]
        # If config has stale connector names from another machine, show live outputs.
        names = configured if all(n in connected for n in configured) else connected
    if len(names) > MAX_SLOTS:
        names = names[:MAX_SLOTS]
    out: list[tuple[str, str]] = []
    # Hide unused rows: only show detected/selected displays (at least one row for first-time setup).
    if not names:
        return [("Display 1 (no outputs detected yet)", "")]
    for i in range(len(names)):
        idx = i + 1
        out.append((f"Display {idx} — {names[i]}", names[i]))
    return out


PAGE_CSS = """
:root { font-family: system-ui, sans-serif; background:#111827; color:#e5e7eb; }
body { max-width: 56rem; margin: 2rem auto; padding: 0 1rem; }
h1 { font-size: 1.25rem; }
label { display:block; margin-top:.5rem; font-size:.8rem; color:#9ca3af; }
input, select { width:100%; padding:.4rem .5rem; border-radius:.35rem; border:1px solid #374151; background:#1f2937; color:#e5e7eb; }
button { margin-top:1rem; padding:.55rem 1rem; border-radius:.35rem; border:0; background:#2563eb; color:#fff; cursor:pointer; }
button.danger { background:#b91c1c; margin-left:.5rem; }
button.warn { background:#92400e; margin-left:.5rem; }
button:disabled { background:#4b5563; color:#9ca3af; cursor:not-allowed; }
.actions { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin-top:1rem; }
.actions form { margin:0; }
.actions button { margin-top:0; }
a { color:#93c5fd; }
.grid { display:grid; grid-template-columns: minmax(10rem,1.1fr) 3rem minmax(16rem,1.6fr) 9rem minmax(10rem,1fr); gap:.5rem .75rem; align-items:end; }
.hdr { font-weight:600; color:#9ca3af; font-size:.75rem; margin-bottom:.25rem; }
.slot { font-family: ui-monospace, monospace; color:#d1d5db; padding:.35rem 0; }
@media (max-width:700px){ .grid { grid-template-columns:1fr; } }
"""


def _sel(name: str, current: str) -> str:
    opts = ["normal", "left", "right", "inverted"]
    parts = []
    for o in opts:
        sel = " selected" if o == current else ""
        parts.append(f'<option value="{o}"{sel}>{html.escape(o)}</option>')
    return f'<select name="{html.escape(name)}">{"".join(parts)}</select>'


def _mode_sel(name: str, current: str, modes: list[str]) -> str:
    options: list[tuple[str, str]] = [("", "global MODE"), ("auto", "auto"), ("default", "default")]
    for m in modes:
        options.append((m, m))
    if current and all(v != current for v, _ in options):
        options.append((current, current))
    parts = []
    for value, label in options:
        sel = " selected" if value == current else ""
        parts.append(f'<option value="{html.escape(value)}"{sel}>{html.escape(label)}</option>')
    return f'<select name="{html.escape(name)}">{"".join(parts)}</select>'


def _version_key(v: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", v)]
    return tuple(nums) if nums else (0,)


def _fetch_latest_version() -> dict[str, str]:
    """Return {'latest': 'vX.Y.Z'} when discoverable, with short in-process cache."""
    global _version_cache_ts, _version_cache_payload
    now = time.time()
    if _version_cache_payload is not None and (now - _version_cache_ts) < _VERSION_CACHE_TTL_SEC:
        return _version_cache_payload

    out: dict[str, str] = {}
    url = os.environ.get("KIOSK_WEBUI_VERSION_URL", DEFAULT_VERSION_URL)
    try:
        with urllib.request.urlopen(url, timeout=2.5) as resp:  # nosec - fixed HTTPS URL or explicit env override
            text = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'^WEBUI_VERSION\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
        if m:
            out["latest"] = m.group(1).strip()
    except OSError:
        out = {}

    _version_cache_payload = out
    _version_cache_ts = now
    return out


def _start_update_check_task() -> None:
    """Background refresh of latest-version cache every N minutes."""
    every_min = int(os.environ.get("KIOSK_WEBUI_UPDATE_CHECK_MIN", "10"))
    every_min = 1 if every_min < 1 else every_min

    def worker() -> None:
        while True:
            _fetch_latest_version()
            time.sleep(every_min * 60)

    t = threading.Thread(target=worker, name="kiosk-webui-update-check", daemon=True)
    t.start()


def _status_panel(title: str, status_file: str, log_file: str, label_id: str) -> str:
    status = _read_text(status_file).strip().lower()
    if status not in {"running", "success", "failed"}:
        return ""
    log_txt = _read_text(log_file)
    lines = [ln for ln in log_txt.splitlines() if ln.strip()]
    tail = "\n".join(lines[-30:]) if lines else "(no output yet)"
    color = "#93c5fd" if status == "running" else ("#86efac" if status == "success" else "#fca5a5")
    return (
        '<div style="margin:.75rem 0;padding:.6rem .75rem;border:1px solid #374151;'
        'background:#111827;border-radius:.35rem">'
        f'<div id="{html.escape(label_id)}" style="font-weight:600;color:{color};margin-bottom:.35rem">'
        f"{html.escape(title)}: {html.escape(status)}</div>"
        f'<pre style="margin:0;white-space:pre-wrap;color:#d1d5db;font-size:.78rem">{html.escape(tail)}</pre>'
        "</div>"
    )


def _update_status_panel() -> str:
    return _status_panel("Update status", UPDATE_STATUS_FILE, UPDATE_LOG_FILE, "update-status-label")


def _posterrx_update_status_panel() -> str:
    return _status_panel(
        "PosterX Docker update",
        POSTERRX_UPDATE_STATUS_FILE,
        POSTERRX_UPDATE_LOG_FILE,
        "posterrx-update-status-label",
    )


def _posterrx_update_available() -> bool:
    """True when the PosterX Docker update helper is installed (rerun installer if missing)."""
    return os.path.isfile(POSTERRX_UPDATE_HELPER) and os.access(POSTERRX_UPDATE_HELPER, os.X_OK)


def _form_page(
    env_map: dict[str, str],
    slots: list[tuple[str, str, str, str]],
    row_meta: list[tuple[str, str]],
    output_modes: dict[str, list[str]],
    msg: str,
    update_banner: str,
    update_available: bool,
    update_panel: str,
    posterrx_panel: str,
    posterrx_helper_ok: bool,
) -> bytes:
    def g(key: str, default: str = "") -> str:
        return html.escape(env_map.get(key, default))

    rows_html = [
        '<div class="grid hdr"><div>Monitor</div><div>Slot</div><div>URL for this display</div><div>Rotation</div><div>Resolution</div></div>'
    ]
    visible_count = min(len(slots), max(1, len(row_meta)))
    for j in range(visible_count):
        slot, u, r, m = slots[j]
        lab = row_meta[j][0] if j < len(row_meta) else f"Display {j + 1}"
        out_name = row_meta[j][1] if j < len(row_meta) else ""
        modes = output_modes.get(out_name, [])
        rows_html.append(
            f'<div class="grid">'
            f'<div style="font-size:.82rem;line-height:1.25">{html.escape(lab)}</div>'
            f'<div class="slot">{html.escape(slot)}</div>'
            f'<div><input name="URL_{slot}" type="text" inputmode="url" value="{html.escape(u)}" '
            f'placeholder="https://…" title="This URL opens in the Chrome window for this monitor" autocomplete="off"/></div>'
            f'<div>{_sel(f"ROT_{slot}", r)}</div>'
            f'<div>{_mode_sel(f"MODE_{slot}", m, modes)}</div>'
            f"</div>"
        )

    msg_html = f'<p style="color:#86efac">{html.escape(msg)}</p>' if msg else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Kiosk display config</title><style>{PAGE_CSS}</style></head><body>
<h1>Kiosk — URLs per display <span style="font-size:.8rem;color:#9ca3af;margin-left:.5rem">{WEBUI_VERSION}</span></h1>
<p>All settings are saved to <code>kiosk.json</code> on this machine. <strong>Each row</strong> maps to one physical monitor (slot <code>01</code> = first in <code>OUTPUT_LIST</code>). The row URL opens in that monitor's fullscreen Chrome window. Empty URL clears that slot (trailing empty slots are dropped on save).</p>
<p><code>OUTPUT_LIST</code>: comma-separated <code>xrandr</code> names, or <code>auto</code> for all connected heads. Only detected displays are shown below.</p>
<p><code>STARTUP_DELAY_SEC</code>: optional delay before first display launch (applies to all setups). Use this when your target web app needs extra boot time. If set to <code>0</code>, kiosk auto-uses <code>60</code> seconds when Docker is installed and at least one image exists.</p>
{msg_html}
{update_banner}
{update_panel}
{posterrx_panel}
<form method="post" action="/save">
  <div class="grid" style="margin-top:.25rem">
    <div style="grid-column:1 / span 2">
      <label>OUTPUT_LIST</label>
      <input name="OUTPUT_LIST" value="{g('OUTPUT_LIST', 'auto')}" autocomplete="off"/>
    </div>
    <div></div>
  </div>
  <div class="grid" style="margin-top:1rem">
    <div><label>MODE</label><input name="MODE" value="{g('MODE', 'auto')}" autocomplete="off"/></div>
    <div><label>CHROME_BIN</label><input name="CHROME_BIN" value="{g('CHROME_BIN', '/usr/bin/google-chrome-stable')}" autocomplete="off"/></div>
    <div></div>
  </div>
  <div class="grid" style="margin-top:.5rem">
    <div><label>SCREEN_WIDTH</label><input name="SCREEN_WIDTH" value="{g('SCREEN_WIDTH', '1024')}" autocomplete="off"/></div>
    <div><label>SCREEN_HEIGHT</label><input name="SCREEN_HEIGHT" value="{g('SCREEN_HEIGHT', '768')}" autocomplete="off"/></div>
    <div></div>
  </div>
  <div class="grid" style="margin-top:.5rem">
    <div><label>STARTUP_DELAY_SEC</label><input name="STARTUP_DELAY_SEC" value="{g('STARTUP_DELAY_SEC', '0')}" inputmode="numeric" autocomplete="off"/></div>
    <div></div>
    <div></div>
  </div>
  <h2 style="margin-top:1.5rem;font-size:1.05rem">Set URL (and rotation) for each display</h2>
  {''.join(rows_html)}
  <div class="actions">
    <button type="submit">Save to kiosk.json</button>
    <button type="submit" formaction="/reload" formmethod="post" class="warn" onclick="return confirm('Reload kiosk browser pages now?');">Reload pages</button>
    <button type="submit" formaction="/fix-layout" formmethod="post" class="warn" onclick="return confirm('Fix Chrome layout now? This should restore one browser window per display.');">Fix Chrome layout</button>
    <button type="submit" formaction="/reboot" formmethod="post" class="danger" onclick="return confirm('Reboot this kiosk now?');">Reboot system</button>
    <button type="submit" formaction="/update" formmethod="post" class="warn" onclick="return confirm('Install update now? System will reboot when finished.');">Update &amp; Reboot</button>
    <button type="submit" formaction="/update-posterrx" formmethod="post" class="warn"{"" if posterrx_helper_ok else " disabled"} onclick="return confirm('Pull latest PosterX Docker image and recreate the posterr container? Kiosk pages may briefly disconnect.');"{"" if posterrx_helper_ok else ' title="Run install-kiosk.sh to enable PosterX Docker updates"'}>Update PosterX Docker</button>
  </div>
</form>
<p style="margin-top:2rem;padding-top:1rem;border-top:1px solid #374151;font-size:.85rem;color:#9ca3af">
Community:
<a href="{html.escape(DISCORD_INVITE_URL)}" rel="noopener noreferrer" target="_blank">Discord server</a>
<span style="color:#6b7280"> — </span>
<span>Ubuntu Display OS / kiosk discussion</span>
</p>
</body></html>"""
    return body.encode("utf-8")


def _root_cmd(argv: list[str]) -> list[str]:
    """Prefix with sudo -n only when not already root (legacy non-root service)."""
    if os.geteuid() == 0:
        return argv
    return ["sudo", "-n", *argv]


def _request_reboot() -> None:
    """Best-effort reboot request; root service or installer sudoers for kiosk user."""
    subprocess.run(
        _root_cmd(["/usr/bin/systemctl", "reboot"]),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _request_update() -> None:
    """Launch installer refresh in background; helper script performs update and service refresh."""
    with open(UPDATE_STATUS_FILE, "w", encoding="utf-8") as f:
        f.write("running\n")
    with open(UPDATE_LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%F %T')}] update requested from web UI\n")
    proc = subprocess.Popen(  # noqa: S603,S607
        _root_cmd(["/usr/local/bin/kiosk-self-update"]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # If helper fails immediately, surface it instead of leaving status stuck at "running".
    time.sleep(0.25)
    rc = proc.poll()
    if rc not in (None, 0):
        with open(UPDATE_STATUS_FILE, "w", encoding="utf-8") as f:
            f.write("failed\n")
        raise OSError("kiosk-self-update failed to start")


def _request_posterrx_update() -> None:
    """Pull PosterX image and recreate container via update helper."""
    if not _posterrx_update_available():
        raise OSError("kiosk-posterrx-update helper not installed")
    with open(POSTERRX_UPDATE_STATUS_FILE, "w", encoding="utf-8") as f:
        f.write("running\n")
    with open(POSTERRX_UPDATE_LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%F %T')}] PosterX Docker update requested from web UI\n")
    proc = subprocess.Popen(  # noqa: S603,S607
        _root_cmd([POSTERRX_UPDATE_HELPER]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.25)
    rc = proc.poll()
    if rc not in (None, 0):
        with open(POSTERRX_UPDATE_STATUS_FILE, "w", encoding="utf-8") as f:
            f.write("failed\n")
        raise OSError("kiosk-posterrx-update failed to start")


class Handler(BaseHTTPRequestHandler):
    server_version = "KioskWebUI/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        user, home, path_json = _kiosk_paths()

        if path == "/reload":
            _trigger_kiosk_reload(user, home)
            self.send_response(303)
            self.send_header("Location", "/?ok=5")
            self.end_headers()
            return
        if path == "/fix-layout":
            _fix_chrome_layout(user, home)
            self.send_response(303)
            self.send_header("Location", "/?ok=6")
            self.end_headers()
            return

        if path == "/api/xrandr.json":
            data = _xrandr_json(user, home).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path not in ("/", ""):
            self.send_response(404)
            self.end_headers()
            return

        q = urllib.parse.parse_qs(parsed.query)
        ok = q.get("ok", [""])[0]
        msg = {
            "1": "Saved.",
            "2": "Reboot requested.",
            "3": "Update started.",
            "4": "PosterX Docker update started.",
            "5": "Pages reloaded.",
            "6": "Chrome layout fix started.",
            "reboot_failed": "Reboot failed (check sudoers).",
            "update_failed": "Update failed (check sudoers/script).",
            "posterrx_update_failed": "PosterX Docker update failed (check sudoers/Docker/helper).",
        }.get(ok, "")
        merged = _merge_kiosk(_load_kiosk_raw(path_json)) if os.path.isfile(path_json) else _default_kiosk()
        env_map = _form_env_map(merged)
        slots = _load_slots(merged)
        row_meta = _display_row_meta(env_map, user, home)
        output_modes = _xrandr_output_modes(user, home)
        ver = _fetch_latest_version()
        latest = ver.get("latest", "").strip()
        update_banner = ""
        update_available = bool(latest and _version_key(latest) > _version_key(WEBUI_VERSION))
        if update_available:
            update_banner = (
                '<div style="margin:.75rem 0;padding:.6rem .75rem;border:1px solid #7c2d12;'
                'background:#431407;border-radius:.35rem;color:#fed7aa">'
                f'New version available: <strong>{html.escape(latest)}</strong> '
                f'(current {html.escape(WEBUI_VERSION)}). Click <strong>Update &amp; Reboot</strong> below.'
                "</div>"
            )
        update_panel = _update_status_panel()
        posterrx_panel = _posterrx_update_status_panel()
        posterrx_helper_ok = _posterrx_update_available()
        page = _form_page(
            env_map,
            slots,
            row_meta,
            output_modes,
            msg,
            update_banner,
            update_available,
            update_panel,
            posterrx_panel,
            posterrx_helper_ok,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        user, home, path_json = _kiosk_paths()
        if parsed.path == "/reboot":
            try:
                _request_reboot()
                self.send_response(303)
                self.send_header("Location", "/?ok=2")
            except (OSError, subprocess.CalledProcessError):
                self.send_response(303)
                self.send_header("Location", "/?ok=reboot_failed")
            self.end_headers()
            return

        if parsed.path == "/update":
            try:
                _request_update()
                self.send_response(303)
                self.send_header("Location", "/?ok=3")
            except OSError:
                self.send_response(303)
                self.send_header("Location", "/?ok=update_failed")
            self.end_headers()
            return

        if parsed.path == "/update-posterrx":
            try:
                _request_posterrx_update()
                self.send_response(303)
                self.send_header("Location", "/?ok=4")
            except OSError:
                self.send_response(303)
                self.send_header("Location", "/?ok=posterrx_update_failed")
            self.end_headers()
            return

        if parsed.path == "/reload":
            _trigger_kiosk_reload(user, home)
            self.send_response(303)
            self.send_header("Location", "/?ok=5")
            self.end_headers()
            return
        if parsed.path == "/fix-layout":
            _fix_chrome_layout(user, home)
            self.send_response(303)
            self.send_header("Location", "/?ok=6")
            self.end_headers()
            return

        if parsed.path != "/save":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        form = urllib.parse.parse_qs(body, keep_blank_values=True)

        def one(name: str) -> str:
            return (form.get(name, [""])[0] or "").strip()

        os.makedirs(os.path.dirname(path_json), mode=0o700, exist_ok=True)

        prev = _merge_kiosk(_load_kiosk_raw(path_json)) if os.path.isfile(path_json) else _default_kiosk()
        displays: list[dict[str, str]] = []
        for i in range(1, MAX_SLOTS + 1):
            slot = f"{i:02d}"
            u = one(f"URL_{slot}")
            r = one(f"ROT_{slot}") or "normal"
            if r not in ROT_OK:
                r = "normal"
            m = one(f"MODE_{slot}")
            displays.append({"url": u, "rotation": r, "mode": m})
        while displays and not displays[-1]["url"].strip():
            displays.pop()

        out = {
            "output_list": one("OUTPUT_LIST") or "auto",
            "mode": one("MODE") or "auto",
            "screen_width": re.sub(r"[^0-9]", "", one("SCREEN_WIDTH")) or "1024",
            "screen_height": re.sub(r"[^0-9]", "", one("SCREEN_HEIGHT")) or "768",
            "chrome_bin": one("CHROME_BIN") or "/usr/bin/google-chrome-stable",
            "startup_delay_sec": re.sub(r"[^0-9]", "", one("STARTUP_DELAY_SEC")) or "0",
            "displays": displays,
        }
        for k in list(prev.keys()):
            if k not in out and k not in ("output_list", "mode", "screen_width", "screen_height", "chrome_bin", "startup_delay_sec", "docker_startup_delay_sec", "displays"):
                out[k] = prev[k]

        _write_kiosk(path_json, out)
        _trigger_kiosk_reload(user, home)

        try:
            pw = pwd.getpwnam(user)
            os.chown(path_json, pw.pw_uid, pw.pw_gid)
            os.chmod(path_json, 0o600)
        except OSError:
            pass

        self.send_response(303)
        self.send_header("Location", "/?ok=1")
        self.end_headers()


def run() -> None:
    conf = _conf()
    host = conf.get("BIND", "127.0.0.1")
    port = int(conf.get("PORT", "8780"))
    _start_update_check_task()
    httpd = HTTPServer((host, port), Handler)
    print(f"kiosk-webui listening on http://{host}:{port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    run()
