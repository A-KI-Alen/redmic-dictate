from __future__ import annotations

import os
from pathlib import Path

from .paths import app_dir


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstance:
    def __init__(self, name: str = "redmic_dictate.lock"):
        self.path = app_dir() / name
        self._handle = None
        self._mutex = None

    def __enter__(self) -> "SingleInstance":
        if os.name == "nt":
            self._lock_windows()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+b")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if os.name == "nt" and self._mutex is not None:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.ReleaseMutex(self._mutex)
            kernel32.CloseHandle(self._mutex)
            self._mutex = None
            return

        if self._handle is None:
            return
        self._handle.close()
        self._handle = None

    def _lock_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        mutex = kernel32.CreateMutexW(None, True, "Local\\RedMicDictateSingleInstance")
        if not mutex:
            raise AlreadyRunningError("Could not create RedMic Dictate instance mutex.")
        already_exists = ctypes.get_last_error() == 183
        if already_exists:
            kernel32.CloseHandle(mutex)
            raise AlreadyRunningError("RedMic Dictate is already running.")
        self._mutex = mutex
