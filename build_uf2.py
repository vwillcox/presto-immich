#!/usr/bin/env python3
"""
build_uf2.py — Build a single presto-photos.uf2

Combines the official Pimoroni Presto MicroPython firmware with a
LittleFS filesystem image pre-loaded with all application Python files.

Requirements (run once):
    pip install littlefs-python

Usage:
    python build_uf2.py                       # auto-increment patch → 0.0.2
    python build_uf2.py --build 1.0.0         # set an explicit version
    python build_uf2.py --firmware my.uf2     # use a local firmware file
    python build_uf2.py --output custom.uf2   # explicit output name

Build numbering (semantic versioning  MAJOR.MINOR.PATCH):
    Starts at 0.0.1.  Each run increments the patch number and saves it
    to build_number.txt.  Use --build MAJOR.MINOR.PATCH to jump to any
    version (e.g. 1.0.0 for a full release); the counter is updated so
    the next auto-run continues from there (e.g. → 1.0.1).

Flash:
    1. Hold BOOTSEL while plugging the Presto into USB
    2. A drive (RPI-RP2 or similar) mounts
    3. Copy the .uf2 onto that drive
    4. The Presto reboots directly into Presto Photos
"""

import sys
import struct
import json
import argparse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
try:
    import littlefs as _lfs_module
except ImportError:
    sys.exit(
        "\nMissing dependency — install with:\n"
        "    pip install littlefs-python\n"
    )

# ---------------------------------------------------------------------------
# UF2 wire format
# ---------------------------------------------------------------------------
_M0       = 0x0A324655
_M1       = 0x9E5D5157
_MEND     = 0x0AB16F30
_F_FAMILY = 0x00002000
_PAY      = 256          # data bytes per UF2 block

# RP2350-ARM-S family ID (Pimoroni Presto); falls back if auto-detected
RP2350_ID = 0xe48bff59

# ---------------------------------------------------------------------------
# Flash / LittleFS geometry for Pimoroni Presto (16 MB W25Q128 flash)
# These must match what the Pimoroni MicroPython firmware was compiled with.
# Override via --fs-start / --flash-size if your board differs.
# ---------------------------------------------------------------------------
FLASH_BASE     = 0x10000000
# Pimoroni Presto MicroPython is compiled with the VFS partition starting at
# the 2 MB mark (0x10200000) on the 16 MB W25Q128 flash.  The auto-detected
# firmware-end address is always lower than this, so we clamp up to 2 MB to
# land the filesystem where MicroPython actually looks for it.
MIN_FS_START   = FLASH_BASE + 2 * 1024 * 1024   # 0x10200000
LFS_BLOCK_SIZE = 4096

# ---------------------------------------------------------------------------
# Files to embed (local relative path  →  absolute path on device)
# ---------------------------------------------------------------------------
APP_FILES = [
    ("main.py",           "/main.py"),
    ("config_manager.py", "/config_manager.py"),
    ("wifi_manager.py",   "/wifi_manager.py"),
    ("display_utils.py",  "/display_utils.py"),
    ("ap_mode.py",        "/ap_mode.py"),
    ("config_server.py",  "/config_server.py"),
    ("immich_client.py",  "/immich_client.py"),
    ("slideshow.py",      "/slideshow.py"),
    ("lib/qrcode.py",     "/lib/qrcode.py"),
]

GITHUB_API = "https://api.github.com/repos/pimoroni/pimoroni-pico/releases"


# ===========================================================================
# UF2 helpers
# ===========================================================================

def _iter_blocks(data: bytes):
    """Yield (address, payload, raw_512) for every valid UF2 block."""
    for off in range(0, len(data) - 511, 512):
        b = data[off:off + 512]
        m0, m1, flags, addr, psz = struct.unpack_from("<IIIII", b)
        if m0 == _M0 and m1 == _M1:
            yield addr, b[32:32 + psz], b


def _highest_fw_addr(fw: bytes) -> int:
    """Return the first byte-after-firmware address, aligned up to LFS_BLOCK_SIZE."""
    top = max(addr + _PAY for addr, _, _ in _iter_blocks(fw))
    return (top + LFS_BLOCK_SIZE - 1) & ~(LFS_BLOCK_SIZE - 1)


def _family_id(fw: bytes) -> int:
    for _, _, blk in _iter_blocks(fw):
        flags, = struct.unpack_from("<I", blk, 8)
        fid,   = struct.unpack_from("<I", blk, 28)
        if flags & _F_FAMILY and fid:
            return fid
    return RP2350_ID


def _make_block(payload: bytes, addr: int, blk_no: int, total: int, fid: int) -> bytes:
    payload = (payload + b"\xff" * _PAY)[:_PAY]
    hdr = struct.pack("<IIIIIIII", _M0, _M1, _F_FAMILY, addr, _PAY, blk_no, total, fid)
    pad = b"\x00" * (512 - 32 - _PAY - 4)
    return hdr + payload + pad + struct.pack("<I", _MEND)


def _binary_to_blocks(data: bytes, start: int, fid: int,
                       skip_empty: bool = True) -> list:
    """
    Convert raw bytes to UF2 blocks.

    skip_empty=True (default): omit all-0xFF blocks — reduces output from ~30 MB
    to ~5 MB for a typical install.  Safe for new / just-erased devices.
    Pass skip_empty=False (--full flag) if upgrading over a previous install
    where stale LittleFS blocks could interfere.
    """
    data += b"\xff" * ((-len(data)) % _PAY)
    n = len(data) // _PAY
    _empty = b"\xff" * _PAY
    blocks = []
    for i in range(n):
        chunk = data[i * _PAY:(i + 1) * _PAY]
        if skip_empty and chunk == _empty:
            continue
        blocks.append(_make_block(chunk, start + i * _PAY, i, n, fid))
    return blocks


def _stitch(fw_blocks: list, fs_blocks: list) -> bytes:
    """Combine and renumber all blocks into a single UF2 byte string."""
    all_blks = fw_blocks + fs_blocks
    total = len(all_blks)
    out = []
    for i, blk in enumerate(all_blks):
        ba = bytearray(blk)
        struct.pack_into("<II", ba, 20, i, total)
        out.append(bytes(ba))
    return b"".join(out)


# ===========================================================================
# Firmware download
# ===========================================================================

def _download_firmware(dest: Path) -> None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "presto-photos-uf2-builder/1.0",
    }
    print("  Querying GitHub for latest Pimoroni release…")
    try:
        req = urllib.request.Request(GITHUB_API + "/latest", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            release = json.loads(r.read())
    except Exception:
        req = urllib.request.Request(GITHUB_API + "?per_page=5", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            releases = json.loads(r.read())
        release = next((r for r in releases if not r.get("prerelease")), releases[0])

    asset = next(
        (a for a in release.get("assets", [])
         if "presto" in a["name"].lower()
         and a["name"].lower().endswith(".uf2")
         and "micropython" in a["name"].lower()),
        None,
    )
    if asset is None:
        names = [a["name"] for a in release.get("assets", [])]
        print(f"  Assets in release '{release['tag_name']}': {names}")
        sys.exit(
            "\nCould not find a Presto MicroPython .uf2 automatically.\n"
            "Download one from: https://github.com/pimoroni/pimoroni-pico/releases\n"
            "Then rerun with:   python build_uf2.py --firmware <file.uf2>"
        )

    url  = asset["browser_download_url"]
    size = asset["size"] // 1024
    print(f"  Downloading {asset['name']} ({size} KB)…")

    def _progress(count, block, total):
        pct = min(100, count * block * 100 // total)
        print(f"\r  {pct:3d}%", end="", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=_progress)
    print(f"\r  Done → {dest}")


# ===========================================================================
# LittleFS image builder
# ===========================================================================

def _lfs_extract_bytes(lfs, block_count: int) -> bytes:
    """
    Extract the raw image bytes from a littlefs-python LittleFS object.
    Handles API differences across library versions.
    """
    size = block_count * LFS_BLOCK_SIZE
    ctx = lfs.context

    # Attempt 1: context.buffer (littlefs-python ≥ 0.4)
    buf = getattr(ctx, "buffer", None)
    if buf is not None:
        try:
            result = bytes(buf)
            if len(result) == size:
                return result
        except TypeError:
            pass
        # Some versions: buf is a ctypes array with .raw
        raw = getattr(buf, "raw", None)
        if raw is not None and len(raw) == size:
            return raw

    # Attempt 2: context itself is bytes-like
    try:
        result = bytes(ctx)
        if len(result) == size:
            return result
    except TypeError:
        pass

    # Attempt 3: internal _buf attribute
    _buf = getattr(ctx, "_buf", None)
    if _buf is not None:
        try:
            result = bytes(_buf)
            if len(result) == size:
                return result
        except TypeError:
            pass

    # Attempt 4: read each block individually via context.read
    if hasattr(ctx, "read"):
        out = bytearray(size)
        try:
            for i in range(block_count):
                blk = bytearray(LFS_BLOCK_SIZE)
                ctx.read(None, i, blk, LFS_BLOCK_SIZE)
                out[i * LFS_BLOCK_SIZE:(i + 1) * LFS_BLOCK_SIZE] = blk
            return bytes(out)
        except Exception:
            pass

    raise RuntimeError(
        "Cannot read LittleFS image bytes from this version of littlefs-python.\n"
        "Try: pip install --upgrade littlefs-python"
    )


def _build_lfs(app_dir: Path, block_count: int) -> bytes:
    """Create a LittleFS2 image containing all app files. Returns raw bytes."""
    # LittleFS disk_version=2 (v2 format) — may not exist in older releases
    kwargs = dict(block_size=LFS_BLOCK_SIZE, block_count=block_count)
    try:
        lfs = _lfs_module.LittleFS(**kwargs, disk_version=0x00020000)
    except TypeError:
        lfs = _lfs_module.LittleFS(**kwargs)

    # Pre-create directories (use string split — Path uses backslash on Windows)
    seen_dirs = set()
    for _, device_path in APP_FILES:
        parent = device_path.rsplit("/", 1)[0]  # e.g. "/lib/qrcode.py" → "/lib"
        if not parent or parent == "/":
            continue
        if parent in seen_dirs:
            continue
        seen_dirs.add(parent)
        try:
            lfs.makedirs(parent)
        except Exception:
            try:
                lfs.mkdir(parent.strip("/"))
            except Exception:
                pass

    # Write Python files
    any_written = False
    for local_rel, device_path in APP_FILES:
        local = app_dir / local_rel
        if not local.exists():
            print(f"  WARNING: {local_rel} not found — skipping")
            continue
        content = local.read_bytes()
        with lfs.open(device_path, "wb") as f:
            f.write(content)
        print(f"  + {device_path:<30}  {len(content):>7,} B")
        any_written = True

    if not any_written:
        sys.exit(
            f"\nNo application files found in: {app_dir}\n"
            "Run this script from the immich project directory, or pass --app-dir."
        )

    return _lfs_extract_bytes(lfs, block_count)


# ===========================================================================
# Semantic version helpers  (MAJOR.MINOR.PATCH)
# ===========================================================================

_VERSION_FILE = Path("build_number.txt")
_VERSION_START = "0.0.1"


def _parse_version(s: str) -> tuple:
    """'1.2.3' → (1, 2, 3).  Raises ValueError on bad format."""
    parts = s.strip().split(".")
    if len(parts) != 3:
        raise ValueError("expected MAJOR.MINOR.PATCH, got: {!r}".format(s))
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise ValueError("version components must be integers, got: {!r}".format(s))


def _fmt_version(major: int, minor: int, patch: int) -> str:
    return "{}.{}.{}".format(major, minor, patch)


def _next_version(current: str) -> str:
    """Increment the patch component: '0.0.5' → '0.0.6'."""
    major, minor, patch = _parse_version(current)
    return _fmt_version(major, minor, patch + 1)


def _resolve_version(requested) -> str:
    """
    Return the version string to use for this build.
    - requested is None  → auto-increment from _VERSION_FILE
    - requested is a str → validate and use as-is
    """
    if requested is not None:
        _parse_version(requested)   # raises ValueError on bad format
        return requested.strip()
    try:
        current = _VERSION_FILE.read_text().strip()
        _parse_version(current)     # validate file contents
        return _next_version(current)
    except (FileNotFoundError, ValueError):
        return _VERSION_START


# ===========================================================================
# Main
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Build a combined Presto Photos .uf2 (firmware + app files).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--firmware", metavar="FILE",
                    help="Local Pimoroni Presto .uf2 (auto-downloaded if omitted)")
    ap.add_argument("--output",   metavar="FILE", default=None,
                    help="Output file (default: presto-photos-vX.Y.Z.uf2)")
    ap.add_argument("--build",    metavar="X.Y.Z", default=None,
                    help="Version to use, e.g. 1.0.0 (default: auto-increment patch)")
    ap.add_argument("--app-dir",  metavar="DIR",  default=".",
                    help="Directory containing Python app files (default: current dir)")
    ap.add_argument("--fs-start", metavar="ADDR", type=lambda x: int(x, 0),
                    help="Override filesystem start address, e.g. 0x10200000")
    ap.add_argument("--flash-size", metavar="MB", type=int, default=16,
                    help="Total flash size in MB (default: 16)")
    ap.add_argument("--full", action="store_true",
                    help="Write the entire filesystem region (even empty blocks). "
                         "Use when upgrading over an existing install to avoid "
                         "stale LittleFS data. Produces a ~30 MB file.")
    args = ap.parse_args()

    app_dir = Path(args.app_dir).resolve()

    # ── Version ───────────────────────────────────────────────────────────────
    try:
        version = _resolve_version(args.build)
    except ValueError as e:
        sys.exit("Invalid --build version: {}".format(e))

    out_path = Path(args.output) if args.output else Path("presto-photos-v{}.uf2".format(version))

    print()
    print("╔══════════════════════════════════════════╗")
    print("║   Presto Photos — UF2 builder            ║")
    print("║   Version v{:<30}║".format(version))
    print("╚══════════════════════════════════════════╝")

    # ── 1. Firmware ─────────────────────────────────────────────────────
    print("\n[1/4] Firmware")
    if args.firmware:
        fw_path = Path(args.firmware)
        if not fw_path.exists():
            sys.exit(f"File not found: {fw_path}")
        print(f"  Using: {fw_path}")
    else:
        fw_path = Path("pimoroni-presto-firmware.uf2")
        if fw_path.exists():
            print(f"  Using cached: {fw_path}")
        else:
            _download_firmware(fw_path)

    fw_data = fw_path.read_bytes()
    fw_raw  = [blk for _, _, blk in _iter_blocks(fw_data)]
    fid     = _family_id(fw_data)
    print(f"  {len(fw_raw):,} blocks  |  family ID 0x{fid:08x}")

    # ── 2. Filesystem geometry ────────────────────────────────────────
    print("\n[2/4] Filesystem location")
    if args.fs_start:
        fs_start = args.fs_start
        print(f"  Override: 0x{fs_start:08x}")
    else:
        fs_start = _highest_fw_addr(fw_data)
        fs_start = max(fs_start, MIN_FS_START)
        print(f"  Detected firmware end: 0x{fs_start:08x}")

    flash_total = args.flash_size * 1024 * 1024
    fs_bytes    = FLASH_BASE + flash_total - fs_start
    if fs_bytes <= 0:
        sys.exit(
            f"Filesystem size is {fs_bytes} B — firmware exceeds flash size.\n"
            f"Try: --flash-size 32"
        )
    block_count = fs_bytes // LFS_BLOCK_SIZE
    print(f"  Range: 0x{fs_start:08x} – 0x{FLASH_BASE + flash_total:08x}"
          f"  ({fs_bytes // 1024 // 1024} MB,  {block_count:,} blocks)")

    # ── 3. LittleFS image ─────────────────────────────────────────────
    print("\n[3/4] Building LittleFS image")
    lfs_image = _build_lfs(app_dir, block_count)
    expected  = block_count * LFS_BLOCK_SIZE
    if len(lfs_image) != expected:
        lfs_image = (lfs_image + b"\xff" * expected)[:expected]

    # Quick local verification: remount and check files are readable
    print("  Verifying filesystem…")
    try:
        kwargs = dict(block_size=LFS_BLOCK_SIZE, block_count=block_count)
        try:
            verify_lfs = _lfs_module.LittleFS(**kwargs, disk_version=0x00020000)
        except TypeError:
            verify_lfs = _lfs_module.LittleFS(**kwargs)

        # Feed the raw image back into a new LittleFS instance via context
        ctx = verify_lfs.context
        for attr in ("buffer", "_buf", "data"):
            buf = getattr(ctx, attr, None)
            if buf is not None:
                try:
                    buf[:] = lfs_image
                    break
                except (TypeError, ValueError):
                    pass

        verify_lfs.mount()
        found = set()
        for root, dirs, files in verify_lfs.walk("/"):
            for fname in files:
                sep = "" if root.endswith("/") else "/"
                found.add(root + sep + fname)
        print(f"  Files visible in image: {sorted(found)}")
    except Exception as e:
        print(f"  WARNING: local verification failed ({e}) — image may still work on device")

    # ── 4. Combine ────────────────────────────────────────────────────
    print("\n[4/4] Assembling .uf2")
    skip = not args.full
    if args.full:
        print("  --full: including all blocks (even empty ones)")
    fs_uf2 = _binary_to_blocks(lfs_image, fs_start, fid, skip_empty=skip)
    print(f"  Firmware blocks:      {len(fw_raw):>6,}")
    print(f"  Filesystem blocks:    {len(fs_uf2):>6,}  "
          f"({'sparse — 0xFF blocks omitted' if skip else 'full'})")

    combined = _stitch(fw_raw, fs_uf2)
    out_path.write_bytes(combined)
    sz_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n  ✓  {out_path}  ({sz_mb:.1f} MB)  [v{version}]")

    # Persist the version after every successful build so the next
    # auto-run increments from here (whether --build was used or not)
    _VERSION_FILE.write_text(version)

    fname = str(out_path)
    print("""
┌─ How to flash ────────────────────────────────────┐
│  1. Hold BOOTSEL on Presto while plugging in USB  │
│  2. A drive (RPI-RP2 or similar) appears          │
│  3. Drag {fname:<41}│
│     onto that drive                               │
│  4. Presto reboots into Presto Photos             │
└───────────────────────────────────────────────────┘

First boot:
  - QR code appears -- scan it to join 'Presto-Photos' WiFi
  - Open http://192.168.4.1 and enter your home WiFi details
  - After reboot, open http://<presto-ip> to connect to Immich

Tip: if the Presto shows a filesystem error, reflash with --full
     to overwrite any stale data from a previous install.
""".format(fname=fname + "  "))


if __name__ == "__main__":
    main()
