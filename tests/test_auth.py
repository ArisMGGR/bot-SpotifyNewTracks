import socket
import threading
import time
import unittest
import urllib.request
from unittest import mock

from spotify_tracker import auth


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LoginFlowTest(unittest.TestCase):
    """The browser and the token exchange are mocked; only the local callback
    server is real."""

    def setUp(self):
        self.port = _free_port()
        self.redirect_uri = f"http://127.0.0.1:{self.port}/callback"
        for patcher in (
            mock.patch.object(auth.webbrowser, "open"),
            mock.patch.object(auth.secrets, "token_urlsafe", return_value="the-state"),
            mock.patch("builtins.print"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_callback_wait_has_a_finite_default_timeout(self):
        self.assertIsNotNone(auth._CallbackServer.timeout)
        self.assertGreater(auth._CallbackServer.timeout, 0)

    def test_gives_up_when_no_redirect_arrives(self):
        with (
            mock.patch.object(auth._CallbackServer, "timeout", 0.2),
            mock.patch.object(auth.requests, "post") as post,
        ):
            with self.assertRaises(SystemExit) as ctx:
                auth.run_login_flow("id", "secret", self.redirect_uri)

        self.assertIn("no browser redirect", str(ctx.exception))
        post.assert_not_called()

    def test_redirect_within_the_timeout_returns_the_token(self):
        def browser_redirect():
            url = f"{self.redirect_uri}?code=the-code&state=the-state"
            for _ in range(50):  # until the callback server is listening
                try:
                    urllib.request.urlopen(url, timeout=5).read()
                    return
                except OSError:
                    time.sleep(0.05)

        token_response = mock.Mock()
        token_response.json.return_value = {"refresh_token": "the-refresh-token"}
        thread = threading.Thread(target=browser_redirect, daemon=True)
        thread.start()
        with (
            mock.patch.object(auth._CallbackServer, "timeout", 10),
            mock.patch.object(auth.requests, "post", return_value=token_response),
        ):
            token = auth.run_login_flow("id", "secret", self.redirect_uri)
        thread.join(5)

        self.assertEqual(token, "the-refresh-token")

    def test_failed_token_exchange_shows_spotifys_reason(self):
        def browser_redirect():
            url = f"{self.redirect_uri}?code=the-code&state=the-state"
            for _ in range(50):
                try:
                    urllib.request.urlopen(url, timeout=5).read()
                    return
                except OSError:
                    time.sleep(0.05)

        token_response = mock.Mock(ok=False, status_code=400, text='{"error":"invalid_grant"}')
        thread = threading.Thread(target=browser_redirect, daemon=True)
        thread.start()
        with (
            mock.patch.object(auth._CallbackServer, "timeout", 10),
            mock.patch.object(auth.requests, "post", return_value=token_response),
        ):
            with self.assertRaises(SystemExit) as ctx:
                auth.run_login_flow("id", "secret", self.redirect_uri)
        thread.join(5)

        self.assertIn("invalid_grant", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
