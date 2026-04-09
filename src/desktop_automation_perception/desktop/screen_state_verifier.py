from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable

from desktop_automation_perception.contracts import ScreenshotBackend
from desktop_automation_perception.models import (
    ScreenCheckType,
    ScreenVerificationCheck,
    ScreenVerificationCheckResult,
    ScreenVerificationResult,
    TemplateSearchRequest,
)


@dataclass(slots=True)
class PyAutoGUIScreenshotBackend:
    def capture_screenshot_to_path(self, path: str | None = None, monitor_id: str | None = None) -> str:
        from datetime import datetime, timezone
        from pathlib import Path

        import pyautogui

        target = Path(path) if path is not None else Path(
            f"verification_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.png"
        )
        screenshot = pyautogui.screenshot()
        screenshot.save(target)
        return str(target)


@dataclass(slots=True)
class ScreenStateVerifier:
    ocr_extractor: object
    template_matcher: object
    window_manager: object
    accessibility_reader: object
    screenshot_backend: ScreenshotBackend
    sleep_fn: Callable[[float], None] = sleep
    roi_calculator: object | None = None

    def verify(
        self,
        checks: list[ScreenVerificationCheck],
        screenshot_path: str | None = None,
    ) -> ScreenVerificationResult:
        captured_path = self.screenshot_backend.capture_screenshot_to_path(screenshot_path)
        passed: list[ScreenVerificationCheckResult] = []
        failed: list[ScreenVerificationCheckResult] = []

        for check in checks:
            result = self._poll_check(check, captured_path)
            if result.passed:
                passed.append(result)
            else:
                failed.append(result)

        return ScreenVerificationResult(
            passed_checks=passed,
            failed_checks=failed,
            screenshot_path=captured_path,
        )

    def _poll_check(
        self,
        check: ScreenVerificationCheck,
        screenshot_path: str,
    ) -> ScreenVerificationCheckResult:
        attempts = max(1, int(round(check.timeout_seconds / max(check.polling_interval_seconds, 0.01))))
        latest_failure = ScreenVerificationCheckResult(
            check_id=check.check_id,
            check_type=check.check_type,
            passed=False,
            detail="Check did not complete.",
        )

        for attempt in range(attempts):
            result = self._run_check(check, screenshot_path)
            if result.passed:
                return result
            latest_failure = result
            if attempt < attempts - 1:
                self.sleep_fn(check.polling_interval_seconds)

        return latest_failure

    def _run_check(
        self,
        check: ScreenVerificationCheck,
        screenshot_path: str,
    ) -> ScreenVerificationCheckResult:
        region_of_interest = check.region_of_interest
        if region_of_interest is None and self.roi_calculator is not None:
            roi_result = self.roi_calculator.calculate_for_check(check)
            if getattr(roi_result, "succeeded", False) and getattr(roi_result, "roi", None) is not None:
                region_of_interest = roi_result.roi.bounds

        if check.check_type is ScreenCheckType.TEXT_PRESENT:
            match = self.ocr_extractor.find_text(
                target=check.target_text or "",
                screenshot_path=screenshot_path,
                region_of_interest=region_of_interest,
            )
            return ScreenVerificationCheckResult(
                check_id=check.check_id,
                check_type=check.check_type,
                passed=match.succeeded,
                detail=match.matched_text if match.succeeded else match.reason,
            )

        if check.check_type is ScreenCheckType.IMAGE_PRESENT:
            results = self.template_matcher.search(
                screenshot_path=screenshot_path,
                requests=[
                    TemplateSearchRequest(
                        template_name=check.template_name or "template",
                        template_path=check.template_path or "",
                        threshold=check.threshold,
                        region_of_interest=region_of_interest,
                    )
                ],
            )
            found = bool(results and results[0].matches)
            return ScreenVerificationCheckResult(
                check_id=check.check_id,
                check_type=check.check_type,
                passed=found,
                detail=f"{len(results[0].matches)} matches found." if found else "No template match found.",
            )

        if check.check_type is ScreenCheckType.ACTIVE_WINDOW:
            windows = self.window_manager.list_windows()
            active = next((window for window in windows if window.focused), None)
            passed = active is not None
            if passed and check.window_title is not None:
                passed = check.window_title.casefold() in active.title.casefold()
            if passed and check.process_name is not None:
                passed = (active.process_name or "").casefold() == check.process_name.casefold()
            return ScreenVerificationCheckResult(
                check_id=check.check_id,
                check_type=check.check_type,
                passed=passed,
                detail=active.title if active is not None else "No focused window found.",
            )

        if check.check_type is ScreenCheckType.ELEMENT_VALUE:
            direct = self.accessibility_reader.find_elements(
                name=check.element_name,
                role=check.element_role,
                value=check.expected_value,
            )
            if direct.matches:
                return ScreenVerificationCheckResult(
                    check_id=check.check_id,
                    check_type=check.check_type,
                    passed=True,
                    detail=direct.matches[0].value,
                )

            fallback = self.accessibility_reader.find_elements(
                name=check.element_name,
                role=check.element_role,
            )
            if fallback.matches:
                return ScreenVerificationCheckResult(
                    check_id=check.check_id,
                    check_type=check.check_type,
                    passed=False,
                    detail=f"Element found with value {fallback.matches[0].value!r}.",
                )
            return ScreenVerificationCheckResult(
                check_id=check.check_id,
                check_type=check.check_type,
                passed=False,
                detail="Element not found.",
            )

        if check.check_type is ScreenCheckType.LOADING_ABSENT:
            match = self.ocr_extractor.find_text(
                target=check.target_text or "loading",
                screenshot_path=screenshot_path,
                region_of_interest=region_of_interest,
            )
            return ScreenVerificationCheckResult(
                check_id=check.check_id,
                check_type=check.check_type,
                passed=not match.succeeded,
                detail="Loading indicator absent." if not match.succeeded else f"Loading text still visible: {match.matched_text}",
            )

        if check.check_type is ScreenCheckType.MODAL_ABSENT:
            modal_query = self.accessibility_reader.find_elements(
                role=check.element_role or "Dialog",
                name=check.element_name,
            )
            present = bool(modal_query.matches)
            return ScreenVerificationCheckResult(
                check_id=check.check_id,
                check_type=check.check_type,
                passed=not present,
                detail="Modal overlay absent." if not present else "Modal or overlay is still present.",
            )

        return ScreenVerificationCheckResult(
            check_id=check.check_id,
            check_type=check.check_type,
            passed=False,
            detail="Unsupported check type.",
        )
