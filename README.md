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
   `SPOTIFY_REFRESH_TOKEN` into `.env` automatically.

## Usage

Run a single check (good for cron):
```bash
python -m spotify_tracker.main
```

Or keep it running and poll every N seconds (e.g. every hour):
```bash
python -m spotify_tracker.main --interval 3600
```

Every run also saves a copy of its console output to its own timestamped
file under `logs/` (e.g. `logs/run_2026-08-06_15-30-00.txt`) — one file per
run, so with cron or `--interval` you get a separate log for each check.
Change the location with `--log-dir`:
```bash
python -m spotify_tracker.main --log-dir /var/log/spotify-tracker
```

### How it works

- On each run, the program fetches every artist your Spotify account
  follows (`GET /me/following`), so there's no name list to maintain —
  following/unfollowing an artist in Spotify is how you manage what's
  tracked.
- The first time an artist is checked, their **complete** albums/singles
  catalog is fetched and recorded as a baseline (no notification), so you
  don't get flooded with their back catalog and old releases can't later be
  mistaken for new ones. If a rate limit interrupts the fetch, nothing is
  recorded and the next run retries that artist. At most 10 artists are
  baselined per run — the rest are deferred to later runs — so a fresh
  `state.json` over a large followed list doesn't turn into one huge burst
  of catalog requests.
- Every API request is spaced at least 6 seconds from the previous one
  (at most 5 requests in any rolling 30-second window, the window Spotify
  rate-limits on), regardless of how requests cluster.
- On every later check only the newest page of releases is fetched and
  compared against `state.json`, which remembers release IDs already seen.
- Only unseen releases dated within the last 30 days trigger a Telegram
  message. Older unseen IDs (Spotify catalog noise: reissues, regional
  editions, relinked duplicates) are absorbed into the state silently.
- When Spotify imposes a rate-limit penalty (any 429), the run stops
  immediately — never retrying in-process — and the deadline is saved to
  `state.json`. Every run before that deadline exits without touching the
  API at all. Because `Retry-After` can under-report (or be missing) while
  the block is still active, the recorded penalty is never shorter than
  30 minutes, plus a 2-minute safety margin — requests made during an
  active penalty extend it, so waiting it out fully is the only way to
  recover.
- Releases are filtered to the Greek market (`GR`) by default, matching
  what's actually playable on a Greek account. Set `SPOTIFY_MARKET` in
  `.env` to another ISO country code to change it.

### Running on a schedule

For a one-off check per run, add a cron entry, e.g. every 30 minutes:
```
*/30 * * * * cd /path/to/server_SpotifyNewTracks && /usr/bin/python3 -m spotify_tracker.main >> tracker.log 2>&1
```

Or just leave it running in the foreground/background with `--interval`.
