"""Entry point: reads artist names from a .txt file and notifies on new releases."""

import argparse
import logging
import time
from pathlib import Path

from spotify_tracker.config import load_config
from spotify_tracker.spotify_client import SpotifyClient
from spotify_tracker.state import StateStore
from spotify_tracker.telegram import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("spotify_tracker")


def read_artist_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            names.append(name)
    return names


def check_artists(
    spotify: SpotifyClient,
    telegram: TelegramNotifier,
    state: StateStore,
    artist_names: list[str],
) -> None:
    for name in artist_names:
        artist = spotify.search_artist(name)
        if artist is None:
            logger.warning("Could not find artist on Spotify: %s", name)
            continue

        artist_id = artist["id"]
        display_name = artist["name"]
        releases = spotify.get_artist_releases(artist_id)
        current_ids = {release["id"] for release in releases}

        if not state.has_artist(artist_id):
            # First time we see this artist: record a baseline instead of
            # notifying about their entire back catalog.
            state.set_release_ids(artist_id, current_ids)
            logger.info("Baseline recorded for %s (%d releases)", display_name, len(current_ids))
            continue

        known_ids = state.known_release_ids(artist_id)
        new_releases = [r for r in releases if r["id"] not in known_ids]

        for release in new_releases:
            release_kind = release.get("album_group") or release.get("album_type") or "release"
            message = (
                f"\U0001F3B5 <b>{display_name}</b> just released:\n"
                f"<b>{release['name']}</b> ({release_kind})\n"
                f"Released: {release.get('release_date', 'unknown')}\n"
                f"{release.get('external_urls', {}).get('spotify', '')}"
            )
            telegram.send_message(message)
            logger.info("Notified about new release: %s - %s", display_name, release["name"])

        state.set_release_ids(artist_id, current_ids)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Track Spotify artists and send a Telegram alert when they release new music."
    )
    parser.add_argument(
        "--artists-file",
        default="artists.txt",
        help="Path to a .txt file with one artist name per line (default: artists.txt).",
    )
    parser.add_argument(
        "--state-file",
        default="state.json",
        help="Path to the JSON file used to remember already-seen releases (default: state.json).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="If set, keep running and poll every N seconds instead of checking once and exiting.",
    )
    args = parser.parse_args()

    config = load_config()
    spotify = SpotifyClient(config.spotify_client_id, config.spotify_client_secret)
    telegram = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    state = StateStore(Path(args.state_file))

    artists_path = Path(args.artists_file)
    if not artists_path.exists():
        raise SystemExit(f"Artists file not found: {artists_path}")

    def run_once() -> None:
        artist_names = read_artist_names(artists_path)
        if not artist_names:
            logger.warning("No artist names found in %s", artists_path)
            return
        check_artists(spotify, telegram, state, artist_names)
        state.save()

    if args.interval > 0:
        logger.info("Starting polling loop every %d seconds (Ctrl+C to stop)", args.interval)
        while True:
            try:
                run_once()
            except Exception:
                logger.exception("Error while checking artists")
            time.sleep(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
