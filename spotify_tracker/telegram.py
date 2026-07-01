"""Sends notifications through the Telegram Bot API."""

import requests

API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._url = f"{API_BASE}/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send_message(self, text: str) -> None:
        response = requests.post(
            self._url,
            data={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        response.raise_for_status()
