from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk

from .data import TaskRepository
from .models import (
    CodexTask,
    DisplayState,
    apply_viewed_task_versions,
    group_tasks,
    normalize_viewed_task_versions,
    short_time_label,
)
from .usage import UsageWindow, fetch_usage
from .win32 import (
    compensate_visible_frame,
    dock_position,
    enable_per_monitor_dpi,
    find_codex_window,
    is_codex_or_self_foreground,
    is_left_mouse_down,
    open_task,
    set_topmost,
    top_level_hwnd,
    visible_window_bounds,
    window_rect,
    work_area,
)


APP_NAME = "Codex 最近任务"
PANEL_WIDTH = 368
PANEL_HEIGHT = 720
MIN_WIDTH = 280
MIN_HEIGHT = 460
RECENT_DAY_OPTIONS = (2, 7, 30)

COLORS = {
    "window": "#111315",
    "surface": "#181A1D",
    "card": "#1D2024",
    "nested": "#16181B",
    "border": "#2A2E33",
    "text": "#F1F3F5",
    "muted": "#9AA1AA",
    "subtle": "#6E7681",
    "accent": "#5B8DEF",
    "running": "#5B9CFF",
    "action": "#F59E42",
    "review": "#43B581",
    "danger": "#E05D68",
}


class SettingsStore:
    def __init__(self) -> None:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CodexRecentTasksSidebar"
        self.path = base / "settings.json"

    def load(self) -> dict[str, object]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, value: dict[str, object]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass


class ScrollFrame(tk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=COLORS["window"])
        self.canvas = tk.Canvas(
            self,
            bg=COLORS["window"],
            highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = tk.Frame(self.canvas, bg=COLORS["window"])
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

    def _sync_scroll_region(self, _event: tk.Event[tk.Misc]) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _wheel(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class SidebarApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.mode = str(self.settings.get("mode", "docked"))
        self.side = str(self.settings.get("side", "right"))
        if self.mode not in {"docked", "pinned"}:
            self.mode = "docked"
        if self.side not in {"left", "right"}:
            self.side = "right"
        try:
            self.recent_days = int(self.settings.get("recent_days", 7))
        except (TypeError, ValueError):
            self.recent_days = 7
        if self.recent_days not in RECENT_DAY_OPTIONS:
            self.recent_days = 7
        self.tasks: list[CodexTask] = []
        self.repository: TaskRepository | None = None
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="codex-sidebar")
        self.full_future: Future[list[CodexTask]] | None = None
        self.runtime_future: Future[list[CodexTask]] | None = None
        self.usage_future: Future[list[UsageWindow]] | None = None
        self.last_usage: list[UsageWindow] = []
        self.search_job: str | None = None
        self.geometry_job: str | None = None
        self.is_applying_dock = False
        self.viewed_task_versions = normalize_viewed_task_versions(
            self.settings.get("viewed_task_versions")
        )
        self.dock_topmost: bool | None = None
        self.closing = False
        self.status_text = tk.StringVar(value="正在读取 Codex 任务…")
        self.summary_text = tk.StringVar(value=f"最近 {self.recent_days} 天")
        self.usage_text = tk.StringVar(value="正在读取剩余用量…")
        self.search_text = tk.StringVar(value="搜索任务")
        self.search_placeholder = True

        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self._refresh_mode_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Configure>", self._on_configure)
        self.root.after(50, self.refresh_full)
        self.root.after(100, self.refresh_usage)
        self.root.after(250, self._dock_tick)
        self.root.after(5_000, self._runtime_tick)
        self.root.after(30_000, self._full_tick)
        self.root.after(60_000, self._usage_tick)

    def _configure_window(self) -> None:
        self.root.title(APP_NAME)
        self.root.configure(bg=COLORS["window"])
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "AppIcon.ico"
        if icon_path.is_file():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.maxsize(960, 1400)
        if self.mode == "docked":
            width, height = self._docked_size()
            geometry = f"{width}x{height}+24+24"
        else:
            geometry = str(self.settings.get("pinned_geometry", f"{PANEL_WIDTH}x{PANEL_HEIGHT}+24+24"))
        self.root.geometry(geometry)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Sidebar.Vertical.TScrollbar",
            background=COLORS["border"],
            troughcolor=COLORS["window"],
            bordercolor=COLORS["window"],
            arrowcolor=COLORS["muted"],
            width=10,
        )
        style.configure("TScrollbar", background=COLORS["border"], troughcolor=COLORS["window"], width=10)
        style.configure("Sidebar.TSizegrip", background=COLORS["surface"])

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=COLORS["surface"], padx=16, pady=12)
        header.pack(fill="x")

        title_row = tk.Frame(header, bg=COLORS["surface"])
        title_row.pack(fill="x")
        title_block = tk.Frame(title_row, bg=COLORS["surface"])
        title_block.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_block,
            text=APP_NAME,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            title_block,
            textvariable=self.summary_text,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        self.refresh_button = self._button(title_row, "刷新", self.refresh_all, compact=True)
        self.refresh_button.pack(side="right")

        usage = tk.Frame(header, bg=COLORS["nested"], padx=10, pady=7, highlightthickness=1, highlightbackground=COLORS["border"])
        usage.pack(fill="x", pady=(12, 8))
        tk.Label(usage, text="剩余用量", bg=COLORS["nested"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        tk.Label(usage, textvariable=self.usage_text, bg=COLORS["nested"], fg=COLORS["text"], font=("Microsoft YaHei UI", 8, "bold")).pack(side="right")

        mode_row = tk.Frame(header, bg=COLORS["surface"])
        mode_row.pack(fill="x")
        self.docked_button = self._button(mode_row, "吸附 Codex", lambda: self.set_mode("docked"))
        self.docked_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.pinned_button = self._button(mode_row, "单独置顶", lambda: self.set_mode("pinned"))
        self.pinned_button.pack(side="left", fill="x", expand=True, padx=(4, 0))

        side_row = tk.Frame(header, bg=COLORS["surface"])
        side_row.pack(fill="x", pady=(8, 0))
        tk.Label(side_row, text="吸附位置", bg=COLORS["surface"], fg=COLORS["subtle"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.left_button = self._button(side_row, "左侧", lambda: self.set_side("left"), small=True)
        self.left_button.pack(side="left", padx=(12, 4))
        self.right_button = self._button(side_row, "右侧", lambda: self.set_side("right"), small=True)
        self.right_button.pack(side="left", padx=4)
        self.side_hint = tk.Label(side_row, text="", bg=COLORS["surface"], fg=COLORS["subtle"], font=("Microsoft YaHei UI", 8))
        self.side_hint.pack(side="right")

        range_row = tk.Frame(header, bg=COLORS["surface"])
        range_row.pack(fill="x", pady=(8, 0))
        tk.Label(range_row, text="查看范围", bg=COLORS["surface"], fg=COLORS["subtle"], font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.range_buttons: dict[int, tk.Button] = {}
        for index, days in enumerate(RECENT_DAY_OPTIONS):
            button = self._button(range_row, f"{days}天", lambda value=days: self.set_recent_days(value), small=True)
            button.pack(side="left", padx=(12 if index == 0 else 4, 0))
            self.range_buttons[days] = button
        self._refresh_range_controls()

        search_wrap = tk.Frame(self.root, bg=COLORS["window"], padx=16, pady=12)
        search_wrap.pack(fill="x")
        self.search_entry = tk.Entry(
            search_wrap,
            textvariable=self.search_text,
            bg=COLORS["nested"],
            fg=COLORS["subtle"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=("Microsoft YaHei UI", 9),
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )
        self.search_entry.pack(fill="x", ipady=7)
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        self.search_text.trace_add("write", self._search_changed)

        separator = tk.Frame(self.root, bg=COLORS["border"], height=1)
        separator.pack(fill="x")
        self.scroller = ScrollFrame(self.root)
        self.scroller.pack(fill="both", expand=True)
        footer = tk.Frame(self.root, bg=COLORS["surface"], padx=16, pady=8)
        footer.pack(fill="x")
        tk.Label(
            footer,
            textvariable=self.status_text,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        resize_grip = ttk.Sizegrip(footer, cursor="size_nw_se", style="Sidebar.TSizegrip")
        resize_grip.pack(side="right")

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: object,
        *,
        compact: bool = False,
        small: bool = False,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            activebackground=COLORS["border"],
            activeforeground=COLORS["text"],
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=8 if compact or small else 12,
            pady=4 if compact or small else 6,
            font=("Microsoft YaHei UI", 8 if small else 9, "bold"),
        )

    def _search_focus_in(self, _event: tk.Event[tk.Misc]) -> None:
        if self.search_placeholder:
            self.search_placeholder = False
            self.search_text.set("")
            self.search_entry.configure(fg=COLORS["text"])

    def _search_focus_out(self, _event: tk.Event[tk.Misc]) -> None:
        if not self.search_text.get().strip():
            self.search_placeholder = True
            self.search_text.set("搜索任务")
            self.search_entry.configure(fg=COLORS["subtle"])

    def _search_changed(self, *_args: object) -> None:
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(120, self.render_tasks)

    def _query(self) -> str:
        return "" if self.search_placeholder else self.search_text.get()

    def refresh_all(self) -> None:
        self.refresh_full()
        self.refresh_usage()

    def refresh_full(self, silent: bool = False) -> None:
        if self.closing or (self.full_future and not self.full_future.done()):
            return
        if not silent:
            self.status_text.set("正在刷新任务…")
            self.refresh_button.configure(state="disabled")

        def load() -> list[CodexTask]:
            if self.repository is None:
                self.repository = TaskRepository()
            return self.repository.load(recent_days=self.recent_days)

        self.full_future = self.executor.submit(load)
        self.full_future.add_done_callback(
            lambda future: self.root.after(0, self._finish_full, future, silent)
        )

    def _finish_full(self, future: Future[list[CodexTask]], silent: bool = False) -> None:
        if self.closing:
            return
        if not silent:
            self.refresh_button.configure(state="normal")
        try:
            loaded = future.result()
            loaded = apply_viewed_task_versions(loaded, self.viewed_task_versions)
            if loaded != self.tasks:
                self.tasks = loaded
                self.render_tasks(preserve_scroll=bool(self.tasks))
            if not silent:
                self.status_text.set(f"已刷新 · 任务数据只读 · {self._mode_status()}")
        except Exception as error:
            self.status_text.set(str(error))
            self._render_message(str(error), error=True)

    def refresh_runtime(self) -> None:
        if self.closing or not self.repository or not self.tasks or (self.runtime_future and not self.runtime_future.done()):
            return
        snapshot = list(self.tasks)
        self.runtime_future = self.executor.submit(self.repository.refresh_runtime, snapshot)
        self.runtime_future.add_done_callback(lambda future: self.root.after(0, self._finish_runtime, future))

    def _finish_runtime(self, future: Future[list[CodexTask]]) -> None:
        if self.closing:
            return
        try:
            updated = future.result()
            if updated != self.tasks:
                self.tasks = updated
                self.render_tasks(preserve_scroll=True)
        except Exception:
            pass

    def refresh_usage(self) -> None:
        if self.closing or (self.usage_future and not self.usage_future.done()):
            return
        self.usage_future = self.executor.submit(fetch_usage)
        self.usage_future.add_done_callback(lambda future: self.root.after(0, self._finish_usage, future))

    def _finish_usage(self, future: Future[list[UsageWindow]]) -> None:
        if self.closing:
            return
        try:
            self.last_usage = future.result()
            self.usage_text.set("  ·  ".join(f"{item.label} {item.remaining_percent}%" for item in self.last_usage))
        except Exception:
            if self.last_usage:
                self.usage_text.set("  ·  ".join(f"{item.label} {item.remaining_percent}%*" for item in self.last_usage))
            else:
                self.usage_text.set("暂时不可用")

    def render_tasks(self, preserve_scroll: bool = False) -> None:
        if self.closing:
            return
        scroll = self.scroller.canvas.yview()[0] if preserve_scroll else 0.0
        groups = group_tasks(self.tasks, self._query())
        count = sum(len(group.tasks) for group in groups)
        self.summary_text.set(f"{len(groups)} 个文件夹 · {count} 个任务 · 最近 {self.recent_days} 天")
        self.scroller.clear()
        if not groups:
            self._render_message("没有匹配的最近任务")
            return
        for index, group in enumerate(groups):
            card = tk.Frame(
                self.scroller.content,
                bg=COLORS["card"],
                padx=0,
                pady=0,
                highlightthickness=1,
                highlightbackground=COLORS["border"],
            )
            card.pack(fill="x", padx=12, pady=(12 if index == 0 else 0, 12))
            heading = tk.Frame(card, bg=COLORS["card"], padx=12, pady=10)
            heading.pack(fill="x")
            tk.Label(heading, text="●", bg=COLORS["card"], fg=COLORS["accent"], font=("Segoe UI Symbol", 8)).pack(side="left")
            tk.Label(heading, text=self._truncate(group.name, 28), bg=COLORS["card"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold"), anchor="w").pack(side="left", padx=(8, 6), fill="x", expand=True)
            tk.Label(heading, text=f"{len(group.tasks)} 条", bg=COLORS["nested"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8), padx=6, pady=2).pack(side="right")
            for task_index, task in enumerate(group.tasks):
                if task_index:
                    tk.Frame(card, bg=COLORS["border"], height=1).pack(fill="x", padx=(36, 0))
                self._task_row(card, task)
        self.root.after_idle(lambda: self.scroller.canvas.yview_moveto(scroll))

    def _task_row(self, parent: tk.Widget, task: CodexTask) -> None:
        row = tk.Frame(parent, bg=COLORS["card"], padx=12, pady=9, cursor="hand2")
        row.pack(fill="x")
        icon = tk.Label(row, text="▣", bg=COLORS["card"], fg=COLORS["subtle"], font=("Segoe UI Symbol", 9), cursor="hand2")
        icon.pack(side="left")
        title = tk.Label(
            row,
            text=self._truncate(task.title, 31),
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            cursor="hand2",
        )
        title.pack(side="left", fill="x", expand=True, padx=(9, 6))
        state = task.display_state
        time_text = short_time_label(task.updated_ms)
        if state == DisplayState.IDLE:
            status = tk.Label(row, text=time_text, bg=COLORS["nested"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold"), padx=7, pady=3, cursor="hand2")
            status.pack(side="right")
        else:
            color = {DisplayState.RUNNING: COLORS["running"], DisplayState.NEEDS_ACTION: COLORS["action"], DisplayState.NEEDS_REVIEW: COLORS["review"]}[state]
            chip = tk.Frame(row, bg=COLORS["nested"], cursor="hand2")
            chip.pack(side="right")
            tk.Label(chip, text=time_text, bg=COLORS["nested"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8), padx=5, pady=3, cursor="hand2").pack(side="left")
            tk.Label(chip, text=f"● {state.badge}", bg=COLORS["nested"], fg=color, font=("Microsoft YaHei UI", 8, "bold"), padx=6, pady=3, cursor="hand2").pack(side="left")
        widgets = [row, icon, title, *row.winfo_children()]
        for widget in widgets:
            widget.bind("<Button-1>", lambda _event, selected=task: self._open_task(selected))
            widget.bind("<Enter>", lambda _event, target=row: self._set_row_bg(target, "#25282D"))
            widget.bind("<Leave>", lambda _event, target=row: self._set_row_bg(target, COLORS["card"]))

    @staticmethod
    def _set_row_bg(row: tk.Frame, color: str) -> None:
        row.configure(bg=color)
        for child in row.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)) and child.cget("bg") in {COLORS["card"], "#25282D"}:
                child.configure(bg=color)

    def _open_task(self, task: CodexTask) -> None:
        try:
            open_task(task.id)
            self.viewed_task_versions[task.id] = max(
                task.updated_ms, self.viewed_task_versions.get(task.id, 0)
            )
            self.viewed_task_versions = normalize_viewed_task_versions(self.viewed_task_versions)
            self.settings["viewed_task_versions"] = self.viewed_task_versions
            self._save_settings()
            self.tasks = [
                item.with_updates(unread=False, current=True)
                if item.id == task.id
                else item.with_updates(current=False)
                for item in self.tasks
            ]
            self.render_tasks(preserve_scroll=True)
            self.status_text.set("已打开对应 Codex 任务")
            self.root.after(1_500, self.refresh_full)
        except OSError as error:
            self.status_text.set(f"无法打开任务：{error}")

    def _render_message(self, message: str, error: bool = False) -> None:
        self.scroller.clear()
        tk.Label(
            self.scroller.content,
            text=message,
            bg=COLORS["window"],
            fg=COLORS["danger"] if error else COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
            wraplength=300,
            justify="left",
            padx=24,
            pady=32,
        ).pack(fill="x")

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        value = value.replace("\r", " ").replace("\n", " ").strip()
        return value if len(value) <= limit else value[: limit - 1] + "…"

    def set_mode(self, mode: str) -> None:
        if mode not in {"docked", "pinned"} or mode == self.mode:
            return
        if self.mode == "docked":
            self.settings["docked_size"] = [self.root.winfo_width(), self.root.winfo_height()]
        else:
            self.settings["pinned_geometry"] = self.root.geometry()
        self.mode = mode
        if mode == "pinned":
            pinned_geometry = str(self.settings.get("pinned_geometry", self.root.geometry()))
            self.root.geometry(pinned_geometry)
            self.root.attributes("-topmost", True)
        else:
            width, height = self._docked_size()
            self.root.geometry(f"{width}x{height}{self.root.winfo_x():+d}{self.root.winfo_y():+d}")
            self._apply_dock()
        self._refresh_mode_controls()
        self._save_settings()
        self.status_text.set(self._mode_status())

    def set_side(self, side: str) -> None:
        if side not in {"left", "right"}:
            return
        self.side = side
        self._refresh_mode_controls()
        self._save_settings()
        if self.mode == "docked":
            self._apply_dock()

    def set_recent_days(self, days: int) -> None:
        if days not in RECENT_DAY_OPTIONS or days == self.recent_days:
            return
        self.recent_days = days
        self.settings["recent_days"] = days
        self._save_settings()
        self._refresh_range_controls()
        self.summary_text.set(f"正在读取最近 {days} 天…")
        if self.full_future and not self.full_future.done():
            self.root.after(250, self.refresh_full)
        else:
            self.refresh_full()

    def _refresh_range_controls(self) -> None:
        for days, button in self.range_buttons.items():
            self._select_button(button, days == self.recent_days)

    def _refresh_mode_controls(self) -> None:
        self._select_button(self.docked_button, self.mode == "docked")
        self._select_button(self.pinned_button, self.mode == "pinned")
        self._select_button(self.left_button, self.mode == "docked" and self.side == "left")
        self._select_button(self.right_button, self.mode == "docked" and self.side == "right")
        state = "normal" if self.mode == "docked" else "disabled"
        self.left_button.configure(state=state)
        self.right_button.configure(state=state)
        self.side_hint.configure(text="跟随 Codex" if self.mode == "docked" else "可自由移动")

    @staticmethod
    def _select_button(button: tk.Button, selected: bool) -> None:
        button.configure(
            bg=COLORS["accent"] if selected else COLORS["card"],
            fg="#FFFFFF" if selected else COLORS["muted"],
            activebackground=COLORS["accent"] if selected else COLORS["border"],
        )

    def _apply_dock(self) -> None:
        if is_left_mouse_down():
            return
        found = find_codex_window()
        if not found:
            self.status_text.set("等待 Codex 窗口")
            return
        codex_hwnd, rect = found
        own_hwnd = top_level_hwnd(self.root.winfo_id())
        area = work_area(codex_hwnd)
        self.root.update_idletasks()
        placement = dock_position(
            rect,
            self.root.winfo_width(),
            self.root.winfo_height(),
            area,
            self.side,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
        )
        if placement.side != self.side:
            self.side = placement.side
            self._refresh_mode_controls()
            self._save_settings()
        visible = visible_window_bounds(own_hwnd)
        outer = window_rect(own_hwnd)
        adjusted_width = placement.width
        outer_x = placement.x
        if visible is not None and outer is not None:
            visible_delta = visible.width - self.root.winfo_width()
            outer_x, adjusted_width = compensate_visible_frame(
                rect,
                area,
                placement.side,
                placement.width,
                placement.width + visible_delta,
                visible.left - outer.left,
                min_width=MIN_WIDTH,
            )
        geometry = (
            f"{adjusted_width}x{placement.height}"
            f"{outer_x:+d}{placement.y:+d}"
        )
        self.is_applying_dock = True
        try:
            if self.root.geometry() != geometry:
                self.root.geometry(geometry)
                self.root.update_idletasks()
        finally:
            self.is_applying_dock = False
        front = is_codex_or_self_foreground(own_hwnd, codex_hwnd)
        if front != self.dock_topmost:
            set_topmost(own_hwnd, front)
            self.dock_topmost = front
        fallback_note = " · 目标侧空间不足，已自动换侧" if placement.used_fallback else ""
        status = f"已吸附{self.side == 'left' and '左侧' or '右侧'} · {'跟随 Codex 前置' if front else '已随 Codex 后置'}{fallback_note}"
        if self.status_text.get() != status:
            self.status_text.set(status)

    def _dock_tick(self) -> None:
        if self.closing:
            return
        if self.mode == "docked":
            self._apply_dock()
        else:
            self.root.attributes("-topmost", True)
        self.root.after(400, self._dock_tick)

    def _runtime_tick(self) -> None:
        if self.closing:
            return
        self.refresh_runtime()
        self.root.after(5_000, self._runtime_tick)

    def _full_tick(self) -> None:
        if self.closing:
            return
        self.refresh_full(silent=True)
        self.root.after(30_000, self._full_tick)

    def _usage_tick(self) -> None:
        if self.closing:
            return
        self.refresh_usage()
        self.root.after(60_000, self._usage_tick)

    def _mode_status(self) -> str:
        return f"吸附{self.side == 'left' and '左侧' or '右侧'}" if self.mode == "docked" else "单独置顶"

    def _on_configure(self, _event: tk.Event[tk.Misc]) -> None:
        if self.closing or self.is_applying_dock:
            return
        if self.geometry_job:
            self.root.after_cancel(self.geometry_job)
        self.geometry_job = self.root.after(500, self._save_window_geometry)

    def _save_window_geometry(self) -> None:
        if self.mode == "pinned":
            self.settings["pinned_geometry"] = self.root.geometry()
        else:
            self.settings["docked_size"] = [self.root.winfo_width(), self.root.winfo_height()]
        self._save_settings()

    def _docked_size(self) -> tuple[int, int]:
        raw = self.settings.get("docked_size")
        if isinstance(raw, list) and len(raw) == 2 and all(isinstance(value, int) for value in raw):
            return max(MIN_WIDTH, min(960, raw[0])), max(MIN_HEIGHT, min(1400, raw[1]))
        return PANEL_WIDTH, PANEL_HEIGHT

    def _save_settings(self) -> None:
        self.settings["mode"] = self.mode
        self.settings["side"] = self.side
        self.settings_store.save(self.settings)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        if self.mode == "pinned":
            self.settings["pinned_geometry"] = self.root.geometry()
        else:
            self.settings["docked_size"] = [self.root.winfo_width(), self.root.winfo_height()]
        self._save_settings()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def run() -> None:
    enable_per_monitor_dpi()
    root = tk.Tk()
    SidebarApp(root)
    root.mainloop()
