from __future__ import annotations

import unittest

from codex_sidebar.watcher import desired_action


class WatcherDecisionTests(unittest.TestCase):
    def test_launches_when_codex_opens_without_sidebar(self) -> None:
        self.assertEqual(desired_action(True, False), "launch")

    def test_closes_sidebar_when_codex_exits(self) -> None:
        self.assertEqual(desired_action(False, True), "close")

    def test_does_not_duplicate_existing_sidebar(self) -> None:
        self.assertEqual(desired_action(True, True), "none")

    def test_launch_retry_throttle_is_respected(self) -> None:
        self.assertEqual(desired_action(True, False, launch_allowed=False), "none")

    def test_nothing_starts_when_codex_is_closed(self) -> None:
        self.assertEqual(desired_action(False, False), "none")


if __name__ == "__main__":
    unittest.main()
