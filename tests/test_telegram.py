import socket
import threading
import traceback
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from spotify_tracker import telegram
from spotify_tracker.telegram import TelegramError, TelegramNotifier

TOKEN = "123456:TEST-TOKEN-not-real"


class _Reject(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b'{"ok":false,"description":"Bad Request: can\'t parse entities"}')

    def log_message(self, format, *args):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TelegramErrorTest(unittest.TestCase):
    def _send_and_capture(self, api_base: str) -> tuple[TelegramError, str]:
        """Send through a notifier pointed at api_base; return the error and
        the traceback text as logger.exception would write it."""
        with mock.patch.object(telegram, "API_BASE", api_base):
            notifier = TelegramNotifier(TOKEN, "1")
        try:
            notifier.send_message("hello")
        except TelegramError as err:
            return err, traceback.format_exc()
        self.fail("send_message did not raise")

    def test_api_error_is_reported_without_the_token(self):
        server = HTTPServer(("127.0.0.1", 0), _Reject)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)

        err, logged = self._send_and_capture(f"http://127.0.0.1:{server.server_port}")
        thread.join(5)

        self.assertIn("400", str(err))
        self.assertIn("can't parse entities", str(err))
        self.assertNotIn(TOKEN, logged)

    def test_connection_error_is_reported_without_the_token(self):
        err, logged = self._send_and_capture(f"http://127.0.0.1:{_free_port()}")
        self.assertIn("ConnectionError", str(err))
        self.assertNotIn(TOKEN, logged)


if __name__ == "__main__":
    unittest.main()
