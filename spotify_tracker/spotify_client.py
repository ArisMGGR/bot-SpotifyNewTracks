"""Minimal Spotify Web API client using the client-credentials flow.

Only needs a Spotify app's client id/secret (no user login required) since
it only reads public artist and release data.
"""

import time

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


class SpotifyClient:
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = None
        self._token_expiry = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token

        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + payload["expires_in"] - 60
        return self._token

    def _get(self, url_or_path: str, params=None) -> dict:
        if url_or_path.startswith("http"):
            url = url_or_path
        else:
            url = f"{API_BASE}{url_or_path}"

        headers = {"Authorization": f"Bearer {self._get_token()}"}
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        return response.json()

    def search_artist(self, name: str) -> dict | None:
        """Resolve an artist name to a Spotify artist object (best match)."""
        data = self._get("/search", params={"q": name, "type": "artist", "limit": 1})
        items = data.get("artists", {}).get("items", [])
        return items[0] if items else None

    def get_artist_releases(self, artist_id: str) -> list[dict]:
        """Return every album/single released under this artist, newest first."""
        releases: list[dict] = []
        url = f"{API_BASE}/artists/{artist_id}/albums"
        params = {"include_groups": "album,single", "limit": 50}

        while url:
            data = self._get(url, params=params)
            releases.extend(data.get("items", []))
            url = data.get("next")
            params = None  # "next" already contains the query string

        releases.sort(key=lambda r: r.get("release_date", ""), reverse=True)
        return releases
