"""Keeps track of which release IDs have already been seen per artist."""

import json
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self._path = path
        self._data: dict[str, list[str]] = {}
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def has_artist(self, artist_id: str) -> bool:
        return artist_id in self._data

    def known_release_ids(self, artist_id: str) -> set[str]:
        return set(self._data.get(artist_id, []))

    def set_release_ids(self, artist_id: str, release_ids: set[str]) -> None:
        self._data[artist_id] = sorted(release_ids)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
