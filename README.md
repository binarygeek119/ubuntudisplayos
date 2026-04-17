# Ubuntu Display OS (kiosk)

Turn **Ubuntu Server/Desktop 24.04** into a **multi-display web kiosk**: one fullscreen Chrome window per connected monitor (as many as `xrandr` reports), optional per-display rotation, autologin, no sleep, and a small **web UI** to change URLs and display settings.

Repository: [github.com/binarygeek119/ubuntudisplayos](https://github.com/binarygeek119/ubuntudisplayos)

**Sister project:** Ubuntu Display OS is meant to run alongside **[posterr](https://github.com/binarygeek119/posterr)** — this repo handles the kiosk host (Chrome, LightDM, multi-head layout), while **[binarygeek119/posterr](https://github.com/binarygeek119/posterr)** (a fork of the original posterr) can serve or coordinate the poster-style content you show on those displays.

## Requirements

- Ubuntu 24.04 (or similar) with root/sudo
- At least **one** physical display; **one Chrome window per connected output** when `OUTPUT_LIST=auto`
- Network access to install Google Chrome (or Chromium fallback)

## Quick install

On the Ubuntu machine:

```bash
git clone https://github.com/binarygeek119/ubuntudisplayos.git
cd ubuntudisplayos
sed -i 's/\r$//' setup-dual-kiosk.sh kiosk-webui.py   # if you copied from Windows
chmod +x setup-dual-kiosk.sh
sudo ./setup-dual-kiosk.sh
sudo reboot
```

Place **`kiosk-webui.py` in the same directory** as `setup-dual-kiosk.sh` when you run the installer so the optional web UI is installed to `/opt/kiosk-webui/`.

## Configuration

### URLs and rotation (per screen)

Under `/home/kiosk/.config/kiosk-urls/` use **numbered** files, one per display in order:

| File | Purpose |
|------|---------|
| `01.txt` | Display 1 — line 1: URL · line 2: rotation |
| `02.txt` | Display 2 — same |
| `03.txt` … `99.txt` | Additional displays |

Rotation values: `normal`, `left`, `right`, `inverted`.

If you have **more monitors than URL files**, the last defined URL is **repeated** for the extra heads. If you have **fewer monitors than files**, extra files are ignored for layout (only the first *N* slots are used, where *N* = number of outputs in use).

Changes are picked up within a few seconds (watcher reloads Chrome). Editing `kiosk.env` also triggers a reload (mtime is watched).

### Display / Chrome (`kiosk.env`)

`/home/kiosk/.config/kiosk.env` — key settings:

- **`OUTPUT_LIST`**: `auto` = use **all connected** outputs in `xrandr` probe order, laid out left-to-right with `--right-of`. Or set a comma-separated list of exact output names (each must be connected), e.g. `HDMI-0,VGA-0,DP-1`.
- **`KIOSK_URL_DIR`**: directory containing `01.txt`, `02.txt`, … (set by installer; web UI keeps it in sync).
- **`MODE`**: `auto` (preferred per panel) or one fixed mode for every head (e.g. `1920x1080`).
- **`SCREEN_WIDTH` / `SCREEN_HEIGHT`**: fallback when geometry cannot be read from `xrandr`.
- **`CHROME_BIN`**: path to Chrome or Chromium.

Legacy installs that still have **`OUTPUT_LEFT`** / **`OUTPUT_RIGHT`** in `kiosk.env` are mapped to **`OUTPUT_LIST=left,right`** at session start.

Over SSH, list outputs:

```bash
sudo -u kiosk DISPLAY=:0 XAUTHORITY=/home/kiosk/.Xauthority xrandr --query
```

### Greeter fallback

If LightDM does not autologin, a one-time password for `kiosk` is stored for root:

```bash
sudo cat /root/kiosk-greeter-password.txt
```

## Web UI (optional)

After install (with `kiosk-webui.py` present):

- Service: `kiosk-webui.service` (runs as user `kiosk`), **enabled on boot** via `multi-user.target` and `graphical.target` (starts even before the GUI session is up).
- Default listen: **`0.0.0.0:8780`**
- Token: `/etc/kiosk-webui.env` (`TOKEN=...`) and copy in `/root/kiosk-webui-token.txt`

Open in a browser:

```text
http://<host-ip>:8780/?token=<YOUR_TOKEN>
```

The form edits **`OUTPUT_LIST`**, global video settings, and up to **16** URL/rotation rows (`01`–`16`). Saving an empty URL for a slot **removes** that `NN.txt` file.

API example:

```bash
curl -H "X-Kiosk-Token: YOUR_TOKEN" http://127.0.0.1:8780/api/xrandr.json
```

**Security:** anyone who has the token can change kiosk URLs and display settings. For LAN-only exposure, set `BIND=127.0.0.1` in `/etc/kiosk-webui.env` and use SSH port forwarding.

## Repository layout

| File | Description |
|------|-------------|
| `setup-dual-kiosk.sh` | Main installer: LightDM, Openbox, Chrome, multi-head `xrandr`, autologin, sleep disable, web UI unit |
| `kiosk-webui.py` | Minimal Python 3 stdlib config UI (up to 16 URL slots) |
| `01.txt` / `02.txt` | Example URL+rotation lines for the first two displays |

`.gitattributes` forces **LF** line endings for `*.sh`, `*.py`, and `*.txt` so scripts run correctly on Linux.

## License

Use and modify at your own risk for kiosk / signage deployments. No warranty implied.
