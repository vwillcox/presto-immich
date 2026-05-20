# Presto Photos

An Immich photo-slideshow app for the **Pimoroni Presto** (RP2350 + touchscreen).

---

## Requirements

| Requirement | Notes |
|---|---|
| Pimoroni Presto | RP2350, capacitive touchscreen, 16 MB flash |
| Python 3.10+ on your PC | For running `build_uf2.py` |
| `littlefs-python` | `pip install littlefs-python` |
| Immich server | v1.90+ self-hosted, reachable on your LAN |

---

## Installing — one-file method (recommended)

`build_uf2.py` downloads the official Pimoroni firmware, embeds all the app
files into a LittleFS filesystem image, and stitches them into a single `.uf2`.

```bash
# 1. Install the one dependency
pip install littlefs-python

# 2. Build the firmware (downloads Pimoroni firmware automatically)
python build_uf2.py

# → writes presto-photos.uf2
```

**To flash:**
1. Hold **BOOTSEL** on the Presto while plugging in USB
2. A drive named **RPI-RP2** (or similar) mounts on your PC
3. Drag `presto-photos.uf2` onto that drive
4. The Presto reboots directly into Presto Photos

If you already downloaded the Pimoroni firmware separately:
```bash
python build_uf2.py --firmware pimoroni-presto-vX.Y.Z-micropython.uf2
```

---

## Installing — manual file upload (alternative)

Use **Thonny** or the **mpremote** CLI if you prefer to upload files directly.
You must first flash the [Pimoroni MicroPython firmware](https://github.com/pimoroni/pimoroni-pico/releases)
separately (it includes the required `jpegdec`, `picographics`, and `presto` modules).

```bash
pip install mpremote

mpremote cp main.py config_manager.py wifi_manager.py display_utils.py \
           ap_mode.py config_server.py immich_client.py slideshow.py :
mpremote mkdir lib
mpremote cp lib/qrcode.py :lib/qrcode.py
```

---

## First boot flow

```
Power on
  │
  ├─ No WiFi saved?
  │    └─ Hotspot: "Presto-Photos" / password: presto1234
  │       • QR code appears on screen – scan to join and open http://192.168.4.1
  │       • Enter your home WiFi SSID + password → Presto reboots
  │
  ├─ Connects to WiFi → shows IP address
  │
  ├─ No Immich configured?
  │    └─ Open http://<presto-ip> in a browser on the same network
  │       • Step 1: Enter Immich URL (e.g. http://192.168.1.50:2283) and API key
  │       • Step 2: Select albums to show (leave all unchecked = show everything)
  │       • Set slideshow interval (seconds) → Presto reboots
  │
  └─ Slideshow starts!
```

---

## Getting your Immich API key

1. Log in to Immich → click your profile picture → **Account Settings**
2. Scroll to **API Keys** → **New API Key** → copy the key

---

## Touchscreen gestures

| Gesture | Action |
|---|---|
| Tap right half | Next photo |
| Tap left half | Previous photo |
| Tap top edge | Info overlay (filename + date) |
| Long press (~1.5 s) | Reset Immich config → re-run setup |

---

## Re-configuring

- **Change WiFi**: hold the BOOTSEL button while powering on, then delete `config.json`  
  via Thonny/mpremote and reboot.
- **Change Immich settings**: long-press the screen for ~1.5 seconds.

---

## Project structure

```
main.py              – Boot orchestration
config_manager.py    – JSON config storage (/config.json on device)
wifi_manager.py      – WiFi STA/AP helpers
display_utils.py     – PicoGraphics UI helpers
ap_mode.py           – Captive-portal hotspot for WiFi setup
config_server.py     – Web UI for Immich configuration
immich_client.py     – Immich REST API client
slideshow.py         – Photo fetch, decode, touch handling
lib/
  qrcode.py          – Self-contained QR code generator (Level M, v1-6)
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| White screen on boot | Wrong MicroPython build – use Pimoroni's |
| "No jpegdec" message | Use Pimoroni MicroPython, not the official RP2040 build |
| Immich connection refused | Check URL includes port, e.g. `http://192.168.1.10:2283` |
| Photos very slow to load | Switch `_THUMB_SIZE` in `slideshow.py` from `preview` to `thumbnail` |
| QR code won't scan | Try a pure-white background; increase module size by narrowing the margin in `display_utils.draw_qr` |
