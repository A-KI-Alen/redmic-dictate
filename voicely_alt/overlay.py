from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass

from .config import AppConfig


LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class _Point:
    x: int
    y: int


class RecordingOverlay:
    def __init__(self, config: AppConfig):
        self.config = config
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def show(self) -> None:
        if not self.config.recording_overlay:
            return
        self._ensure_started()
        self._queue.put("show")

    def hide(self) -> None:
        if self._thread is not None:
            self._queue.put("hide")

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put("stop")

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            LOG.exception("tkinter is unavailable; recording overlay disabled")
            return

        size = max(48, int(self.config.overlay_size))
        transparent = "#ff00ff"
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", transparent)
        except tk.TclError:
            root.attributes("-alpha", 0.88)
        root.configure(bg=transparent)

        canvas = tk.Canvas(root, width=size, height=size, bg=transparent, highlightthickness=0)
        canvas.pack()
        _draw_microphone(canvas, size)

        taskbar = _create_taskbar_overlay(root, self.config)
        root.update_idletasks()
        _make_click_through(root)
        if taskbar is not None:
            taskbar.update_idletasks()
            _make_click_through(taskbar)

        visible = False

        def poll() -> None:
            nonlocal visible
            try:
                while True:
                    command = self._queue.get_nowait()
                    if command == "show":
                        visible = True
                        root.deiconify()
                        if taskbar is not None:
                            _place_taskbar_overlay(taskbar, self.config)
                            taskbar.deiconify()
                    elif command == "hide":
                        visible = False
                        root.withdraw()
                        if taskbar is not None:
                            taskbar.withdraw()
                    elif command == "stop":
                        root.destroy()
                        return
            except queue.Empty:
                pass

            if visible:
                point = _cursor_position(root)
                root.geometry(f"{size}x{size}+{point.x - size // 2}+{point.y - size // 2}")
                root.lift()

            root.after(35, poll)

        root.after(0, poll)
        root.mainloop()


def _draw_microphone(canvas, size: int) -> None:
    pad = max(5, size // 14)
    red = "#e11932"
    dark = "#9f1022"
    white = "#ffffff"

    canvas.create_oval(pad, pad, size - pad, size - pad, fill=red, outline=dark, width=max(2, size // 32))

    mic_w = size * 0.26
    mic_h = size * 0.42
    x1 = (size - mic_w) / 2
    y1 = size * 0.20
    x2 = x1 + mic_w
    y2 = y1 + mic_h
    radius = mic_w / 2

    canvas.create_round_rectangle(x1, y1, x2, y2, radius=radius, fill=white, outline=white)
    canvas.create_line(size * 0.34, size * 0.47, size * 0.34, size * 0.56, fill=white, width=max(3, size // 18))
    canvas.create_line(size * 0.66, size * 0.47, size * 0.66, size * 0.56, fill=white, width=max(3, size // 18))
    canvas.create_arc(
        size * 0.30,
        size * 0.40,
        size * 0.70,
        size * 0.70,
        start=180,
        extent=180,
        style="arc",
        outline=white,
        width=max(3, size // 18),
    )
    canvas.create_line(size * 0.50, size * 0.70, size * 0.50, size * 0.82, fill=white, width=max(3, size // 18))
    canvas.create_line(size * 0.39, size * 0.82, size * 0.61, size * 0.82, fill=white, width=max(3, size // 18))


def _create_taskbar_overlay(root, config: AppConfig):
    if not config.taskbar_recording_overlay:
        return None
    try:
        import tkinter as tk

        window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", max(0.15, min(0.95, float(config.taskbar_overlay_alpha))))
        window.configure(bg="#e11932")
        return window
    except Exception:
        LOG.debug("Could not create taskbar recording overlay", exc_info=True)
        return None


def _place_taskbar_overlay(window, config: AppConfig) -> None:
    height = max(24, int(config.taskbar_overlay_height))
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            total_height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            window.geometry(f"{width}x{height}+{left}+{top + total_height - height}")
            return
        except Exception:
            LOG.debug("Could not read virtual screen metrics", exc_info=True)

    width = window.winfo_screenwidth()
    total_height = window.winfo_screenheight()
    window.geometry(f"{width}x{height}+0+{total_height - height}")


def _cursor_position(root) -> _Point:
    if os.name == "nt":
        try:
            import ctypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            point = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return _Point(point.x, point.y)
        except Exception:
            LOG.debug("GetCursorPos failed", exc_info=True)
    return _Point(root.winfo_pointerx(), root.winfo_pointery())


def _make_click_through(root) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = root.winfo_id()
        ex_style = user32.GetWindowLongW(hwnd, -20)
        ex_style |= 0x00000020  # WS_EX_TRANSPARENT
        ex_style |= 0x00000080  # WS_EX_TOOLWINDOW
        ex_style |= 0x08000000  # WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, -20, ex_style)
    except Exception:
        LOG.debug("Could not make recording overlay click-through", exc_info=True)
