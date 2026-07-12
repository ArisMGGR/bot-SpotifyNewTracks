"""Entry point: checks the artists you follow on Spotify and notifies on new releases."""

import argparse
import calendar
import logging
import math
import time
from datetime import datetime, timedelta, timezone
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

# Only notify about releases at most this old. Anything older that shows up
# as "unseen" is Spotify catalog noise (relinked/duplicate IDs, regional
# editions, availability changes), not a new release — absorb it silently.
NOTIFY_MAX_AGE_DAYS = 30


# Baselining an artist fetches their whole catalog in one burst (one request
# per 10 releases). Spotify's limit is a rolling 30-second window, so after
# each baseline we pause proportionally to the burst size, keeping the
# sustained rate low even when many new artists are baselined back to back.
BASELINE_COOLDOWN_PER_PAGE = 2.0  # seconds per catalog page just fetched
BASELINE_COOLDOWN_MAX = 30.0  # seconds; one full rate-limit window

# Extra buffer past Spotify's Retry-After before touching the API again.
# Any request during an active penalty extends the penalty, so resuming
# even slightly early restarts the whole block.
RATE_LIMIT_SAFETY_MARGIN = 120  # seconds


def baseline_cooldown(release_count: int) -> float:
    pages = max(1, math.ceil(release_count / 10))
    return min(BASELINE_COOLDOWN_MAX, pages * BASELINE_COOLDOWN_PER_PAGE)


def released_recently(release_date: str | None, max_age_days: int = NOTIFY_MAX_AGE_DAYS) -> bool:
    """True if the release date is within the notification window.

    Spotify dates come in three precisions: "2026", "2026-07", "2026-07-08".
    Coarse dates are read as the *end* of the stated period, erring toward
    notifying rather than dropping a genuinely fresh release.
    """
    if not release_date:
        return False
    parts = release_date.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 12
        day = int(parts[2]) if len(parts) > 2 else calendar.monthrange(year, month)[1]
        released = datetime(year, month, day, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return False
    return datetime.now(timezone.utc) - released <= timedelta(days=max_age_days)


def get_artists_to_check(spotify: SpotifyClient, state: StateStore) -> list[dict]:
    """Followed artists, most-overdue first, from cache when fresh enough.

    Raises SpotifyRateLimitError if the refresh gets rate limited: a penalty
    is active, and continuing to check artists (even from a cached list)
    would keep poking the API and extend it.
    """
    artists = state.cached_followed_artists(FOLLOWED_CACHE_MAX_AGE)
    if artists is None:
        artists = spotify.get_followed_artists()
        state.set_followed_artists(artists)
        state.save()
        logger.info("Refreshed followed-artists list (%d artists)", len(artists))

    # Least recently checked first, so short rate-limited runs rotate through
    # everyone over time instead of rechecking the same few. Never-checked
    # artists sort first.
    return sorted(artists, key=lambda a: state.checked_at(a["id"]))


def record_penalty(state: StateStore, retry_after: int) -> None:
    """Persist the penalty deadline so later runs (cron or --interval) skip
    the API entirely until it has passed."""
    until = time.time() + retry_after + RATE_LIMIT_SAFETY_MARGIN
    state.set_rate_limited_until(until)
    state.save()
    logger.warning(
        "Penalty recorded: no Spotify requests until %s",
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(until)),
    )


def check_artists(spotify: SpotifyClient, telegram: TelegramNotifier, state: StateStore) -> None:
    penalty_left = state.rate_limited_until() - time.time()
    if penalty_left > 0:
        logger.warning(
            "Rate-limit penalty from a previous run still active for %.0f more minutes; skipping this run",
            penalty_left / 60,
        )
        return

    try:
        artists = get_artists_to_check(spotify, state)
    except SpotifyRateLimitError as err:
        logger.warning(
            "Spotify rate limit hit while refreshing the followed list (retry after %ds); skipping this run",
            err.retry_after,
        )
        record_penalty(state, err.retry_after)
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
            is_baseline = not state.has_artist(artist_id)
            try:
                if is_baseline:
                    # First time we see this artist: fetch the complete
                    # catalog so the baseline is exhaustive. If this raises
                    # mid-pagination, nothing is recorded (atomic) and the
                    # next run retries this artist from scratch.
                    releases = spotify.get_all_releases(artist_id)
                else:
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
                record_penalty(state, err.retry_after)
                return
            except Exception:
                # One artist failing shouldn't kill the whole run; the next run
                # will pick this artist up again.
                logger.exception("Failed to fetch releases for %s; skipping", display_name)
                continue

            current_ids = {release["id"] for release in releases}

            if is_baseline:
                # Record the full catalog as a baseline instead of notifying
                # about it.
                state.record_check(artist_id, display_name, current_ids)
                state.save()
                checked += 1
                logger.info(
                    "Baseline recorded for %s (%d releases, full catalog)", display_name, len(current_ids)
                )
                # The catalog fetch was a burst of requests; cool down before
                # the next artist so consecutive baselines stay under the
                # rolling rate-limit window.
                cooldown = baseline_cooldown(len(releases))
                progress.set_postfix_str(f"{display_name} — cooling down {cooldown:.0f}s")
                time.sleep(cooldown)
                continue

            known_ids = state.known_release_ids(artist_id)
            new_releases = [r for r in releases if r["id"] not in known_ids]

            absorbed = 0
            for release in new_releases:
                if not released_recently(release.get("release_date")):
                    # Unseen but old: catalog noise, not a new release. Merge
                    # it into the known set below without notifying.
                    absorbed += 1
                    continue
                release_kind = release.get("album_group") or release.get("album_type") or "release"
                message = (
                    f"\U0001F3B5 <b>{display_name}</b> just released:\n"
                    f"<b>{release['name']}</b> ({release_kind})\n"
                    f"Released: {release.get('release_date', 'unknown')}\n"
                    f"{release.get('external_urls', {}).get('spotify', '')}"
                )
                telegram.send_message(message)
                logger.info("Notified about new release: %s - %s", display_name, release["name"])
            if absorbed:
                logger.info(
                    "Absorbed %d unseen but old release(s) for %s without notifying", absorbed, display_name
                )

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
    spotify = SpotifyClient(
        config.spotify_client_id,
        config.spotify_client_secret,
        config.spotify_refresh_token,
        market=config.spotify_market,
    )
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
