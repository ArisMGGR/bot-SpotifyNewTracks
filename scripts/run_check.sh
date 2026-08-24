#!/usr/bin/env bash
#
# Hourly cron guard for the Spotify New Tracks notifier.
#
# Standard cron cannot fire "every 25 hours" (its fields are fixed clock
# times, which drift). Instead, install_cron.sh registers this wrapper to run
# once an hour, and the wrapper only actually performs a check once at least
# ~25 hours have elapsed since the last real run. That gives a true ~25h
# cadence using nothing but plain cron.
#
# A timestamp of the last real run is kept in state/last_cron_run. A lock
# (flock) prevents two checks from overlapping.
#
set -euo pipefail

# Resolve the repository root (this script lives in <repo>/scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

# Desired gap between real runs, in hours (override with CRON_INTERVAL_HOURS).
INTERVAL_HOURS="${CRON_INTERVAL_HOURS:-25}"

# Because the guard is only polled once an hour, subtract half of the polling
# interval (30 min) from the threshold so the hourly tick nearest the 25h mark
# fires, keeping the average gap close to 25h rather than always rounding up.
MIN_GAP_SECONDS=$(( INTERVAL_HOURS * 3600 - 1800 ))

STATE_DIR="${REPO_DIR}/state"
STAMP_FILE="${STATE_DIR}/last_cron_run"
LOCK_FILE="${STATE_DIR}/cron.lock"
mkdir -p "${STATE_DIR}"

# Pick the interpreter: prefer the project venv, else fall back to system.
if [ -x "${REPO_DIR}/venv/bin/python" ]; then
    PYTHON="${REPO_DIR}/venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

now="$(date +%s)"

# Skip if a real run happened recently enough.
if [ -f "${STAMP_FILE}" ]; then
    last="$(cat "${STAMP_FILE}" 2>/dev/null || echo 0)"
    case "${last}" in
        ''|*[!0-9]*) last=0 ;;
    esac
    elapsed=$(( now - last ))
    if [ "${elapsed}" -lt "${MIN_GAP_SECONDS}" ]; then
        # Not due yet; stay quiet so the hourly cron log isn't spammed.
        exit 0
    fi
fi

# Serialize runs: if a previous check is somehow still going, skip this tick.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] previous run still active; skipping" >&2
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] running check (>= ${INTERVAL_HOURS}h since last run)"

# Record the timestamp before running so a crash mid-run still counts as an
# attempt and we don't hammer the API every hour after a failure.
echo "${now}" > "${STAMP_FILE}"

exec "${PYTHON}" -m spotify_tracker.main "$@"
