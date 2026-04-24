# Ubuntu Display OS (kiosk)

Turn **Ubuntu Server/Desktop 24.04** into a **multi-display web kiosk**: one fullscreen Chrome window per connected monitor (as many as `xrandr` reports), optional per-display rotation, autologin, no sleep, and a small **web UI** to change URLs and display settings.

Repository: [github.com/binarygeek119/ubuntudisplayos](https://github.com/binarygeek119/ubuntudisplayos)

**Discord:** [https://discord.gg/vftKQvpT](https://discord.gg/vftKQvpT) — community and kiosk discussion.

> Built purely with AI assistance.

**Sister project:** Ubuntu Display OS is meant to run alongside **[posterrX](https://github.com/binarygeek119/posterrX)** — this repo handles the kiosk host (Chrome, LightDM, multi-head layout), while **[binarygeek119/posterrX](https://github.com/binarygeek119/posterrX)** (a fork of the original Posterr) can serve or coordinate the poster-style content you show on those displays.

## Requirements

- Ubuntu 24.04.4 LTS with root/sudo
- At least **one** physical display; **one Chrome window per connected output** when `output_list` is `auto`
- Network access to install Google Chrome (or Chromium fallback)

## Quick install

On the Ubuntu machine:
--
```bash
git clone https://github.com/binarygeek119/ubuntudisplayos.git
cd ubuntudisplayos
sed -i 's/\r$//' install-kiosk.sh kiosk-webui.py   # if you copied from Windows
chmod +x install-kiosk.sh
sudo ./install-kiosk.sh
sudo reboot
```

The installer runs **`apt-get update`** then a noninteractive **`apt-get upgrade`** (updates installed packages without **`full-upgrade`** / **`dist-upgrade`**). To skip that step (faster reruns or air‑gapped hosts): **`KIOSK_SKIP_SYSTEM_UPGRADE=1 sudo ./install-kiosk.sh`**.

For fast reruns on an already prepared kiosk (refresh Openbox autostart, `kiosk.json` handling, LightDM snippets, and web UI/service wiring without apt work), use: **`KIOSK_UPDATE_ONLY=1 sudo ./install-kiosk.sh`** (requires an already installed Chrome/Chromium binary).
Reruns also remove stale custom startup overrides that can break kiosk launch (`/etc/xdg/openbox/autostart`, `98-kiosk-session-hook.conf`, and old custom `kiosk-openbox` session wrappers).
The installer now writes a dedicated LightDM session (`kiosk-openbox`) that explicitly runs `/home/kiosk/.config/openbox/autostart` before `openbox-session` so kiosk launch logic is consistent across machines.

Next command to run on kiosk:

```bash
cd /home/binarygeek119/ubuntudisplayos
chmod +x install-kiosk.sh
sudo KIOSK_UPDATE_ONLY=1 ./install-kiosk.sh
sudo systemctl restart lightdm
```

The **web UI** is installed to **`/usr/lib/kiosk-webui/kiosk-webui.py`** (plus **`/usr/bin/kiosk-webui`**, **`kiosk-webui.service`**, and the **“Kiosk display config”** menu entry) when **`install-kiosk.sh`** can find **`kiosk-webui.py`**: same folder as the script, **`KIOSK_WEBUI_SRC=/path/to/kiosk-webui.py`**, or—if the machine has network—by **downloading** from GitHub (`main` branch) unless **`KIOSK_WEBUI_SKIP_DOWNLOAD=1`**. Override the URL with **`KIOSK_WEBUI_URL=…`** if needed.

## Configuration

### `kiosk.json` (single config file)

Everything the kiosk session and web UI need lives in **`/home/kiosk/.config/kiosk.json`** (JSON). There are no per-display `*.txt` files and no separate `kiosk.env`.

| Field | Purpose |
|--------|---------|
| **`output_list`** | Comma-separated `xrandr` output names, or **`auto`** for all connected heads in probe order, laid out left-to-right with `--right-of`. |
| **`mode`** | `auto` / `default` per panel, or one fixed mode for every head (e.g. `1920x1080`). |
| **`screen_width`** / **`screen_height`** | Fallback when geometry cannot be read from `xrandr`. |
| **`chrome_bin`** | Path to Chrome or Chromium. |
| **`startup_delay_sec`** | Delay (seconds) before the first kiosk window launch for any setup; useful when target apps/services need extra startup time. If set to `0`, kiosk auto-adds **60s** when Docker is installed and at least one Docker image exists. |
| **`displays`** | Array of objects **`{ "url": "…", "rotation": "normal", "mode": "" }`**, in order: index **0** = first / leftmost monitor, then **1**, … up to **24** slots in the web form. `mode` is per-display resolution (`auto`, `default`, or values like `1920x1080`); empty falls back to global `mode`. |

**`rotation`** values: `normal`, `left`, `right`, `inverted`.

If you have **more monitors than non-empty URLs** in `displays`, the **last** defined URL (and its rotation) is **repeated** for the extra heads. If you have **fewer monitors than entries**, extra `displays` entries are ignored for layout (only the first *N* are used, where *N* = number of outputs in use).

If you move the install to another PC and connector names changed, the launcher automatically falls back to all currently connected outputs (auto order) instead of failing on stale `output_list` names.

Changes are applied immediately when you click **Save** in the web UI (it writes `kiosk.json` and triggers a browser relaunch). Direct edits to **`kiosk.json`** are also picked up by the watcher within a few seconds.

Example (abbreviated):

```json
{
  "output_list": "auto",
  "mode": "auto",
  "screen_width": "1024",
  "screen_height": "768",
  "chrome_bin": "/usr/bin/google-chrome-stable",
  "startup_delay_sec": "0",
  "displays": [
    { "url": "https://example.com/", "rotation": "normal", "mode": "1920x1080" },
    { "url": "https://example.org/", "rotation": "normal", "mode": "1080x1920" }
  ]
}
```

On first install, **`install-kiosk.sh`** creates **`kiosk.json`** if it is missing. If you upgrade from an older layout, the installer **migrates** from **`kiosk.env`** and **`kiosk-urls/NN.txt`** (and legacy **`kiosk-url/display.txt`**) into **`kiosk.json`** once.

### Displays on 24/7 (no sleep, no X blanking)

The installer aims to keep panels awake around the clock:

- **systemd-logind** (`/etc/systemd/logind.conf.d/99-kiosk-no-sleep.conf`): lid and idle sleep ignored; **`sleep.target` / `suspend.target` / `hibernate.target` / `hybrid-sleep.target`** are masked.
- **Xorg** (`/etc/X11/xorg.conf.d/10-kiosk-no-blanking.conf`): server blank/standby/suspend/off timers set to **0** (the default X blank interval is often ~10 minutes otherwise).
- **Openbox session** (`kiosk` autostart): **`xset`** turns off the screensaver, **DPMS**, and “noblank”; the URL watcher **re-applies** those settings about every minute in case a driver or browser toggles them back.
- **Chrome session locks:** before launching kiosk windows, the autostart script clears stale Chrome/Chromium singleton lock/socket files to avoid “Opening in existing browser session” on reboot or crash recovery.
- **Kernel** (`/etc/default/grub.d/zz-kiosk-consoleblank.cfg`): **`consoleblank=0`** on the kernel command line (after `update-grub` + reboot) so text virtual consoles do not blank independently of X.

If a **TV or monitor** still powers down, check its own **ECO / power timer / HDMI CEC** menus; that behavior is outside the PC.

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

- **Program:** `/usr/lib/kiosk-webui/kiosk-webui.py` · **CLI:** `kiosk-webui` (symlink) · **Config:** `/etc/kiosk-webui.env`
- **Desktop:** “Kiosk display config” in the system application menu (uses `xdg-open` to `http://127.0.0.1:8780/`).
- Service: `kiosk-webui.service` (runs as user `kiosk`), **enabled on boot** via `multi-user.target` and `graphical.target` (starts even before the GUI session is up).
- Default listen: **`0.0.0.0:8780`** — **no token**; host and port come from **`BIND`** and **`PORT`** in `/etc/kiosk-webui.env` (defaults shown here).

### Web URLs (config UI)

Use plain **HTTP** (not HTTPS). Replace **`<host>`** with the kiosk’s IP or hostname, and **`<port>`** with `PORT` (default **8780**).

| What | URL |
|------|-----|
| **Settings form** (same machine) | `http://127.0.0.1:8780/` |
| **Settings form** (another PC on the LAN) | `http://<host>:8780/` |
| **After save** (redirect) | `http://<host>:8780/?ok=1` |
| **Connected outputs (JSON)** | `http://<host>:8780/api/xrandr.json` |
| **Save** (browser only) | `POST` to `http://<host>:8780/save` (use the form; no separate login page) |
| **Reboot system** (browser button) | `POST` to `http://<host>:8780/reboot` |
| **Update WebUI** (browser button) | `POST` to `http://<host>:8780/update` (runs installer refresh in background) |

- **`BIND=0.0.0.0`** (default): listen on **all interfaces** — use the kiosk’s LAN address from another device, or `127.0.0.1` on the kiosk itself.
- **`BIND=127.0.0.1`**: only **this machine** can open the URLs above; use SSH port forwarding to reach it remotely.
- **`KIOSK_WEBUI_UPDATE_CHECK_MIN`** (optional env var): background task interval (minutes) for checking whether a newer WebUI version exists; default **10**.

The form edits **`output_list`**, global video settings, and up to **24** URL/rotation rows. Each row is labeled for the matching monitor (from `output_list` or live `xrandr` when `output_list` is `auto`). The URL in a row is what Chrome opens fullscreen on that monitor. Saving writes **`/home/kiosk/.config/kiosk.json`**; trailing rows with an empty URL are dropped.

If a rotation does not apply on a specific host/GPU, check the kiosk log (`/home/kiosk/.config/kiosk-autostart.log`) for `xrandr` warnings; the launcher now retries rotation per output when a combined `xrandr` command fails.

**API example** (on the kiosk):

```bash
curl -sS http://127.0.0.1:8780/api/xrandr.json
```

**Security:** there is **no authentication** on the web UI. Anyone who can reach the port can change kiosk URLs/settings, **trigger a reboot**, and run the **Update WebUI** action (installer grants `kiosk` passwordless access to limited helper commands via `/etc/sudoers.d/`). For a closed network or single-machine use, set **`BIND=127.0.0.1`** in `/etc/kiosk-webui.env` and use SSH port forwarding if you need remote access. After editing the env file: `sudo systemctl restart kiosk-webui`.

## Repository layout

| File | Description |
|------|-------------|
| `install-kiosk.sh` | Main installer: LightDM, Openbox, Chrome, multi-head `xrandr`, autologin, sleep + X blanking off, web UI unit |
| `kiosk-webui.py` | Minimal Python 3 stdlib config UI (edits `kiosk.json`, up to 24 display slots, per-display labels) |

On the installed machine, **`kiosk.json`** lives under **`/home/kiosk/.config/`** and is not committed in this repo.

`.gitattributes` forces **LF** line endings for `*.sh`, `*.py`, and `*.txt` so scripts run correctly on Linux.

### Git: auto-renormalize on commit (optional)

After cloning, point Git at the repo’s hooks once (from the repo root):

```bash
git config core.hooksPath githooks
```

Each **`git commit`** then runs **`git add --renormalize`** on **staged paths only**, so line endings match `.gitattributes` without restaging the whole tree.

## License

Use and modify at your own risk for kiosk / signage deployments. No warranty implied.

