from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .config import AppConfig
from .paths import logs_dir


LOG = logging.getLogger(__name__)


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

    size = max(48, int(config.overlay_size))

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()

    cursor = create_cursor_overlay(root, size)
    cursor.deiconify()
    cursor.update_idletasks()
    make_click_through(cursor)

    taskbars = create_taskbar_overlays(root, config)
    for taskbar in taskbars:
        taskbar.deiconify()
        taskbar.update_idletasks()
        make_click_through(taskbar)

    def follow_cursor() -> None:
        point = cursor_position(root)
        move_window(cursor, point.x - size // 2, point.y - size // 2, size, size)
        for taskbar in taskbars:
            taskbar.lift()
        cursor.lift()
        root.after(35, follow_cursor)

    root.after(0, follow_cursor)
    root.mainloop()


def create_cursor_overlay(root, size: int):
    import tkinter as tk

    window = tk.Toplevel(root)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.attributes("-alpha", 0.92)
    window.configure(bg="#e11932")
    canvas = tk.Canvas(window, width=size, height=size, bg="#e11932", highlightthickness=0)
    canvas.pack()
    draw_cursor_halo(canvas, size)
    return window


def draw_cursor_halo(canvas, size: int) -> None:
    pad = max(4, size // 12)
    red = "#e11932"
    white = "#ffffff"
    line = max(4, size // 11)

    canvas.create_rectangle(0, 0, size, size, fill=red, outline=red)
    canvas.create_oval(pad, pad, size - pad, size - pad, outline=white, width=line)
    canvas.create_line(size / 2, 0, size / 2, size * 0.28, fill=white, width=max(3, size // 18))
    canvas.create_line(size / 2, size * 0.72, size / 2, size, fill=white, width=max(3, size // 18))
    canvas.create_line(0, size / 2, size * 0.28, size / 2, fill=white, width=max(3, size // 18))
    canvas.create_line(size * 0.72, size / 2, size, size / 2, fill=white, width=max(3, size // 18))
    canvas.create_text(size / 2, size / 2, text="REC", fill=white, font=("Segoe UI", max(9, size // 7), "bold"))


def create_taskbar_overlays(root, config: AppConfig) -> list:
    if not config.taskbar_recording_overlay:
        return []

    import tkinter as tk

    overlays = []
    for left, top, right, bottom in monitor_rects(root):
        height = max(8, int(config.taskbar_overlay_height))
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", max(0.15, min(0.95, float(config.taskbar_overlay_alpha))))
        window.configure(bg="#e11932")
        window.geometry(f"{right - left}x{height}+{left}+{bottom - height}")
        overlays.append(window)
    return overlays


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
    window.geometry(f"{width}x{height}+{x}+{y}")


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


if __name__ == "__main__":
    main()
