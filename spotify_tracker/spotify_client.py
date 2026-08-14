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

# Global pacing for every API request. Spotify's limit is a rolling 30-second
# window, so even spacing of WINDOW / MAX_PER_WINDOW guarantees the window
# count stays under the cap regardless of how requests cluster (catalog
# pagination, back-to-back baselines, the followed-list refresh). A sustained
# ~10 requests per 30s got this app blocked; 5 per 30s leaves headroom.
RATE_LIMIT_WINDOW = 30.0  # seconds
MAX_REQUESTS_PER_WINDOW = 5
MIN_REQUEST_INTERVAL = RATE_LIMIT_WINDOW / MAX_REQUESTS_PER_WINDOW


class SpotifyRateLimitError(Exception):
    """Raised on any 429 from Spotify.

    A 429 means a penalty is already active, and any request during the
    penalty window (including an in-process retry) extends it — so the
    caller must abort the whole run and try again much later. retry_after
    is Spotify's Retry-After header, or 0 if it wasn't sent.
    """

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Spotify rate limit active; retry after {retry_after}s")


class SpotifyDailyLimitError(Exception):
    """Raised before a request that would exceed the day's self-imposed call
    budget.

    Unlike a 429, this penalty is our own: Spotify allows roughly 105 calls
    per day, so the client refuses to make a call once the day's budget
    (default 100) is spent, stopping short of the point where Spotify itself
    would start blocking. The remaining artists are picked up on the next
    day's runs, once the budget resets.
    """

    def __init__(self, calls_today: int, limit: int):
        self.calls_today = calls_today
        self.limit = limit
        super().__init__(f"Daily Spotify call budget reached ({calls_today}/{limit})")


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        market: str | None = None,
        daily_call_limit: int | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._market = market
        self._app_token = None
        self._app_token_expiry = 0.0
        self._user_token = None
        self._user_token_expiry = 0.0
        self._last_request_at = 0.0
        # Count of HTTP requests actually sent to Spotify (token refreshes and
        # API calls). Reported per run so you can see how close a run came to
        # the rate limit, even when one stops it early.
        self.request_count = 0
        # Running total of calls attributed to the current calendar day,
        # spanning every run of the day (persisted between runs by the caller
        # via start_day). Spotify's daily quota is what actually blocks the app
        # long-term, so this — not request_count — is what the budget guards.
        self.daily_calls = 0
        self._daily_call_limit = daily_call_limit

    def start_day(self, calls_already_made: int) -> None:
        """Seed the day's running call total from persisted state at the start
        of a run, so the daily budget is enforced across separate runs (cron)
        as well as within one long-lived process (--interval)."""
        self.daily_calls = calls_already_made

    def _check_daily_budget(self) -> None:
        if self._daily_call_limit is not None and self.daily_calls >= self._daily_call_limit:
            raise SpotifyDailyLimitError(self.daily_calls, self._daily_call_limit)

    def _account_request(self) -> None:
        """Refuse a request that would exceed the day's budget, otherwise count
        the one about to be sent (toward both the per-run and per-day totals)."""
        self._check_daily_budget()
        self.request_count += 1
        self.daily_calls += 1

    def _get_app_token(self) -> str:
        if self._app_token and time.time() < self._app_token_expiry:
            return self._app_token

        self._account_request()
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

        self._account_request()
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

        # Bail before the pacing sleep if the day's budget is already spent —
        # no point waiting 6 seconds for a call we're going to refuse.
        self._check_daily_budget()

        # Evenly space every request so the rolling-window count can never
        # exceed MAX_REQUESTS_PER_WINDOW, no matter how calls cluster.
        elapsed = time.time() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

        token = self._get_user_token() if auth == "user" else self._get_app_token()
        headers = {"Authorization": f"Bearer {token}"}
        self._account_request()
        response = requests.get(url, headers=headers, params=params, timeout=15)
        self._last_request_at = time.time()

        if response.status_code == 429:
            # Never retry in-process: any request during the penalty window
            # extends it, and Retry-After can under-report (or be missing
            # entirely) while the block is still active.
            try:
                retry_after = int(response.headers.get("Retry-After", "0"))
            except ValueError:
                retry_after = 0
            raise SpotifyRateLimitError(retry_after)

        if not response.ok:
            # Surface Spotify's actual error body instead of a bare "400 Bad
            # Request", since that's usually the only clue as to what it
            # didn't like.
            raise requests.exceptions.HTTPError(
                f"{response.status_code} error for url {response.url}: {response.text}",
                response=response,
            )
        return response.json()

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

    def _release_params(self) -> dict:
        # A market filter hides releases not licensed in that market, so it
        # must match where the user actually listens (SPOTIFY_MARKET,
        # default GR) — not a hardcoded faraway market like the old "US".
        params = {"include_groups": "album,single", "limit": 10}
        if self._market:
            params["market"] = self._market
        return params

    def get_all_releases(self, artist_id: str) -> list[dict]:
        """Return the artist's complete albums/singles catalog (every page).

        Fetched on every check so no release can be missed because of Spotify's
        page ordering. May raise SpotifyRateLimitError mid-pagination; the
        caller must then discard the partial result rather than record it.
        """
        releases: list[dict] = []
        url = f"{API_BASE}/artists/{artist_id}/albums"
        params = self._release_params()
        while url:
            data = self._get(url, params=params)
            releases.extend(data.get("items", []))
            url = data.get("next")
            params = None
        return releases
