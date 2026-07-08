"""Loads required settings from the environment (and a .env file)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


@dataclass
class Config:
    spotify_client_id: str
    spotify_client_secret: str
    spotify_refresh_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    # ISO country code used to filter releases. Defaults to GR; set the
    # SPOTIFY_MARKET env var to override.
    spotify_market: str = "GR"


def _require(name: str, missing: list[str]) -> str:
    value = os.getenv(name)
    if not value:
        missing.append(name)
    return value or ""


def load_client_credentials() -> tuple[str, str, str]:
    """Just the pieces needed to run the one-time login flow (no refresh token yet)."""
    missing: list[str] = []
    client_id = _require("SPOTIFY_CLIENT_ID", missing)
    client_secret = _require("SPOTIFY_CLIENT_SECRET", missing)
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET first."
        )
    return client_id, client_secret, redirect_uri


def load_config() -> Config:
    missing: list[str] = []
    client_id = _require("SPOTIFY_CLIENT_ID", missing)
    client_secret = _require("SPOTIFY_CLIENT_SECRET", missing)
    refresh_token = _require("SPOTIFY_REFRESH_TOKEN", missing)
    telegram_bot_token = _require("TELEGRAM_BOT_TOKEN", missing)
    telegram_chat_id = _require("TELEGRAM_CHAT_ID", missing)

    if missing:
        hint = ""
        if "SPOTIFY_REFRESH_TOKEN" in missing:
            hint = " Run 'python -m spotify_tracker.login' once to generate SPOTIFY_REFRESH_TOKEN."
        raise SystemExit(
            "Missing required environment variables: " + ", ".join(missing) + "." + hint
        )

    return Config(
        spotify_client_id=client_id,
        spotify_client_secret=client_secret,
        spotify_refresh_token=refresh_token,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        spotify_market=os.getenv("SPOTIFY_MARKET") or "GR",
    )
