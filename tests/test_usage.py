from __future__ import annotations

import unittest

from codex_sidebar.usage import parse_usage


class UsageTests(unittest.TestCase):
    def test_parse_usage_windows(self) -> None:
        windows = parse_usage(
            {"rateLimits": {"primary": {"usedPercent": 35, "windowDurationMins": 300}, "secondary": {"usedPercent": 90, "windowDurationMins": 10080}}}
        )
        self.assertEqual([(item.label, item.remaining_percent) for item in windows], [("5 小时", 65), ("每周", 10)])

    def test_clamps_percent(self) -> None:
        windows = parse_usage({"rateLimits": {"primary": {"usedPercent": 140}}})
        self.assertEqual(windows[0].remaining_percent, 0)


if __name__ == "__main__":
    unittest.main()
