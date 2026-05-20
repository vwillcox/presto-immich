"""
Configuration web server for Immich settings.
Served after a successful WiFi connection if the device is not yet configured.

Flow:
  GET /           → Step 1: Immich URL + API key form
  POST /immich    → Validate, fetch albums, show Step 2
  POST /albums    → Save album selection + interval, reboot
"""

import socket
import time
import machine
import json

import config_manager
import display_utils
from immich_client import ImmichClient, ImmichError

_CSS = """
body{font-family:sans-serif;background:#1a1a2e;color:#eee;
     display:flex;flex-direction:column;align-items:center;
     justify-content:center;min-height:100vh;margin:0;padding:16px;
     box-sizing:border-box}
h1{color:#80b0ff;margin-bottom:8px}
p{color:#aaa;margin:0 0 20px}
form{background:#16213e;padding:24px;border-radius:12px;
     width:100%;max-width:400px;box-sizing:border-box}
label{display:block;margin-bottom:4px;font-size:.9rem;color:#bbb}
input[type=text],input[type=password],input[type=number],select{
  width:100%;padding:10px;border:1px solid #334;border-radius:6px;
  background:#0f3460;color:#fff;font-size:1rem;
  box-sizing:border-box;margin-bottom:14px}
.check-group{max-height:220px;overflow-y:auto;background:#0f3460;
             border:1px solid #334;border-radius:6px;padding:8px;
             margin-bottom:14px}
.check-group label{display:flex;align-items:center;gap:8px;
                   padding:4px 0;cursor:pointer;font-size:.95rem;color:#ccc}
input[type=checkbox]{width:16px;height:16px;flex-shrink:0}
button{width:100%;padding:12px;background:#4c7fd6;color:#fff;
       border:none;border-radius:6px;font-size:1.1rem;cursor:pointer}
button:hover{background:#6699ee}
.err{color:#f87;font-size:.85rem;margin-top:10px}
.hint{font-size:.8rem;color:#888;margin-bottom:14px;margin-top:-10px}
"""

_TMPL_HEAD = """\
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Presto Photos – Setup</title>
<style>{css}</style></head><body>
<h1>Presto Photos</h1>
""".format(css=_CSS).replace("{", "{{").replace("}", "}}")

_STEP1 = _TMPL_HEAD + """\
<p>Connect to your Immich server</p>
<form method="post" action="/immich">
  <label>Immich server URL</label>
  <input type="text" name="url" required placeholder="http://192.168.1.10:2283"
         value="{url}">
  <label>API key</label>
  <input type="password" name="api_key" required placeholder="Your Immich API key"
         value="{api_key}">
  <label>Slideshow interval (seconds)</label>
  <input type="number" name="interval" min="5" max="3600" value="{interval}">
  <label style="flex-direction:row;align-items:center;gap:8px;display:flex">
    <input type="checkbox" name="ambient_light" value="1" {ambient_checked}
           style="width:18px;height:18px;margin-bottom:0">
    Ambient light
  </label>
  <button type="submit">Next &rarr;</button>
  {error}
</form></body></html>
"""

_STEP2_TOP = _TMPL_HEAD + """\
<p>Choose which albums to display</p>
<form method="post" action="/albums">
  <input type="hidden" name="url" value="{url}">
  <input type="hidden" name="api_key" value="{api_key}">
  <input type="hidden" name="interval" value="{interval}">
  <input type="hidden" name="ambient_light" value="{ambient_light}">
  <div class="check-group">
"""

_STEP2_BOT = """\
  </div>
  <p class="hint">Leave all unchecked to show every album.</p>
  <button type="submit">Save &amp; Start</button>
</form></body></html>
"""

_HTML_DONE = (_TMPL_HEAD + """\
<p style="color:#80ff80;font-size:1.2rem">Configuration saved!</p>
<p>Presto Photos is restarting&hellip;</p>
</body></html>
""").format()


def _urldecode(s):
    out = []
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            out.append(chr(int(s[i + 1:i + 3], 16)))
            i += 3
        elif s[i] == "+":
            out.append(" ")
            i += 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _parse_form(body):
    params = {}
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            k = _urldecode(k)
            v = _urldecode(v)
            if k in params:
                existing = params[k]
                if isinstance(existing, list):
                    existing.append(v)
                else:
                    params[k] = [existing, v]
            else:
                params[k] = v
    return params


def _recv_request(cl):
    data = b""
    cl.settimeout(10)
    try:
        while True:
            chunk = cl.recv(1024)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                hi = data.index(b"\r\n\r\n") + 4
                header = data[:hi].decode("utf-8", "ignore")
                content_length = 0
                for line in header.splitlines():
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":", 1)[1].strip())
                if len(data) >= hi + content_length:
                    break
    except OSError:
        pass
    return data.decode("utf-8", "ignore")


def _respond(cl, status, body, content_type="text/html; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode()
    cl.sendall(
        "HTTP/1.0 {}\r\nContent-Type: {}\r\nContent-Length: {}\r\n\r\n".format(
            status, content_type, len(body)
        ).encode()
    )
    cl.sendall(body)
    cl.close()


def run(display, ip, existing_cfg):
    """Block until the user has submitted Immich configuration, then reboot."""
    display_utils.show_message(
        display,
        "Setup required",
        "http://{}".format(ip),
    )

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(addr)
    srv.listen(3)
    srv.settimeout(1)

    # Pre-fill from existing config (useful on re-configuration)
    saved_url = existing_cfg.get("immich_url", "")
    saved_key = existing_cfg.get("immich_api_key", "")
    saved_interval = existing_cfg.get("slideshow_interval", 30)
    saved_ambient = existing_cfg.get("ambient_light", True)

    # Cached albums list after step-1 validation
    cached_albums = []
    cached_url = saved_url
    cached_key = saved_key
    cached_interval = saved_interval
    cached_ambient = saved_ambient

    while True:
        try:
            cl, _ = srv.accept()
        except OSError:
            continue

        raw = _recv_request(cl)
        if not raw:
            cl.close()
            continue

        lines = raw.split("\r\n")
        try:
            method, path, _ = lines[0].split(" ", 2)
        except ValueError:
            cl.close()
            continue

        body = raw.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw else ""

        if method == "GET":
            _respond(
                cl, "200 OK",
                _STEP1.format(
                    url=saved_url, api_key=saved_key,
                    interval=saved_interval,
                    ambient_checked="checked" if saved_ambient else "",
                    error=""
                ),
            )

        elif method == "POST" and path == "/immich":
            params = _parse_form(body)
            url = params.get("url", "").strip().rstrip("/")
            api_key = params.get("api_key", "").strip()
            interval = int(params.get("interval", 30) or 30)
            ambient = params.get("ambient_light") == "1"

            if not url or not api_key:
                _respond(
                    cl, "200 OK",
                    _STEP1.format(
                        url=url, api_key=api_key, interval=interval,
                        ambient_checked="checked" if ambient else "",
                        error='<p class="err">URL and API key are required.</p>'
                    ),
                )
                continue

            display_utils.show_message(display, "Checking Immich...", url)
            client = ImmichClient(url, api_key)

            try:
                albums = client.get_albums()
            except Exception as exc:
                _respond(
                    cl, "200 OK",
                    _STEP1.format(
                        url=url, api_key=api_key, interval=interval,
                        ambient_checked="checked" if ambient else "",
                        error='<p class="err">Cannot reach Immich: {}</p>'.format(exc)
                    ),
                )
                display_utils.show_message(
                    display, "Setup required", "http://{}".format(ip)
                )
                continue

            cached_url = url
            cached_key = api_key
            cached_interval = interval
            cached_ambient = ambient
            cached_albums = albums

            # Build step-2 HTML
            html = _STEP2_TOP.format(
                url=url, api_key=api_key, interval=interval,
                ambient_light="1" if ambient else "0"
            )
            for album in albums:
                aid = album.get("id", "")
                name = album.get("albumName", aid)
                count = album.get("assetCount", "?")
                html += (
                    '<label><input type="checkbox" name="album_id" value="{}">'
                    " {} ({} photos)</label>\n".format(aid, name, count)
                )
            html += _STEP2_BOT
            _respond(cl, "200 OK", html)
            display_utils.show_message(
                display, "Pick albums", "http://{}".format(ip)
            )

        elif method == "POST" and path == "/albums":
            params = _parse_form(body)
            album_ids = params.get("album_id", [])
            if isinstance(album_ids, str):
                album_ids = [album_ids]
            # Fall back to cached values in case hidden fields are missing
            url = params.get("url", cached_url).strip().rstrip("/")
            api_key = params.get("api_key", cached_key).strip()
            interval = int(params.get("interval", cached_interval) or 30)
            ambient_raw = params.get("ambient_light", "1" if cached_ambient else "0")
            ambient = ambient_raw == "1"

            cfg = config_manager.load()
            cfg["immich_url"] = url
            cfg["immich_api_key"] = api_key
            cfg["album_ids"] = list(album_ids)
            cfg["slideshow_interval"] = interval
            cfg["ambient_light"] = ambient
            config_manager.save(cfg)

            _respond(cl, "200 OK", _HTML_DONE)
            display_utils.show_message(
                display, "Saved!", "Restarting...", bg=(20, 40, 20)
            )
            time.sleep(2)
            srv.close()
            machine.reset()

        else:
            _respond(cl, "404 Not Found", "Not found")
