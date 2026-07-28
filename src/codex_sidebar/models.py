from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path


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

    @property
    def project_path(self) -> str:
        try:
            return str(Path(self.cwd).resolve(strict=False))
        except OSError:
            return self.cwd

    @property
    def project_name(self) -> str:
        return Path(self.project_path).name or "未归类"

    @property
    def display_state(self) -> DisplayState:
        return DisplayState.resolve(self.runtime, self.unread, self.current)

    def with_updates(self, **changes: object) -> "CodexTask":
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class TaskGroup:
    path: str
    name: str
    tasks: tuple[CodexTask, ...]

    @property
    def latest_update(self) -> int:
        return self.tasks[0].updated_ms if self.tasks else 0


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
        grouped.setdefault(task.project_path, []).append(task)
    result = []
    for path, project_tasks in grouped.items():
        project_tasks.sort(key=lambda item: (-item.updated_ms, item.id))
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
