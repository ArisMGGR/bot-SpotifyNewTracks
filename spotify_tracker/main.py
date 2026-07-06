"""Entry point: checks the artists you follow on Spotify and notifies on new releases."""

import argparse
import logging
import time
from pathlib import Path

from tqdm import tqdm

from spotify_tracker.config import load_config
from spotify_tracker.spotify_client import SpotifyClient, SpotifyRateLimitError
from spotify_tracker.state import StateStore
from spotify_tracker.telegram import TelegramNotifier


class TqdmLoggingHandler(logging.Handler):
    """Writes log lines via tqdm so they appear above an active progress bar
    instead of mangling it."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


_handler = TqdmLoggingHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("spotify_tracker")


FOLLOWED_CACHE_MAX_AGE = 24 * 3600  # refresh the followed-artists list daily


def get_artists_to_check(spotify: SpotifyClient, state: StateStore) -> list[dict]:
    """Followed artists, most-overdue first, from cache when fresh enough."""
    artists = state.cached_followed_artists(FOLLOWED_CACHE_MAX_AGE)
    if artists is None:
        try:
            artists = spotify.get_followed_artists()
            state.set_followed_artists(artists)
            state.save()
            logger.info("Refreshed followed-artists list (%d artists)", len(artists))
        except SpotifyRateLimitError as err:
            artists = state.any_cached_followed_artists()
            if artists is None:
                raise
            logger.warning(
                "Rate limited refreshing the followed list (retry after %ds); using cached list",
                err.retry_after,
            )

    # Least recently checked first, so short rate-limited runs rotate through
    # everyone over time instead of rechecking the same few. Never-checked
    # artists sort first.
    return sorted(artists, key=lambda a: state.checked_at(a["id"]))


def check_artists(spotify: SpotifyClient, telegram: TelegramNotifier, state: StateStore) -> None:
    try:
        artists = get_artists_to_check(spotify, state)
    except SpotifyRateLimitError as err:
        logger.warning(
            "Spotify rate limit hit and no cached artist list yet (retry after %ds); skipping this run",
            err.retry_after,
        )
        return
    if not artists:
        logger.warning("Spotify returned no followed artists for this account.")
        return

    logger.info("Checking %d followed artists (most overdue first)", len(artists))
    checked = 0
    with tqdm(artists, desc="Checking artists", unit="artist") as progress:
        for artist in progress:
            artist_id = artist["id"]
            display_name = artist["name"]
            progress.set_postfix_str(display_name)
            try:
                releases = spotify.get_latest_releases(artist_id)
            except SpotifyRateLimitError as err:
                # Any further request during the penalty window extends it, so
                # stop here; already-checked artists are saved and the next run
                # starts with the ones we didn't reach.
                progress.close()
                logger.warning(
                    "Spotify rate limit hit (retry after %ds); stopping early: %d/%d artists checked, %d remaining",
                    err.retry_after,
                    checked,
                    len(artists),
                    len(artists) - checked,
                )
                return
            except Exception:
                # One artist failing shouldn't kill the whole run; the next run
                # will pick this artist up again.
                logger.exception("Failed to fetch releases for %s; skipping", display_name)
                continue

            current_ids = {release["id"] for release in releases}

            if not state.has_artist(artist_id):
                # First time we see this artist: record a baseline instead of
                # notifying about their entire back catalog.
                state.record_check(artist_id, display_name, current_ids)
                state.save()
                checked += 1
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

            # Merge rather than replace: we only fetch the newest page now, so
            # older IDs must stay known to avoid re-notifying about them later.
            state.record_check(artist_id, display_name, known_ids | current_ids)
            state.save()
            checked += 1

    logger.info("Run complete: checked all %d artists", checked)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Track the artists you follow on Spotify and send a Telegram alert when they release new music."
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
    spotify = SpotifyClient(config.spotify_client_id, config.spotify_client_secret, config.spotify_refresh_token)
    telegram = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    state = StateStore(Path(args.state_file))

    def run_once() -> None:
        check_artists(spotify, telegram, state)
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
