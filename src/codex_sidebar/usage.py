from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class UsageWindow:
    label: str
    remaining_percent: int


def parse_usage(result: dict[str, Any]) -> list[UsageWindow]:
    limits = result.get("rateLimits")
    if not isinstance(limits, dict):
        raise ValueError("缺少 rateLimits")
    windows: list[UsageWindow] = []
    for key, fallback in (("primary", "主要额度"), ("secondary", "次要额度")):
        raw = limits.get(key)
        if not isinstance(raw, dict) or not isinstance(raw.get("usedPercent"), (int, float)):
            continue
        used = min(100, max(0, int(raw["usedPercent"])))
        windows.append(UsageWindow(_duration_label(raw.get("windowDurationMins"), fallback), 100 - used))
    if not windows:
        raise ValueError("没有可显示的用量周期")
    return windows


def _duration_label(minutes: object, fallback: str) -> str:
    if not isinstance(minutes, (int, float)) or int(minutes) <= 0:
        return fallback
    value = int(minutes)
    if value == 10_080:
        return "每周"
    if value % 1_440 == 0:
        return f"{value // 1_440} 天"
    if value % 60 == 0:
        return f"{value // 60} 小时"
    return f"{value} 分钟"


def _codex_executable() -> str:
    override = os.environ.get("CODEX_APP_SERVER_OVERRIDE")
    if override and Path(override).is_file():
        return override
    local_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    candidates = sorted(local_bin.glob("*/codex.exe"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if candidates:
        return str(candidates[0])
    executable = shutil.which("codex")
    if executable:
        return executable
    raise FileNotFoundError("没有找到 Codex 官方程序")


def fetch_usage(timeout: float = 15.0) -> list[UsageWindow]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [_codex_executable(), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    output: queue.Queue[dict[str, Any]] = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    output.put(value)
            except json.JSONDecodeError:
                continue

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def send(value: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex-recent-tasks-sidebar-windows", "title": "Codex 最近任务", "version": "1.0.0"}}})
        while True:
            message = output.get(timeout=timeout)
            if message.get("id") == 1:
                if message.get("error") is not None:
                    raise RuntimeError("Codex 用量服务初始化失败")
                break
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read", "params": None})
        while True:
            message = output.get(timeout=timeout)
            if message.get("id") != 2:
                continue
            if message.get("error") is not None or not isinstance(message.get("result"), dict):
                raise RuntimeError("Codex 用量服务读取失败")
            return parse_usage(message["result"])
    except queue.Empty as error:
        raise TimeoutError("读取 Codex 剩余用量超时") from error
    finally:
        if process.stdin:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
