#!/usr/bin/env bash
#
# Auto-installer for the Spotify New Tracks notifier's scheduled run.
#
# What it does:
#   1. Creates a Python virtualenv in ./venv (if missing) and installs
#      requirements.txt into it.
#   2. Installs an hourly crontab entry that runs scripts/run_check.sh, which
#      only performs a real check once ~25 hours have elapsed (see that file).
#      The effective cadence is therefore ~25h using plain cron.
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

# Hourly poll; the wrapper enforces the real ~25h gap.
CRON_SCHEDULE="0 * * * *"

DO_VENV=1

for arg in "$@"; do
    case "${arg}" in
        --uninstall)
            NEW_CRON="$(crontab -l 2>/dev/null | grep -vF "${MARKER}" | grep -vF "${WRAPPER}" || true)"
            printf '%s\n' "${NEW_CRON}" | crontab -
            echo "Removed the Spotify tracker cron entry."
            exit 0
            ;;
        --no-venv)
            DO_VENV=0
            ;;
        -h|--help)
            grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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

# --- 3. Install the cron entry (idempotent) ---------------------------------
CRON_LINE="${CRON_SCHEDULE} /usr/bin/env bash ${WRAPPER} >> ${CRON_LOG} 2>&1 ${MARKER}"

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
echo "Cron output is appended to: ${CRON_LOG}"
echo "To remove it later: ${REPO_DIR}/install_cron.sh --uninstall"
