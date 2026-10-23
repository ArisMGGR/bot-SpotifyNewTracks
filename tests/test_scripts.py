import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _bash_works() -> bool:
    if not shutil.which("bash"):
        return False
    try:
        return subprocess.run(["bash", "-c", "true"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(_bash_works(), "needs bash")
class ShellScriptTest(unittest.TestCase):
    """Each script runs from a copy in a temporary directory, so nothing here
    touches the real crontab, state/ or the tracker itself."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(REPO_DIR / "scripts" / "run_check.sh", self.root / "scripts")
        shutil.copy(REPO_DIR / "install_cron.sh", self.root)

    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", *args],
            cwd=self.root,
            env=dict(os.environ, **(env or {})),
            # Not a terminal, so the installer's setup step never prompts.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _cron_log(self) -> str:
        return (self.root / "logs" / "cron.log").read_text(encoding="utf-8")

    def _install(self, env: dict[str, str] | None = None, *flags: str) -> subprocess.CompletedProcess:
        """Run install_cron.sh --no-venv with stub flock and crontab in a
        directory of their own (like Homebrew's: on the installer's PATH but
        not in cron's /usr/bin:/bin), and a stub venv python. The stubs record
        that they ran; crontab writes the new table to stubs/crontab.out.
        EXTRA_PATH, if given in env, goes on the installer's PATH too."""
        stubs = self.root / "stubs"
        stubs.mkdir()
        (stubs / "flock").write_text('#!/bin/sh\ntouch "$(dirname "$0")/flock-ran"\n', newline="\n")
        (stubs / "crontab").write_text(
            '#!/bin/sh\n[ "$1" = "-l" ] && exit 1\ncat > "$(dirname "$0")/crontab.out"\n', newline="\n"
        )
        (self.root / "venv" / "bin").mkdir(parents=True)
        (self.root / "venv" / "bin" / "python").write_text('#!/bin/sh\necho PYTHON_RAN "$@"\n', newline="\n")
        for stub in (stubs / "flock", stubs / "crontab", self.root / "venv" / "bin" / "python"):
            stub.chmod(0o755)

        return self._run(
            "-c",
            'PATH="$PWD/stubs:${EXTRA_PATH:+$EXTRA_PATH:}$PATH" bash install_cron.sh --no-venv "$@"',
            "install",
            *flags,
            env=dict({"CRON_INTERVAL_HOURS": "", "EXTRA_PATH": ""}, **(env or {})),
        )

    def _run_entry(self) -> subprocess.CompletedProcess:
        """Run the installed crontab command the way cron would: % unescaped,
        through sh, with only /usr/bin:/bin."""
        script = (
            "cmd=$(cut -d' ' -f6- stubs/crontab.out | sed 's/\\\\%/%/g')\n"
            'env -i PATH=/usr/bin:/bin sh -c "$cmd"\n'
        )
        return self._run("-c", script)

    def test_run_check_rejects_a_bad_interval(self):
        for value in ("010", "0", "-1", "abc", "1.5"):
            with self.subTest(CRON_INTERVAL_HOURS=value):
                result = self._run("scripts/run_check.sh", env={"CRON_INTERVAL_HOURS": value})
                self.assertEqual(result.returncode, 1)
                cron_log = self._cron_log()
                self.assertIn("CRON_INTERVAL_HOURS must be a positive whole number", cron_log)
                self.assertIn(f"(got '{value}')", cron_log)
                self.assertFalse((self.root / "state").exists())

    def test_installed_entry_finds_flock_under_crons_minimal_path(self):
        result = self._install()
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self._run_entry()

        self.assertEqual(result.returncode, 0, result.stderr)
        cron_log = self._cron_log()
        self.assertNotIn("flock not found", cron_log)
        self.assertIn("PYTHON_RAN", cron_log)
        self.assertTrue((self.root / "stubs" / "flock-ran").exists())

    def test_install_runs_the_setup_step_unless_skipped(self):
        # The stub venv python stands in for spotify_tracker.setup. _install
        # passes --no-venv, which the setup must get too, or it would build
        # ./venv itself.
        result = self._install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PYTHON_RAN -m spotify_tracker.setup --no-venv", result.stdout)

    def test_install_with_no_setup_skips_it_and_warns(self):
        result = self._install(None, "--no-setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("PYTHON_RAN", result.stdout)
        self.assertIn("config.env not found", result.stdout)

    def test_installed_entry_stays_short_with_a_long_path(self):
        # Cron silently cuts commands longer than ~1000 characters, so the
        # installer's PATH must not end up in the entry.
        long_path = ":".join(f"/opt/tool{i}/bin" for i in range(150))
        result = self._install(env={"EXTRA_PATH": long_path})
        self.assertEqual(result.returncode, 0, result.stderr)

        entry = (self.root / "stubs" / "crontab.out").read_text(encoding="utf-8").strip()
        self.assertLess(len(entry), 998, entry)
        self.assertNotIn(long_path, entry)
        self.assertIn(long_path, (self.root / "cron.env").read_text(encoding="utf-8"))

    def test_scheduled_run_recreates_a_deleted_logs_dir(self):
        result = self._install()
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.rmtree(self.root / "logs")

        result = self._run_entry()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PYTHON_RAN", self._cron_log())

    def test_interval_given_to_the_installer_reaches_the_scheduled_run(self):
        result = self._install(env={"CRON_INTERVAL_HOURS": "12"})
        self.assertEqual(result.returncode, 0, result.stderr)
        # The last real run was 13 hours ago: due at 12 hours, not at the
        # default 25.
        (self.root / "state").mkdir()
        (self.root / "state" / "last_cron_run").write_text(str(int(time.time()) - 13 * 3600))

        result = self._run_entry()

        self.assertEqual(result.returncode, 0, result.stderr)
        cron_log = self._cron_log()
        self.assertIn(">= 12h since last run", cron_log)
        self.assertIn("PYTHON_RAN", cron_log)

    def test_install_help_prints_only_the_header(self):
        result = self._run("install_cron.sh", "--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Auto-installer for the Spotify New Tracks notifier", result.stdout)
        self.assertIn("./install_cron.sh --uninstall", result.stdout)
        self.assertNotIn("!/usr/bin/env bash", result.stdout)
        self.assertNotIn("Quote a path", result.stdout)


if __name__ == "__main__":
    unittest.main()
