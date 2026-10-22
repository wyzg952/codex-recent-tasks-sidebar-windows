from __future__ import annotations

import ctypes
import tkinter as tk
import unittest
from unittest.mock import patch

from codex_sidebar.app import APP_NAME, SidebarApp
from codex_sidebar.tray import shell32
from codex_sidebar.watcher import close_sidebar_windows, desired_action, sidebar_windows
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
        if not self.app.closing:
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

    def test_close_button_hides_until_tray_double_click_in_both_modes(self) -> None:
        for mode in ("pinned", "docked"):
            with self.subTest(mode=mode), patch.object(self.app, "_apply_dock"):
                self.app.mode = mode
                self.find.return_value = self.target
                self.app._sync_companion_visibility()
                user32.PostMessageW(self.hwnd, 0x0010, 0, 0)
                self.root.update()
                for _ in range(3):
                    self.app._sync_companion_visibility()
                self.assertFalse(self.app.closing)
                self.assertFalse(user32.IsWindowVisible(self.hwnd))
                self.assertIn(self.hwnd, sidebar_windows())
                self.assertTrue(self.app.tray.added)
                self.assertTrue(self.app.manual_hidden)
                user32.PostMessageW(self.app.tray.hwnd, self.app.tray.callback_message, 0, 0x0203)
                self.root.update()
                self.app._dock_tick()
                self.assertTrue(user32.IsWindowVisible(self.hwnd))
                self.assertFalse(self.app.manual_hidden)
                self.assert_tool_window()

    def test_manual_hide_resets_only_after_codex_hidden_then_restored(self) -> None:
        self.find.return_value = self.target
        self.app._sync_companion_visibility()
        self.app.hide_to_tray()
        self.find.return_value = None
        self.app.restore_from_tray()
        self.assertFalse(user32.IsWindowVisible(self.hwnd))
        self.find.return_value = self.target
        self.app._sync_companion_visibility()
        self.assertTrue(user32.IsWindowVisible(self.hwnd))
        self.app.hide_to_tray()
        self.find.return_value = None
        self.app._sync_companion_visibility()
        self.find.return_value = self.target
        self.app._sync_companion_visibility()
        self.assertTrue(user32.IsWindowVisible(self.hwnd))

    def test_internal_shutdown_still_exits(self) -> None:
        close_sidebar_windows([self.hwnd])
        self.root.update()
        self.app._dock_tick()
        self.assertTrue(self.app.closing)
        self.assertFalse(self.app.tray.added)

    def test_visibility_poll_is_rescheduled_after_transient_error(self) -> None:
        with patch.object(self.app, "_sync_companion_visibility", side_effect=OSError("temporary")), \
                patch.object(self.root, "after") as after:
            with self.assertRaises(OSError):
                self.app._dock_tick()
            after.assert_called_once_with(400, self.app._dock_tick)

    def test_real_bound_window_minimize_does_not_switch_to_background_window(self) -> None:
        primary = tk.Toplevel(self.root)
        primary.title("主要测试窗口")
        primary.geometry("600x400+40+40")
        other = tk.Toplevel(self.root)
        other.title("后台测试窗口")
        other.geometry("800x600+60+60")
        self.root.update()
        first = top_level_hwnd(primary.winfo_id())
        second = top_level_hwnd(other.winfo_id())
        def enumerate_targets(callback, param):
            callback(first, param)
            callback(second, param)
        try:
            with patch("codex_sidebar.win32.user32.EnumWindows", side_effect=enumerate_targets), \
                    patch("codex_sidebar.win32._process_path", return_value=r"C:\WindowsApps\OpenAI.Codex_test\app\ChatGPT.exe"), \
                    patch("codex_sidebar.win32.user32.GetForegroundWindow", return_value=first) as foreground, \
                    patch("codex_sidebar.app.find_codex_window", side_effect=find_codex_window):
                self.app._sync_companion_visibility()
                self.assertEqual(self.app.codex_hwnd, first)
                self.assertTrue(user32.IsWindowVisible(self.hwnd))
                user32.ShowWindow(first, 6)
                foreground.return_value = 0
                self.root.update()
                self.app._sync_companion_visibility()
                self.assertTrue(user32.IsWindowVisible(second))
                self.assertFalse(user32.IsWindowVisible(self.hwnd))
                self.assertEqual(self.app.codex_hwnd, first)
                primary.withdraw()
                self.root.update()
                self.app._sync_companion_visibility()
                self.assertFalse(user32.IsWindowVisible(self.hwnd))
                primary.deiconify()
                self.root.update()
                self.app._sync_companion_visibility()
                self.assertTrue(user32.IsWindowVisible(self.hwnd))
                foreground.return_value = second
                self.app._sync_companion_visibility()
                self.assertEqual(self.app.codex_hwnd, second)
                self.assert_tool_window()
        finally:
            primary.destroy()
            other.destroy()


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
