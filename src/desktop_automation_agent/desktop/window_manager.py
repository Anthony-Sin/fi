from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
from dataclasses import dataclass, replace
from time import sleep
from typing import Callable

from desktop_automation_agent.contracts import WindowBackend
from desktop_automation_agent.models import WindowContext, WindowOperationResult


logger = logging.getLogger(__name__)

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9


@dataclass(slots=True)
class Win32WindowBackend:
    def enumerate_windows(self) -> list[WindowContext]:
        user32 = ctypes.windll.user32
        windows: list[WindowContext] = []
        foreground = self.get_foreground_window_handle()

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def callback(hwnd: int, lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.strip()
            if not title:
                return True

            context = self.get_window_by_handle(int(hwnd))
            if context is None:
                return True

            context.focused = context.handle == foreground
            windows.append(context)
            return True

        user32.EnumWindows(enum_proc(callback), 0)
        return windows

    def get_foreground_window_handle(self) -> int | None:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    def focus_window(self, handle: int) -> bool:
        user32 = ctypes.windll.user32
        user32.ShowWindow(handle, SW_RESTORE)
        return bool(user32.SetForegroundWindow(handle))

    def move_resize_window(self, handle: int, x: int, y: int, width: int, height: int) -> bool:
        return bool(ctypes.windll.user32.MoveWindow(handle, x, y, width, height, True))

    def show_window(self, handle: int, command: int) -> bool:
        return bool(ctypes.windll.user32.ShowWindow(handle, command))

    def get_window_by_handle(self, handle: int) -> WindowContext | None:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(handle):
            return None

        length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, title_buffer, length + 1)

        rect = ctypes.wintypes.RECT()
        has_rect = bool(user32.GetWindowRect(handle, ctypes.byref(rect)))
        if not has_rect:
            return None

        process_name = self._get_process_name_for_window(handle)
        return WindowContext(
            handle=int(handle),
            title=title_buffer.value,
            process_name=process_name,
            position=(rect.left, rect.top),
            size=(rect.right - rect.left, rect.bottom - rect.top),
            focused=int(handle) == (self.get_foreground_window_handle() or -1),
            minimized=bool(user32.IsIconic(handle)),
            maximized=bool(user32.IsZoomed(handle)),
        )

    def _get_process_name_for_window(self, handle: int) -> str | None:
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except AttributeError:
            return None
        process_id = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if not process_id.value:
            return None

        process_handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not process_handle:
            return None

        try:
            buffer_length = ctypes.wintypes.DWORD(260)
            buffer = ctypes.create_unicode_buffer(buffer_length.value)
            if kernel32.QueryFullProcessImageNameW(process_handle, 0, buffer, ctypes.byref(buffer_length)):
                return os.path.basename(buffer.value)
        finally:
            kernel32.CloseHandle(process_handle)
        return None


class DesktopWindowManager:
    def __init__(
        self,
        backend: WindowBackend | None = None,
        timeout_seconds: float = 3.0,
        retry_count: int = 10,
        sleep_fn: Callable[[float], None] = sleep,
    ):
        self._backend = backend or Win32WindowBackend()
        self._timeout_seconds = timeout_seconds
        self._retry_count = retry_count
        self._sleep_fn = sleep_fn

    def list_windows(self) -> list[WindowContext]:
        return self._backend.enumerate_windows()

    def focus_window(self, title: str | None = None, process_name: str | None = None) -> WindowOperationResult:
        target = self._find_window(title=title, process_name=process_name)
        if target is None:
            return WindowOperationResult(succeeded=False, reason="No matching window found.")

        if not self._backend.focus_window(target.handle):
            return WindowOperationResult(succeeded=False, reason="Failed to focus the target window.")

        return self._poll_for_window(
            lambda: self._backend.get_window_by_handle(target.handle),
            lambda window: window is not None and window.focused,
            failure_reason="Window did not become focused within the configured timeout.",
        )

    def move_resize_window(
        self,
        handle: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> WindowOperationResult:
        if not self._backend.move_resize_window(handle, x, y, width, height):
            return WindowOperationResult(succeeded=False, reason="Failed to move or resize the target window.")

        return self._poll_for_window(
            lambda: self._backend.get_window_by_handle(handle),
            lambda window: window is not None and window.position == (x, y) and window.size == (width, height),
            failure_reason="Window did not reach the requested position and size within the configured timeout.",
        )

    def minimize_window(self, handle: int) -> WindowOperationResult:
        if not self._backend.show_window(handle, SW_MINIMIZE):
            return WindowOperationResult(succeeded=False, reason="Failed to minimize the target window.")

        return self._poll_for_window(
            lambda: self._backend.get_window_by_handle(handle),
            lambda window: window is not None and window.minimized,
            failure_reason="Window did not minimize within the configured timeout.",
        )

    def maximize_window(self, handle: int) -> WindowOperationResult:
        if not self._backend.show_window(handle, SW_MAXIMIZE):
            return WindowOperationResult(succeeded=False, reason="Failed to maximize the target window.")

        return self._poll_for_window(
            lambda: self._backend.get_window_by_handle(handle),
            lambda window: window is not None and window.maximized,
            failure_reason="Window did not maximize within the configured timeout.",
        )

    def wait_for_new_window(self) -> WindowOperationResult:
        existing_handles = {window.handle for window in self._backend.enumerate_windows()}
        attempts = self._retry_count if self._retry_count > 0 else 1
        interval = self._poll_interval()

        for attempt in range(attempts):
            current_windows = self._backend.enumerate_windows()
            for window in current_windows:
                if window.handle not in existing_handles:
                    return WindowOperationResult(
                        succeeded=True,
                        window=self._snapshot_window(window),
                    )

            if attempt < attempts - 1:
                self._sleep_fn(interval)

        return WindowOperationResult(
            succeeded=False,
            reason="No new window appeared within the configured timeout.",
        )

    def _find_window(self, title: str | None, process_name: str | None) -> WindowContext | None:
        if not title and not process_name:
            return None
        normalized_title = title.casefold() if title else None
        normalized_process = process_name.casefold() if process_name else None

        for window in self._backend.enumerate_windows():
            if normalized_title and normalized_title not in window.title.casefold():
                continue
            if normalized_process and (window.process_name or "").casefold() != normalized_process:
                continue
            return window
        return None

    def _poll_for_window(
        self,
        getter: Callable[[], WindowContext | None],
        predicate: Callable[[WindowContext | None], bool],
        failure_reason: str,
    ) -> WindowOperationResult:
        attempts = self._retry_count if self._retry_count > 0 else 1
        interval = self._poll_interval()
        latest: WindowContext | None = None

        for attempt in range(attempts):
            latest = getter()
            if predicate(latest):
                return WindowOperationResult(
                    succeeded=True,
                    window=self._snapshot_window(latest),
                )
            if attempt < attempts - 1:
                self._sleep_fn(interval)

        return WindowOperationResult(
            succeeded=False,
            window=self._snapshot_window(latest),
            reason=failure_reason,
        )

    def _poll_interval(self) -> float:
        if self._retry_count <= 0:
            return self._timeout_seconds
        return self._timeout_seconds / self._retry_count

    def _snapshot_window(self, window: WindowContext | None) -> WindowContext | None:
        if window is None:
            return None
        return replace(window)
