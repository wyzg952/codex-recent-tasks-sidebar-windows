from __future__ import annotations

import unittest

from codex_sidebar.win32 import Rect, compensate_visible_frame, dock_position


class DockTests(unittest.TestCase):
    def test_right_dock(self) -> None:
        placement = dock_position(Rect(100, 50, 1100, 850), 300, 650, Rect(0, 0, 1920, 1040), "right")
        self.assertEqual((placement.x, placement.y, placement.width, placement.height, placement.side), (1100, 50, 300, 650, "right"))

    def test_falls_back_and_reports_actual_side(self) -> None:
        placement = dock_position(Rect(500, 20, 1200, 820), 360, 700, Rect(0, 0, 1200, 900), "right")
        self.assertEqual((placement.x, placement.side, placement.used_fallback), (140, "left", True))

    def test_preserves_user_height_and_clamps_width_to_available_space(self) -> None:
        placement = dock_position(Rect(349, 70, 1920, 960), 368, 640, Rect(0, 0, 1920, 1040), "left")
        self.assertEqual((placement.x, placement.y, placement.width, placement.height), (0, 70, 349, 640))

    def test_tracks_codex_window_position_changes(self) -> None:
        first = dock_position(Rect(100, 50, 1100, 850), 300, 650, Rect(0, 0, 1920, 1040), "right")
        moved = dock_position(Rect(180, 90, 1180, 890), 300, 650, Rect(0, 0, 1920, 1040), "right")
        self.assertEqual((moved.x - first.x, moved.y - first.y), (80, 40))

    def test_compensates_invisible_frame_on_right(self) -> None:
        x, width = compensate_visible_frame(
            Rect(411, 62, 1609, 940),
            Rect(0, 0, 1920, 1040),
            "right",
            client_width=323,
            visible_width=313,
            visible_left_inset=7,
        )
        self.assertEqual((x, width), (1602, 321))

    def test_compensates_invisible_frame_on_left(self) -> None:
        x, width = compensate_visible_frame(
            Rect(411, 62, 1609, 940),
            Rect(0, 0, 1920, 1040),
            "left",
            client_width=323,
            visible_width=313,
            visible_left_inset=7,
        )
        self.assertEqual((x, width), (91, 323))


if __name__ == "__main__":
    unittest.main()
