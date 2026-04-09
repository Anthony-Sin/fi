from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable

from desktop_automation_perception.contracts import DifferenceBackend, ScreenCaptureBackend
from desktop_automation_perception.models import ScreenChangeEvent, ScreenChangeResult, UIStateFingerprint
from desktop_automation_perception.desktop.ui_state_fingerprinter import UIStateFingerprinter


@dataclass(slots=True)
class PyAutoGUIScreenCaptureBackend:
    def capture(
        self,
        region_of_interest: tuple[int, int, int, int] | None = None,
        monitor_id: str | None = None,
    ):
        import pyautogui

        if region_of_interest is None:
            return pyautogui.screenshot()
        left, top, right, bottom = region_of_interest
        return pyautogui.screenshot(region=(left, top, right - left, bottom - top))

    def save(self, image, path: str) -> str:
        image.save(path)
        return path


@dataclass(slots=True)
class PILDifferenceBackend:
    def compute_difference(self, previous_image, current_image) -> float:
        from PIL import ImageChops, ImageStat

        diff = ImageChops.difference(previous_image, current_image)
        stat = ImageStat.Stat(diff)
        mean = sum(stat.mean) / len(stat.mean) if stat.mean else 0.0
        return float(mean / 255.0)


@dataclass(slots=True)
class ScreenChangeDetectionMonitor:
    capture_backend: ScreenCaptureBackend
    difference_backend: DifferenceBackend
    fingerprinter: UIStateFingerprinter | None = None
    sleep_fn: Callable[[float], None] = sleep

    def sample_change(
        self,
        previous_image,
        *,
        region_of_interest: tuple[int, int, int, int] | None = None,
        change_threshold: float = 0.1,
        screenshot_path: str | None = None,
        monitor_id: str | None = None,
    ) -> ScreenChangeResult:
        current_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
        difference = self.difference_backend.compute_difference(previous_image, current_image)
        if difference >= change_threshold:
            saved_path = self.capture_backend.save(current_image, screenshot_path) if screenshot_path else None
            return ScreenChangeResult(
                changed=True,
                event=ScreenChangeEvent(
                    difference_metric=difference,
                    threshold=change_threshold,
                    screenshot_path=saved_path,
                    region_of_interest=region_of_interest,
                ),
            )
        return ScreenChangeResult(
            changed=False,
            reason=f"Difference metric {difference:.3f} did not exceed threshold {change_threshold:.3f}.",
        )

    def wait_for_change(
        self,
        *,
        region_of_interest: tuple[int, int, int, int] | None = None,
        change_threshold: float = 0.1,
        timeout_seconds: float = 5.0,
        polling_interval_seconds: float = 0.25,
        screenshot_path: str | None = None,
        monitor_id: str | None = None,
    ) -> ScreenChangeResult:
        previous_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
        attempts = max(1, int(round(timeout_seconds / max(polling_interval_seconds, 0.01))))
        latest_reason = "No meaningful change detected."

        for attempt in range(attempts):
            current_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
            difference = self.difference_backend.compute_difference(previous_image, current_image)
            if difference >= change_threshold:
                saved_path = self.capture_backend.save(current_image, screenshot_path) if screenshot_path else None
                return ScreenChangeResult(
                    changed=True,
                    event=ScreenChangeEvent(
                        difference_metric=difference,
                        threshold=change_threshold,
                        screenshot_path=saved_path,
                        region_of_interest=region_of_interest,
                    ),
                )

            latest_reason = f"Difference metric {difference:.3f} did not exceed threshold {change_threshold:.3f}."
            previous_image = current_image
            if attempt < attempts - 1:
                self.sleep_fn(polling_interval_seconds)

        return ScreenChangeResult(
            changed=False,
            reason=f"{latest_reason} Timeout expired while waiting for change.",
        )

    def compare_to_fingerprint(
        self,
        baseline_fingerprint: UIStateFingerprint,
        *,
        region_of_interest: tuple[int, int, int, int] | None = None,
    ) -> float:
        if self.fingerprinter is None:
            raise ValueError("UI fingerprinter is not configured for this monitor.")
        return self.fingerprinter.compare_to_current(
            baseline_fingerprint,
            region_of_interest=region_of_interest,
        )
