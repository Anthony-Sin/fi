from __future__ import annotations

from desktop_automation_agent._time import utc_now

import ctypes
import ctypes.wintypes
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from desktop_automation_agent.models import (
    DisplayConfigurationChangeResult,
    DisplayConfigurationSnapshot,
    MonitorDescriptor,
    ScreenBounds,
    WindowOperationResult,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PILVirtualDesktopCaptureBackend:
    def capture(self, monitor: MonitorDescriptor | None = None) -> Any | None:
        try:
            from PIL import ImageGrab

            bbox = None if monitor is None else monitor.bounds
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception as e:
            logger.warning(f"PIL capture failed: {e}")
            return None

    def save(self, image: Any, path: str) -> str | None:
        try:
            target = Path(path)
            image.save(target)
            return str(target)
        except Exception as e:
            logger.warning(f"Failed to save image to {path}: {e}")
            return None


@dataclass(slots=True)
class Win32MonitorBackend:
    def enumerate_monitors(self) -> list[MonitorDescriptor]:
        user32 = ctypes.windll.user32
        monitors: list[MonitorDescriptor] = []

        monitor_enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.wintypes.HMONITOR,
            ctypes.wintypes.HDC,
            ctypes.POINTER(ctypes.wintypes.RECT),
            ctypes.wintypes.LPARAM,
        )

        class MonitorInfoEx(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        def callback(handle, _hdc, _rect, _lparam):
            info = MonitorInfoEx()
            info.cbSize = ctypes.sizeof(MonitorInfoEx)
            if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                return 1

            bounds = (
                int(info.rcMonitor.left),
                int(info.rcMonitor.top),
                int(info.rcMonitor.right),
                int(info.rcMonitor.bottom),
            )
            work_area = (
                int(info.rcWork.left),
                int(info.rcWork.top),
                int(info.rcWork.right),
                int(info.rcWork.bottom),
            )
            monitors.append(
                MonitorDescriptor(
                    monitor_id=str(info.szDevice),
                    bounds=bounds,
                    work_area=work_area,
                    resolution=(bounds[2] - bounds[0], bounds[3] - bounds[1]),
                    primary=bool(info.dwFlags & 1),
                    device_name=str(info.szDevice),
                )
            )
            return 1

        user32.EnumDisplayMonitors(0, 0, monitor_enum_proc(callback), 0)
        return sorted(monitors, key=lambda monitor: (not monitor.primary, monitor.bounds[0], monitor.bounds[1]))


@dataclass(slots=True)
class MultiMonitorDisplayHandler:
    window_manager: object
    monitor_backend: object
    capture_backend: object | None = None
    session_snapshot: DisplayConfigurationSnapshot | None = None

    def list_monitors(self) -> list[MonitorDescriptor]:
        return list(self.monitor_backend.enumerate_monitors())

    def get_monitor(self, monitor_id: str | None = None) -> MonitorDescriptor | None:
        monitors = self.list_monitors()
        if not monitors:
            return None
        if monitor_id is None:
            return next((monitor for monitor in monitors if monitor.primary), monitors[0])
        for monitor in monitors:
            if monitor.monitor_id == monitor_id:
                return monitor
        return None

    def get_screen_bounds(self, monitor_id: str | None = None) -> ScreenBounds:
        if monitor_id is not None:
            monitor = self.get_monitor(monitor_id)
            if monitor is None:
                logger.warning(f"Unknown monitor: {monitor_id}")
                return ScreenBounds(width=0, height=0)
            return self._bounds_to_screen_bounds(monitor.bounds)

        monitors = self.list_monitors()
        if not monitors:
            return ScreenBounds(width=0, height=0)
        left = min(monitor.bounds[0] for monitor in monitors)
        top = min(monitor.bounds[1] for monitor in monitors)
        right = max(monitor.bounds[2] for monitor in monitors)
        bottom = max(monitor.bounds[3] for monitor in monitors)
        return ScreenBounds(width=right - left, height=bottom - top, origin_x=left, origin_y=top)

    def get_monitor_bounds(self, monitor_id: str | None = None) -> ScreenBounds:
        return self.get_screen_bounds(monitor_id)

    def get_monitor_for_point(self, point: tuple[int, int]) -> MonitorDescriptor | None:
        for monitor in self.list_monitors():
            bounds = self._bounds_to_screen_bounds(monitor.bounds)
            if bounds.contains_point(point):
                return monitor
        return None

    def start_session(self) -> DisplayConfigurationSnapshot:
        snapshot = self.capture_configuration()
        self.session_snapshot = snapshot
        return snapshot

    def capture_configuration(self) -> DisplayConfigurationSnapshot:
        return DisplayConfigurationSnapshot(monitors=self.list_monitors(), captured_at=utc_now())

    def detect_configuration_change(self) -> DisplayConfigurationChangeResult:
        current = self.capture_configuration()
        baseline = self.session_snapshot
        if baseline is None:
            self.session_snapshot = current
            return DisplayConfigurationChangeResult(
                changed=False,
                baseline=current,
                current=current,
                reason="No baseline monitor configuration was stored; current configuration has been recorded.",
            )

        if self._snapshot_signature(baseline) != self._snapshot_signature(current):
            return DisplayConfigurationChangeResult(
                changed=True,
                baseline=baseline,
                current=current,
                reason="The monitor configuration changed during the current session.",
            )
        return DisplayConfigurationChangeResult(changed=False, baseline=baseline, current=current)

    def capture_image(self, *, monitor_id: str | None = None) -> Any | None:
        backend = self._require_capture_backend()
        if backend is None:
            return None
        monitor = self.get_monitor(monitor_id) if monitor_id is not None else None
        return backend.capture(monitor)

    def capture_screenshot_to_path(self, path: str, *, monitor_id: str | None = None) -> str | None:
        backend = self._require_capture_backend()
        if backend is None:
            return None
        image = self.capture_image(monitor_id=monitor_id)
        if image is None:
            return None
        return backend.save(image, path)

    def move_window_to_monitor(self, handle: int, monitor_id: str) -> WindowOperationResult:
        monitor = self.get_monitor(monitor_id)
        if monitor is None:
            return WindowOperationResult(succeeded=False, reason="Target monitor was not found.")

        window = next((item for item in self.window_manager.list_windows() if item.handle == handle), None)
        if window is None:
            return WindowOperationResult(succeeded=False, reason="Target window was not found.")

        x, y, width, height = self._compute_window_destination(window, monitor)
        result = self.window_manager.move_resize_window(handle=handle, x=x, y=y, width=width, height=height)
        if result.window is not None:
            result.window.monitor_id = monitor.monitor_id
        return result

    def _require_capture_backend(self) -> Any | None:
        if self.capture_backend is None:
            logger.warning("A capture backend is required for screenshot operations.")
            return None
        return self.capture_backend

    def _compute_window_destination(
        self,
        window,
        monitor: MonitorDescriptor,
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = monitor.work_area or monitor.bounds
        available_width = max(1, right - left)
        available_height = max(1, bottom - top)
        width = min(max(1, window.size[0]), available_width)
        height = min(max(1, window.size[1]), available_height)
        x = left + max(0, (available_width - width) // 2)
        y = top + max(0, (available_height - height) // 2)
        return x, y, width, height

    @staticmethod
    def _bounds_to_screen_bounds(bounds: tuple[int, int, int, int]) -> ScreenBounds:
        left, top, right, bottom = bounds
        return ScreenBounds(width=right - left, height=bottom - top, origin_x=left, origin_y=top)

    @staticmethod
    def _snapshot_signature(snapshot: DisplayConfigurationSnapshot) -> tuple[tuple[object, ...], ...]:
        return tuple(
            sorted(
                (
                    monitor.monitor_id,
                    monitor.bounds,
                    monitor.work_area,
                    monitor.resolution,
                    monitor.primary,
                    monitor.device_name,
                )
                for monitor in snapshot.monitors
            )
        )


