"""Loads required settings from the environment (and a .env file)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    spotify_client_id: str
    spotify_client_secret: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config() -> Config:
    missing = []

    def require(name: str) -> str:
        value = os.getenv(name)
        if not value:
            missing.append(name)
        return value or ""

    config = Config(
        spotify_client_id=require("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=require("SPOTIFY_CLIENT_SECRET"),
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=require("TELEGRAM_CHAT_ID"),
    )

    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in the values."
        )

    return config
