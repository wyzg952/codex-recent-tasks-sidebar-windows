from __future__ import annotations

import ctypes
import os
from collections import deque
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .win32 import kernel32, user32


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.WORD
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayIcon:
    """Hidden native window; Tk's thread message loop dispatches its events."""

    def __init__(self, icon_path: Path, on_activate: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self.hwnd = None
        self.icon = None
        self.registered = False
        self.added = False
        self.on_activate = on_activate
        self.on_exit = on_exit
        self.pending_events: deque[str] = deque()
        self.instance = kernel32.GetModuleHandleW(None)
        self.class_name = f"CodexSidebarTray_{os.getpid()}"
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.callback_message = 0x8001
        self._wndproc = WNDPROC(self._dispatch)
        window_class = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=self.instance, lpszClassName=self.class_name)
        try:
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.registered = True
            self.hwnd = user32.CreateWindowExW(0x80, self.class_name, "CodexSidebarTrayHost", 0,
                                              0, 0, 0, 0, None, None, self.instance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.icon = user32.LoadImageW(None, str(icon_path), 1, 0, 0, 0x10 | 0x40)
            if not self.icon:
                raise ctypes.WinError(ctypes.get_last_error())
            self.data = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd,
                                        uID=1, uFlags=7, uCallbackMessage=self.callback_message,
                                        hIcon=self.icon, szTip="Codex 最近任务 · 双击恢复 · 随 Codex 显示和隐藏")
            self.ensure_icon()
        except Exception:
            self.close()
            raise

    def ensure_icon(self) -> None:
        if not self.added:
            self.added = bool(shell32.Shell_NotifyIconW(0, ctypes.byref(self.data)))

    def _dispatch(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == 0x0010:  # Internal shutdown; the host has no visible close button.
            self.pending_events.append("exit")
            return 0
        if message == self.taskbar_created:
            self.added = False
            if hasattr(self, "data"):
                self.ensure_icon()
            return 0
        if message == self.callback_message and lparam == 0x0203:
            self.pending_events.append("activate")
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def dispatch_pending(self) -> None:
        # Never enter Tcl from a ctypes WndProc callback. The ordinary Tk timer
        # drains these events outside native message dispatch (avoids GIL crashes).
        while self.pending_events:
            event = self.pending_events.popleft()
            if event == "exit":
                self.on_exit()
                return
            self.on_activate()

    def close(self) -> None:
        if self.added:
            shell32.Shell_NotifyIconW(2, ctypes.byref(self.data))
            self.added = False
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.icon:
            user32.DestroyIcon(self.icon)
            self.icon = None
        if self.registered:
            user32.UnregisterClassW(self.class_name, self.instance)
            self.registered = False
