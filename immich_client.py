"""
Minimal Immich REST API client for MicroPython.

All calls are synchronous (blocking). Images are returned as raw bytes.
"""

import urequests
import json


class ImmichError(Exception):
    pass


class ImmichClient:
    def __init__(self, base_url, api_key):
        self.base = base_url.rstrip("/")
        self._headers = {"x-api-key": api_key, "Accept": "application/json"}

    def _get(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + "&".join("{}={}".format(k, v) for k, v in params.items())
        resp = urequests.get(url, headers=self._headers)
        if resp.status_code not in (200, 201):
            resp.close()
            raise ImmichError("HTTP {} for {}".format(resp.status_code, path))
        data = resp.json()
        resp.close()
        return data

    # ------------------------------------------------------------------
    def ping(self):
        """Return True if the server responds."""
        try:
            self._get("/api/server/ping")
            return True
        except Exception:
            return False

    def get_albums(self):
        """Return list of dicts: [{id, albumName, assetCount, ...}]"""
        return self._get("/api/albums")

    def get_album(self, album_id):
        """Return album dict including 'assets' list."""
        return self._get("/api/albums/{}".format(album_id))

    def get_all_assets(self, album_ids, on_progress=None):
        """
        Return a flat list of asset dicts from the given album IDs.
        If album_ids is empty, fetches all assets from all albums.

        on_progress(done, total, album_name, asset_count) is called:
          - once before fetching each album  (done = index before fetch)
          - once after  fetching each album  (done = index + 1, name known)
        Any argument may be None/0 if not yet available.
        """
        if not album_ids:
            if on_progress:
                on_progress(0, 1, "Fetching album list...", 0)
            albums = self.get_albums()
            album_ids = [a["id"] for a in albums]

        assets = []
        seen = set()
        total = len(album_ids)
        for i, aid in enumerate(album_ids):
            # Signal that we are about to fetch this album
            if on_progress:
                on_progress(i, total, "Album {}/{}".format(i + 1, total), len(assets))
            try:
                album = self.get_album(aid)
                name = album.get("albumName", "")
                for asset in album.get("assets", []):
                    if asset["id"] not in seen:
                        seen.add(asset["id"])
                        assets.append(asset)
                # Signal completion of this album (name now known)
                if on_progress:
                    on_progress(i + 1, total, name, len(assets))
            except ImmichError:
                if on_progress:
                    on_progress(i + 1, total, "(error)", len(assets))
        return assets

    def download_thumbnail(self, asset_id, size="thumbnail"):
        """
        Download a JPEG thumbnail for the given asset into RAM.
        Use 'thumbnail' (small, ~250px) to keep memory usage low.
        Returns raw bytes.
        """
        url = "{}/api/assets/{}/thumbnail?size={}".format(
            self.base, asset_id, size
        )
        headers = dict(self._headers)
        headers["Accept"] = "image/jpeg,image/*"
        resp = urequests.get(url, headers=headers)
        if resp.status_code != 200:
            resp.close()
            raise ImmichError("Thumbnail HTTP {}".format(resp.status_code))
        data = resp.content
        resp.close()
        return data

    def stream_thumbnail(self, asset_id, dest_path, size="preview"):
        """
        Stream a thumbnail directly to a file without loading it into RAM.
        Use with a path on the SD card and jpegdec.open_file().
        """
        url = "{}/api/assets/{}/thumbnail?size={}".format(
            self.base, asset_id, size
        )
        headers = dict(self._headers)
        headers["Accept"] = "image/jpeg,image/*"
        resp = urequests.get(url, headers=headers)
        if resp.status_code != 200:
            resp.close()
            raise ImmichError("Thumbnail HTTP {}".format(resp.status_code))
        try:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.raw.read(4096)
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            resp.close()
