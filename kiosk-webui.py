#!/usr/bin/env python3
"""
Minimal kiosk display / URL config web UI (Python 3 stdlib only).
Config: /etc/kiosk-webui.env (TOKEN=..., BIND=0.0.0.0, PORT=8780)
Supports multiple displays via kiosk-urls/01.txt … and OUTPUT_LIST in kiosk.env.
"""
from __future__ import annotations

import glob
import html
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_SLOTS = 16


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


def _kiosk_paths() -> tuple[str, str, str, str]:
    user = os.environ.get("KIOSK_USER", "kiosk")
    home = f"/home/{user}"
    return (
        user,
        home,
        f"{home}/.config/kiosk.env",
        f"{home}/.config/kiosk-urls",
    )


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def _parse_env(content: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == "'") or (v[0] == v[-1] == '"')):
            v = v[1:-1]
        d[k] = v
    return d


def _write_env(path: str, keys: dict[str, str], url_dir: str) -> None:
    lines = [
        "# Managed by kiosk-webui.",
        f"KIOSK_URL_DIR={shlex.quote(url_dir)}",
        f"OUTPUT_LIST={shlex.quote(keys.get('OUTPUT_LIST', 'auto'))}",
        f"MODE={shlex.quote(keys.get('MODE', 'auto'))}",
        f"SCREEN_WIDTH={shlex.quote(keys.get('SCREEN_WIDTH', '1024'))}",
        f"SCREEN_HEIGHT={shlex.quote(keys.get('SCREEN_HEIGHT', '768'))}",
        f"CHROME_BIN={shlex.quote(keys.get('CHROME_BIN', '/usr/bin/google-chrome-stable'))}",
        "",
    ]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def _write_url_file(path: str, url: str, rotation: str) -> None:
    url = (url or "").strip()
    rotation = (rotation or "normal").strip().lower() or "normal"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(url + "\n" + rotation + "\n")
    os.replace(tmp, path)


def _load_slots(dir_urls: str) -> list[tuple[str, str, str]]:
    """Return (slot, url, rot) for 01..MAX_SLOTS, reading files when present."""
    rows: list[tuple[str, str, str]] = []
    for i in range(1, MAX_SLOTS + 1):
        slot = f"{i:02d}"
        p = os.path.join(dir_urls, f"{slot}.txt")
        txt = _read_text(p) if os.path.isfile(p) else ""
        lines = [ln.rstrip("\r") for ln in txt.splitlines() if ln.strip() != ""]
        u = lines[0] if lines else ""
        r = lines[1] if len(lines) > 1 else "normal"
        if r not in ("normal", "left", "right", "inverted"):
            r = "normal"
        rows.append((slot, u, r))
    return rows


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


def _token_from_request(handler: BaseHTTPRequestHandler, form_token: str | None) -> str:
    h = handler.headers.get("X-Kiosk-Token", "")
    if h:
        return h
    q = urllib.parse.urlparse(handler.path).query
    qtok = urllib.parse.parse_qs(q).get("token", [""])[0]
    if qtok:
        return qtok
    if form_token:
        return form_token
    return ""


PAGE_CSS = """
:root { font-family: system-ui, sans-serif; background:#111827; color:#e5e7eb; }
body { max-width: 56rem; margin: 2rem auto; padding: 0 1rem; }
h1 { font-size: 1.25rem; }
label { display:block; margin-top:.5rem; font-size:.8rem; color:#9ca3af; }
input, select { width:100%; padding:.4rem .5rem; border-radius:.35rem; border:1px solid #374151; background:#1f2937; color:#e5e7eb; }
button { margin-top:1rem; padding:.55rem 1rem; border-radius:.35rem; border:0; background:#2563eb; color:#fff; cursor:pointer; }
a { color:#93c5fd; }
.grid { display:grid; grid-template-columns: 4rem 1fr 9rem; gap:.5rem .75rem; align-items:end; }
.hdr { font-weight:600; color:#9ca3af; font-size:.75rem; margin-bottom:.25rem; }
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
    env: dict[str, str],
    slots: list[tuple[str, str, str]],
    msg: str,
    token_value: str,
) -> bytes:
    def g(key: str, default: str = "") -> str:
        return html.escape(env.get(key, default))

    rows_html = ['<div class="grid hdr"><div>#</div><div>URL</div><div>Rotation</div></div>']
    for slot, u, r in slots:
        rows_html.append(
            f'<div class="grid"><div style="padding:.4rem 0">{html.escape(slot)}</div>'
            f'<div><input name="URL_{slot}" value="{html.escape(u)}" placeholder="empty = remove file" autocomplete="off"/></div>'
            f'<div>{_sel(f"ROT_{slot}", r)}</div></div>'
        )

    msg_html = f'<p style="color:#86efac">{html.escape(msg)}</p>' if msg else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Kiosk display config</title><style>{PAGE_CSS}</style></head><body>
<h1>Kiosk — multi-display</h1>
<p><code>OUTPUT_LIST</code>: comma-separated xrandr names, or <code>auto</code> for <strong>all connected</strong> outputs in order. One URL file per display: <code>01.txt</code> … <code>{MAX_SLOTS:02d}.txt</code> (line1=URL, line2=rotation).</p>
<p>Auth: header <code>X-Kiosk-Token</code> or <code>?token=…</code> (see <code>/etc/kiosk-webui.env</code>).</p>
{msg_html}
<form method="post" action="/save">
  <input type="hidden" name="token" value="{html.escape(token_value)}"/>
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
  <h2 style="margin-top:1.5rem;font-size:1rem">URLs per display</h2>
  {''.join(rows_html)}
  <button type="submit">Save</button>
</form>
<p><a href="/api/xrandr.json?token={html.escape(urllib.parse.quote(token_value, safe=''))}">Connected outputs (JSON)</a></p>
</body></html>"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "KioskWebUI/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _need_auth(self, conf: dict[str, str], form_token: str | None = None) -> bool:
        want = conf.get("TOKEN", "")
        if not want:
            return False
        return _token_from_request(self, form_token) == want

    def do_GET(self) -> None:  # noqa: N802
        conf = _conf()
        tok = conf.get("TOKEN", "")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        user, home, path_env, dir_urls = _kiosk_paths()

        if path == "/api/xrandr.json":
            if not self._need_auth(conf):
                self.send_response(401)
                self.end_headers()
                return
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

        if not tok or not self._need_auth(conf):
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "<p>Unauthorized. Open with <code>?token=YOUR_TOKEN</code> (see <code>/etc/kiosk-webui.env</code>).</p>"
            self.wfile.write(msg.encode())
            return

        q = urllib.parse.parse_qs(parsed.query)
        msg = "Saved." if q.get("ok", [""])[0] == "1" else ""
        env_map = _parse_env(_read_text(path_env))
        slots = _load_slots(dir_urls)
        token_q = q.get("token", [tok])[0]
        page = _form_page(env_map, slots, msg, token_q)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        conf = _conf()
        tok = conf.get("TOKEN", "")
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

        form_tok = one("token")
        if not tok or not self._need_auth(conf, form_tok):
            self.send_response(401)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Unauthorized\n")
            return

        user, home, path_env, dir_urls = _kiosk_paths()
        os.makedirs(dir_urls, mode=0o700, exist_ok=True)

        keys = {
            "OUTPUT_LIST": one("OUTPUT_LIST") or "auto",
            "MODE": one("MODE") or "auto",
            "SCREEN_WIDTH": re.sub(r"[^0-9]", "", one("SCREEN_WIDTH")) or "1024",
            "SCREEN_HEIGHT": re.sub(r"[^0-9]", "", one("SCREEN_HEIGHT")) or "768",
            "CHROME_BIN": one("CHROME_BIN") or "/usr/bin/google-chrome-stable",
        }
        _write_env(path_env, keys, dir_urls)

        for i in range(1, MAX_SLOTS + 1):
            slot = f"{i:02d}"
            u = one(f"URL_{slot}")
            r = one(f"ROT_{slot}") or "normal"
            path = os.path.join(dir_urls, f"{slot}.txt")
            if u:
                _write_url_file(path, u, r)
            elif os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        try:
            pw = pwd.getpwnam(user)
            os.chown(path_env, pw.pw_uid, pw.pw_gid)
            os.chmod(path_env, 0o600)
            for p in glob.glob(os.path.join(dir_urls, "[0-9][0-9].txt")):
                os.chown(p, pw.pw_uid, pw.pw_gid)
                os.chmod(p, 0o600)
        except OSError:
            pass

        self.send_response(303)
        self.send_header("Location", "/?ok=1&token=" + urllib.parse.quote(tok, safe=""))
        self.end_headers()


def run() -> None:
    conf = _conf()
    token = conf.get("TOKEN", "")
    if not token:
        print("Missing TOKEN in /etc/kiosk-webui.env", file=sys.stderr)
        sys.exit(1)
    host = conf.get("BIND", "127.0.0.1")
    port = int(conf.get("PORT", "8780"))
    httpd = HTTPServer((host, port), Handler)
    print(f"kiosk-webui listening on http://{host}:{port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    run()
