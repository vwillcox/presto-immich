"""
Minimal Immich REST API client for MicroPython.

All calls are synchronous (blocking). Images are returned as raw bytes.
"""

import urequests
import json
import gc


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
        try:
            data = resp.json()
        finally:
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
        Return a flat list of minimal asset dicts from the given album IDs.
        If album_ids is empty, fetches all assets from all albums.

        Each returned dict contains only the fields the slideshow needs:
            {"id", "originalFileName", "fileCreatedAt"}
        The full album JSON is discarded immediately to keep RAM free.

        on_progress(done, total, album_name, asset_count) is called:
          - once before fetching each album  (done = index before fetch)
          - once after  fetching each album  (done = index + 1, name known)
        """
        if not album_ids:
            if on_progress:
                on_progress(0, 1, "Fetching album list...", 0)
            albums = self.get_albums()
            album_ids = [a["id"] for a in albums]
            del albums
            gc.collect()

        assets = []
        seen = set()
        total = len(album_ids)
        for i, aid in enumerate(album_ids):
            if on_progress:
                on_progress(i, total, "Album {}/{}".format(i + 1, total), len(assets))
            try:
                album = self.get_album(aid)
                name = album.get("albumName", "")
                # Strip each asset to only the 3 fields used by the slideshow.
                # This frees the bulk of the album JSON as soon as possible.
                for asset in album.get("assets", []):
                    asset_id = asset.get("id")
                    if asset_id and asset_id not in seen:
                        seen.add(asset_id)
                        assets.append({
                            "id": asset_id,
                            "originalFileName": asset.get("originalFileName", ""),
                            "fileCreatedAt": asset.get("fileCreatedAt", ""),
                        })
                del album
                gc.collect()
                if on_progress:
                    on_progress(i + 1, total, name, len(assets))
            except Exception as e:
                print("Error loading album {}: {}".format(aid, e))
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
