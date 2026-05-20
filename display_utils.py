import time

# Colours (created lazily after display is set)
_pens = {}


def _pen(display, r, g, b):
    key = (r, g, b)
    if key not in _pens:
        _pens[key] = display.create_pen(r, g, b)
    return _pens[key]


def clear(display, r=0, g=0, b=0):
    display.set_pen(_pen(display, r, g, b))
    display.clear()


def show_message(display, title, subtitle=None, fg=(255, 255, 255), bg=(20, 20, 40)):
    w, h = display.get_bounds()
    clear(display, *bg)
    display.set_pen(_pen(display, *fg))
    # Centre title
    scale = 3
    char_w = 6 * scale
    tx = max(0, (w - len(title) * char_w) // 2)
    ty = h // 2 - 20 if subtitle else h // 2 - 10
    display.text(title, tx, ty, w, scale)
    if subtitle:
        scale2 = 2
        char_w2 = 6 * scale2
        sx = max(0, (w - len(subtitle) * char_w2) // 2)
        display.text(subtitle, sx, ty + 30, w, scale2)
    display.update()


def show_error(display, msg, detail=None):
    show_message(display, msg, detail, fg=(255, 80, 80), bg=(40, 0, 0))


def show_progress(display, title, pct, subtitle=None):
    """0.0 – 1.0 progress bar with optional subtitle line."""
    w, h = display.get_bounds()
    clear(display, 20, 20, 40)

    # Layout: if subtitle present, shift title up to make room
    title_y = h // 2 - 50 if subtitle else h // 2 - 30

    display.set_pen(_pen(display, 255, 255, 255))
    scale = 2
    char_w = 6 * scale
    max_chars = w // char_w
    t = title if len(title) <= max_chars else title[:max_chars]
    tx = max(0, (w - len(t) * char_w) // 2)
    display.text(t, tx, title_y, w, scale)

    if subtitle:
        display.set_pen(_pen(display, 160, 180, 220))
        sub = subtitle if len(subtitle) <= max_chars else subtitle[:max_chars]
        sx = max(0, (w - len(sub) * char_w) // 2)
        display.text(sub, sx, title_y + 26, w, scale)

    # Progress bar
    bar_x, bar_y = 20, h // 2 + 10
    bar_w, bar_h = w - 40, 12
    display.set_pen(_pen(display, 60, 60, 80))
    display.rectangle(bar_x, bar_y, bar_w, bar_h)
    display.set_pen(_pen(display, 80, 160, 255))
    fill = int(bar_w * max(0.0, min(1.0, pct)))
    if fill > 0:
        display.rectangle(bar_x, bar_y, fill, bar_h)

    # Percentage label below bar
    pct_label = "{}%".format(int(pct * 100))
    display.set_pen(_pen(display, 120, 140, 180))
    pl_x = max(0, (w - len(pct_label) * char_w) // 2)
    display.text(pct_label, pl_x, bar_y + bar_h + 6, w, scale)

    display.update()


def draw_qr(display, matrix, title=None, subtitle=None):
    """Draw a QR code matrix centred on the display."""
    w, h = display.get_bounds()

    # Embed the mandatory 4-module quiet zone into the matrix so scanners see it
    quiet = 4
    n_orig = len(matrix)
    n = n_orig + quiet * 2
    padded = [[0] * n for _ in range(n)]
    for r in range(n_orig):
        for c in range(n_orig):
            padded[r + quiet][c + quiet] = matrix[r][c]

    title_h = 26 if title else 0
    sub_h = 22 if subtitle else 0
    margin = 4

    avail_w = w - margin * 2
    avail_h = h - margin * 2 - title_h - sub_h
    module_px = max(1, min(avail_w, avail_h) // n)
    qr_size = n * module_px

    ox = (w - qr_size) // 2
    oy = margin + title_h + (avail_h - qr_size) // 2

    clear(display, 255, 255, 255)

    if title:
        display.set_pen(_pen(display, 0, 0, 0))
        scale = 2
        char_w = 6 * scale
        tx = max(0, (w - len(title) * char_w) // 2)
        display.text(title, tx, margin, w, scale)

    black = _pen(display, 0, 0, 0)
    white = _pen(display, 255, 255, 255)

    for row in range(n):
        for col in range(n):
            display.set_pen(black if padded[row][col] else white)
            display.rectangle(
                ox + col * module_px,
                oy + row * module_px,
                module_px,
                module_px,
            )

    if subtitle:
        display.set_pen(_pen(display, 0, 0, 0))
        scale2 = 2
        char_w2 = 6 * scale2
        sx = max(0, (w - len(subtitle) * char_w2) // 2)
        sy = oy + qr_size + 4
        if sy + 16 <= h:
            display.text(subtitle, sx, sy, w, scale2)

    display.update()


def show_info_overlay(display, text, duration=2):
    """Semi-transparent bottom bar with text, then restore."""
    w, h = display.get_bounds()
    bar_h = 32
    display.set_pen(display.create_pen(0, 0, 0))
    display.rectangle(0, h - bar_h, w, bar_h)
    display.set_pen(display.create_pen(220, 220, 220))
    display.text(text, 8, h - bar_h + 8, w - 8, 2)
    display.update()
    if duration:
        time.sleep(duration)
