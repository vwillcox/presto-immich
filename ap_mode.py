"""
AP (Access Point) mode for initial WiFi setup.
- Starts a WiFi hotspot named "Presto-Photos"
- Shows a QR code on screen to connect + open the setup page
- Serves a simple HTML form to capture SSID / password
- Saves credentials and triggers a reboot
"""

import socket
import time
import machine

import config_manager
import wifi_manager
import display_utils
from lib.qrcode import encode as qr_encode

# The URL the user navigates to after joining the hotspot
SETUP_URL = "http://192.168.4.1"

_HTML = """\
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Presto Photos – WiFi Setup</title>
<style>
  body{{font-family:sans-serif;background:#1a1a2e;color:#eee;display:flex;
       flex-direction:column;align-items:center;justify-content:center;
       min-height:100vh;margin:0;padding:16px;box-sizing:border-box}}
  h1{{color:#80b0ff;margin-bottom:8px}}
  p{{color:#aaa;margin:0 0 24px}}
  form{{background:#16213e;padding:24px;border-radius:12px;
        width:100%;max-width:360px;box-shadow:0 4px 20px rgba(0,0,0,.4)}}
  label{{display:block;margin-bottom:4px;font-size:.9rem;color:#bbb}}
  input{{width:100%;padding:10px;border:1px solid #334;border-radius:6px;
         background:#0f3460;color:#fff;font-size:1rem;
         box-sizing:border-box;margin-bottom:16px}}
  button{{width:100%;padding:12px;background:#4c7fd6;color:#fff;
          border:none;border-radius:6px;font-size:1.1rem;cursor:pointer}}
  button:hover{{background:#6699ee}}
  .err{{color:#f87;font-size:.85rem;margin-top:8px}}
</style></head><body>
<h1>Presto Photos</h1>
<p>Enter your WiFi credentials</p>
<form method="post" action="/setup">
  <label>Network name (SSID)</label>
  <input type="text" name="ssid" required placeholder="My WiFi">
  <label>Password</label>
  <input type="password" name="password" placeholder="Leave blank if open">
  <button type="submit">Connect</button>
  {error}
</form></body></html>
"""

_HTML_OK = """\
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8">
<title>Saved!</title>
<style>
  body{{font-family:sans-serif;background:#1a1a2e;color:#eee;
       display:flex;flex-direction:column;align-items:center;
       justify-content:center;min-height:100vh;margin:0;padding:16px}}
  h1{{color:#80ff80}}p{{color:#aaa}}
</style></head><body>
<h1>Saved!</h1>
<p>Presto Photos is restarting and will connect to <strong>{ssid}</strong>.</p>
<p>Reconnect to your normal WiFi network.</p>
</body></html>
"""


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
            params[_urldecode(k)] = _urldecode(v)
    return params


def _recv_request(cl):
    data = b""
    cl.settimeout(5)
    try:
        while True:
            chunk = cl.recv(512)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                # Check if we have the full body for POST
                header_end = data.index(b"\r\n\r\n") + 4
                header = data[:header_end].decode("utf-8", "ignore")
                content_length = 0
                for line in header.splitlines():
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":", 1)[1].strip())
                if len(data) >= header_end + content_length:
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


def run(display):
    """Start AP mode and block until WiFi credentials are saved."""
    ap = wifi_manager.start_ap()

    # Show QR code for the WiFi hotspot credentials
    wifi_str = "WIFI:T:WPA;S:{};P:{};;".format(
        wifi_manager.AP_SSID, wifi_manager.AP_PASSWORD
    )
    try:
        matrix = qr_encode(wifi_str)
        display_utils.draw_qr(
            display,
            matrix,
            title="Scan to set up WiFi",
            subtitle=SETUP_URL,
        )
    except Exception:
        # Fallback if QR fails
        display_utils.show_message(
            display,
            "WiFi Setup",
            "Join: {}\nPW: {}\n{}".format(
                wifi_manager.AP_SSID, wifi_manager.AP_PASSWORD, SETUP_URL
            ),
        )

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(addr)
    srv.listen(3)
    srv.settimeout(1)

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
        if not lines:
            cl.close()
            continue

        try:
            method, path, _ = lines[0].split(" ", 2)
        except ValueError:
            cl.close()
            continue

        # Captive-portal detection: redirect everything except our own pages
        # so that iOS / Android / Windows shows the "Sign In" prompt.
        known = ("/", "/setup")
        if path not in known:
            try:
                cl.send(
                    "HTTP/1.0 302 Found\r\n"
                    "Location: {}\r\n"
                    "Content-Length: 0\r\n\r\n".format(SETUP_URL).encode()
                )
            except OSError:
                pass
            cl.close()
            continue

        if method == "POST" and path == "/setup":
            body = raw.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw else ""
            params = _parse_form(body)
            ssid = params.get("ssid", "").strip()
            password = params.get("password", "")

            if not ssid:
                _respond(
                    cl, "200 OK",
                    _HTML.format(error='<p class="err">SSID cannot be empty.</p>'),
                )
                continue

            # Save credentials
            cfg = config_manager.load()
            cfg["wifi_ssid"] = ssid
            cfg["wifi_password"] = password
            config_manager.save(cfg)

            _respond(cl, "200 OK", _HTML_OK.format(ssid=ssid))
            display_utils.show_message(
                display, "WiFi saved!", "Restarting...", bg=(20, 40, 20)
            )
            time.sleep(2)
            srv.close()
            machine.reset()
        else:
            _respond(cl, "200 OK", _HTML.format(error=""))
