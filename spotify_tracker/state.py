"""Persistent state: seen releases per artist, check times, and the cached
followed-artists list.

File layout (state.json):
{
  "artists": {
    "<artist_id>": {
      "name": "...",             # for human readability of the file
      "releases": ["<album_id>", ...],
      "checked_at": 1751400000   # epoch seconds of the last successful check
    }
  },
  "followed": {
    "fetched_at": 1751400000,
    "artists": [{"id": "...", "name": "..."}, ...]
  },
  "rate_limited_until": 0     # epoch seconds; runs are skipped before this
}

Older versions of this file were just {"<artist_id>": ["<album_id>", ...]};
those are migrated on load (check times start at 0 so they sort as most
overdue).
"""

import json
import time
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {"artists": {}, "followed": None, "rate_limited_until": 0}
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._data = self._migrate(raw)

    @staticmethod
    def _migrate(raw: dict) -> dict:
        if "artists" in raw and isinstance(raw.get("artists"), dict):
            raw.setdefault("followed", None)
            raw.setdefault("rate_limited_until", 0)
            return raw
        # Old flat format: {artist_id: [release ids]}
        return {
            "artists": {
                artist_id: {"name": "", "releases": ids, "checked_at": 0}
                for artist_id, ids in raw.items()
            },
            "followed": None,
            "rate_limited_until": 0,
        }

    # --- rate-limit penalty persistence ---

    def rate_limited_until(self) -> float:
        """Epoch seconds until which Spotify's penalty is assumed active."""
        return self._data.get("rate_limited_until") or 0

    def set_rate_limited_until(self, timestamp: float) -> None:
        self._data["rate_limited_until"] = int(timestamp)

    # --- per-artist release tracking ---

    def has_artist(self, artist_id: str) -> bool:
        return artist_id in self._data["artists"]

    def known_release_ids(self, artist_id: str) -> set[str]:
        entry = self._data["artists"].get(artist_id)
        return set(entry["releases"]) if entry else set()

    def checked_at(self, artist_id: str) -> float:
        entry = self._data["artists"].get(artist_id)
        return entry.get("checked_at", 0) if entry else 0

    def record_check(self, artist_id: str, name: str, release_ids: set[str]) -> None:
        self._data["artists"][artist_id] = {
            "name": name,
            "releases": sorted(release_ids),
            "checked_at": int(time.time()),
        }

    # --- followed-artists cache ---

    def cached_followed_artists(self, max_age_seconds: float) -> list[dict] | None:
        cache = self._data.get("followed")
        if not cache:
            return None
        if time.time() - cache.get("fetched_at", 0) > max_age_seconds:
            return None
        return cache["artists"]

    def set_followed_artists(self, artists: list[dict]) -> None:
        self._data["followed"] = {
            "fetched_at": int(time.time()),
            "artists": [{"id": a["id"], "name": a["name"]} for a in artists],
        }

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
