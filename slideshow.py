"""
Photo slideshow for Pimoroni Presto.

- Streams Immich thumbnails to the SD card and decodes via jpegdec
- Falls back to small RAM thumbnails if no SD card is present
- Touch left half  → previous photo
- Touch right half → next photo
- Touch top bar   → show info overlay (album name / date)
- Long press      → return to config (clears Immich config)
"""

import time
import gc
import random
import machine

import config_manager
import display_utils
from immich_client import ImmichClient, ImmichError

try:
    import jpegdec
    _HAVE_JPEG = True
except ImportError:
    _HAVE_JPEG = False

_LONG_PRESS_MS = 1500
_INFO_BAR_H = 40
_THUMB_SIZE = "preview"
_SD_CACHE = "/sd/presto_img.jpg"


def _mount_sd():
    """Try to mount the SD card using Presto's SPI pins. Returns True on success."""
    import os
    try:
        os.listdir("/sd")
        return True
    except OSError:
        pass
    try:
        import sdcard
        import uos
        sd_spi = machine.SPI(
            0,
            sck=machine.Pin(34, machine.Pin.OUT),
            mosi=machine.Pin(35, machine.Pin.OUT),
            miso=machine.Pin(36, machine.Pin.OUT),
        )
        sd = sdcard.SDCard(sd_spi, machine.Pin(39))
        uos.mount(sd, "/sd")
        return True
    except Exception as e:
        print("SD mount failed:", e)
        return False


def _pick_scale(img_w, img_h, disp_w, disp_h):
    """
    Return (jpegdec scale constant, scaled_w, scaled_h) that fills the display
    as much as possible while maintaining aspect ratio.

    Strategy:
    - If the image fits at full scale, centre it (jpegdec cannot upscale).
    - Otherwise prefer the largest scale where the crop on each axis is ≤ 40 %
      of the scaled dimension — this fills one axis completely and clips the
      other slightly, matching photo-frame "cover" behaviour.
    - Fall back to the largest fully-contained scale if every scale clips too much.
    """
    # Image already fits — centre at native size (no upscale possible)
    if img_w <= disp_w and img_h <= disp_h:
        return jpegdec.JPEG_SCALE_FULL, img_w, img_h

    scales = (
        (jpegdec.JPEG_SCALE_FULL,    1),
        (jpegdec.JPEG_SCALE_HALF,    2),
        (jpegdec.JPEG_SCALE_QUARTER, 4),
        (jpegdec.JPEG_SCALE_EIGHTH,  8),
    )

    contain = None  # best fully-contained scale (fallback)

    for const, div in scales:
        sw, sh = img_w // div, img_h // div
        if sw <= disp_w and sh <= disp_h:
            if contain is None:
                contain = (const, sw, sh)
            continue  # keep looking for a scale with less wasted space
        # Image still larger than display on at least one axis.
        # Accept if crop fraction on each axis stays within 40 %.
        w_crop = (sw - disp_w) / sw if sw > disp_w else 0.0
        h_crop = (sh - disp_h) / sh if sh > disp_h else 0.0
        if w_crop <= 0.40 and h_crop <= 0.40:
            return const, sw, sh

    return contain or (jpegdec.JPEG_SCALE_EIGHTH, img_w // 8, img_h // 8)


def _is_jpeg(path_or_bytes):
    """Return True if the data starts with the JPEG magic bytes FF D8 FF."""
    try:
        if isinstance(path_or_bytes, str):
            with open(path_or_bytes, "rb") as f:
                header = f.read(3)
        else:
            header = path_or_bytes[:3]
        return header == b"\xff\xd8\xff"
    except Exception:
        return False


def _format_date(ts_str):
    if not ts_str:
        return ""
    return ts_str[:10]


class Slideshow:
    def __init__(self, display, touch, cfg):
        self.display = display
        self.touch = touch
        self.cfg = cfg
        self.w, self.h = display.get_bounds()
        self.client = ImmichClient(cfg["immich_url"], cfg["immich_api_key"])
        self.assets = []
        self.index = 0
        self._use_sd = _mount_sd()
        self._has_image = False
        self._skip_ids = set()
        print("SD card:", "OK" if self._use_sd else "not available — using thumbnail mode")

    # ------------------------------------------------------------------
    def _load_assets(self):
        display_utils.show_progress(self.display, "Loading albums...", 0.1)
        album_ids = self.cfg.get("album_ids", [])
        try:
            assets = self.client.get_all_assets(album_ids)
        except ImmichError as e:
            display_utils.show_error(self.display, "Immich error", str(e))
            time.sleep(5)
            return False
        if not assets:
            display_utils.show_error(self.display, "No photos found", "Check album config")
            time.sleep(5)
            return False
        for i in range(len(assets) - 1, 0, -1):
            j = random.randint(0, i)
            assets[i], assets[j] = assets[j], assets[i]
        self.assets = assets
        return True

    # ------------------------------------------------------------------
    def _display_asset(self, asset):
        """Download and display one asset. Returns True on success, False to skip."""
        asset_id = asset.get("id", "")
        img_bytes = None

        try:
            if not self._has_image:
                display_utils.show_progress(self.display, "Loading...", 0.5)
            if self._use_sd:
                self.client.stream_thumbnail(asset_id, _SD_CACHE, _THUMB_SIZE)
                if not _is_jpeg(_SD_CACHE):
                    print("Skipping non-JPEG asset", asset_id)
                    self._skip_ids.add(asset_id)
                    return False
            else:
                img_bytes = self.client.download_thumbnail(asset_id, "thumbnail")
                if not _is_jpeg(img_bytes):
                    print("Skipping non-JPEG asset", asset_id)
                    self._skip_ids.add(asset_id)
                    return False
        except ImmichError as e:
            print("Load error:", e)
            self._skip_ids.add(asset_id)
            return False

        if not _HAVE_JPEG:
            display_utils.show_message(
                self.display, "No jpegdec", "Install Pimoroni MicroPython"
            )
            time.sleep(3)
            return False

        try:
            pg = self.display._pg if hasattr(self.display, "_pg") else self.display
            j = jpegdec.JPEG(pg)

            if self._use_sd:
                j.open_file(_SD_CACHE)
            else:
                j.open_RAM(img_bytes)

            img_w = j.get_width()
            img_h = j.get_height()

            scale, scaled_w, scaled_h = _pick_scale(img_w, img_h, self.w, self.h)

            # Centre the scaled image; negative offsets are clipped by jpegdec
            x = (self.w - scaled_w) // 2
            y = (self.h - scaled_h) // 2

            display_utils.clear(self.display, 0, 0, 0)
            j.decode(x, y, scale, dither=True)
            self.display.update()
            self._has_image = True
            return True

        except Exception as e:
            print("Decode error:", e)
            self._skip_ids.add(asset_id)
            return False
        finally:
            del img_bytes
            gc.collect()

    # ------------------------------------------------------------------
    def _show_info(self, asset):
        ts = _format_date(asset.get("fileCreatedAt", ""))
        fname = asset.get("originalFileName", "")
        label = "{} {}".format(ts, fname)[:40]
        display_utils.show_info_overlay(self.display, label, duration=3)
        self._display_asset(asset)

    # ------------------------------------------------------------------
    def _handle_touch(self):
        if self.touch is None:
            return None

        self.touch.poll()
        if not self.touch.state:
            return None

        # Finger is down — record position and start time
        tx = self.touch.x
        ty = self.touch.y
        t_start = time.ticks_ms()
        print("Touch down at ({}, {})".format(tx, ty))

        # Block until release, checking for long press
        while self.touch.state:
            self.touch.poll()
            time.sleep_ms(20)
            if time.ticks_diff(time.ticks_ms(), t_start) > _LONG_PRESS_MS:
                # Long press confirmed — drain remaining touch then return
                while self.touch.state:
                    self.touch.poll()
                    time.sleep_ms(20)
                print("Touch: long_press at ({}, {})".format(tx, ty))
                return "long_press"

        elapsed = time.ticks_diff(time.ticks_ms(), t_start)

        if ty < _INFO_BAR_H:
            action = "info"
        elif tx < self.w // 2:
            action = "prev"
        else:
            action = "next"

        print("Touch: {} at ({}, {}) after {}ms".format(action, tx, ty, elapsed))
        return action

    # ------------------------------------------------------------------
    def _advance(self, delta):
        """Move to the next/prev displayable asset, skipping known-bad ones instantly."""
        n = len(self.assets)
        for _ in range(n):
            self.index = (self.index + delta) % n
            asset = self.assets[self.index]
            if asset.get("id") in self._skip_ids:
                continue
            if self._display_asset(asset):
                return
        display_utils.show_error(self.display, "No photos", "No JPEG images found")

    # ------------------------------------------------------------------
    def run(self):
        if not self._load_assets():
            return

        interval = self.cfg.get("slideshow_interval", 30)
        next_advance = time.time() + interval

        # Show first displayable asset
        n = len(self.assets)
        for _ in range(n):
            if self._display_asset(self.assets[self.index]):
                break
            self.index = (self.index + 1) % n
        else:
            display_utils.show_error(self.display, "No photos", "No JPEG images found")
            return

        while True:
            action = self._handle_touch()

            if action == "next" or time.time() >= next_advance:
                self._advance(1)
                next_advance = time.time() + interval

            elif action == "prev":
                self._advance(-1)
                next_advance = time.time() + interval

            elif action == "info":
                self._show_info(self.assets[self.index])
                next_advance = time.time() + interval

            elif action == "long_press":
                display_utils.show_message(
                    self.display, "Resetting config...", "Long press detected"
                )
                cfg = config_manager.load()
                cfg["immich_url"] = ""
                cfg["immich_api_key"] = ""
                cfg["album_ids"] = []
                config_manager.save(cfg)
                time.sleep(1)
                machine.reset()

            time.sleep_ms(50)
