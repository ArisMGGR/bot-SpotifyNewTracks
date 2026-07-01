"""Spotify Web API client.

Uses two separate token types:
- an app-only client-credentials token for public catalog data (an artist's
  releases), which never requires the user to log in.
- a user access token (refreshed from a stored refresh token obtained via
  spotify_tracker.login) for data that belongs to the logged-in account,
  such as the list of artists they follow.
"""

import logging
import time

import requests

logger = logging.getLogger("spotify_tracker")

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Spacing between API requests plus 429 back-off handling. Development Mode
# apps have a low rate limit, and checking many followed artists means one
# albums request per artist (paginated 10 at a time).
MIN_REQUEST_INTERVAL = 0.6  # seconds
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_WAIT = 120  # seconds; give up rather than sleep for hours


class SpotifyClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._app_token = None
        self._app_token_expiry = 0.0
        self._user_token = None
        self._user_token_expiry = 0.0
        self._last_request_at = 0.0

    def _get_app_token(self) -> str:
        if self._app_token and time.time() < self._app_token_expiry:
            return self._app_token

        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        self._app_token = payload["access_token"]
        self._app_token_expiry = time.time() + payload["expires_in"] - 60
        return self._app_token

    def _get_user_token(self) -> str:
        if self._user_token and time.time() < self._user_token_expiry:
            return self._user_token

        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            auth=(self._client_id, self._client_secret),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        self._user_token = payload["access_token"]
        self._user_token_expiry = time.time() + payload["expires_in"] - 60
        return self._user_token

    def _get(self, url_or_path: str, params=None, *, auth: str = "app") -> dict:
        if url_or_path.startswith("http"):
            url = url_or_path
        else:
            url = f"{API_BASE}{url_or_path}"

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            # Keep a minimum gap between requests so a big followed-artists
            # list doesn't burst straight into Spotify's rate limit.
            elapsed = time.time() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL:
                time.sleep(MIN_REQUEST_INTERVAL - elapsed)

            token = self._get_user_token() if auth == "user" else self._get_app_token()
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(url, headers=headers, params=params, timeout=15)
            self._last_request_at = time.time()

            if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                # Spotify says how long to back off in the Retry-After header.
                try:
                    wait = int(response.headers.get("Retry-After", "5"))
                except ValueError:
                    wait = 5
                wait = min(wait + 1, MAX_RATE_LIMIT_WAIT)
                logger.warning("Rate limited by Spotify; waiting %ds before retrying", wait)
                time.sleep(wait)
                continue

            if not response.ok:
                # Surface Spotify's actual error body instead of a bare "400 Bad
                # Request", since that's usually the only clue as to what it
                # didn't like.
                raise requests.exceptions.HTTPError(
                    f"{response.status_code} error for url {response.url}: {response.text}",
                    response=response,
                )
            return response.json()

        raise requests.exceptions.HTTPError(f"Still rate limited after retries: {url}")

    def get_followed_artists(self) -> list[dict]:
        """Return every artist the logged-in user follows."""
        artists: list[dict] = []
        url = f"{API_BASE}/me/following"
        params = {"type": "artist", "limit": 50}

        while url:
            data = self._get(url, params=params, auth="user")
            block = data.get("artists", {})
            artists.extend(block.get("items", []))
            url = block.get("next")
            params = None

        return artists

    def get_artist_releases(self, artist_id: str) -> list[dict]:
        """Return every album/single released under this artist, newest first."""
        releases: list[dict] = []
        url = f"{API_BASE}/artists/{artist_id}/albums"
        # Spotify's February 2026 Development Mode changes lowered the max
        # page size for catalog endpoints from 50 down to 10.
        params = {"include_groups": "album,single", "limit": 10, "market": "US"}

        while url:
            data = self._get(url, params=params)
            releases.extend(data.get("items", []))
            url = data.get("next")
            params = None  # "next" already contains the query string

        releases.sort(key=lambda r: r.get("release_date", ""), reverse=True)
        return releases
