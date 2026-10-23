"""Interactive first-time setup: create config.env and run the Spotify login.

Run with: python -m spotify_tracker.setup
(install_cron.sh runs it for you.)

0. Unless run with the project's own Python, creates ./venv if missing,
   installs requirements.txt into it and continues inside it (see
   spotify_tracker.bootstrap). --no-venv uses the current Python as it is.
1. Renames a .env from before the rename to config.env, or creates config.env
   from config.env.example if there is neither.
2. Asks for each required setting that is still empty or a template
   placeholder. Pressing Enter leaves it as it is.
3. Runs the Spotify login if there is no SPOTIFY_REFRESH_TOKEN yet.

With --non-interactive (or when not run from a terminal) steps 2 and 3 are
skipped, and the settings still missing are listed.
"""

import argparse
import getpass
import shutil
import sys

if __name__ == "__main__":
    # Before the imports below, which need the dependencies installed.
    from spotify_tracker.bootstrap import ensure_venv

    if not {"--no-venv", "-h", "--help"} & set(sys.argv[1:]):
        ensure_venv(sys.argv[1:])

from dotenv import dotenv_values, load_dotenv

from spotify_tracker import config, login
from spotify_tracker.env_file import set_env_value

# (name, prompt, secret). Secrets are read without echoing them.
PROMPTS = [
    ("SPOTIFY_CLIENT_ID", "Spotify Client ID", False),
    ("SPOTIFY_CLIENT_SECRET", "Spotify Client secret", True),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", True),
    ("TELEGRAM_CHAT_ID", "Telegram chat ID", False),
]

# The template's stand-in values all start with this.
PLACEHOLDER_PREFIX = "your_"


def _is_missing(value: str | None) -> bool:
    return not value or value.startswith(PLACEHOLDER_PREFIX)


def ensure_config_file() -> None:
    """Make sure config.env exists, keeping any settings from a legacy .env."""
    if config.ENV_PATH.exists():
        if config.LEGACY_ENV_PATH.exists():
            print(f"Note: {config.LEGACY_ENV_PATH} is ignored now that {config.ENV_PATH} exists; you can delete it.")
        return
    if config.LEGACY_ENV_PATH.exists():
        config.LEGACY_ENV_PATH.rename(config.ENV_PATH)
        print(f"Renamed {config.LEGACY_ENV_PATH} to {config.ENV_PATH}.")
    else:
        shutil.copyfile(config.TEMPLATE_PATH, config.ENV_PATH)
        print(f"Created {config.ENV_PATH} from {config.TEMPLATE_PATH.name}.")


def missing_settings() -> list[str]:
    values = dotenv_values(config.ENV_PATH)
    names = [name for name, _, _ in PROMPTS] + ["SPOTIFY_REFRESH_TOKEN"]
    return [name for name in names if _is_missing(values.get(name))]


def prompt_for_settings() -> None:
    values = dotenv_values(config.ENV_PATH)
    for name, label, secret in PROMPTS:
        if not _is_missing(values.get(name)):
            continue
        ask = getpass.getpass if secret else input
        value = ask(f"{label} ({name}, Enter to skip): ").strip()
        if value:
            set_env_value(config.ENV_PATH, name, value)


def maybe_run_login() -> None:
    values = dotenv_values(config.ENV_PATH)
    if not _is_missing(values.get("SPOTIFY_REFRESH_TOKEN")):
        return
    if _is_missing(values.get("SPOTIFY_CLIENT_ID")) or _is_missing(values.get("SPOTIFY_CLIENT_SECRET")):
        print("Skipping the Spotify login: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are needed first.")
        return
    answer = input("Run the Spotify login now? It opens a browser on this machine. [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("Skipped. Run 'python -m spotify_tracker.login' later (see README for servers without a browser).")
        return
    # Settings typed above aren't in os.environ yet; the login reads them there.
    load_dotenv(config.ENV_PATH, override=True)
    login.main()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="only create config.env; don't ask for settings or run the login",
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="use this Python as it is instead of ./venv (its dependencies must be installed)",
    )
    args = parser.parse_args(argv)

    ensure_config_file()
    # Both ends, not just stdin: Windows reports NUL (stdin from /dev/null) as
    # a terminal, and getpass there reads the console directly and would hang.
    if not args.non_interactive and sys.stdin.isatty() and sys.stdout.isatty():
        prompt_for_settings()
        maybe_run_login()

    missing = missing_settings()
    if missing:
        print(
            f"Still missing in {config.ENV_PATH}: {', '.join(missing)}. "
            "Fill them in (or re-run 'python -m spotify_tracker.setup') before the first check."
        )
    else:
        print(f"{config.ENV_PATH} is complete.")


if __name__ == "__main__":
    main()
