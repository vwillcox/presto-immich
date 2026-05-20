"""
Presto Photos – main entry point.

Imports are intentionally lazy (loaded only when needed) so that any
ImportError is visible in the Thonny shell rather than causing a silent
hard-fault before the display comes up.
"""

import time
import machine
import sys


class _Display:
    """Wraps PicoGraphics so display.update() calls presto.update()."""
    def __init__(self, pg, presto):
        self._pg = pg
        self._p = presto

    def __getattr__(self, name):
        return getattr(self._pg, name)

    def update(self):
        self._p.update()


def _init_display(ambient_light=True):
    """Initialise Presto display and touch.  Returns (display, touch)."""
    from presto import Presto
    p = Presto(full_res=True, ambient_light=ambient_light)
    return _Display(p.display, p), p.touch


def main():
    # ── Keep this print as the very first line so you can confirm
    #    the script is actually running in the Thonny shell ──────
    print("Presto Photos: starting")

    # Load config early so ambient_light reaches Presto() before display init
    import config_manager as _cm
    _early_cfg = _cm.load()

    try:
        display, touch = _init_display(_early_cfg.get("ambient_light", True))
        print("Display OK")
    except Exception as e:
        print("FATAL – display init failed:", e)
        sys.print_exception(e)
        time.sleep(5)
        machine.reset()
        return

    # Safe to show things on screen now
    import display_utils
    display_utils.show_message(display, "Presto Photos", "Starting up...")
    print("show_message OK")

    import config_manager
    import wifi_manager
    print("core imports OK")

    cfg = config_manager.load()

    # ── Step 1: WiFi setup ───────────────────────────────────────
    if not config_manager.is_wifi_configured(cfg):
        display_utils.show_message(display, "No WiFi", "Starting hotspot...")
        time.sleep(1)
        print("Entering AP mode")
        import ap_mode
        ap_mode.run(display)        # blocks; reboots on success
        return

    # ── Step 2: Connect to WiFi ──────────────────────────────────
    display_utils.show_message(display, "Connecting...", cfg["wifi_ssid"])
    print("Connecting to", cfg["wifi_ssid"])
    if not wifi_manager.connect_sta(cfg["wifi_ssid"], cfg["wifi_password"]):
        display_utils.show_error(display, "WiFi failed", "Check credentials")
        print("WiFi connection failed")
        time.sleep(3)
        config_manager.clear_wifi(cfg)
        machine.reset()
        return

    ip = wifi_manager.get_ip()
    print("Connected, IP:", ip)
    display_utils.show_message(display, "Connected", ip or "")
    time.sleep(1)

    # ── Step 3: Immich configuration ─────────────────────────────
    if not config_manager.is_immich_configured(cfg):
        display_utils.show_message(display, "Immich setup", "http://{}".format(ip))
        time.sleep(1)
        print("Entering config server")
        import config_server
        config_server.run(display, ip, cfg)  # blocks; reboots on success
        return

    # ── Step 4: Slideshow ────────────────────────────────────────
    cfg = config_manager.load()
    display_utils.show_message(display, "Loading photos...", "")
    print("Starting slideshow")
    from slideshow import Slideshow
    slideshow = Slideshow(display, touch, cfg)
    slideshow.run()


main()
