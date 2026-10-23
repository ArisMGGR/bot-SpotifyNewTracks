"""Sends notifications through the Telegram Bot API."""

import requests

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """A failed send. requests puts the full request URL, bot token included,
    into its error messages, so those are scrubbed before being raised here
    (and from there written to the logs)."""


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._url = f"{API_BASE}/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def _redact(self, text: str) -> str:
        return text.replace(self._bot_token, "<bot-token>") if self._bot_token else text

    def send_message(self, text: str) -> None:
        try:
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
        except requests.RequestException as err:
            # "from None" keeps the original exception (and its token-bearing
            # message) out of the logged traceback.
            raise TelegramError(self._redact(f"{type(err).__name__}: {err}")) from None
        if not response.ok:
            # Telegram's error body says what it rejected (e.g. "can't parse
            # entities"), which a bare status code doesn't.
            raise TelegramError(self._redact(f"Telegram API error {response.status_code}: {response.text}"))
