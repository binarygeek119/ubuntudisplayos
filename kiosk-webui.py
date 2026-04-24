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
WEBUI_VERSION = "v1.0.7"
DISCORD_INVITE_URL = "https://discord.gg/vftKQvpT"
DEFAULT_VERSION_URL = "https://raw.githubusercontent.com/binarygeek119/ubuntudisplayos/main/kiosk-webui.py"
_VERSION_CACHE_TTL_SEC = 300
_version_cache_ts = 0.0
_version_cache_payload: dict[str, str] | None = None
UPDATE_STATUS_FILE = "/tmp/kiosk-self-update.status"
UPDATE_LOG_FILE = "/tmp/kiosk-self-update.log"


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
            {"url": "http://127.0.0.1:9877", "rotation": "normal"},
            {"url": "http://127.0.0.1:9876", "rotation": "normal"},
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


def _load_slots(data: dict) -> list[tuple[str, str, str]]:
    """(slot, url, rot) for 01..MAX_SLOTS from displays array."""
    disp = data.get("displays")
    if not isinstance(disp, list):
        disp = []
    rows: list[tuple[str, str, str]] = []
    for i in range(1, MAX_SLOTS + 1):
        slot = f"{i:02d}"
        u, r = "", "normal"
        if i - 1 < len(disp) and isinstance(disp[i - 1], dict):
            u = str(disp[i - 1].get("url", "")).strip()
            r = str(disp[i - 1].get("rotation", "normal")).strip().lower() or "normal"
            if r not in ROT_OK:
                r = "normal"
        rows.append((slot, u, r))
    return rows


def _write_kiosk(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _trigger_kiosk_reload() -> None:
    """Best-effort: terminate browser windows so the watcher relaunches with fresh config."""
    for pat in ("google-chrome", "chromium", "chromium-browser"):
        try:
            subprocess.run(["pkill", "-f", pat], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def _display_row_labels(env_map: dict[str, str], user: str, home: str) -> list[str]:
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
    out: list[str] = []
    # Hide unused rows: only show detected/selected displays (at least one row for first-time setup).
    if not names:
        return ["Display 1 (no outputs detected yet)"]
    for i in range(len(names)):
        idx = i + 1
        out.append(f"Display {idx} — {names[i]}")
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
.grid { display:grid; grid-template-columns: minmax(10rem,1.1fr) 3rem 1fr 9rem; gap:.5rem .75rem; align-items:end; }
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


def _update_status_panel() -> str:
    status = _read_text(UPDATE_STATUS_FILE).strip().lower()
    if status not in {"running", "success", "failed"}:
        return ""
    log_txt = _read_text(UPDATE_LOG_FILE)
    lines = [ln for ln in log_txt.splitlines() if ln.strip()]
    tail = "\n".join(lines[-30:]) if lines else "(no output yet)"
    color = "#93c5fd" if status == "running" else ("#86efac" if status == "success" else "#fca5a5")
    return (
        '<div style="margin:.75rem 0;padding:.6rem .75rem;border:1px solid #374151;'
        'background:#111827;border-radius:.35rem">'
        f'<div id="update-status-label" style="font-weight:600;color:{color};margin-bottom:.35rem">Update status: {html.escape(status)}</div>'
        f'<pre style="margin:0;white-space:pre-wrap;color:#d1d5db;font-size:.78rem">{html.escape(tail)}</pre>'
        "</div>"
    )


def _form_page(
    env_map: dict[str, str],
    slots: list[tuple[str, str, str]],
    row_labels: list[str],
    msg: str,
    update_banner: str,
    update_available: bool,
    update_panel: str,
) -> bytes:
    def g(key: str, default: str = "") -> str:
        return html.escape(env_map.get(key, default))

    rows_html = [
        '<div class="grid hdr"><div>Monitor</div><div>Slot</div><div>URL for this display</div><div>Rotation</div></div>'
    ]
    visible_count = min(len(slots), max(1, len(row_labels)))
    for j in range(visible_count):
        slot, u, r = slots[j]
        lab = row_labels[j] if j < len(row_labels) else f"Display {j + 1}"
        rows_html.append(
            f'<div class="grid">'
            f'<div style="font-size:.82rem;line-height:1.25">{html.escape(lab)}</div>'
            f'<div class="slot">{html.escape(slot)}</div>'
            f'<div><input name="URL_{slot}" type="text" inputmode="url" value="{html.escape(u)}" '
            f'placeholder="https://…" title="This URL opens in the Chrome window for this monitor" autocomplete="off"/></div>'
            f'<div>{_sel(f"ROT_{slot}", r)}</div>'
            f"</div>"
        )

    msg_html = f'<p style="color:#86efac">{html.escape(msg)}</p>' if msg else ""
    update_poll_js = """
<script>
(() => {
  const label = document.getElementById("update-status-label");
  if (!label) return;
  const txt = (label.textContent || "").toLowerCase();
  if (txt.includes("running")) {
    setTimeout(() => window.location.reload(), 2000);
  }
})();
</script>
""" if update_panel else ""

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
    <button type="submit" formaction="/reboot" formmethod="post" class="danger" onclick="return confirm('Reboot this kiosk now?');">Reboot system</button>
    <button type="submit" formaction="/update" formmethod="post" class="warn" onclick="return confirm('Install update now? System will reboot when finished.');">Update &amp; Reboot</button>
  </div>
</form>
<p style="margin-top:2rem;padding-top:1rem;border-top:1px solid #374151;font-size:.85rem;color:#9ca3af">
Community:
<a href="{html.escape(DISCORD_INVITE_URL)}" rel="noopener noreferrer" target="_blank">Discord server</a>
<span style="color:#6b7280"> — </span>
<span>Ubuntu Display OS / kiosk discussion</span>
</p>
{update_poll_js}
</body></html>"""
    return body.encode("utf-8")


def _request_reboot() -> None:
    """Best-effort reboot request; installer grants kiosk NOPASSWD for this command."""
    subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "reboot"],
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
        ["sudo", "-n", "/usr/local/bin/kiosk-self-update"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # If sudo/helper fails immediately, surface it instead of leaving status stuck at "running".
    time.sleep(0.25)
    rc = proc.poll()
    if rc not in (None, 0):
        with open(UPDATE_STATUS_FILE, "w", encoding="utf-8") as f:
            f.write("failed\n")
        raise OSError("kiosk-self-update failed to start")


class Handler(BaseHTTPRequestHandler):
    server_version = "KioskWebUI/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        user, home, path_json = _kiosk_paths()

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
        msg = (
            "Saved."
            if ok == "1"
            else (
                "Reboot requested."
                if ok == "2"
                else (
                    "Update started."
                    if ok == "3"
                    else (
                        "Already up to date."
                        if ok == "4"
                        else (
                        "Reboot failed (check sudoers)."
                        if ok == "reboot_failed"
                        else ("Update failed (check sudoers/script)." if ok == "update_failed" else "")
                        )
                    )
                )
            )
        )
        merged = _merge_kiosk(_load_kiosk_raw(path_json)) if os.path.isfile(path_json) else _default_kiosk()
        env_map = _form_env_map(merged)
        slots = _load_slots(merged)
        labels = _display_row_labels(env_map, user, home)
        ver = _fetch_latest_version()
        latest = ver.get("latest", "").strip()
        update_banner = ""
        update_available = bool(latest and _version_key(latest) > _version_key(WEBUI_VERSION))
        if update_available:
            update_banner = (
                '<div style="margin:.75rem 0;padding:.6rem .75rem;border:1px solid #7c2d12;'
                'background:#431407;border-radius:.35rem;color:#fed7aa">'
                f'New version available: <strong>{html.escape(latest)}</strong> '
                f'(current {html.escape(WEBUI_VERSION)}). Click <strong>Update WebUI</strong> below.'
                "</div>"
            )
        update_panel = _update_status_panel()
        page = _form_page(env_map, slots, labels, msg, update_banner, update_available, update_panel)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
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

        if parsed.path != "/save":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        form = urllib.parse.parse_qs(body, keep_blank_values=True)

        def one(name: str) -> str:
            return (form.get(name, [""])[0] or "").strip()

        user, home, path_json = _kiosk_paths()
        os.makedirs(os.path.dirname(path_json), mode=0o700, exist_ok=True)

        prev = _merge_kiosk(_load_kiosk_raw(path_json)) if os.path.isfile(path_json) else _default_kiosk()
        displays: list[dict[str, str]] = []
        for i in range(1, MAX_SLOTS + 1):
            slot = f"{i:02d}"
            u = one(f"URL_{slot}")
            r = one(f"ROT_{slot}") or "normal"
            if r not in ROT_OK:
                r = "normal"
            displays.append({"url": u, "rotation": r})
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
        _trigger_kiosk_reload()

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
