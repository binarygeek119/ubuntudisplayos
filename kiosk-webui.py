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
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_SLOTS = 24
ROT_OK = frozenset({"normal", "left", "right", "inverted"})


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


def _default_kiosk() -> dict:
    return {
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
    ol = (env_map.get("OUTPUT_LIST") or "auto").strip().lower()
    if ol in ("", "auto", "detect", "any"):
        names = _connected_output_names(user, home)
    else:
        names = [x.strip() for x in (env_map.get("OUTPUT_LIST") or "").split(",") if x.strip()]
    out: list[str] = []
    for i in range(MAX_SLOTS):
        idx = i + 1
        if i < len(names):
            out.append(f"Display {idx} — {names[i]}")
        elif not names:
            out.append(f"Display {idx} (set output_list=auto or run under X to detect outputs)")
        else:
            out.append(f"Display {idx} (beyond {len(names)} head(s); URL repeats last monitor on host)")
    return out


PAGE_CSS = """
:root { font-family: system-ui, sans-serif; background:#111827; color:#e5e7eb; }
body { max-width: 56rem; margin: 2rem auto; padding: 0 1rem; }
h1 { font-size: 1.25rem; }
label { display:block; margin-top:.5rem; font-size:.8rem; color:#9ca3af; }
input, select { width:100%; padding:.4rem .5rem; border-radius:.35rem; border:1px solid #374151; background:#1f2937; color:#e5e7eb; }
button { margin-top:1rem; padding:.55rem 1rem; border-radius:.35rem; border:0; background:#2563eb; color:#fff; cursor:pointer; }
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


def _form_page(
    env_map: dict[str, str],
    slots: list[tuple[str, str, str]],
    row_labels: list[str],
    msg: str,
) -> bytes:
    def g(key: str, default: str = "") -> str:
        return html.escape(env_map.get(key, default))

    rows_html = [
        '<div class="grid hdr"><div>Monitor</div><div>Slot</div><div>URL for this display</div><div>Rotation</div></div>'
    ]
    for j, (slot, u, r) in enumerate(slots):
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
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Kiosk display config</title><style>{PAGE_CSS}</style></head><body>
<h1>Kiosk — URLs per display</h1>
<p>All settings are saved to <code>kiosk.json</code> on this machine. <strong>Each row</strong> maps to one physical monitor (slot <code>01</code> = first in <code>OUTPUT_LIST</code>). The row URL opens in that monitor's fullscreen Chrome window. Empty URL clears that slot (trailing empty slots are dropped on save).</p>
<p><code>OUTPUT_LIST</code>: comma-separated <code>xrandr</code> names, or <code>auto</code> for all connected heads. Up to <strong>{MAX_SLOTS}</strong> rows below.</p>
{msg_html}
<form method="post" action="/save">
  <label>OUTPUT_LIST</label>
  <input name="OUTPUT_LIST" value="{g('OUTPUT_LIST', 'auto')}" autocomplete="off"/>
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
  <h2 style="margin-top:1.5rem;font-size:1.05rem">Set URL (and rotation) for each display</h2>
  {''.join(rows_html)}
  <button type="submit">Save to kiosk.json</button>
</form>
<p><a href="/api/xrandr.json">Connected outputs (JSON)</a></p>
</body></html>"""
    return body.encode("utf-8")


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
        msg = "Saved." if q.get("ok", [""])[0] == "1" else ""
        merged = _merge_kiosk(_load_kiosk_raw(path_json)) if os.path.isfile(path_json) else _default_kiosk()
        env_map = _form_env_map(merged)
        slots = _load_slots(merged)
        labels = _display_row_labels(env_map, user, home)
        page = _form_page(env_map, slots, labels, msg)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
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
            "displays": displays,
        }
        for k in list(prev.keys()):
            if k not in out and k not in ("output_list", "mode", "screen_width", "screen_height", "chrome_bin", "displays"):
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
    httpd = HTTPServer((host, port), Handler)
    print(f"kiosk-webui listening on http://{host}:{port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    run()
