import network
import time

AP_SSID = "Presto-Photos"
AP_PASSWORD = "presto1234"
AP_IP = "192.168.4.1"


def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    time.sleep(0.5)
    # CYW43 (Presto) uses 'ssid'/'security', not 'essid'/'authmode'.
    # CYW43_AUTH_WPA2_AES_PSK = 0x00400004; fall back to open if that fails.
    try:
        ap.config(ssid=AP_SSID, password=AP_PASSWORD,
                  security=network.CYW43_AUTH_WPA2_AES_PSK)
    except (ValueError, AttributeError):
        # Older Pimoroni builds may not expose the constant; try the raw value
        try:
            ap.config(ssid=AP_SSID, password=AP_PASSWORD, security=0x00400004)
        except ValueError:
            ap.config(ssid=AP_SSID, password=AP_PASSWORD)
    ap.active(True)
    timeout = 10
    while not ap.active() and timeout > 0:
        time.sleep(0.5)
        timeout -= 1
    # Explicitly set the AP IP so 192.168.4.1 is always reachable
    ap.ifconfig((AP_IP, "255.255.255.0", AP_IP, "8.8.8.8"))
    return ap


def stop_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(False)


def connect_sta(ssid, password, timeout=20):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        sta.disconnect()
        time.sleep(1)
    sta.connect(ssid, password)
    deadline = time.time() + timeout
    while not sta.isconnected() and time.time() < deadline:
        time.sleep(0.5)
    return sta.isconnected()


def get_ip():
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return sta.ifconfig()[0]
    return None


def disconnect_sta():
    sta = network.WLAN(network.STA_IF)
    sta.disconnect()
    sta.active(False)
