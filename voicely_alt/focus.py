from __future__ import annotations

import logging
import os
from dataclasses import dataclass


LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class FocusTarget:
    hwnd: int = 0

    def restore(self) -> bool:
        if os.name != "nt" or not self.hwnd:
            return False

        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(self.hwnd)
            if not user32.IsWindow(hwnd):
                return False

            sw_restore = 9
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, sw_restore)

            current_thread = kernel32.GetCurrentThreadId()
            foreground = user32.GetForegroundWindow()
            foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)

            attached_foreground = False
            attached_target = False
            try:
                if foreground_thread and foreground_thread != current_thread:
                    attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
                if target_thread and target_thread != current_thread:
                    attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))

                _nudge_foreground_permission(user32)
                user32.BringWindowToTop(hwnd)
                return bool(user32.SetForegroundWindow(hwnd))
            finally:
                if attached_target:
                    user32.AttachThreadInput(current_thread, target_thread, False)
                if attached_foreground:
                    user32.AttachThreadInput(current_thread, foreground_thread, False)
        except Exception:
            LOG.debug("Could not restore foreground window", exc_info=True)
            return False


def capture_focus_target() -> FocusTarget | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        if hwnd:
            return FocusTarget(hwnd=hwnd)
    except Exception:
        LOG.debug("Could not capture foreground window", exc_info=True)
    return None


def _nudge_foreground_permission(user32) -> None:
    try:
        vk_menu = 0x12
        keyeventf_keyup = 0x0002
        user32.keybd_event(vk_menu, 0, 0, 0)
        user32.keybd_event(vk_menu, 0, keyeventf_keyup, 0)
    except Exception:
        LOG.debug("Could not nudge foreground permission", exc_info=True)
