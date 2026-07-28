from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from codex_sidebar.models import CodexTask, DisplayState, RuntimeState, group_tasks, short_time_label


class ModelTests(unittest.TestCase):
    def test_display_priority(self) -> None:
        self.assertEqual(DisplayState.resolve(RuntimeState.NEEDS_ACTION, True), DisplayState.NEEDS_ACTION)
        self.assertEqual(DisplayState.resolve(RuntimeState.RUNNING, True), DisplayState.RUNNING)
        self.assertEqual(DisplayState.resolve(RuntimeState.IDLE, True), DisplayState.NEEDS_REVIEW)
        self.assertEqual(DisplayState.resolve(RuntimeState.IDLE, True, current=True), DisplayState.IDLE)
        self.assertEqual(DisplayState.resolve(RuntimeState.UNKNOWN, False), DisplayState.IDLE)

    def test_grouping_and_search_support_chinese(self) -> None:
        tasks = [
            CodexTask("2", "整理素材", "C:/项目/甲", "main", 20),
            CodexTask("1", "剪辑测试", "C:/项目/甲", "main", 30),
            CodexTask("3", "英文任务", "C:/项目/乙", "feature", 10),
        ]
        groups = group_tasks(tasks)
        self.assertEqual([group.name for group in groups], ["甲", "乙"])
        self.assertEqual([task.id for task in groups[0].tasks], ["1", "2"])
        self.assertEqual([group.name for group in group_tasks(tasks, "素材")], ["甲"])

    def test_time_labels(self) -> None:
        now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(short_time_label(int((now - timedelta(seconds=20)).timestamp() * 1000), now), "刚刚")
        self.assertEqual(short_time_label(int((now - timedelta(minutes=12)).timestamp() * 1000), now), "12 分钟")
        self.assertEqual(short_time_label(int((now - timedelta(days=1)).timestamp() * 1000), now), "昨天 12:00")


if __name__ == "__main__":
    unittest.main()
