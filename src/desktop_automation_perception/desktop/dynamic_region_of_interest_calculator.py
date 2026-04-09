from __future__ import annotations

from dataclasses import dataclass, field

from desktop_automation_perception.models import (
    DynamicRegionOfInterest,
    DynamicRegionResult,
    ScreenVerificationCheck,
    WindowContext,
    WindowZone,
    WindowZoneType,
)


@dataclass(slots=True)
class DynamicRegionOfInterestCalculator:
    window_manager: object
    toolbar_height_ratio: float = 0.12
    status_bar_height_ratio: float = 0.08
    sidebar_width_ratio: float = 0.22
    _last_signature: tuple[int, tuple[int, int], tuple[int, int]] | None = field(default=None, init=False, repr=False)
    _last_zones: list[WindowZone] = field(default_factory=list, init=False, repr=False)

    def calculate_for_check(self, check: ScreenVerificationCheck) -> DynamicRegionResult:
        active_window = self._focused_window()
        if active_window is None:
            return DynamicRegionResult(succeeded=False, reason="No focused application window is available.")

        zones = self._zones_for_window(active_window)
        zone_type = self._infer_zone(check)
        zone = next((item for item in zones if item.zone_type is zone_type), None)
        if zone is None:
            zone = next((item for item in zones if item.zone_type is WindowZoneType.FULL_WINDOW), None)
        if zone is None:
            return DynamicRegionResult(succeeded=False, zones=zones, reason="Unable to calculate a region of interest.")

        window_bounds = self._window_bounds(active_window)
        return DynamicRegionResult(
            succeeded=True,
            roi=DynamicRegionOfInterest(
                window_handle=active_window.handle,
                bounds=zone.bounds,
                zone_type=zone.zone_type,
                window_bounds=window_bounds,
                confidence=zone.confidence,
                detail=f"Calculated ROI for {zone.zone_type.value} zone.",
            ),
            zones=zones,
        )

    def zones_for_active_window(self) -> DynamicRegionResult:
        active_window = self._focused_window()
        if active_window is None:
            return DynamicRegionResult(succeeded=False, reason="No focused application window is available.")
        zones = self._zones_for_window(active_window)
        full_zone = next((item for item in zones if item.zone_type is WindowZoneType.FULL_WINDOW), None)
        return DynamicRegionResult(
            succeeded=True,
            roi=None
            if full_zone is None
            else DynamicRegionOfInterest(
                window_handle=active_window.handle,
                bounds=full_zone.bounds,
                zone_type=full_zone.zone_type,
                window_bounds=self._window_bounds(active_window),
                confidence=full_zone.confidence,
                detail="Calculated zones for active window.",
            ),
            zones=zones,
        )

    def _focused_window(self) -> WindowContext | None:
        windows = self.window_manager.list_windows()
        return next((window for window in windows if getattr(window, "focused", False)), None)

    def _zones_for_window(self, window: WindowContext) -> list[WindowZone]:
        signature = (window.handle, window.position, window.size)
        if self._last_signature == signature and self._last_zones:
            return list(self._last_zones)

        left, top = window.position
        width, height = window.size
        right = left + width
        bottom = top + height
        toolbar_height = max(1, int(round(height * self.toolbar_height_ratio)))
        status_bar_height = max(1, int(round(height * self.status_bar_height_ratio)))
        sidebar_width = max(1, int(round(width * self.sidebar_width_ratio)))

        toolbar_bottom = min(bottom, top + toolbar_height)
        status_bar_top = max(top, bottom - status_bar_height)
        sidebar_right = min(right, left + sidebar_width)
        content_left = sidebar_right
        content_top = toolbar_bottom
        content_right = right
        content_bottom = status_bar_top

        zones = [
            WindowZone(WindowZoneType.FULL_WINDOW, (left, top, right, bottom), confidence=1.0),
            WindowZone(WindowZoneType.TOOLBAR, (left, top, right, toolbar_bottom), confidence=0.86),
            WindowZone(WindowZoneType.STATUS_BAR, (left, status_bar_top, right, bottom), confidence=0.82),
            WindowZone(WindowZoneType.SIDEBAR, (left, toolbar_bottom, sidebar_right, status_bar_top), confidence=0.8),
            WindowZone(
                WindowZoneType.CONTENT,
                (
                    min(content_left, content_right),
                    min(content_top, content_bottom),
                    max(content_left, content_right),
                    max(content_top, content_bottom),
                ),
                confidence=0.9,
            ),
        ]
        self._last_signature = signature
        self._last_zones = list(zones)
        return zones

    def _window_bounds(self, window: WindowContext) -> tuple[int, int, int, int]:
        left, top = window.position
        width, height = window.size
        return (left, top, left + width, top + height)

    def _infer_zone(self, check: ScreenVerificationCheck) -> WindowZoneType:
        role = (check.element_role or "").casefold()
        name = (check.element_name or "").casefold()
        target_text = (check.target_text or "").casefold()
        template_name = (check.template_name or "").casefold()
        keywords = " ".join(part for part in (role, name, target_text, template_name) if part)

        if any(token in keywords for token in ("toolbar", "menu", "tab", "ribbon", "search", "nav")):
            return WindowZoneType.TOOLBAR
        if any(token in keywords for token in ("status", "footer", "progress")):
            return WindowZoneType.STATUS_BAR
        if any(token in keywords for token in ("sidebar", "panel", "tree", "folder", "navigation")):
            return WindowZoneType.SIDEBAR
        if any(token in role for token in ("dialog", "modal")):
            return WindowZoneType.CONTENT
        if check.check_type.value in {"text_present", "image_present", "element_value", "loading_absent", "modal_absent"}:
            return WindowZoneType.CONTENT
        return WindowZoneType.FULL_WINDOW
