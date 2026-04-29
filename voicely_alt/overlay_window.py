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
    transparent = "#ff00ff"

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=transparent)
    try:
        root.attributes("-transparentcolor", transparent)
    except tk.TclError:
        root.attributes("-alpha", 0.92)

    canvas = tk.Canvas(root, width=size, height=size, bg=transparent, highlightthickness=0)
    canvas.pack()
    draw_microphone(canvas, size)
    root.update_idletasks()
    make_click_through(root)

    taskbar = create_taskbar_overlay(root, config)
    if taskbar is not None:
        place_taskbar_overlay(taskbar, config)
        taskbar.deiconify()
        taskbar.update_idletasks()
        make_click_through(taskbar)

    def follow_cursor() -> None:
        point = cursor_position(root)
        root.geometry(f"{size}x{size}+{point.x - size // 2}+{point.y - size // 2}")
        root.lift()
        if taskbar is not None:
            taskbar.lift()
        root.after(35, follow_cursor)

    root.after(0, follow_cursor)
    root.mainloop()


def draw_microphone(canvas, size: int) -> None:
    pad = max(5, size // 14)
    red = "#e11932"
    dark = "#9f1022"
    white = "#ffffff"
    line = max(3, size // 18)

    canvas.create_oval(pad, pad, size - pad, size - pad, fill=red, outline=dark, width=max(2, size // 32))

    mic_w = size * 0.26
    mic_h = size * 0.42
    x1 = (size - mic_w) / 2
    y1 = size * 0.20
    x2 = x1 + mic_w
    y2 = y1 + mic_h
    radius = mic_w / 2

    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=white, outline=white)
    canvas.create_oval(x1, y1, x2, y1 + mic_w, fill=white, outline=white)
    canvas.create_oval(x1, y2 - mic_w, x2, y2, fill=white, outline=white)
    canvas.create_arc(
        size * 0.30,
        size * 0.40,
        size * 0.70,
        size * 0.70,
        start=180,
        extent=180,
        style="arc",
        outline=white,
        width=line,
    )
    canvas.create_line(size * 0.50, size * 0.70, size * 0.50, size * 0.82, fill=white, width=line)
    canvas.create_line(size * 0.39, size * 0.82, size * 0.61, size * 0.82, fill=white, width=line)


def create_taskbar_overlay(root, config: AppConfig):
    if not config.taskbar_recording_overlay:
        return None

    import tkinter as tk

    window = tk.Toplevel(root)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.attributes("-alpha", max(0.15, min(0.95, float(config.taskbar_overlay_alpha))))
    window.configure(bg="#e11932")
    return window


def place_taskbar_overlay(window, config: AppConfig) -> None:
    height = max(24, int(config.taskbar_overlay_height))
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)
            top = user32.GetSystemMetrics(77)
            width = user32.GetSystemMetrics(78)
            total_height = user32.GetSystemMetrics(79)
            window.geometry(f"{width}x{height}+{left}+{top + total_height - height}")
            return
        except Exception:
            LOG.debug("Could not read virtual screen metrics", exc_info=True)

    width = window.winfo_screenwidth()
    total_height = window.winfo_screenheight()
    window.geometry(f"{width}x{height}+0+{total_height - height}")


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

