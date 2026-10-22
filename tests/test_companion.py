from __future__ import annotations

import ctypes
import tkinter as tk
import unittest
from unittest.mock import patch

from codex_sidebar.app import APP_NAME, SidebarApp
from codex_sidebar.tray import shell32
from codex_sidebar.watcher import desired_action, sidebar_windows
from codex_sidebar.win32 import (
    Rect, acquire_sidebar_instance, find_codex_window, kernel32, top_level_hwnd, user32,
)


class CompanionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patches = [
            patch("codex_sidebar.app.SettingsStore"),
            patch.object(SidebarApp, "refresh_full"),
            patch.object(SidebarApp, "refresh_usage"),
            patch("codex_sidebar.app.find_codex_window"),
        ]
        store, _, _, self.find = [item.start() for item in self.patches]
        store.return_value.load.return_value = {"mode": "pinned"}
        self.find.return_value = None
        self.root = tk.Tk()
        self.app = SidebarApp(self.root)
        self.root.update()
        self.hwnd = top_level_hwnd(self.root.winfo_id())
        self.target = (self.hwnd, Rect(600, 100, 1300, 900))

    def tearDown(self) -> None:
        for job in self.root.tk.call("after", "info"):
            self.root.after_cancel(job)
        self.app.close()
        for item in reversed(self.patches):
            item.stop()

    def assert_tool_window(self) -> None:
        style = user32.GetWindowLongW(self.hwnd, -20)
        self.assertTrue(style & 0x80)
        self.assertFalse(style & 0x40000)
        self.assertFalse(style & 0x08000000, "Normal clicking must remain possible")

    def test_start_hidden_with_real_tray_icon(self) -> None:
        self.assertFalse(user32.IsWindowVisible(self.hwnd))
        self.assertTrue(self.app.tray.added)
        self.assertTrue(shell32.Shell_NotifyIconW(1, ctypes.byref(self.app.tray.data)))
        self.assertEqual(self.root.title(), APP_NAME)
        self.assert_tool_window()

    def test_both_modes_follow_visibility_without_focus_or_taskbar_button(self) -> None:
        for mode in ("pinned", "docked"):
            with self.subTest(mode=mode):
                self.app.mode = mode
                self.find.return_value = self.target
                foreground = user32.GetForegroundWindow()
                with patch.object(self.app, "_apply_dock") as dock:
                    self.app._sync_companion_visibility()
                    self.root.update()
                self.assertEqual(dock.called, mode == "docked")
                self.assertTrue(user32.IsWindowVisible(self.hwnd))
                # Windows may finish transferring focus away from the preceding
                # hidden test window; only activation of this sidebar is a steal.
                if foreground != self.hwnd:
                    self.assertNotEqual(user32.GetForegroundWindow(), self.hwnd)
                self.assert_tool_window()
                self.find.return_value = None
                self.app._sync_companion_visibility()
                self.root.update()
                self.assertFalse(user32.IsWindowVisible(self.hwnd))
                self.assertEqual(self.root.state(), "withdrawn")
                self.assertTrue(self.app.tray.added)
                self.assert_tool_window()

    def test_hidden_instance_still_detected_by_watcher(self) -> None:
        self.assertIn(self.hwnd, sidebar_windows())
        self.assertEqual(desired_action(True, bool(sidebar_windows())), "none")

    def test_hidden_geometry_is_not_overwritten(self) -> None:
        self.app.settings["pinned_geometry"] = "380x740+40+40"
        self.app._save_window_geometry()
        self.assertEqual(self.app.settings["pinned_geometry"], "380x740+40+40")
        self.app.settings_store.save.assert_not_called()

    def test_tray_recovers_after_explorer_restart(self) -> None:
        with patch("codex_sidebar.tray.shell32.Shell_NotifyIconW", return_value=True) as notify:
            self.app.tray._dispatch(self.app.tray.hwnd, self.app.tray.taskbar_created, 0, 0)
            notify.assert_called_once()
            self.assertEqual(notify.call_args.args[0], 0)


class InstanceTests(unittest.TestCase):
    def test_second_sidebar_instance_is_rejected(self) -> None:
        first = acquire_sidebar_instance()
        if first is None:
            self.assertIsNone(acquire_sidebar_instance())
            return
        try:
            self.assertIsNone(acquire_sidebar_instance())
        finally:
            kernel32.CloseHandle(first)


class WindowDetectionTests(unittest.TestCase):
    def test_real_window_minimize_hide_restore(self) -> None:
        window = tk.Tk()
        window.title("侧栏测试目标窗口")
        window.geometry("600x400+40+40")
        window.attributes("-toolwindow", True)
        window.update()
        hwnd = top_level_hwnd(window.winfo_id())
        try:
            with patch("codex_sidebar.win32.user32.EnumWindows", side_effect=lambda callback, param: callback(hwnd, param)), \
                    patch("codex_sidebar.win32._process_path", return_value=r"C:\WindowsApps\OpenAI.Codex_test\app\ChatGPT.exe"):
                self.assertIsNotNone(find_codex_window())
                user32.ShowWindow(hwnd, 6)
                window.update()
                self.assertTrue(user32.IsIconic(hwnd))
                self.assertIsNone(find_codex_window())
                window.withdraw()
                window.update()
                self.assertIsNone(find_codex_window())
                window.deiconify()
                window.update()
                self.assertIsNotNone(find_codex_window())
        finally:
            window.destroy()


if __name__ == "__main__":
    unittest.main()
