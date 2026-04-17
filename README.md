# Ubuntu Display OS (kiosk)

Turn **Ubuntu Server/Desktop 24.04** into a **dual-display web kiosk**: two fullscreen Chrome windows (one per monitor), optional per-display rotation, autologin, no sleep, and a small **web UI** to change URLs and display settings.

Repository: [github.com/binarygeek119/ubuntudisplayos](https://github.com/binarygeek119/ubuntudisplayos)

## Requirements

- Ubuntu 24.04 (or similar) with root/sudo
- Two physical displays (script expects **two connected** outputs from `xrandr`)
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

Files on the kiosk user (default `kiosk`):

| File | Purpose |
|------|---------|
| `/home/kiosk/.config/kiosk-urls/left.txt` | Line 1: URL · Line 2: rotation |
| `/home/kiosk/.config/kiosk-urls/right.txt` | Same |

Rotation values: `normal`, `left`, `right`, `inverted`.

Changes are picked up within a few seconds (watcher reloads Chrome).

### Display / Chrome (`kiosk.env`)

`/home/kiosk/.config/kiosk.env` — key settings:

- **`OUTPUT_LEFT` / `OUTPUT_RIGHT`**: exact `xrandr` output names, or **`auto`** to use the first two **connected** outputs.
- **`MODE`**: `auto` (preferred per panel) or a fixed mode like `1920x1080`.
- **`SCREEN_WIDTH` / `SCREEN_HEIGHT`**: fallback for window sizing when geometry is inferred.
- **`CHROME_BIN`**: path to Chrome or Chromium.

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

- Service: `kiosk-webui.service` (runs as user `kiosk`)
- Default listen: **`0.0.0.0:8780`**
- Token: `/etc/kiosk-webui.env` (`TOKEN=...`) and copy in `/root/kiosk-webui-token.txt`

Open in a browser:

```text
http://<host-ip>:8780/?token=<YOUR_TOKEN>
```

API example:

```bash
curl -H "X-Kiosk-Token: YOUR_TOKEN" http://127.0.0.1:8780/api/xrandr.json
```

**Security:** anyone who has the token can change kiosk URLs and display settings. For LAN-only exposure, set `BIND=127.0.0.1` in `/etc/kiosk-webui.env` and use SSH port forwarding.

## Repository layout

| File | Description |
|------|-------------|
| `setup-dual-kiosk.sh` | Main installer: LightDM, Openbox, Chrome, dual layout, autologin, sleep disable, web UI unit |
| `kiosk-webui.py` | Minimal Python 3 stdlib config UI |
| `left.txt` / `right.txt` | Example URL+rotation lines (copy to server paths above if you like) |

`.gitattributes` forces **LF** line endings for `*.sh`, `*.py`, and `*.txt` so scripts run correctly on Linux.

## License

Use and modify at your own risk for kiosk / signage deployments. No warranty implied.
