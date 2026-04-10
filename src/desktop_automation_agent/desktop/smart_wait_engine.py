from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import monotonic, sleep
from typing import Callable, Any

from desktop_automation_agent.models import (
    SmartWaitLogEntry,
    SmartWaitRequest,
    SmartWaitResult,
    TemplateSearchRequest,
    WaitType,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SmartWaitEngine:
    ocr_extractor: Any | None = None
    template_matcher: object | None = None
    accessibility_reader: object | None = None
    change_monitor: object | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic
    wait_logs: list[SmartWaitLogEntry] = field(default_factory=list)

    def wait(self, request: SmartWaitRequest) -> SmartWaitResult:
        """Wait for a specific condition to be satisfied on the desktop."""
        started_at = self.monotonic_fn()
        attempts = 0
        latest_detail = "Wait condition was not satisfied."

        while True:
            attempts += 1
            try:
                satisfied, detail, screenshot_path = self._evaluate(request)
            except Exception as e:
                logger.warning("Error during SmartWait evaluation: %s", e)
                satisfied, detail, screenshot_path = False, f"Evaluation failed: {e}", None

            elapsed = self.monotonic_fn() - started_at
            latest_detail = detail
            if satisfied:
                return self._finalize(
                    request=request,
                    elapsed_seconds=elapsed,
                    attempts=attempts,
                    succeeded=True,
                    detail=detail,
                    screenshot_path=screenshot_path,
                )

            if elapsed >= request.timeout_seconds:
                return self._finalize(
                    request=request,
                    elapsed_seconds=elapsed,
                    attempts=attempts,
                    succeeded=False,
                    detail=f"{latest_detail} Timeout expired while waiting for condition.",
                    screenshot_path=screenshot_path,
                )

            if elapsed + request.polling_interval_seconds > request.timeout_seconds:
                return self._finalize(
                    request=request,
                    elapsed_seconds=elapsed,
                    attempts=attempts,
                    succeeded=False,
                    detail=f"{latest_detail} Timeout expired while waiting for condition.",
                    screenshot_path=screenshot_path,
                )

            self.sleep_fn(request.polling_interval_seconds)

    def wait_until_element_appears(self, request: SmartWaitRequest) -> SmartWaitResult:
        return self.wait(self._with_type(request, WaitType.ELEMENT_APPEARS))

    def wait_until_element_disappears(self, request: SmartWaitRequest) -> SmartWaitResult:
        return self.wait(self._with_type(request, WaitType.ELEMENT_DISAPPEARS))

    def wait_until_screen_changes(self, request: SmartWaitRequest) -> SmartWaitResult:
        return self.wait(self._with_type(request, WaitType.SCREEN_CHANGES))

    def wait_until_text_visible(self, request: SmartWaitRequest) -> SmartWaitResult:
        return self.wait(self._with_type(request, WaitType.TEXT_VISIBLE))

    def wait_until_network_idle(self, request: SmartWaitRequest) -> SmartWaitResult:
        return self.wait(self._with_type(request, WaitType.NETWORK_IDLE))

    def _evaluate(self, request: SmartWaitRequest) -> tuple[bool, str, str | None]:
        if request.wait_type is WaitType.ELEMENT_APPEARS:
            return self._evaluate_element_presence(request, expected_present=True)
        if request.wait_type is WaitType.ELEMENT_DISAPPEARS:
            return self._evaluate_element_presence(request, expected_present=False)
        if request.wait_type is WaitType.SCREEN_CHANGES:
            return self._evaluate_screen_change(request)
        if request.wait_type is WaitType.TEXT_VISIBLE:
            return self._evaluate_text_visible(request)
        if request.wait_type is WaitType.NETWORK_IDLE:
            return self._evaluate_network_idle(request)
        return False, "Unsupported wait type.", None

    def _evaluate_element_presence(
        self,
        request: SmartWaitRequest,
        *,
        expected_present: bool,
    ) -> tuple[bool, str, str | None]:
        accessibility_matches = self._find_accessibility_matches(request)
        template_matches = self._find_template_matches(request)
        present = bool(accessibility_matches) or bool(template_matches)
        if expected_present:
            return present, (
                "Element appeared."
                if present
                else "Element is not visible yet."
            ), None
        return (not present), (
            "Element disappeared."
            if not present
            else "Element is still visible."
        ), None

    def _evaluate_screen_change(self, request: SmartWaitRequest) -> tuple[bool, str, str | None]:
        if self.change_monitor is None:
            logger.warning("Screen change waits require a change detection monitor.")
            return False, "Missing change detection monitor.", None

        result = self.change_monitor.wait_for_change(
            region_of_interest=request.region_of_interest,
            change_threshold=request.threshold,
            timeout_seconds=0.0,
            polling_interval_seconds=request.polling_interval_seconds,
            screenshot_path=request.screenshot_path,
            monitor_id=request.monitor_id,
        )
        if result.changed:
            detail = "Screen content changed."
            screenshot_path = result.event.screenshot_path if result.event is not None else None
            return True, detail, screenshot_path
        return False, result.reason or "Screen content has not changed yet.", None

    def _evaluate_text_visible(self, request: SmartWaitRequest) -> tuple[bool, str, str | None]:
        if self.ocr_extractor is None:
            logger.warning("Text visibility waits require an OCR extractor.")
            return False, "Missing OCR extractor.", None

        match = self.ocr_extractor.find_text(
            target=request.target_text or "",
            screenshot_path=request.screenshot_path,
            region_of_interest=request.region_of_interest,
        )
        if match.succeeded:
            return True, f"Text became visible: {match.matched_text}", request.screenshot_path
        return False, match.reason or "Target text is not visible yet.", request.screenshot_path

    def _evaluate_network_idle(self, request: SmartWaitRequest) -> tuple[bool, str, str | None]:
        indicator_text = request.network_indicator_text or request.target_text or "loading"
        text_match = self._find_text_match(indicator_text, request) if indicator_text else None
        if text_match is not None and text_match.succeeded:
            return False, f"Network activity indicator is still visible: {text_match.matched_text}", request.screenshot_path

        if request.element_name or request.element_role or request.expected_value:
            accessibility_matches = self._find_accessibility_matches(request)
            if accessibility_matches:
                return False, "Network activity indicator is still visible in the accessibility tree.", request.screenshot_path

        if request.template_path:
            template_matches = self._find_template_matches(request)
            if template_matches:
                return False, "Network activity indicator is still visible in the current screenshot.", request.screenshot_path

        if text_match is not None or request.element_name or request.element_role or request.expected_value or request.template_path:
            return True, "Network activity indicator is gone.", request.screenshot_path

        return False, "Network idle wait requires an OCR, accessibility, or template indicator.", request.screenshot_path

    def _find_accessibility_matches(self, request: SmartWaitRequest) -> list[object]:
        if self.accessibility_reader is None:
            return []
        query = self.accessibility_reader.find_elements(
            name=request.element_name,
            role=request.element_role,
            value=request.expected_value,
        )
        return list(getattr(query, "matches", []))

    def _find_template_matches(self, request: SmartWaitRequest) -> list[object]:
        if self.template_matcher is None or not request.template_path:
            return []
        results = self.template_matcher.search(
            screenshot_path=request.screenshot_path,
            requests=[
                TemplateSearchRequest(
                    template_name=request.template_name or "template",
                    template_path=request.template_path,
                    threshold=request.threshold,
                    region_of_interest=request.region_of_interest,
                )
            ],
        )
        if not results:
            return []
        return list(results[0].matches)

    def _find_text_match(self, text: str, request: SmartWaitRequest):
        if self.ocr_extractor is None:
            return None
        return self.ocr_extractor.find_text(
            target=text,
            screenshot_path=request.screenshot_path,
            region_of_interest=request.region_of_interest,
        )

    def _finalize(
        self,
        *,
        request: SmartWaitRequest,
        elapsed_seconds: float,
        attempts: int,
        succeeded: bool,
        detail: str | None,
        screenshot_path: str | None,
    ) -> SmartWaitResult:
        log_entry = SmartWaitLogEntry(
            wait_id=request.wait_id,
            wait_type=request.wait_type,
            elapsed_seconds=elapsed_seconds,
            succeeded=succeeded,
            attempts=attempts,
            detail=detail,
        )
        self.wait_logs.append(log_entry)
        return SmartWaitResult(
            succeeded=succeeded,
            request=request,
            elapsed_seconds=elapsed_seconds,
            attempts=attempts,
            detail=detail,
            screenshot_path=screenshot_path,
            log_entry=log_entry,
        )

    def _with_type(self, request: SmartWaitRequest, wait_type: WaitType) -> SmartWaitRequest:
        return SmartWaitRequest(
            wait_id=request.wait_id,
            wait_type=wait_type,
            timeout_seconds=request.timeout_seconds,
            polling_interval_seconds=request.polling_interval_seconds,
            template_name=request.template_name,
            template_path=request.template_path,
            threshold=request.threshold,
            target_text=request.target_text,
            element_name=request.element_name,
            element_role=request.element_role,
            expected_value=request.expected_value,
            region_of_interest=request.region_of_interest,
            monitor_id=request.monitor_id,
            screenshot_path=request.screenshot_path,
            network_indicator_text=request.network_indicator_text,
        )
