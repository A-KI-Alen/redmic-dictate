from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .paths import logs_dir, overlay_status_path


LOG = logging.getLogger(__name__)
RED = "#e11932"
DARK_RED = "#7d0718"
HUD_BG = "#15181d"
WHITE = "#ffffff"
TRANSPARENT = "#ff00ff"


@dataclass(slots=True)
class Point:
    x: int
    y: int


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(logs_dir() / "overlay.log", encoding="utf-8")],
    )
    config = AppConfig.load_or_create()
    run_overlay(config)


def run_overlay(config: AppConfig) -> None:
    import tkinter as tk

    size = max(54, int(config.overlay_size))
    hud_width = 560
    hud_height = 112

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()

    cursor = create_cursor_overlay(root, size)
    hud = create_hud_overlay(root, hud_width, hud_height)
    taskbars = create_taskbar_overlays(root, config)

    rects = monitor_rects(root)
    angle = 0

    def refresh() -> None:
        nonlocal angle
        status = read_status(config)
        mode = status.get("mode", "recording")
        if mode == "hidden":
            root.quit()
            return

        point = cursor_position(root)
        monitor = monitor_for_point(rects, point)
        left, top, right, bottom = monitor

        processing = mode == "processing"
        draw_cursor_ring(cursor.canvas, size, processing, angle)
        draw_hud(hud.canvas, hud_width, hud_height, status, processing, angle)

        cursor_x, cursor_y = cursor_indicator_position(monitor, point, size)
        move_window(cursor, cursor_x, cursor_y, size, size)
        move_window(hud, left + 16, top + 16, hud_width, hud_height)

        for taskbar in taskbars:
            taskbar.configure(bg=RED if not processing else DARK_RED)
            taskbar.lift()
        hud.lift()
        cursor.lift()

        angle = (angle + (18 if processing else 0)) % 360
        root.after(45 if processing else 90, refresh)

    root.after(0, refresh)
    root.mainloop()


def create_cursor_overlay(root, size: int):
    import tkinter as tk

    window = tk.Toplevel(root)
    window.withdraw()
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.configure(bg=TRANSPARENT)
    try:
        window.attributes("-transparentcolor", TRANSPARENT)
    except Exception:
        window.attributes("-alpha", 0.92)
    canvas = tk.Canvas(window, width=size, height=size, bg=TRANSPARENT, highlightthickness=0, bd=0)
    canvas.pack()
    window.canvas = canvas
    window.update_idletasks()
    make_click_through(window)
    move_window(window, -size * 2, -size * 2, size, size)
    return window


def create_hud_overlay(root, width: int, height: int):
    import tkinter as tk

    window = tk.Toplevel(root)
    window.withdraw()
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.attributes("-alpha", 0.92)
    window.configure(bg=HUD_BG)
    canvas = tk.Canvas(window, width=width, height=height, bg=HUD_BG, highlightthickness=0, bd=0)
    canvas.pack()
    window.canvas = canvas
    window.update_idletasks()
    make_click_through(window)
    move_window(window, -width * 2, -height * 2, width, height)
    return window


def draw_cursor_ring(canvas, size: int, processing: bool, angle: int) -> None:
    canvas.delete("all")
    pad = max(6, size // 9)
    line = max(5, size // 11)
    box = (pad, pad, size - pad, size - pad)
    if processing:
        canvas.create_oval(*box, outline="#4d0610", width=line)
        canvas.create_arc(*box, start=angle, extent=125, style="arc", outline=RED, width=line)
        canvas.create_arc(*box, start=angle + 185, extent=55, style="arc", outline=RED, width=line)
    else:
        canvas.create_oval(*box, outline=RED, width=line)
        dot = max(7, size // 8)
        canvas.create_oval(size // 2 - dot, size // 2 - dot, size // 2 + dot, size // 2 + dot, fill=RED, outline=RED)


def draw_hud(canvas, width: int, height: int, status: dict[str, Any], processing: bool, angle: int) -> None:
    canvas.delete("all")
    canvas.create_rectangle(0, 0, width, height, fill=HUD_BG, outline=RED, width=2)
    canvas.create_rectangle(0, 0, 78, height, fill=RED, outline=RED)

    canvas.create_oval(21, 18, 57, 54, fill=RED if not processing else HUD_BG, outline=WHITE, width=4)
    if processing:
        canvas.create_arc(21, 18, 57, 54, start=angle, extent=115, style="arc", outline=WHITE, width=4)
    else:
        canvas.create_oval(32, 29, 46, 43, fill=WHITE, outline=WHITE)

    headline = "VERARBEITE TEXT" if processing else "AUFNAHME"
    subline = status.get("message") or ("Bitte warten" if processing else "Mikrofon aktiv")
    canvas.create_text(94, 20, text=headline, fill=WHITE, anchor="nw", font=("Segoe UI", 15, "bold"))
    canvas.create_text(94, 45, text=subline, fill="#f5c7cd", anchor="nw", font=("Segoe UI", 10))

    stop = _hotkey_label(status.get("stop_hotkey", "space"))
    cancel = _hotkey_label(status.get("cancel_hotkey", "esc"))
    hard_abort = _hotkey_label(status.get("hard_abort_hotkey", "space+esc"))
    live = _hotkey_label(status.get("live_hotkey", "alt+y"))
    clipboard = _hotkey_label(status.get("clipboard_hotkey", "alt+shift+y"))
    canvas.create_text(
        94,
        72,
        text=f"Stop: {stop}    Abbruch: {cancel}",
        fill=WHITE,
        anchor="nw",
        font=("Segoe UI", 10, "bold"),
    )
    canvas.create_text(
        94,
        91,
        text=f"Hart: {hard_abort}    Live: {live}    Zwischenablage: {clipboard}",
        fill="#d9dde5",
        anchor="nw",
        font=("Segoe UI", 9),
    )


def create_taskbar_overlays(root, config: AppConfig) -> list:
    if not config.taskbar_recording_overlay:
        return []

    import tkinter as tk

    overlays = []
    for left, top, right, bottom in monitor_rects(root):
        height = max(8, int(config.taskbar_overlay_height))
        window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", max(0.15, min(0.95, float(config.taskbar_overlay_alpha))))
        window.configure(bg=RED)
        window.update_idletasks()
        make_click_through(window)
        move_window(window, left, bottom - height, right - left, height)
        overlays.append(window)
    return overlays


def read_status(config: AppConfig) -> dict[str, Any]:
    defaults = {
        "mode": "recording",
        "message": "Mikrofon aktiv",
        "live_hotkey": config.live_hotkey,
        "clipboard_hotkey": config.clipboard_hotkey,
        "stop_hotkey": config.stop_hotkey,
        "cancel_hotkey": config.cancel_hotkey,
        "hard_abort_hotkey": config.hard_abort_hotkey,
    }
    try:
        status = json.loads(overlay_status_path().read_text(encoding="utf-8"))
        if isinstance(status, dict):
            return {**defaults, **status}
    except Exception:
        LOG.debug("Could not read overlay status", exc_info=True)
    return defaults


def monitor_rects(root) -> list[tuple[int, int, int, int]]:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            rects: list[tuple[int, int, int, int]] = []

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(RECT),
                wintypes.LPARAM,
            )

            def callback(monitor, hdc, rect, data):
                del monitor, hdc, data
                rects.append((rect.contents.left, rect.contents.top, rect.contents.right, rect.contents.bottom))
                return True

            user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(callback), 0)
            if rects:
                return rects
        except Exception:
            LOG.debug("Could not enumerate monitors", exc_info=True)

    return [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


def monitor_for_point(rects: list[tuple[int, int, int, int]], point: Point) -> tuple[int, int, int, int]:
    for rect in rects:
        left, top, right, bottom = rect
        if left <= point.x < right and top <= point.y < bottom:
            return rect
    return rects[0]


def cursor_indicator_position(monitor: tuple[int, int, int, int], point: Point, size: int) -> tuple[int, int]:
    left, top, right, bottom = monitor
    gap = max(14, size // 5)
    x = point.x + gap
    y = point.y + gap
    if x + size > right:
        x = point.x - size - gap
    if y + size > bottom:
        y = point.y - size - gap
    return max(left, x), max(top, y)


def cursor_position(root) -> Point:
    if os.name == "nt":
        try:
            import ctypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            point = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return Point(point.x, point.y)
        except Exception:
            LOG.debug("GetCursorPos failed", exc_info=True)
    return Point(root.winfo_pointerx(), root.winfo_pointery())


def move_window(window, x: int, y: int, width: int, height: int) -> None:
    window.geometry(f"{width}x{height}+{x}+{y}")
    try:
        window.deiconify()
    except Exception:
        LOG.debug("Could not deiconify overlay window", exc_info=True)
    make_click_through(window)

    if os.name == "nt":
        try:
            import ctypes

            hwnd_topmost = -1
            swp_noactivate = 0x0010
            swp_showwindow = 0x0040
            ctypes.windll.user32.SetWindowPos(
                window.winfo_id(),
                hwnd_topmost,
                int(x),
                int(y),
                int(width),
                int(height),
                swp_noactivate | swp_showwindow,
            )
            return
        except Exception:
            LOG.debug("SetWindowPos failed", exc_info=True)


def make_click_through(root) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = root.winfo_id()
        ex_style = user32.GetWindowLongW(hwnd, -20)
        ex_style |= 0x00000020
        ex_style |= 0x00000080
        ex_style |= 0x08000000
        user32.SetWindowLongW(hwnd, -20, ex_style)
    except Exception:
        LOG.debug("Could not make recording overlay click-through", exc_info=True)


def _hotkey_label(hotkey: str) -> str:
    parts = []
    for part in str(hotkey).split("+"):
        key = part.strip().lower()
        if key == "space":
            parts.append("Leertaste")
        elif key == "esc":
            parts.append("Esc")
        elif key == "alt":
            parts.append("Alt")
        elif key == "shift":
            parts.append("Shift")
        elif key in {"win", "windows"}:
            parts.append("Windows")
        else:
            parts.append(key.upper() if len(key) == 1 else key.title())
    return "+".join(parts)


if __name__ == "__main__":
    main()
