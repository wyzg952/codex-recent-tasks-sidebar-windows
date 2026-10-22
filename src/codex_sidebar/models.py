from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import ntpath
from pathlib import Path
from typing import Any


MAX_VIEWED_TASKS = 500
MAX_COLLAPSED_PROJECTS = 200


def normalize_project_path(raw_path: str) -> str:
    try:
        value = str(Path(raw_path).resolve(strict=False))
    except OSError:
        value = raw_path
    if value.casefold().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normpath(value)


def project_path_key(raw_path: str) -> str:
    return ntpath.normcase(normalize_project_path(raw_path))


class RuntimeState(str, Enum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    RUNNING = "running"
    NEEDS_ACTION = "needs_action"


class DisplayState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    NEEDS_ACTION = "needs_action"
    NEEDS_REVIEW = "needs_review"

    @classmethod
    def resolve(cls, runtime: RuntimeState, unread: bool, current: bool = False) -> "DisplayState":
        if runtime == RuntimeState.NEEDS_ACTION:
            return cls.NEEDS_ACTION
        if runtime == RuntimeState.RUNNING:
            return cls.RUNNING
        if current:
            return cls.IDLE
        return cls.NEEDS_REVIEW if unread else cls.IDLE

    @property
    def badge(self) -> str | None:
        return {
            self.RUNNING: "运行中",
            self.NEEDS_ACTION: "待操作",
            self.NEEDS_REVIEW: "待查看",
        }.get(self)


@dataclass(frozen=True, slots=True)
class CodexTask:
    id: str
    title: str
    cwd: str
    git_branch: str
    updated_ms: int
    rollout_path: str = ""
    unread: bool = False
    runtime: RuntimeState = RuntimeState.UNKNOWN
    current: bool = False
    activity_ms: int = 0

    @property
    def project_path(self) -> str:
        return normalize_project_path(self.cwd)

    @property
    def project_key(self) -> str:
        return project_path_key(self.cwd)

    @property
    def project_name(self) -> str:
        return Path(self.project_path).name or "未归类"

    @property
    def display_state(self) -> DisplayState:
        return DisplayState.resolve(self.runtime, self.unread, self.current)

    @property
    def recent_ms(self) -> int:
        return max(self.updated_ms, self.activity_ms)

    def with_updates(self, **changes: object) -> "CodexTask":
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class TaskGroup:
    path: str
    name: str
    tasks: tuple[CodexTask, ...]

    @property
    def latest_update(self) -> int:
        return self.tasks[0].recent_ms if self.tasks else 0


def normalize_viewed_task_versions(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    valid = {
        task_id: updated_ms
        for task_id, updated_ms in raw.items()
        if isinstance(task_id, str)
        and 0 < len(task_id) <= 128
        and isinstance(updated_ms, int)
        and not isinstance(updated_ms, bool)
        and updated_ms > 0
    }
    return dict(sorted(valid.items(), key=lambda item: item[1], reverse=True)[:MAX_VIEWED_TASKS])


def apply_viewed_task_versions(tasks: list[CodexTask], viewed: dict[str, int]) -> list[CodexTask]:
    return [
        task.with_updates(unread=False)
        if task.unread and viewed.get(task.id, 0) >= task.updated_ms
        else task
        for task in tasks
    ]


def filter_existing_project_tasks(
    tasks: list[CodexTask],
    path_exists: Any = None,
) -> list[CodexTask]:
    exists = path_exists or (lambda value: Path(value).is_dir())
    visible: list[CodexTask] = []
    for task in tasks:
        try:
            if exists(task.project_path):
                visible.append(task)
        except OSError:
            continue
    return visible


def normalize_collapsed_projects(raw: Any) -> set[str]:
    if not isinstance(raw, list):
        return set()
    valid: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value or len(value) > 32_768 or value in seen:
            continue
        normalized = project_path_key(value)
        if normalized in seen:
            continue
        valid.append(normalized)
        seen.add(normalized)
        if len(valid) >= MAX_COLLAPSED_PROJECTS:
            break
    return set(valid)


def is_project_collapsed(project_path: str, collapsed_projects: set[str], search: str = "") -> bool:
    return not search.strip() and project_path_key(project_path) in collapsed_projects


def projects_with_new_activity(previous: list[CodexTask], current: list[CodexTask]) -> set[str]:
    old_by_id = {task.id: task for task in previous}
    active_states = {RuntimeState.RUNNING, RuntimeState.NEEDS_ACTION}
    changed: set[str] = set()
    for task in current:
        old = old_by_id.get(task.id)
        if (
            old is None
            or task.recent_ms > old.recent_ms
            or (task.unread and not old.unread)
            or (task.runtime != old.runtime and task.runtime in active_states)
        ):
            changed.add(task.project_key)
    return changed


def group_tasks(tasks: list[CodexTask], search: str = "") -> list[TaskGroup]:
    query = search.strip().casefold()
    visible = [
        task
        for task in tasks
        if not query
        or query in task.title.casefold()
        or query in task.project_name.casefold()
        or query in task.git_branch.casefold()
    ]
    grouped: dict[str, list[CodexTask]] = {}
    for task in visible:
        grouped.setdefault(task.project_key, []).append(task)
    result = []
    for path, project_tasks in grouped.items():
        project_tasks.sort(key=lambda item: (-item.recent_ms, item.id))
        result.append(TaskGroup(path, project_tasks[0].project_name, tuple(project_tasks)))
    return sorted(result, key=lambda group: (-group.latest_update, group.name.casefold()))


def short_time_label(updated_ms: int, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    moment = datetime.fromtimestamp(updated_ms / 1000, tz=now.tzinfo)
    elapsed = max(0, int((now - moment).total_seconds()))
    if elapsed < 60:
        return "刚刚"
    if elapsed < 3600:
        return f"{max(1, elapsed // 60)} 分钟"
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if (now.date() - moment.date()).days == 1:
        return f"昨天 {moment:%H:%M}"
    return moment.strftime("%m-%d")
