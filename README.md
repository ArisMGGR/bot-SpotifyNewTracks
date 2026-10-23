# Spotify New Tracks Notifier

Watches the artists you follow on Spotify and sends you a Telegram message
whenever one of them releases a new album or single.

## Setup

1. **Install dependencies** (Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Spotify app** at https://developer.spotify.com/dashboard:
   - Click **Create app**, fill in any name/description.
   - Under **Redirect URIs**, add exactly: `http://127.0.0.1:8888/callback`
     (if port 8888 is taken, register another, e.g.
     `http://127.0.0.1:9090/callback`, and set the same value as
     `SPOTIFY_REDIRECT_URI` in `.env`; the login step listens on its port)
   - Check **Web API** and save.
   - On the app's **Settings** page, copy the **Client ID** and (after
     clicking "View client secret") the **Client secret**.

3. **Create a Telegram bot**: message [@BotFather](https://t.me/BotFather),
   run `/newbot`, and copy the bot token it gives you.

4. **Get your chat ID**: message your new bot once, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   read the `chat.id` field from the response.

5. **Configure secrets**: copy `.env.example` to `.env` and fill in
   `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`:
   ```bash
   cp .env.example .env
   ```

6. **Authorize the app to read your followed artists** (one-time):
   ```bash
   python -m spotify_tracker.login
   ```
   This opens your browser to log in and approve access, then saves
   `SPOTIFY_REFRESH_TOKEN` into `.env` automatically. Spotify sends the
   browser back to `127.0.0.1:8888` on the machine running the command, so
   on a server without a browser either run this step on your own computer
   and copy the `SPOTIFY_REFRESH_TOKEN=` line into the server's `.env`, or
   forward the port while it runs (`ssh -L 8888:127.0.0.1:8888 <server>`)
   and open the printed link locally. The login gives up after 5 minutes if
   no redirect arrives.

## Usage

Run a single check (good for cron):
```bash
python -m spotify_tracker.main
```

Or keep it running and poll every N seconds (e.g. every hour):
```bash
python -m spotify_tracker.main --interval 3600
```

Every run also saves a copy of its console output to a dated file under
`logs/` in the repo (e.g. `logs/2026-08-06.log`) — one file per day, so with
cron or `--interval` every check on the same day is appended to that day's
log. `state.json` and `logs/` default to the repo root whatever directory
you run from. Change the log location with `--log-dir`:
```bash
python -m spotify_tracker.main --log-dir /var/log/spotify-tracker
```

### How it works

- The program fetches every artist your Spotify account follows
  (`GET /me/following`), so there's no name list to maintain —
  following/unfollowing an artist in Spotify is how you manage what's
  tracked. The list is cached in `state.json` and refreshed at most once
  every 24 hours, so a newly followed artist can take up to a day to be
  picked up.
- Each check of an artist fetches their **10 newest albums and 10 newest
  singles**: one API request each, so every artist costs 2 calls no matter
  how big their catalog is. Albums and singles are requested separately
  because a combined request lists every album before any single, which can
  push a new single many pages deep.
- The first time an artist is checked, those newest releases are recorded as
  a baseline (no notification), so you don't get alerts for music that was
  out before you started tracking them. If a rate limit interrupts the
  check, nothing is recorded and the next run retries that artist.
- Every API request is spaced at least 6 seconds from the previous one
  (at most 5 requests in any rolling 30-second window, the window Spotify
  rate-limits on), regardless of how requests cluster.
- On every later check the newest releases are compared against
  `state.json`, which remembers release IDs already seen.
- Each run reports how many Spotify API requests it made (`Spotify API
  requests this run: N`), printed at the end of the run — and also when a
  rate limit stops the run early or it crashes — so you can see how close a
  run came to the limit. The count includes token refreshes and appears in
  both the console and that day's `logs/` file.
- Spotify's daily quota for this app is about 105 requests, so the program
  keeps a running total of the calls it makes across every run of the
  calendar day (persisted in `state.json`, reset at UTC midnight) and stops
  at **100** — leaving headroom before Spotify itself starts blocking. Once
  the day's budget is spent, later runs that day exit without touching the
  API and print `Spotify API calls used today: N/100`.
- When fewer calls are left in the day than one more artist needs (2, plus
  1 spare for a token refresh), the run stops checking instead of starting a
  check that would hit the limit halfway. The unchecked artists stay "most
  overdue" and go first once the budget resets. Each artist's actual cost is
  saved as `last_cost` in `state.json`.
- Only unseen releases dated within the last 30 days trigger a Telegram
  message. Older unseen IDs (Spotify catalog noise: reissues, regional
  editions, relinked duplicates) are absorbed into the state silently. If
  one check finds the same new release twice under different IDs (same name
  and date), you get one alert.
- When Spotify imposes a rate-limit penalty (any 429), the run stops
  immediately — never retrying in-process — and the deadline is saved to
  `state.json`. Every run before that deadline exits without touching the
  API at all. Because `Retry-After` can under-report (or be missing) while
  the block is still active, the recorded penalty is never shorter than
  30 minutes, plus a 2-minute safety margin — requests made during an
  active penalty extend it, so waiting it out fully is the only way to
  recover.
- If Spotify can't be reached at all, or an access token can't be fetched
  (for example a revoked refresh token — the log shows Spotify's reason),
  the run stops with an error instead of spending a call on every
  remaining artist. Artists already checked are kept.
- Releases are **not** filtered by country. With the Greek (`GR`) filter
  the tracker used to apply, Spotify's API left out some releases that
  Spotify's own artist page lists, such as Bastille's collaborations with
  Misfits Together, so they were never alerted on. `SPOTIFY_MARKET` in
  `.env` is now ignored.

### Features on other artists' releases ("Appears On")

By default only an artist's own albums and singles are checked. To also get
alerts when an artist features on someone else's record, list them in `.env`
(names as Spotify shows them, or Spotify artist IDs, comma-separated):
```
APPEARS_ON_ARTISTS=Bastille, Eminem
```
Then you get messages like "**Bastille** appears on: **ODYSSEY** by
ILLENIUM". Compilations ("Pop Hits 2015" and the like) are skipped.

This costs extra API calls, so pick only the artists you care about most.
Spotify doesn't list "Appears On" releases newest first, so the **whole**
list has to be read on every check: one call per 10 releases. Bastille, for
example, appears on about 460 releases, so checking them takes about 47
extra calls, roughly half a day's budget. Each listed artist's cost is
measured and budgeted for, and the check is deferred to a later run if it
won't fit in what's left of the day. An artist whose list would take more
than 50 calls only gets its first page checked (a warning is logged). A
name that doesn't match anyone you follow is reported in the log. Because
the list is split on commas, an artist whose name contains one (e.g.
"Tyler, The Creator") must be listed by Spotify artist ID — the ID is the
last part of the artist's `open.spotify.com/artist/<ID>` link.

### Running on a schedule

#### Auto-install (recommended): a check every ~25 hours

From the repo root:
```bash
./install_cron.sh
```
This does the full setup in one command:

1. Creates a virtualenv in `./venv` and installs `requirements.txt` into it.
2. Installs a crontab entry that runs the check on a **~25-hour** cadence.

You still need a configured `.env` and a completed one-time
`python -m spotify_tracker.login` (the installer warns if `.env` is missing).

To remove the schedule later:
```bash
./install_cron.sh --uninstall
```
Other flags: `--no-venv` (install only the cron job, skip venv/pip),
`--help`.

**Why 25 hours needs a wrapper.** Plain cron can't express "every 25 hours"
— its fields are fixed clock times, so any attempt drifts. So the installed
crontab entry actually polls **hourly** and calls `scripts/run_check.sh`,
which performs a real check only once ~25 hours have elapsed since the last
one (tracked in `state/last_cron_run`, with an `flock` so runs never
overlap). The result is a genuine ~25h gap using nothing but standard cron.
To change the interval, pass `CRON_INTERVAL_HOURS` to the installer, e.g.
`CRON_INTERVAL_HOURS=12 ./install_cron.sh` (a positive whole number of hours;
anything else makes the wrapper exit with an error in `logs/cron.log`).
Re-running the installer without it goes back to 25 hours.
The wrapper appends its output to `logs/cron.log` (recreating `logs/` if it
was deleted); each run is also appended to that day's
`logs/<YYYY-MM-DD>.log` as usual.

The wrapper needs `flock` (part of util-linux on Linux; `brew install flock`
on macOS). Without it every scheduled run fails with an error in
`logs/cron.log`, and the installer warns about it up front. Cron runs jobs
with a minimal `PATH`, so the installer saves the `PATH` of the shell you ran
`./install_cron.sh` from (and any `CRON_INTERVAL_HOURS`) to `cron.env` in the
repo root, and the wrapper uses it; if you install `flock` (or move Python)
later, re-run the installer so `cron.env` picks it up.

The scheduled job uses `./venv/bin/python` if present, otherwise the system
`python3`.

#### Manual cron

If you'd rather manage cron yourself, add a one-off check per run, e.g. every
30 minutes:
```
*/30 * * * * cd /path/to/server_SpotifyNewTracks && /usr/bin/python3 -m spotify_tracker.main >> tracker.log 2>&1
```

Or just leave it running in the foreground/background with `--interval`.

## Development

The unit tests use only the standard library and never call Spotify or
Telegram. Run them from the repo root:
```bash
python -m unittest
```

## License

MIT — see [LICENSE](LICENSE).
