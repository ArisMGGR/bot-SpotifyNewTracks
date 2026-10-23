#!/usr/bin/env bash
#
# Auto-installer for the Spotify New Tracks notifier's scheduled run.
#
# What it does:
#   1. Creates a Python virtualenv in ./venv (if missing) and installs
#      requirements.txt into it.
#   2. Installs an hourly crontab entry that runs scripts/run_check.sh, which
#      only performs a real check once ~25 hours have elapsed (see that file).
#      The effective cadence is therefore ~25h using plain cron. Your current
#      PATH (and CRON_INTERVAL_HOURS, if set) is saved to cron.env, which the
#      wrapper reads, so the tools found now (flock, python3) are found by
#      cron too; re-run this script if they move.
#
# The crontab entry is tagged with a marker comment, so re-running this script
# updates the entry in place instead of creating duplicates.
#
# Usage:
#   ./install_cron.sh              # full setup + install cron job
#   ./install_cron.sh --uninstall  # remove the cron job (leaves venv alone)
#   ./install_cron.sh --no-venv    # skip venv/pip, only install the cron job
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="# spotify-tracker-cron (managed by install_cron.sh)"
WRAPPER="${REPO_DIR}/scripts/run_check.sh"
CRON_LOG="${REPO_DIR}/logs/cron.log"
CRON_ENV="${REPO_DIR}/cron.env"

# Hourly poll; the wrapper enforces the real ~25h gap.
CRON_SCHEDULE="0 * * * *"

DO_VENV=1

for arg in "$@"; do
    case "${arg}" in
        --uninstall)
            NEW_CRON="$(crontab -l 2>/dev/null | grep -vF "${MARKER}" | grep -vF "${WRAPPER}" || true)"
            printf '%s\n' "${NEW_CRON}" | crontab -
            rm -f "${CRON_ENV}"
            echo "Removed the Spotify tracker cron entry."
            exit 0
            ;;
        --no-venv)
            DO_VENV=0
            ;;
        -h|--help)
            # Print only the header comment block (after the shebang).
            awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "Unknown option: ${arg}" >&2
            exit 1
            ;;
    esac
done

# --- 1. Python environment --------------------------------------------------
if [ "${DO_VENV}" -eq 1 ]; then
    if [ ! -x "${REPO_DIR}/venv/bin/python" ]; then
        echo "Creating virtualenv in ${REPO_DIR}/venv ..."
        python3 -m venv "${REPO_DIR}/venv"
    else
        echo "Reusing existing virtualenv at ${REPO_DIR}/venv"
    fi
    echo "Installing dependencies ..."
    "${REPO_DIR}/venv/bin/pip" install --upgrade pip >/dev/null
    "${REPO_DIR}/venv/bin/pip" install -r "${REPO_DIR}/requirements.txt"
fi

# --- 2. Sanity checks -------------------------------------------------------
chmod +x "${WRAPPER}"
mkdir -p "${REPO_DIR}/logs"

if [ ! -f "${REPO_DIR}/.env" ]; then
    echo "WARNING: ${REPO_DIR}/.env not found. The scheduled run will fail"
    echo "         until you create it (see README) and run:"
    echo "           python -m spotify_tracker.login"
fi

if ! command -v flock >/dev/null 2>&1; then
    echo "WARNING: flock not found. scripts/run_check.sh needs it to run, so"
    echo "         every scheduled check will fail until you install it"
    echo "         (util-linux, or 'brew install flock' on macOS)."
fi

# --- 3. Save the environment the scheduled run needs ------------------------
# Cron runs jobs with a minimal PATH (often just /usr/bin:/bin), so a flock or
# python3 found here (e.g. Homebrew's /opt/homebrew/bin) would be missing in
# the scheduled run. Save this shell's PATH, the one checked above, for the
# wrapper to read. It goes in a file rather than the crontab line because
# cron silently cuts commands longer than ~1000 characters, which a long PATH
# would exceed. CRON_INTERVAL_HOURS rides along so an interval given to the
# installer reaches the scheduled run.
{
    printf 'PATH=%s\n' "${PATH}"
    if [ -n "${CRON_INTERVAL_HOURS:-}" ]; then
        printf 'CRON_INTERVAL_HOURS=%s\n' "${CRON_INTERVAL_HOURS}"
    fi
} > "${CRON_ENV}"

# --- 4. Install the cron entry (idempotent) ---------------------------------
# Quote a path for the crontab command line, which cron hands to /bin/sh:
# single-quote it (so spaces etc. survive) and escape "%", which cron would
# otherwise turn into a newline.
cron_quote() {
    local s="$1"
    s="${s//\'/\'\\\'\'}"
    s="${s//%/\\%}"
    printf "'%s'" "${s}"
}

# The wrapper appends its own output to logs/cron.log (creating logs/ first),
# so the entry needs no redirect into a directory that may have been deleted.
CRON_LINE="${CRON_SCHEDULE} /usr/bin/env bash $(cron_quote "${WRAPPER}") ${MARKER}"

# Keep every existing line except a previous entry of ours, then append fresh.
EXISTING="$(crontab -l 2>/dev/null | grep -vF "${MARKER}" | grep -vF "${WRAPPER}" || true)"
{
    if [ -n "${EXISTING}" ]; then
        printf '%s\n' "${EXISTING}"
    fi
    printf '%s\n' "${CRON_LINE}"
} | crontab -

echo
echo "Installed cron entry (polls hourly; runs a real check every ~25h):"
echo "  ${CRON_LINE}"
echo
echo "Check interval: ${CRON_INTERVAL_HOURS:-25} hours (saved in ${CRON_ENV})"
echo "Cron output is appended to: ${CRON_LOG}"
echo "To remove it later: ${REPO_DIR}/install_cron.sh --uninstall"
