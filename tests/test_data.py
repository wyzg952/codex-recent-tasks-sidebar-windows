from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from codex_sidebar.data import DataPaths, RolloutStateReader, TaskRepository
from codex_sidebar.models import RuntimeState


class DataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rollouts = self.root / "sessions"
        self.rollouts.mkdir()
        self.db = self.root / "state.sqlite"
        self.index = self.root / "session_index.jsonl"
        self.global_state = self.root / "global.json"
        connection = sqlite3.connect(self.db)
        connection.executescript(
            """
            CREATE TABLE threads (
              id TEXT PRIMARY KEY, title TEXT, cwd TEXT, git_branch TEXT,
              updated_at_ms INTEGER, updated_at INTEGER, archived INTEGER DEFAULT 0,
              thread_source TEXT, source TEXT, agent_path TEXT, rollout_path TEXT
            );
            CREATE TABLE thread_spawn_edges (
              parent_thread_id TEXT NOT NULL, child_thread_id TEXT NOT NULL PRIMARY KEY, status TEXT NOT NULL
            );
            """
        )
        self.running = self.rollouts / "running.jsonl"
        self.running.write_text(
            '{"type":"event_msg","payload":{"type":"task_started"}}\n', encoding="utf-8"
        )
        future_ms = 4_102_444_800_000
        connection.executemany(
            "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("00000000-0000-0000-0000-000000000001", "原始标题", "C:/测试/项目甲", "main", future_ms, 0, 0, "", "", "", str(self.running)),
                ("00000000-0000-0000-0000-000000000002", "第二任务", "C:/测试/项目乙", "", future_ms - 1, 0, 0, "", "", "", ""),
                ("00000000-0000-0000-0000-000000000003", "内部任务", "C:/测试/项目甲", "", future_ms - 2, 0, 0, "subagent", '{"subagent":true}', "C:/agent", ""),
                ("00000000-0000-0000-0000-000000000004", "根任务", "C:/测试/项目甲", "", future_ms - 3, 0, 0, "subagent", "", "", ""),
                ("00000000-0000-0000-0000-000000000005", "子任务", "C:/测试/项目甲", "", future_ms - 4, 0, 0, "subagent", "", "", ""),
            ],
        )
        connection.execute(
            "INSERT INTO thread_spawn_edges VALUES (?,?,?)",
            ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000005", "running"),
        )
        connection.commit()
        connection.close()
        self.index.write_text(
            '{"id":"00000000-0000-0000-0000-000000000001","thread_name":"旧名称"}\n'
            '{"id":"00000000-0000-0000-0000-000000000001","thread_name":"中文新名称"}\n'
            '{"partial"',
            encoding="utf-8",
        )
        self.global_state.write_text(
            json.dumps({"electron-persisted-atom-state": {"unread-thread-ids-by-host-v1": {"local": ["00000000-0000-0000-0000-000000000002"]}}}),
            encoding="utf-8",
        )
        self.paths = DataPaths(self.db, self.index, self.global_state, self.rollouts)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_repository_filters_and_overrides_without_writes(self) -> None:
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.db, self.index, self.global_state, self.running)}
        tasks = TaskRepository(self.paths).load(now_ms=4_102_444_800_000)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
        self.assertEqual(before, after)
        self.assertEqual([task.id[-1] for task in tasks], ["1", "2", "4"])
        self.assertEqual(tasks[0].title, "中文新名称")
        self.assertEqual(tasks[0].runtime, RuntimeState.RUNNING)
        self.assertTrue(tasks[1].unread)
        self.assertEqual(tasks[1].display_state.value, "needs_review")

    def test_incremental_runtime_state(self) -> None:
        reader = RolloutStateReader(self.rollouts)
        self.assertEqual(reader.read(str(self.running)), RuntimeState.RUNNING)
        with self.running.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"response_item","payload":{"type":"function_call","name":"request_user_input","call_id":"call-1"}}\n')
        self.assertEqual(reader.read(str(self.running)), RuntimeState.NEEDS_ACTION)
        with self.running.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"response_item","payload":{"type":"function_call_output","call_id":"call-1"}}\n')
        self.assertEqual(reader.read(str(self.running)), RuntimeState.RUNNING)
        with self.running.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"event_msg","payload":{"type":"task_complete"}}\n')
        self.assertEqual(reader.read(str(self.running)), RuntimeState.IDLE)

    def test_rejects_rollout_outside_root(self) -> None:
        outside = self.root / "outside.jsonl"
        outside.write_text('{"type":"event_msg","payload":{"type":"task_started"}}\n', encoding="utf-8")
        self.assertEqual(RolloutStateReader(self.rollouts).read(str(outside)), RuntimeState.UNKNOWN)

    def test_configurable_recent_day_range(self) -> None:
        now_ms = 4_102_444_800_000
        older_id = "00000000-0000-0000-0000-000000000006"
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                older_id,
                "三天前任务",
                "C:/测试/项目丙",
                "",
                now_ms - 3 * 24 * 60 * 60 * 1000,
                0,
                0,
                "",
                "",
                "",
                "",
            ),
        )
        connection.commit()
        connection.close()
        two_days = TaskRepository(self.paths).load(now_ms=now_ms, recent_days=2)
        seven_days = TaskRepository(self.paths).load(now_ms=now_ms, recent_days=7)
        self.assertNotIn(older_id, {task.id for task in two_days})
        self.assertIn(older_id, {task.id for task in seven_days})


if __name__ == "__main__":
    unittest.main()
