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
# albums request per artist.
MIN_REQUEST_INTERVAL = 1.0  # seconds
MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_WAIT = 60  # seconds


class SpotifyRateLimitError(Exception):
    """Raised when Spotify's rate-limit penalty is too long to wait out.

    Retrying while the penalty window is active only extends it, so the
    caller should abort the whole run and try again later.
    """

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Spotify rate limit active; retry after {retry_after}s")


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

            if response.status_code == 429:
                # Spotify says how long to back off in the Retry-After header.
                try:
                    wait = int(response.headers.get("Retry-After", "5"))
                except ValueError:
                    wait = 5
                if wait > MAX_RATE_LIMIT_WAIT or attempt >= MAX_RATE_LIMIT_RETRIES:
                    # The penalty is too long to wait out (it can run to many
                    # minutes), and retrying during it only extends it.
                    raise SpotifyRateLimitError(wait)
                logger.warning("Rate limited by Spotify; waiting %ds before retrying", wait + 1)
                time.sleep(wait + 1)
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

        raise AssertionError("unreachable")  # loop always returns or raises

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

    def get_latest_releases(self, artist_id: str) -> list[dict]:
        """Return the artist's most recent albums/singles (one page).

        Detecting a new release only requires the newest items, and fetching
        the full catalog cost several requests per artist — enough to trip
        Spotify's Development Mode rate limit across a large followed list.
        (The Feb 2026 Dev Mode changes also cap the page size at 10.)
        """
        data = self._get(
            f"/artists/{artist_id}/albums",
            params={"include_groups": "album,single", "limit": 10, "market": "US"},
        )
        releases = data.get("items", [])
        releases.sort(key=lambda r: r.get("release_date", ""), reverse=True)
        return releases
