# Spotify New Tracks Notifier

Watches a list of Spotify artists and sends you a Telegram message whenever
one of them releases a new album or single.

## Setup

1. **Install dependencies** (Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Spotify app** at https://developer.spotify.com/dashboard to get
   a `Client ID` and `Client Secret`. No user login/redirect URI is needed —
   this project only reads public artist data.

3. **Create a Telegram bot**: message [@BotFather](https://t.me/BotFather),
   run `/newbot`, and copy the bot token it gives you.

4. **Get your chat ID**: message your new bot once, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   read the `chat.id` field from the response.

5. **Configure secrets**: copy `.env.example` to `.env` and fill in the four
   values:
   ```bash
   cp .env.example .env
   ```

6. **List the artists you want to track**: copy `artists.example.txt` to
   `artists.txt` and put one artist name per line:
   ```bash
   cp artists.example.txt artists.txt
   ```

## Usage

Run a single check (good for cron):
```bash
python -m spotify_tracker.main
```

Or keep it running and poll every N seconds (e.g. every hour):
```bash
python -m spotify_tracker.main --interval 3600
```

Other options:
```bash
python -m spotify_tracker.main --artists-file my_artists.txt --state-file my_state.json
```

### How it works

- Artist names are resolved to Spotify artist IDs via the Spotify search API.
- Each artist's albums/singles are fetched and compared against
  `state.json`, which remembers release IDs already seen.
- The first time an artist is checked, its current catalog is recorded as a
  baseline (no notification), so you don't get flooded with their entire
  back catalog. Only releases that show up afterward trigger a Telegram
  message.

### Running on a schedule

For a one-off check per run, add a cron entry, e.g. every 30 minutes:
```
*/30 * * * * cd /path/to/server_SpotifyNewTracks && /usr/bin/python3 -m spotify_tracker.main >> tracker.log 2>&1
```

Or just leave it running in the foreground/background with `--interval`.
