"""
Self-contained QR code generator for MicroPython.
Supports byte-mode encoding, Error Correction Level M, versions 1-6.

Usage:
    from lib.qrcode import encode
    matrix = encode("WIFI:T:WPA;S:MySSID;P:MyPass;;")
    # matrix is a list of lists of int (0=light, 1=dark)
"""

# ---------------------------------------------------------------------------
# GF(256) arithmetic (primitive polynomial 285 = x^8+x^4+x^3+x^2+1)
# ---------------------------------------------------------------------------
_EXP = bytearray(512)
_LOG = bytearray(256)
_v = 1
for _i in range(255):
    _EXP[_i] = _v
    _LOG[_v] = _i
    _v <<= 1
    if _v > 255:
        _v ^= 285
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]
del _v, _i


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n):
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, gj in enumerate(g):
            ng[j] ^= gj
            ng[j + 1] ^= _gf_mul(gj, _EXP[i])
        g = ng
    return g


def _rs_encode(data, n_ec):
    gen = _rs_generator(n_ec)
    msg = bytearray(data) + bytearray(n_ec)
    for i in range(len(data)):
        c = msg[i]
        if c:
            for j, gj in enumerate(gen):
                msg[i + j] ^= _gf_mul(gj, c)
    return bytes(msg[len(data):])


# ---------------------------------------------------------------------------
# Version / block parameters for Level M
# (n_blocks, data_codewords_per_block, ec_codewords_per_block)
# ---------------------------------------------------------------------------
_BLOCK_M = {
    1: ((1, 16, 10),),
    2: ((1, 28, 16),),
    3: ((1, 44, 26),),
    4: ((2, 32, 18),),
    5: ((2, 43, 24),),
    6: ((4, 27, 16),),
}

# Maximum byte-mode data capacity at Level M
_CAP_M = {1: 14, 2: 26, 3: 42, 4: 62, 5: 86, 6: 106}

# Alignment-pattern grid positions (row and column indices, all combinations
# used except those that overlap finder patterns)
_ALIGN_POS = [
    [],
    [],
    [6, 18],
    [6, 22],
    [6, 26],
    [6, 30],
    [6, 34],
]

# Remainder bits appended after data+EC codewords (some versions only)
_REMAINDER = [0, 0, 7, 7, 7, 7, 7]


# ---------------------------------------------------------------------------
# Format information for Level M (EC bits = 0b00)
# Precomputed: BCH(data5) XOR 0x5412
# ---------------------------------------------------------------------------
def _fmt_info(mask):
    # data5 = mask (since EC level M = 0b00, data5 = 0b00_mmm = mask)
    g = 0x537  # BCH generator x^10+x^8+x^5+x^4+x^2+x+1
    p = mask << 10
    for i in range(14, 9, -1):
        if p & (1 << i):
            p ^= g << (i - 10)
    return ((mask << 10) | (p & 0x3FF)) ^ 0x5412


# Format info copy-1 module positions (row, col) for bits 0..14
_FMT1 = [
    (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5),
    (8, 7), (8, 8),
    (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
]


def _fmt2(size):
    return [
        (size - 1, 8), (size - 2, 8), (size - 3, 8),
        (size - 4, 8), (size - 5, 8), (size - 6, 8), (size - 7, 8),
        (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
        (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1),
    ]


# ---------------------------------------------------------------------------
# Matrix helpers
# ---------------------------------------------------------------------------
_DATA = 2   # marker: module is available for data
_FUNC = 3   # marker: function pattern (reserved)


def _make(size):
    return [[_DATA] * size for _ in range(size)]


def _set(m, r, c, val):
    m[r][c] = val  # 0 = light, 1 = dark, _FUNC = reserved


def _place_finder(m, r0, c0):
    pat = [0b1111111, 0b1000001, 0b1011101,
           0b1011101, 0b1011101, 0b1000001, 0b1111111]
    size = len(m)
    for dr in range(7):
        for dc in range(7):
            v = (pat[dr] >> (6 - dc)) & 1
            m[r0 + dr][c0 + dc] = v  # actual value, mark as func later

    # Separator (white border)
    for i in range(8):
        for dr, dc in [
            (7, i), (i, 7),          # bottom/right of top-left finder
        ]:
            nr, nc = r0 + dr, c0 + dc
            if 0 <= nr < size and 0 <= nc < size:
                m[nr][nc] = 0


def _reserve_func(m, r, c):
    if 0 <= r < len(m) and 0 <= c < len(m):
        v = m[r][c]
        # keep value if already placed, just mark as function
        m[r][c] = v if v in (0, 1) else 0
        # encode as reserved: store old value + offset
        # Actually simpler: use a separate reserved mask
        pass  # handled via _is_func


def _build_matrix(version, data_bits):
    size = 4 * version + 17
    m = _make(size)

    # Finder patterns
    _place_finder(m, 0, 0)           # top-left
    _place_finder(m, 0, size - 7)    # top-right
    _place_finder(m, size - 7, 0)    # bottom-left

    # Additional separators for top-right and bottom-left finders
    for i in range(8):
        if i < size:
            m[7][size - 1 - i] = 0   # below top-right
            m[i][size - 8] = 0       # left of top-right
            m[size - 8][i] = 0       # above bottom-left
            m[size - 1 - i][7] = 0   # right of bottom-left

    # Timing patterns
    for i in range(8, size - 8):
        v = 1 if i % 2 == 0 else 0
        m[6][i] = v
        m[i][6] = v

    # Alignment patterns
    apos = _ALIGN_POS[version]
    for r in apos:
        for c in apos:
            # Skip positions that overlap finder areas
            if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 8) or \
               (r >= size - 8 and c <= 8):
                continue
            # 5x5 alignment pattern centred at (r, c)
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    edge = abs(dr) == 2 or abs(dc) == 2
                    centre = dr == 0 and dc == 0
                    m[r + dr][c + dc] = 1 if (edge or centre) else 0

    # Dark module
    m[4 * version + 9][8] = 1

    # Reserve format information areas (write placeholder 0; real values placed later)
    for bit in range(15):
        r1, c1 = _FMT1[bit]
        m[r1][c1] = 0
        r2, c2 = _fmt2(size)[bit]
        m[r2][c2] = 0

    # Build a function-pattern mask (True = not available for data)
    func = [[False] * size for _ in range(size)]

    # Mark finders + separators (top-left 9x9, top-right 9x8, bottom-left 8x9)
    for r in range(9):
        for c in range(9):
            func[r][c] = True
    for r in range(9):
        for c in range(size - 8, size):
            func[r][c] = True
    for r in range(size - 8, size):
        for c in range(9):
            func[r][c] = True

    # Timing
    for i in range(size):
        func[6][i] = True
        func[i][6] = True

    # Alignment patterns
    for r in apos:
        for c in apos:
            if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 8) or \
               (r >= size - 8 and c <= 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    func[r + dr][c + dc] = True

    # Format info areas
    for bit in range(15):
        r1, c1 = _FMT1[bit]
        func[r1][c1] = True
        r2, c2 = _fmt2(size)[bit]
        func[r2][c2] = True

    # Dark module
    func[4 * version + 9][8] = True

    # ---------------------------------------------------------------------------
    # Place data bits using zigzag pattern
    # ---------------------------------------------------------------------------
    data_idx = 0
    going_up = True
    col = size - 1

    while col >= 0:
        if col == 6:
            col -= 1
            continue

        rows = range(size - 1, -1, -1) if going_up else range(size)
        for row in rows:
            for c in (col, col - 1):
                if c < 0:
                    continue
                if not func[row][c]:
                    if data_idx < len(data_bits):
                        m[row][c] = data_bits[data_idx]
                    else:
                        m[row][c] = 0
                    data_idx += 1

        going_up = not going_up
        col -= 2

    return m, func


def _apply_mask(m, func, mask):
    size = len(m)
    result = [row[:] for row in m]  # copy
    for r in range(size):
        for c in range(size):
            if func[r][c]:
                continue
            invert = False
            if mask == 0:
                invert = (r + c) % 2 == 0
            elif mask == 1:
                invert = r % 2 == 0
            elif mask == 2:
                invert = c % 3 == 0
            elif mask == 3:
                invert = (r + c) % 3 == 0
            elif mask == 4:
                invert = (r // 2 + c // 3) % 2 == 0
            elif mask == 5:
                invert = (r * c) % 2 + (r * c) % 3 == 0
            elif mask == 6:
                invert = ((r * c) % 2 + (r * c) % 3) % 2 == 0
            elif mask == 7:
                invert = ((r + c) % 2 + (r * c) % 3) % 2 == 0
            if invert:
                result[r][c] ^= 1
    return result


def _penalty(m):
    size = len(m)
    score = 0

    # Rule 1: 5+ consecutive same colour in each row/column
    for is_row in (True, False):
        for i in range(size):
            run = 1
            prev = m[i][0] if is_row else m[0][i]
            for j in range(1, size):
                curr = m[i][j] if is_row else m[j][i]
                if curr == prev:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    run = 1
                    prev = curr

    # Rule 2: 2x2 blocks of same colour
    for r in range(size - 1):
        for c in range(size - 1):
            v = m[r][c]
            if m[r][c + 1] == v and m[r + 1][c] == v and m[r + 1][c + 1] == v:
                score += 3

    # Rule 4: proportion of dark modules
    dark = sum(m[r][c] for r in range(size) for c in range(size))
    total = size * size
    pct = dark * 100 // total
    score += (abs(pct - 50) // 5) * 10

    return score


def _place_format(m, version, mask):
    size = len(m)
    fmt = _fmt_info(mask)
    pos2 = _fmt2(size)
    for bit in range(15):
        v = (fmt >> bit) & 1
        r1, c1 = _FMT1[bit]
        m[r1][c1] = v
        r2, c2 = pos2[bit]
        m[r2][c2] = v
    # Dark module always 1
    m[4 * version + 9][8] = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode(text):
    """
    Encode *text* (str or bytes) as a QR code matrix at Level M.
    Returns a list of lists of int (0 = light, 1 = dark).
    Raises ValueError if the text is too long (> 106 bytes).
    """
    if isinstance(text, str):
        data = text.encode("iso-8859-1")
    else:
        data = bytes(text)

    n = len(data)

    # Select version
    version = None
    for v in range(1, 7):
        if n <= _CAP_M[v]:
            version = v
            break
    if version is None:
        raise ValueError("Data too long for QR version 1-6 level M ({} bytes)".format(n))

    # Build data codewords
    bits = []
    # Mode indicator: byte = 0100
    bits += [0, 1, 0, 0]
    # Character count (8 bits for versions 1-9)
    for i in range(7, -1, -1):
        bits.append((n >> i) & 1)
    # Data bytes
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    # Terminator
    bits += [0, 0, 0, 0]

    # Total data codewords for this version/level
    blocks = _BLOCK_M[version]
    total_data = sum(b[0] * b[1] for b in blocks)
    total_bits = total_data * 8

    # Pad to byte boundary
    while len(bits) % 8 != 0:
        bits.append(0)

    # Pad codewords: alternating 0xEC 0x11
    pad = [0xEC, 0x11]
    pi = 0
    while len(bits) < total_bits:
        for i in range(7, -1, -1):
            bits.append((pad[pi] >> i) & 1)
        pi = 1 - pi

    # Convert bits → bytes
    codewords = bytearray(total_bits // 8)
    for i in range(len(codewords)):
        for b in range(8):
            codewords[i] = (codewords[i] << 1) | bits[i * 8 + b]

    # Split into blocks, generate EC codewords, interleave
    data_blocks = []
    ec_blocks = []
    idx = 0
    for n_blocks, data_per, ec_per in blocks:
        for _ in range(n_blocks):
            block = codewords[idx: idx + data_per]
            idx += data_per
            data_blocks.append(block)
            ec_blocks.append(_rs_encode(block, ec_per))

    # Interleave data blocks
    interleaved = bytearray()
    max_data = max(len(b) for b in data_blocks)
    for i in range(max_data):
        for b in data_blocks:
            if i < len(b):
                interleaved.append(b[i])

    # Interleave EC blocks
    max_ec = max(len(b) for b in ec_blocks)
    for i in range(max_ec):
        for b in ec_blocks:
            if i < len(b):
                interleaved.append(b[i])

    # Build final bit stream
    final_bits = []
    for byte in interleaved:
        for i in range(7, -1, -1):
            final_bits.append((byte >> i) & 1)
    # Remainder bits
    final_bits += [0] * _REMAINDER[version]

    # Build matrix and choose best mask
    m, func = _build_matrix(version, final_bits)

    best_mask = 0
    best_score = None
    best_matrix = None

    for mask in range(8):
        candidate = _apply_mask(m, func, mask)
        _place_format(candidate, version, mask)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best_score = score
            best_mask = mask
            best_matrix = candidate

    return best_matrix
