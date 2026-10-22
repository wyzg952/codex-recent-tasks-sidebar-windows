from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any

from .models import CodexTask, RuntimeState


DAY_MS = 24 * 60 * 60 * 1000
DEFAULT_RECENT_DAYS = 7
MAX_GLOBAL_STATE_SIZE = 64 * 1024 * 1024
MAX_ROLLOUT_SIZE = 512 * 1024 * 1024
MAX_INITIAL_SCAN = 16 * 1024 * 1024
MAX_INCREMENT = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DataPaths:
    database: Path
    session_index: Path
    global_state: Path
    rollout_root: Path

    @classmethod
    def discover(cls) -> "DataPaths":
        root = Path.home() / ".codex"
        database_override = os.environ.get("CODEX_TASK_DB_OVERRIDE")
        if database_override:
            database = Path(database_override)
        else:
            candidates = [root / "state_5.sqlite", root / "sqlite" / "state_5.sqlite"]
            existing = [path for path in candidates if path.exists()]
            if not existing:
                raise FileNotFoundError("没有找到 Codex 本地任务数据库。请先打开并使用一次 Codex。")
            database = max(existing, key=lambda path: path.stat().st_mtime_ns)
        return cls(
            database=database,
            session_index=Path(os.environ.get("CODEX_SESSION_INDEX_OVERRIDE", root / "session_index.jsonl")),
            global_state=Path(os.environ.get("CODEX_GLOBAL_STATE_OVERRIDE", root / ".codex-global-state.json")),
            rollout_root=Path(os.environ.get("CODEX_ROLLOUT_ROOT_OVERRIDE", root / "sessions")),
        )


@dataclass(slots=True)
class _RolloutCache:
    size: int
    mtime_ns: int
    state: RuntimeState
    pending: set[str]
    incremental: bool


class RolloutStateReader:
    def __init__(self, rollout_root: Path) -> None:
        self.rollout_root = rollout_root.resolve(strict=False)
        self._cache: dict[str, _RolloutCache] = {}
        self._lock = threading.Lock()

    def read(self, raw_path: str) -> RuntimeState:
        path = self._validated_path(raw_path)
        if path is None:
            return RuntimeState.UNKNOWN
        try:
            stat = path.stat()
        except OSError:
            return RuntimeState.UNKNOWN
        if stat.st_size <= 0 or stat.st_size > MAX_ROLLOUT_SIZE:
            return RuntimeState.UNKNOWN
        key = str(path)
        with self._lock:
            cached = self._cache.get(key)
        if cached and cached.size == stat.st_size and cached.mtime_ns == stat.st_mtime_ns:
            return cached.state
        if (
            cached
            and cached.incremental
            and stat.st_size > cached.size
            and stat.st_size - cached.size <= MAX_INCREMENT
            and self._ends_with_newline(path)
        ):
            state, pending = self._scan_forward(path, cached.size, cached.state, set(cached.pending))
            entry = _RolloutCache(stat.st_size, stat.st_mtime_ns, state, pending, True)
        else:
            state, pending = self._scan_reverse(path, stat.st_size)
            entry = _RolloutCache(
                stat.st_size,
                stat.st_mtime_ns,
                state,
                pending,
                self._ends_with_newline(path),
            )
        with self._lock:
            self._cache[key] = entry
        return entry.state

    def _validated_path(self, raw_path: str) -> Path | None:
        if not raw_path or len(raw_path.encode("utf-8", errors="ignore")) > 4096:
            return None
        try:
            path = Path(raw_path).resolve(strict=True)
            if not path.is_file() or not path.is_relative_to(self.rollout_root):
                return None
            return path
        except (OSError, ValueError):
            return None

    @staticmethod
    def _ends_with_newline(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                handle.seek(-1, os.SEEK_END)
                return handle.read(1) == b"\n"
        except OSError:
            return False

    def _scan_reverse(self, path: Path, size: int) -> tuple[RuntimeState, set[str]]:
        start = max(0, size - MAX_INITIAL_SCAN)
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                if start:
                    handle.readline()
                lines = handle.read().splitlines()
        except OSError:
            return RuntimeState.UNKNOWN, set()
        completed: set[str] = set()
        for raw in reversed(lines):
            record = _state_record(raw)
            if record is None:
                continue
            record_type, payload_type, payload = record
            event_state = _event_state(record_type, payload_type, payload)
            if event_state is not None:
                return event_state, set()
            call_id = payload.get("call_id")
            if record_type == "response_item" and isinstance(call_id, str):
                if payload_type in {"function_call_output", "custom_tool_call_output"}:
                    completed.add(call_id)
                elif payload_type in {"function_call", "custom_tool_call"}:
                    if call_id in completed:
                        completed.remove(call_id)
                    elif _requires_user_action(payload):
                        return RuntimeState.NEEDS_ACTION, {call_id}
        return RuntimeState.UNKNOWN, set()

    def _scan_forward(
        self,
        path: Path,
        offset: int,
        state: RuntimeState,
        pending: set[str],
    ) -> tuple[RuntimeState, set[str]]:
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                lines = handle.read(MAX_INCREMENT + 1).splitlines()
        except OSError:
            return state, pending
        for raw in lines:
            record = _state_record(raw)
            if record is None:
                continue
            record_type, payload_type, payload = record
            event_state = _event_state(record_type, payload_type, payload)
            if event_state is not None:
                state = event_state
                pending.clear()
                continue
            call_id = payload.get("call_id")
            if record_type != "response_item" or not isinstance(call_id, str):
                continue
            if payload_type in {"function_call", "custom_tool_call"} and _requires_user_action(payload):
                pending.add(call_id)
                state = RuntimeState.NEEDS_ACTION
            elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                pending.discard(call_id)
                if not pending:
                    state = RuntimeState.RUNNING
        return state, pending


def _state_record(raw: bytes) -> tuple[str, str, dict[str, Any]] | None:
    if not raw or len(raw) > 4 * 1024 * 1024:
        return None
    if not any(token in raw for token in (b"task_", b"turn_aborted", b"thread/status/changed", b"function_call", b"custom_tool_call")):
        return None
    try:
        obj = json.loads(raw)
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            return None
        record_type = obj.get("type")
        payload_type = payload.get("type")
        if not isinstance(record_type, str) or not isinstance(payload_type, str):
            return None
        return record_type, payload_type, payload
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _event_state(record_type: str, payload_type: str, payload: dict[str, Any]) -> RuntimeState | None:
    if record_type != "event_msg":
        return None
    if payload_type in {"task_complete", "turn_aborted"}:
        return RuntimeState.IDLE
    if payload_type == "task_started":
        return RuntimeState.RUNNING
    if payload_type == "thread/status/changed":
        status = payload.get("status")
        if not isinstance(status, dict):
            return None
        if status.get("type") == "idle":
            return RuntimeState.IDLE
        if status.get("type") == "active":
            flags = status.get("activeFlags") or []
            if "waitingOnApproval" in flags or "waitingOnUserInput" in flags:
                return RuntimeState.NEEDS_ACTION
            return RuntimeState.RUNNING
    return None


def _requires_user_action(payload: dict[str, Any]) -> bool:
    name = payload.get("name")
    if name == "request_user_input":
        return True
    if name not in {"exec", "exec_command"}:
        return False
    raw = payload.get("arguments", payload.get("input"))
    if not isinstance(raw, str) or len(raw.encode("utf-8", errors="ignore")) > 64 * 1024:
        return False
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(args, dict) and args.get("sandbox_permissions") == "require_escalated"


class TaskRepository:
    QUERY_TEMPLATE = """
    SELECT id,
      CASE WHEN trim(title) = '' THEN '未命名任务' ELSE substr(title, 1, 240) END AS title,
      cwd, COALESCE(git_branch, '') AS git_branch,
      CAST(COALESCE(NULLIF(updated_at_ms, 0), updated_at * 1000) AS INTEGER) AS updated_ms,
      CAST({activity_expression} AS INTEGER) AS activity_ms,
      COALESCE(rollout_path, '') AS rollout_path
    FROM threads AS task
    WHERE archived = 0
      AND COALESCE(source, '') NOT LIKE '{{"subagent"%'
      AND (agent_path IS NULL OR agent_path = '')
      AND NOT EXISTS (
        SELECT 1 FROM thread_spawn_edges AS edge WHERE edge.child_thread_id = task.id
      )
    ORDER BY activity_ms DESC, updated_ms DESC, id ASC
    """

    def __init__(self, paths: DataPaths | None = None) -> None:
        self.paths = paths or DataPaths.discover()
        self.rollout_reader = RolloutStateReader(self.paths.rollout_root)

    def load(self, now_ms: int | None = None, recent_days: int = DEFAULT_RECENT_DAYS) -> list[CodexTask]:
        if not self.paths.database.exists():
            raise FileNotFoundError("没有找到 Codex 本地任务数据库。请先打开并使用一次 Codex。")
        uri = self.paths.database.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.8)
        try:
            connection.execute("PRAGMA query_only=ON")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
            updated_expression = "COALESCE(NULLIF(task.updated_at_ms, 0), task.updated_at * 1000, 0)"
            recency_parts = []
            if "recency_at_ms" in columns:
                recency_parts.append("NULLIF(task.recency_at_ms, 0)")
            if "recency_at" in columns:
                recency_parts.append("NULLIF(task.recency_at, 0) * 1000")
            if recency_parts:
                recency_expression = f"COALESCE({', '.join(recency_parts)}, 0)"
                activity_expression = f"MAX({updated_expression}, {recency_expression})"
            else:
                activity_expression = updated_expression
            query = self.QUERY_TEMPLATE.format(activity_expression=activity_expression)
            rows = connection.execute(query).fetchall()
        finally:
            connection.close()
        names = self._load_names()
        unread_ids = self._load_unread_ids()
        bounded_days = min(365, max(1, int(recent_days)))
        cutoff = (now_ms if now_ms is not None else int(time() * 1000)) - bounded_days * DAY_MS
        tasks: list[CodexTask] = []
        for row in rows:
            updated_ms = int(row[4])
            activity_ms = int(row[5])
            if activity_ms < cutoff:
                continue
            task_id = str(row[0])
            task = CodexTask(
                id=task_id,
                title=names.get(task_id, str(row[1])),
                cwd=str(row[2]),
                git_branch=str(row[3]),
                updated_ms=updated_ms,
                rollout_path=str(row[6]),
                unread=task_id in unread_ids,
                activity_ms=activity_ms,
            )
            tasks.append(task.with_updates(runtime=self.rollout_reader.read(task.rollout_path)))
        return tasks

    def refresh_runtime(self, tasks: list[CodexTask]) -> list[CodexTask]:
        return [task.with_updates(runtime=self.rollout_reader.read(task.rollout_path)) for task in tasks]

    def _load_names(self) -> dict[str, str]:
        path = self.paths.session_index
        if not path.is_file() or path.stat().st_size > MAX_GLOBAL_STATE_SIZE:
            return {}
        names: dict[str, str] = {}
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    task_id, raw_name = record.get("id"), record.get("thread_name")
                    if isinstance(task_id, str) and isinstance(raw_name, str) and raw_name.strip():
                        names[task_id] = raw_name.strip()
        except OSError:
            return {}
        return names

    def _load_unread_ids(self) -> set[str]:
        path = self.paths.global_state
        try:
            if not path.is_file() or path.stat().st_size > MAX_GLOBAL_STATE_SIZE:
                return set()
            root = json.loads(path.read_text(encoding="utf-8"))
            by_host = root.get("electron-persisted-atom-state", {}).get("unread-thread-ids-by-host-v1", {})
            return {value for values in by_host.values() if isinstance(values, list) for value in values if isinstance(value, str) and len(value) <= 128}
        except (OSError, json.JSONDecodeError, AttributeError):
            return set()
