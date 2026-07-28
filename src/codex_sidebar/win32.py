from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
GA_ROOT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class DockPlacement:
    x: int
    y: int
    width: int
    height: int
    side: str
    used_fallback: bool


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def enable_per_monitor_dpi() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def _process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def visible_window_bounds(hwnd: int) -> Rect | None:
    # GetWindowRect 包含 Windows 11 的透明阴影边距，会让两个窗口视觉上重叠。
    # DWM 扩展边框是用户实际看到的窗口边界，吸附时应以它为准。
    rect = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    ) == 0:
        return Rect(rect.left, rect.top, rect.right, rect.bottom)
    fallback = window_rect(hwnd)
    if fallback is not None:
        return fallback
    return None


def window_rect(hwnd: int) -> Rect | None:
    rect = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return Rect(rect.left, rect.top, rect.right, rect.bottom)
    return None


def find_codex_window() -> tuple[int, Rect] | None:
    found: list[tuple[int, Rect]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        item = visible_window_bounds(hwnd)
        if item is None:
            return True
        if item.width < 500 or item.height < 320:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = _process_path(pid.value).casefold()
        if "openai.codex_" in path and path.endswith("\\chatgpt.exe"):
            found.append((hwnd, item))
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return max(found, key=lambda pair: pair[1].width * pair[1].height) if found else None


def codex_window_exists() -> bool:
    """Return whether the unified desktop app currently has a Codex window.

    Unlike ``find_codex_window``, minimized windows count as existing so the
    companion sidebar is not closed merely because Codex was minimized.
    """
    found = False
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = _process_path(pid.value).casefold()
        if "openai.codex_" in path and path.endswith("\\chatgpt.exe"):
            found = True
            return False
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return found


def work_area(hwnd: int) -> Rect:
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        value = info.rcWork
        return Rect(value.left, value.top, value.right, value.bottom)
    return Rect(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def dock_position(
    codex: Rect,
    panel_width: int,
    panel_height: int,
    work: Rect,
    side: str,
    gap: int = 0,
    min_width: int = 280,
    min_height: int = 420,
) -> DockPlacement:
    height = min(max(panel_height, min_height), work.height)
    y = min(max(codex.top, work.top), work.bottom - height)
    available = {
        "left": max(0, codex.left - work.left - gap),
        "right": max(0, work.right - codex.right - gap),
    }
    opposite = "right" if side == "left" else "left"
    actual_side = side
    used_fallback = False
    if available[actual_side] < min_width and available[opposite] >= min_width:
        actual_side = opposite
        used_fallback = True

    width = min(max(panel_width, min_width), max(min_width, available[actual_side]))
    if actual_side == "left":
        x = codex.left - width - gap
    else:
        x = codex.right + gap
    x = min(max(x, work.left), work.right - width)
    return DockPlacement(x, y, width, height, actual_side, used_fallback)


def compensate_visible_frame(
    codex: Rect,
    work: Rect,
    side: str,
    client_width: int,
    visible_width: int,
    visible_left_inset: int,
    min_width: int = 280,
) -> tuple[int, int]:
    """Translate Tk client geometry so the DWM-visible edge touches Codex exactly."""
    available = codex.left - work.left if side == "left" else work.right - codex.right
    visible_delta = visible_width - client_width
    adjusted_width = min(client_width, max(min_width, available - visible_delta))
    adjusted_visible_width = adjusted_width + visible_delta
    if side == "left":
        outer_x = codex.left - visible_left_inset - adjusted_visible_width
    else:
        outer_x = codex.right - visible_left_inset
    return outer_x, adjusted_width


def is_codex_or_self_foreground(self_hwnd: int, codex_hwnd: int) -> bool:
    foreground = user32.GetForegroundWindow()
    return foreground in {self_hwnd, codex_hwnd}


def is_left_mouse_down() -> bool:
    return bool(user32.GetAsyncKeyState(0x01) & 0x8000)


def set_topmost(hwnd: int, enabled: bool) -> None:
    user32.SetWindowPos(hwnd, HWND_TOPMOST if enabled else HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def top_level_hwnd(hwnd: int) -> int:
    return int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


def open_task(thread_id: str) -> None:
    os.startfile(f"codex://threads/{thread_id}")
