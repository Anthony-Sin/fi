from __future__ import annotations

import logging
from dataclasses import dataclass
from time import sleep
from typing import Callable, Any

from desktop_automation_agent.contracts import DifferenceBackend, ScreenCaptureBackend
from desktop_automation_agent.models import ScreenChangeEvent, ScreenChangeResult, UIStateFingerprint
from desktop_automation_agent.desktop.ui_state_fingerprinter import UIStateFingerprinter


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PyAutoGUIScreenCaptureBackend:
    def capture(
        self,
        region_of_interest: tuple[int, int, int, int] | None = None,
        monitor_id: str | None = None,
    ) -> Any | None:
        try:
            import pyautogui

            if region_of_interest is None:
                return pyautogui.screenshot()
            left, top, right, bottom = region_of_interest
            return pyautogui.screenshot(region=(left, top, right - left, bottom - top))
        except Exception as e:
            logger.warning(f"PyAutoGUI capture failed: {e}")
            return None

    def save(self, image: Any, path: str) -> str | None:
        try:
            image.save(path)
            return path
        except Exception as e:
            logger.warning(f"Failed to save image to {path}: {e}")
            return None


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
        previous_image: Any,
        *,
        region_of_interest: tuple[int, int, int, int] | None = None,
        change_threshold: float = 0.1,
        screenshot_path: str | None = None,
        monitor_id: str | None = None,
    ) -> ScreenChangeResult:
        current_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
        if current_image is None or previous_image is None:
            return ScreenChangeResult(
                changed=False,
                reason="Unable to capture screen for comparison.",
            )
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
        """Wait for the screen content to change beyond the specified threshold."""
        try:
            previous_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
        except Exception as e:
            logger.warning("Initial capture failed in ChangeMonitor: %s", e)
            return ScreenChangeResult(succeeded=False, reason=f"Initial capture failed: {e}")
        attempts = max(1, int(round(timeout_seconds / max(polling_interval_seconds, 0.01))))
        latest_reason = "No meaningful change detected."

        for attempt in range(attempts):
            try:
                current_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
                if current_image is None or previous_image is None:
                    latest_reason = "Unable to capture screen for comparison."
                    if attempt < attempts - 1:
                        self.sleep_fn(polling_interval_seconds)
                        if previous_image is None:
                            previous_image = self.capture_backend.capture(region_of_interest, monitor_id=monitor_id)
                        continue
                    break

                difference = self.difference_backend.compute_difference(previous_image, current_image)
            except Exception as e:
                logger.warning("Comparison failed in ChangeMonitor loop: %s", e)
                difference = 0.0
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
            logger.warning("UI fingerprinter is not configured for this monitor.")
            return 0.0
        return self.fingerprinter.compare_to_current(
            baseline_fingerprint,
            region_of_interest=region_of_interest,
        )
