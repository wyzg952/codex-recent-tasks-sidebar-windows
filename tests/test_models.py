from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from codex_sidebar.models import (
    MAX_VIEWED_TASKS,
    MAX_COLLAPSED_PROJECTS,
    CodexTask,
    DisplayState,
    RuntimeState,
    apply_viewed_task_versions,
    filter_existing_project_tasks,
    group_tasks,
    is_project_collapsed,
    normalize_collapsed_projects,
    normalize_project_path,
    project_path_key,
    projects_with_new_activity,
    normalize_viewed_task_versions,
    short_time_label,
)


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

    def test_grouping_merges_equivalent_windows_paths(self) -> None:
        tasks = [
            CodexTask("1", "普通路径", "C:/项目/中文项目", "main", 30),
            CodexTask("2", "扩展路径", r"\\?\c:\项目\中文项目", "main", 20),
        ]
        groups = group_tasks(tasks)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].tasks), 2)
        self.assertFalse(normalize_project_path(tasks[1].cwd).startswith("\\\\?\\"))
        self.assertEqual(project_path_key(tasks[0].cwd), project_path_key(tasks[1].cwd))

    def test_missing_project_tasks_are_hidden_without_deleting_history(self) -> None:
        tasks = [
            CodexTask("1", "存在项目", "C:/项目/存在", "main", 30),
            CodexTask("2", "旧项目", "C:/项目/已改名", "main", 20),
        ]
        visible = filter_existing_project_tasks(
            tasks,
            path_exists=lambda path: path.endswith("存在"),
        )
        self.assertEqual([task.id for task in visible], ["1"])
        self.assertEqual(len(tasks), 2)

    def test_time_labels(self) -> None:
        now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(short_time_label(int((now - timedelta(seconds=20)).timestamp() * 1000), now), "刚刚")
        self.assertEqual(short_time_label(int((now - timedelta(minutes=12)).timestamp() * 1000), now), "12 分钟")
        self.assertEqual(short_time_label(int((now - timedelta(days=1)).timestamp() * 1000), now), "昨天 12:00")

    def test_viewed_versions_survive_refresh_until_a_new_reply(self) -> None:
        tasks = [
            CodexTask("任务甲", "已查看", "C:/项目/甲", "main", 100, unread=True),
            CodexTask("任务乙", "有新回复", "C:/项目/乙", "main", 200, unread=True),
        ]
        updated = apply_viewed_task_versions(tasks, {"任务甲": 100, "任务乙": 150})
        self.assertFalse(updated[0].unread)
        self.assertEqual(updated[0].display_state, DisplayState.IDLE)
        self.assertTrue(updated[1].unread)
        self.assertEqual(updated[1].display_state, DisplayState.NEEDS_REVIEW)

    def test_viewed_versions_are_validated_and_bounded(self) -> None:
        raw = {f"任务{index}": index + 1 for index in range(MAX_VIEWED_TASKS + 10)}
        raw.update({"": 10, "错误时间": "10", "布尔值": True})
        normalized = normalize_viewed_task_versions(raw)
        self.assertEqual(len(normalized), MAX_VIEWED_TASKS)
        self.assertNotIn("", normalized)
        self.assertNotIn("错误时间", normalized)
        self.assertNotIn("布尔值", normalized)
        self.assertEqual(next(iter(normalized.values())), MAX_VIEWED_TASKS + 10)

    def test_project_collapse_state_and_search_override(self) -> None:
        collapsed = normalize_collapsed_projects(["C:/项目/甲", "C:/项目/甲", "", 42])
        self.assertEqual(collapsed, {project_path_key("C:/项目/甲")})
        self.assertTrue(is_project_collapsed("C:/项目/甲", collapsed))
        self.assertFalse(is_project_collapsed("C:/项目/甲", collapsed, "素材"))
        self.assertFalse(is_project_collapsed("C:/项目/乙", collapsed))

    def test_collapsed_projects_are_bounded(self) -> None:
        raw = [f"C:/项目/{index}" for index in range(MAX_COLLAPSED_PROJECTS + 10)]
        self.assertEqual(len(normalize_collapsed_projects(raw)), MAX_COLLAPSED_PROJECTS)

    def test_new_activity_identifies_project_to_expand(self) -> None:
        previous = [
            CodexTask("1", "旧状态", "C:/项目/甲", "main", 100, activity_ms=100),
            CodexTask("2", "无变化", "C:/项目/乙", "main", 100, activity_ms=100),
        ]
        current = [
            CodexTask("1", "最近打开", "C:/项目/甲", "main", 100, activity_ms=200),
            CodexTask("2", "无变化", "C:/项目/乙", "main", 100, activity_ms=100),
        ]
        self.assertEqual(projects_with_new_activity(previous, current), {current[0].project_key})


if __name__ == "__main__":
    unittest.main()
