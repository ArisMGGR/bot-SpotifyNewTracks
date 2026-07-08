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

### How it works

- On each run, the program fetches every artist your Spotify account
  follows (`GET /me/following`), so there's no name list to maintain —
  following/unfollowing an artist in Spotify is how you manage what's
  tracked.
- The first time an artist is checked, their **complete** albums/singles
  catalog is fetched and recorded as a baseline (no notification), so you
  don't get flooded with their back catalog and old releases can't later be
  mistaken for new ones. If a rate limit interrupts the fetch, nothing is
  recorded and the next run retries that artist. After each baseline the
  tool cools down for a few seconds (proportional to the catalog size) so
  back-to-back baselines don't trip Spotify's rate limit.
- On every later check only the newest page of releases is fetched and
  compared against `state.json`, which remembers release IDs already seen.
- Only unseen releases dated within the last 30 days trigger a Telegram
  message. Older unseen IDs (Spotify catalog noise: reissues, regional
  editions, relinked duplicates) are absorbed into the state silently.
- Releases are filtered to the Greek market (`GR`) by default, matching
  what's actually playable on a Greek account. Set `SPOTIFY_MARKET` in
  `.env` to another ISO country code to change it.

### Running on a schedule

For a one-off check per run, add a cron entry, e.g. every 30 minutes:
```
*/30 * * * * cd /path/to/server_SpotifyNewTracks && /usr/bin/python3 -m spotify_tracker.main >> tracker.log 2>&1
```

Or just leave it running in the foreground/background with `--interval`.
