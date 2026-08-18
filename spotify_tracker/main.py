"""Entry point: checks the artists you follow on Spotify and notifies on new releases."""

import argparse
import calendar
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tqdm import tqdm

from spotify_tracker.config import load_config
from spotify_tracker.spotify_client import (
    SpotifyClient,
    SpotifyDailyLimitError,
    SpotifyRateLimitError,
)
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


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

_handler = TqdmLoggingHandler()
_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("spotify_tracker")


# Each run also writes a timestamped copy of its console output to a file in
# this directory, one file per run.
DEFAULT_LOG_DIR = "logs"


def _begin_run_log(log_dir: Path) -> logging.Handler:
    """Attach a fresh per-run log file so this run's console output is also
    saved to logs/run_<timestamp>.txt. Returns the handler to pass to
    _end_run_log when the run finishes."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = log_dir / f"run_{timestamp}.txt"
    # Guard against two runs starting in the same second (e.g. a short
    # --interval) overwriting each other's file.
    counter = 1
    while path.exists():
        path = log_dir / f"run_{timestamp}_{counter}.txt"
        counter += 1

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    logger.info("Logging this run to %s", path)
    return handler


def _end_run_log(handler: logging.Handler) -> None:
    """Detach and close a per-run log file opened by _begin_run_log."""
    logger.removeHandler(handler)
    handler.close()


FOLLOWED_CACHE_MAX_AGE = 24 * 3600  # refresh the followed-artists list daily

# Only notify about releases at most this old. Anything older that shows up
# as "unseen" is Spotify catalog noise (relinked/duplicate IDs, regional
# editions, availability changes), not a new release — absorb it silently.
NOTIFY_MAX_AGE_DAYS = 30


# Baselining an artist fetches their whole catalog (one request per 10
# releases), so a fresh state file over a large followed list means hundreds
# of requests in a single run even with per-request spacing. Cap how many
# artists get baselined per run; the rest are picked up on later runs
# (never-checked artists always sort first).
MAX_BASELINES_PER_RUN = 10

# Extra buffer past Spotify's Retry-After before touching the API again.
# Any request during an active penalty extends the penalty, so resuming
# even slightly early restarts the whole block.
RATE_LIMIT_SAFETY_MARGIN = 120  # seconds

# Never record a penalty shorter than this. Spotify's Retry-After can be
# small or missing while the block actually lasts much longer, and one
# request sent mid-penalty restarts the whole block — so when in doubt,
# stay away longer.
RATE_LIMIT_MIN_PENALTY = 30 * 60  # seconds


# Spotify's daily quota for this app is ~105 requests; stop at 100 to keep a
# margin before Spotify itself starts blocking. The count spans every run of
# the calendar day (persisted in state) and resets at UTC midnight.
DAILY_CALL_LIMIT = 100

# What to assume a never-checked artist's full-catalog fetch will cost, when
# there's no recorded cost to go on yet. Most artists paginate in a few
# requests (10 releases per page); once checked, their real cost replaces this.
DEFAULT_ARTIST_COST = 4

# Reserve one extra call on top of an artist's estimated cost, to cover a
# token refresh that might land mid-fetch.
COST_SAFETY_MARGIN = 1


def estimated_cost(state: StateStore, artist_id: str) -> int:
    """Predicted number of Spotify calls to fully check this artist, from last
    run's measured cost (plus a small margin) or a default for new artists."""
    last = state.last_cost(artist_id)
    base = last if last is not None else DEFAULT_ARTIST_COST
    return base + COST_SAFETY_MARGIN


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
    until = time.time() + max(retry_after, RATE_LIMIT_MIN_PENALTY) + RATE_LIMIT_SAFETY_MARGIN
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

    if spotify.daily_calls >= DAILY_CALL_LIMIT:
        logger.warning(
            "Daily Spotify call budget already spent (%d/%d); skipping this run until the budget resets",
            spotify.daily_calls,
            DAILY_CALL_LIMIT,
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
    except SpotifyDailyLimitError as err:
        logger.warning(
            "Daily Spotify call budget reached (%d/%d) while refreshing the followed list; skipping this run",
            err.calls_today,
            err.limit,
        )
        return
    if not artists:
        logger.warning("No followed artists to check (the followed-artists list is empty).")
        return

    logger.info("Checking %d followed artists (most overdue first)", len(artists))
    checked = 0
    baselined = 0
    deferred_baselines = 0
    budget_deferred = 0
    with tqdm(artists, desc="Checking artists", unit="artist") as progress:
        for artist in progress:
            artist_id = artist["id"]
            display_name = artist["name"]
            progress.set_postfix_str(display_name)
            is_baseline = not state.has_artist(artist_id)
            if is_baseline and baselined >= MAX_BASELINES_PER_RUN:
                # A first-time artist records their whole back catalog silently;
                # cap how many we onboard per run so a fresh state file over a
                # large followed list doesn't fire off one huge burst of
                # catalog requests. The rest are picked up on later runs.
                deferred_baselines += 1
                continue

            # Don't even start an artist we predict won't fit in the day's
            # remaining budget: get_all_releases is atomic, so hitting the
            # limit mid-fetch wastes those calls with nothing recorded. Skip
            # to the next artist (a cheaper one may still fit); this artist,
            # left unchecked, stays most-overdue and is retried first once the
            # budget resets.
            estimate = estimated_cost(state, artist_id)
            remaining = DAILY_CALL_LIMIT - spotify.daily_calls
            if estimate > remaining:
                budget_deferred += 1
                logger.info(
                    "Deferring %s: ~%d calls needed but only %d left in today's budget",
                    display_name,
                    estimate,
                    remaining,
                )
                continue

            calls_before_artist = spotify.request_count
            try:
                # Always fetch the artist's complete albums/singles catalog,
                # so no release can be missed because of Spotify's page
                # ordering. If this raises mid-pagination, nothing is recorded
                # (atomic) and the next run retries this artist from scratch.
                releases = spotify.get_all_releases(artist_id)
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
            except SpotifyDailyLimitError as err:
                # The estimate fell short (a grown catalog or a mid-fetch token
                # refresh pushed us over). The partial fetch is discarded; stop
                # cleanly and let the remaining artists wait for the reset.
                progress.close()
                logger.warning(
                    "Daily Spotify call budget reached (%d/%d); stopping: %d/%d artists checked",
                    err.calls_today,
                    err.limit,
                    checked,
                    len(artists),
                )
                return
            except Exception:
                # One artist failing shouldn't kill the whole run; the next run
                # will pick this artist up again.
                logger.exception("Failed to fetch releases for %s; skipping", display_name)
                continue

            # How many calls this full check actually cost, saved with the
            # artist so the next run can budget for them.
            cost = spotify.request_count - calls_before_artist
            current_ids = {release["id"] for release in releases}

            if is_baseline:
                # Record the full catalog as a baseline instead of notifying
                # about it.
                state.record_check(artist_id, display_name, current_ids, cost=cost)
                state.set_api_calls_today(spotify.daily_calls)
                state.save()
                checked += 1
                baselined += 1
                logger.info(
                    "Baseline recorded for %s (%d releases, full catalog, %d API calls)",
                    display_name,
                    len(current_ids),
                    cost,
                )
                continue

            known_ids = state.known_release_ids(artist_id)
            new_releases = [r for r in releases if r["id"] not in known_ids]

            # Build up the set to record as we go. A release becomes "known"
            # only once we've actually dealt with it: absorbed as old noise, or
            # successfully notified. A release whose Telegram send fails is left
            # out, so it stays unknown and is retried on the next run instead of
            # being silently marked seen (which would drop the alert entirely).
            resolved_ids = set(known_ids)
            absorbed = 0
            notify_failed = 0
            for release in new_releases:
                if not released_recently(release.get("release_date")):
                    # Unseen but old: catalog noise, not a new release. Merge
                    # it into the known set without notifying.
                    absorbed += 1
                    resolved_ids.add(release["id"])
                    continue
                release_kind = release.get("album_group") or release.get("album_type") or "release"
                message = (
                    f"\U0001F3B5 <b>{display_name}</b> just released:\n"
                    f"<b>{release['name']}</b> ({release_kind})\n"
                    f"Released: {release.get('release_date', 'unknown')}\n"
                    f"{release.get('external_urls', {}).get('spotify', '')}"
                )
                try:
                    telegram.send_message(message)
                except Exception:
                    # Don't mark this release known: leaving it out means the
                    # next run retries the alert rather than dropping it.
                    notify_failed += 1
                    logger.exception(
                        "Failed to send Telegram alert for %s - %s; will retry next run",
                        display_name,
                        release["name"],
                    )
                    continue
                resolved_ids.add(release["id"])
                logger.info("Notified about new release: %s - %s", display_name, release["name"])
            if absorbed:
                logger.info(
                    "Absorbed %d unseen but old release(s) for %s without notifying", absorbed, display_name
                )

            # resolved_ids already holds everything that should stay known:
            # the previously-known IDs, plus this run's absorbed-and-notified
            # releases, minus any whose notification failed. Merge rather than
            # replace so a release that temporarily drops out of the
            # market-filtered catalog stays known and isn't re-notified later.
            state.record_check(artist_id, display_name, resolved_ids, cost=cost)
            state.set_api_calls_today(spotify.daily_calls)
            state.save()
            checked += 1

    deferred_notes = []
    if deferred_baselines:
        deferred_notes.append(f"{deferred_baselines} first-time baselines")
    if budget_deferred:
        deferred_notes.append(f"{budget_deferred} artists over today's call budget")
    if deferred_notes:
        logger.info(
            "Run complete: checked %d artists; deferred %s to later runs",
            checked,
            " and ".join(deferred_notes),
        )
    else:
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
    parser.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help=f"Directory for per-run log files (one .txt per run; default: {DEFAULT_LOG_DIR}).",
    )
    args = parser.parse_args()
    log_dir = Path(args.log_dir)

    config = load_config()
    spotify = SpotifyClient(
        config.spotify_client_id,
        config.spotify_client_secret,
        config.spotify_refresh_token,
        market=config.spotify_market,
        daily_call_limit=DAILY_CALL_LIMIT,
    )
    telegram = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    state = StateStore(Path(args.state_file))

    def run_once(catch_errors: bool) -> None:
        # Mirror this run's console output to its own logs/run_<timestamp>.txt.
        # check_artists persists state itself on every path that mutates it
        # (per artist, on penalty, and after refreshing the followed list);
        # the finally below persists the day's call total as a backstop.
        log_handler = _begin_run_log(log_dir)
        # Seed the day's running call total from state so the budget is
        # enforced across runs; api_calls_today resets itself on a new day.
        spotify.start_day(state.api_calls_today())
        calls_before = spotify.request_count
        try:
            check_artists(spotify, telegram, state)
        except Exception:
            if not catch_errors:
                raise
            # In the polling loop, keep going after a failed run — but log the
            # error into this run's file first.
            logger.exception("Error while checking artists")
        finally:
            # Persist the day's call total even if the run crashed or stopped
            # early, so the next run inherits an accurate budget.
            state.set_api_calls_today(spotify.daily_calls)
            state.save()
            # Always report the request count — including when a rate limit
            # stopped the run early or it crashed outright. This runs before
            # the exception (if any) propagates, so the number is never lost.
            logger.info("Spotify API requests this run: %d", spotify.request_count - calls_before)
            logger.info(
                "Spotify API calls used today: %d/%d", spotify.daily_calls, DAILY_CALL_LIMIT
            )
            _end_run_log(log_handler)

    if args.interval > 0:
        logger.info("Starting polling loop every %d seconds (Ctrl+C to stop)", args.interval)
        while True:
            run_once(catch_errors=True)
            time.sleep(args.interval)
    else:
        run_once(catch_errors=False)


if __name__ == "__main__":
    main()
