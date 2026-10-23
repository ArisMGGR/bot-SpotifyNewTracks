import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dotenv import dotenv_values

from spotify_tracker import config, setup

TEMPLATE = (Path(__file__).resolve().parent.parent / "config.env.example").read_text(encoding="utf-8")


class SetupTest(unittest.TestCase):
    """Each test points config's paths at a temporary directory, so the real
    config.env is never read or written."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "config.env.example").write_text(TEMPLATE, encoding="utf-8")
        for name, path in (
            ("ENV_PATH", self.root / "config.env"),
            ("LEGACY_ENV_PATH", self.root / ".env"),
            ("TEMPLATE_PATH", self.root / "config.env.example"),
        ):
            patcher = mock.patch.object(config, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _values(self) -> dict[str, str | None]:
        return dotenv_values(self.root / "config.env")

    def _run(self, *, tty: bool, inputs: list[str] = (), secrets: list[str] = ()) -> mock.Mock:
        with (
            mock.patch("sys.stdin.isatty", return_value=tty),
            mock.patch("sys.stdout.isatty", return_value=tty),
            mock.patch("builtins.input", side_effect=list(inputs)),
            mock.patch("getpass.getpass", side_effect=list(secrets)),
            mock.patch.object(setup.login, "main") as login_main,
            mock.patch("builtins.print"),
        ):
            setup.main([])
        return login_main

    def test_creates_config_env_from_the_template(self):
        login_main = self._run(tty=False)
        self.assertEqual((self.root / "config.env").read_text(encoding="utf-8"), TEMPLATE)
        login_main.assert_not_called()

    def test_renames_a_legacy_env_keeping_its_settings(self):
        (self.root / ".env").write_text("SPOTIFY_CLIENT_ID=old-id\n", encoding="utf-8")
        self._run(tty=False)
        self.assertFalse((self.root / ".env").exists())
        self.assertEqual(self._values()["SPOTIFY_CLIENT_ID"], "old-id")

    def test_existing_config_env_is_left_alone(self):
        (self.root / "config.env").write_text("SPOTIFY_CLIENT_ID=kept\n", encoding="utf-8")
        (self.root / ".env").write_text("SPOTIFY_CLIENT_ID=legacy\n", encoding="utf-8")
        self._run(tty=False)
        self.assertEqual(self._values()["SPOTIFY_CLIENT_ID"], "kept")
        self.assertTrue((self.root / ".env").exists())

    def test_prompts_for_placeholders_then_runs_the_login(self):
        login_main = self._run(
            tty=True,
            # client ID, chat ID, then "run the login?"
            inputs=["new-id", "42", ""],
            secrets=["new-secret", "bot-token"],
        )
        values = self._values()
        self.assertEqual(values["SPOTIFY_CLIENT_ID"], "new-id")
        self.assertEqual(values["SPOTIFY_CLIENT_SECRET"], "new-secret")
        self.assertEqual(values["TELEGRAM_BOT_TOKEN"], "bot-token")
        self.assertEqual(values["TELEGRAM_CHAT_ID"], "42")
        login_main.assert_called_once()
        # The login reads the client credentials from the environment.
        self.assertEqual(os.environ["SPOTIFY_CLIENT_ID"], "new-id")

    def test_only_missing_settings_are_asked_for(self):
        (self.root / "config.env").write_text(
            "SPOTIFY_CLIENT_ID=id\nSPOTIFY_CLIENT_SECRET=secret\nSPOTIFY_REFRESH_TOKEN=token\n"
            "TELEGRAM_BOT_TOKEN=bot\nTELEGRAM_CHAT_ID=\n",
            encoding="utf-8",
        )
        login_main = self._run(tty=True, inputs=["7"])
        self.assertEqual(self._values()["TELEGRAM_CHAT_ID"], "7")
        login_main.assert_not_called()

    def test_skipped_client_credentials_skip_the_login(self):
        login_main = self._run(tty=True, inputs=["", ""], secrets=["", ""])
        login_main.assert_not_called()
        self.assertEqual(self._values()["SPOTIFY_CLIENT_ID"], "your_spotify_client_id")

    def test_declining_the_login_skips_it(self):
        login_main = self._run(tty=True, inputs=["id", "1", "n"], secrets=["secret", "bot"])
        login_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
