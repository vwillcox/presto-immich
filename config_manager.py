import json
import os

CONFIG_PATH = "/config.json"

_DEFAULTS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "immich_url": "",
    "immich_api_key": "",
    "album_ids": [],
    "slideshow_interval": 30,
    "brightness": 0.8,
    "ambient_light": True,
}


def load():
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        cfg = dict(_DEFAULTS)
        cfg.update(data)
        return cfg
    except (OSError, ValueError):
        return dict(_DEFAULTS)


def save(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)


def clear_wifi(cfg):
    cfg["wifi_ssid"] = ""
    cfg["wifi_password"] = ""
    save(cfg)


def is_wifi_configured(cfg):
    return bool(cfg.get("wifi_ssid"))


def is_immich_configured(cfg):
    return bool(cfg.get("immich_url")) and bool(cfg.get("immich_api_key"))
