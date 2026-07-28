from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from .app import APP_NAME
from .win32 import codex_window_exists, kernel32, user32


MUTEX_NAME = "Local\\CodexRecentTasksSidebarWatcher"
ERROR_ALREADY_EXISTS = 183
WM_CLOSE = 0x0010
POLL_SECONDS = 1.0
LAUNCH_RETRY_SECONDS = 5.0

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


def desired_action(codex_open: bool, sidebar_open: bool, launch_allowed: bool = True) -> str:
    if codex_open and not sidebar_open and launch_allowed:
        return "launch"
    if not codex_open and sidebar_open:
        return "close"
    return "none"


def sidebar_windows() -> list[int]:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length != len(APP_NAME):
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        if buffer.value == APP_NAME:
            found.append(int(hwnd))
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return found


def close_sidebar_windows(windows: list[int] | None = None) -> None:
    for hwnd in windows if windows is not None else sidebar_windows():
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _pythonw_executable() -> Path:
    current = Path(sys.executable).resolve()
    candidate = current.with_name("pythonw.exe")
    return candidate if candidate.exists() else current


def launch_sidebar() -> None:
    project_root = Path(__file__).resolve().parents[2]
    main = project_root / "src" / "main.py"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(_pythonw_executable()), str(main)],
        cwd=project_root,
        close_fds=True,
        creationflags=creation_flags,
    )


def acquire_single_instance() -> int | None:
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def run() -> int:
    mutex = acquire_single_instance()
    if mutex is None:
        return 0

    next_launch_at = 0.0
    try:
        while True:
            codex_open = codex_window_exists()
            windows = sidebar_windows()
            now = time.monotonic()
            action = desired_action(codex_open, bool(windows), now >= next_launch_at)
            if action == "launch":
                launch_sidebar()
                next_launch_at = now + LAUNCH_RETRY_SECONDS
            elif action == "close":
                close_sidebar_windows(windows)
            time.sleep(POLL_SECONDS)
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(run())
