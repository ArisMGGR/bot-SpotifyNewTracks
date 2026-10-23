import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spotify_tracker import state as state_module
from spotify_tracker.state import StateStore


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_round_trips(self):
        store = StateStore(self.path)
        store.record_check("a1", "Artist", {"r2", "r1"}, cost=3)
        store.save()

        reloaded = StateStore(self.path)
        self.assertEqual(reloaded.known_release_ids("a1"), {"r1", "r2"})
        self.assertEqual(reloaded.last_cost("a1"), 3)

    def test_failed_save_keeps_previous_file_and_no_temp_file(self):
        store = StateStore(self.path)
        store.record_check("a1", "Artist", {"r1"})
        store.save()
        before = self.path.read_text(encoding="utf-8")

        # json.dump streams, so this fails after part of the file is written.
        store._data["unserialisable"] = object()
        with self.assertRaises(TypeError):
            store.save()

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(os.listdir(self._tmp.name), ["state.json"])

    def test_save_replaces_the_file_in_one_step(self):
        store = StateStore(self.path)
        store.record_check("a1", "Artist", {"r1"})
        store.save()
        before = self.path.read_text(encoding="utf-8")
        store.record_check("a1", "Artist", {"r1", "r2"})

        real_replace = os.replace
        seen = []

        def spy(src, dst):
            # Until the swap, state.json must still hold the old, complete data.
            seen.append(Path(dst) == self.path and self.path.read_text(encoding="utf-8") == before)
            real_replace(src, dst)

        with mock.patch.object(state_module.os, "replace", spy):
            store.save()

        self.assertEqual(seen, [True])
        self.assertEqual(StateStore(self.path).known_release_ids("a1"), {"r1", "r2"})

    def test_migrates_old_flat_format(self):
        self.path.write_text(json.dumps({"a1": ["r1", "r2"]}), encoding="utf-8")
        store = StateStore(self.path)
        self.assertEqual(store.known_release_ids("a1"), {"r1", "r2"})
        self.assertEqual(store.checked_at("a1"), 0)

    def test_api_calls_reset_on_a_new_day(self):
        store = StateStore(self.path)
        with mock.patch.object(state_module, "utc_today", return_value="2026-10-23"):
            store.set_api_calls_today(42)
            self.assertEqual(store.api_calls_today(), 42)
        with mock.patch.object(state_module, "utc_today", return_value="2026-10-24"):
            self.assertEqual(store.api_calls_today(), 0)


if __name__ == "__main__":
    unittest.main()
