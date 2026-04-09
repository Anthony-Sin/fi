from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic, sleep
from typing import Callable

from desktop_automation_perception.models import (
    AnimationCompletionSignal,
    AnimationWaitLogEntry,
    AnimationWaitRequest,
    AnimationWaitResult,
    TemplateSearchRequest,
)


@dataclass(slots=True)
class AnimationTransitionWaitModule:
    capture_backend: object
    difference_backend: object
    template_matcher: object | None = None
    ocr_extractor: object | None = None
    accessibility_reader: object | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic
    wait_logs: list[AnimationWaitLogEntry] = field(default_factory=list)

    def wait_for_completion(self, request: AnimationWaitRequest) -> AnimationWaitResult:
        previous_image = self.capture_backend.capture(
            request.region_of_interest,
            monitor_id=request.monitor_id,
        )
        started_at = self.monotonic_fn()
        attempts = 0
        stable_frames = 0
        sticky_signals: set[AnimationCompletionSignal] = set()
        last_change_rate = 0.0
        latest_detail = "Animation is still in progress."
        latest_screenshot_path = None

        while True:
            attempts += 1
            current_image = self.capture_backend.capture(
                request.region_of_interest,
                monitor_id=request.monitor_id,
            )
            last_change_rate = self.difference_backend.compute_difference(previous_image, current_image)
            if last_change_rate <= request.settle_threshold:
                stable_frames += 1
            else:
                stable_frames = 0

            completed_signals, latest_detail = self._completed_signals(
                request,
                stable_frames=stable_frames,
                last_change_rate=last_change_rate,
                sticky_signals=sticky_signals,
            )
            sticky_signals.update(completed_signals)
            elapsed = self.monotonic_fn() - started_at

            if self._is_complete(request, completed_signals):
                completed_elapsed = self.monotonic_fn() - started_at
                latest_screenshot_path = self._save_image_if_requested(current_image, request.screenshot_path)
                return self._finalize(
                    request=request,
                    elapsed_seconds=completed_elapsed,
                    attempts=attempts,
                    succeeded=True,
                    detail=latest_detail,
                    screenshot_path=latest_screenshot_path,
                    completed_signals=completed_signals,
                    last_change_rate=last_change_rate,
                )

            if elapsed >= request.timeout_seconds:
                timeout_elapsed = self.monotonic_fn() - started_at
                latest_screenshot_path = self._save_image_if_requested(current_image, request.screenshot_path)
                return self._finalize(
                    request=request,
                    elapsed_seconds=timeout_elapsed,
                    attempts=attempts,
                    succeeded=False,
                    detail=f"{latest_detail} Timeout expired while waiting for animation completion.",
                    screenshot_path=latest_screenshot_path,
                    completed_signals=completed_signals,
                    last_change_rate=last_change_rate,
                )

            previous_image = current_image
            self.sleep_fn(request.sampling_interval_seconds)

    def wait_for_animation_to_settle(self, request: AnimationWaitRequest) -> AnimationWaitResult:
        return self.wait_for_completion(
            self._with_signals(request, (AnimationCompletionSignal.SETTLED,))
        )

    def wait_for_spinner_to_disappear(self, request: AnimationWaitRequest) -> AnimationWaitResult:
        return self.wait_for_completion(
            self._with_signals(request, (AnimationCompletionSignal.SPINNER_GONE,))
        )

    def wait_for_progress_completion(self, request: AnimationWaitRequest) -> AnimationWaitResult:
        return self.wait_for_completion(
            self._with_signals(request, (AnimationCompletionSignal.PROGRESS_COMPLETE,))
        )

    def _completed_signals(
        self,
        request: AnimationWaitRequest,
        *,
        stable_frames: int,
        last_change_rate: float,
        sticky_signals: set[AnimationCompletionSignal],
    ) -> tuple[tuple[AnimationCompletionSignal, ...], str]:
        completed: list[AnimationCompletionSignal] = []
        details: list[str] = []

        if (
            AnimationCompletionSignal.SETTLED in sticky_signals
            or stable_frames >= max(1, request.consecutive_stable_frames)
        ):
            completed.append(AnimationCompletionSignal.SETTLED)
            details.append(
                f"Animation settled with change rate {last_change_rate:.3f} "
                f"across {stable_frames} stable frames."
            )
        else:
            details.append(f"Change rate remains {last_change_rate:.3f}.")

        spinner_present = None
        if AnimationCompletionSignal.SPINNER_GONE in sticky_signals:
            completed.append(AnimationCompletionSignal.SPINNER_GONE)
            details.append("Loading spinner is no longer visible.")
        else:
            spinner_present = self._spinner_present(request)
        if spinner_present is False:
            completed.append(AnimationCompletionSignal.SPINNER_GONE)
            details.append("Loading spinner is no longer visible.")
        elif self._spinner_signal_configured(request):
            details.append("Loading spinner is still visible.")

        progress_complete = None
        if AnimationCompletionSignal.PROGRESS_COMPLETE in sticky_signals:
            completed.append(AnimationCompletionSignal.PROGRESS_COMPLETE)
            details.append("Progress indicator reached completion.")
        else:
            progress_complete = self._progress_complete(request)
        if progress_complete is True:
            completed.append(AnimationCompletionSignal.PROGRESS_COMPLETE)
            details.append("Progress indicator reached completion.")
        elif self._progress_signal_configured(request):
            details.append("Progress indicator has not reached completion yet.")

        return tuple(completed), " ".join(details)

    def _spinner_present(self, request: AnimationWaitRequest) -> bool | None:
        template_present = self._template_present(
            template_name=request.spinner_template_name,
            template_path=request.spinner_template_path,
            request=request,
        )
        if template_present is not None:
            return template_present

        if self.accessibility_reader is not None and (request.spinner_element_name or request.spinner_element_role):
            query = self.accessibility_reader.find_elements(
                name=request.spinner_element_name,
                role=request.spinner_element_role,
                value=None,
            )
            return bool(getattr(query, "matches", []))

        if self.ocr_extractor is not None and request.spinner_text:
            match = self.ocr_extractor.find_text(
                target=request.spinner_text,
                screenshot_path=request.screenshot_path,
                region_of_interest=request.region_of_interest,
            )
            return bool(getattr(match, "succeeded", False))

        return None

    def _progress_complete(self, request: AnimationWaitRequest) -> bool | None:
        if self.accessibility_reader is not None and (
            request.progress_element_name
            or request.progress_element_role
            or request.progress_expected_value
        ):
            query = self.accessibility_reader.find_elements(
                name=request.progress_element_name,
                role=request.progress_element_role,
                value=request.progress_expected_value,
            )
            if getattr(query, "matches", []):
                return True

        if self.ocr_extractor is not None and request.progress_complete_text:
            match = self.ocr_extractor.find_text(
                target=request.progress_complete_text,
                screenshot_path=request.screenshot_path,
                region_of_interest=request.region_of_interest,
            )
            return bool(getattr(match, "succeeded", False))

        return None

    def _template_present(
        self,
        *,
        template_name: str | None,
        template_path: str | None,
        request: AnimationWaitRequest,
    ) -> bool | None:
        if self.template_matcher is None or not template_path:
            return None
        results = self.template_matcher.search(
            screenshot_path=request.screenshot_path,
            requests=[
                TemplateSearchRequest(
                    template_name=template_name or "animation-template",
                    template_path=template_path,
                    threshold=request.settle_threshold,
                    region_of_interest=request.region_of_interest,
                )
            ],
        )
        if not results:
            return False
        return bool(results[0].matches)

    def _is_complete(
        self,
        request: AnimationWaitRequest,
        completed_signals: tuple[AnimationCompletionSignal, ...],
    ) -> bool:
        return all(signal in completed_signals for signal in request.required_signals)

    def _spinner_signal_configured(self, request: AnimationWaitRequest) -> bool:
        return bool(
            request.spinner_template_path
            or request.spinner_text
            or request.spinner_element_name
            or request.spinner_element_role
        )

    def _progress_signal_configured(self, request: AnimationWaitRequest) -> bool:
        return bool(
            request.progress_complete_text
            or request.progress_element_name
            or request.progress_element_role
            or request.progress_expected_value
        )

    def _save_image_if_requested(self, image, screenshot_path: str | None) -> str | None:
        if not screenshot_path:
            return None
        return self.capture_backend.save(image, screenshot_path)

    def _finalize(
        self,
        *,
        request: AnimationWaitRequest,
        elapsed_seconds: float,
        attempts: int,
        succeeded: bool,
        detail: str | None,
        screenshot_path: str | None,
        completed_signals: tuple[AnimationCompletionSignal, ...],
        last_change_rate: float,
    ) -> AnimationWaitResult:
        log_entry = AnimationWaitLogEntry(
            wait_id=request.wait_id,
            elapsed_seconds=elapsed_seconds,
            succeeded=succeeded,
            attempts=attempts,
            detail=detail,
            completed_signals=completed_signals,
        )
        self.wait_logs.append(log_entry)
        return AnimationWaitResult(
            succeeded=succeeded,
            request=request,
            elapsed_seconds=elapsed_seconds,
            attempts=attempts,
            detail=detail,
            screenshot_path=screenshot_path,
            completed_signals=completed_signals,
            last_change_rate=last_change_rate,
            log_entry=log_entry,
        )

    def _with_signals(
        self,
        request: AnimationWaitRequest,
        required_signals: tuple[AnimationCompletionSignal, ...],
    ) -> AnimationWaitRequest:
        return AnimationWaitRequest(
            wait_id=request.wait_id,
            timeout_seconds=request.timeout_seconds,
            sampling_interval_seconds=request.sampling_interval_seconds,
            settle_threshold=request.settle_threshold,
            consecutive_stable_frames=request.consecutive_stable_frames,
            region_of_interest=request.region_of_interest,
            monitor_id=request.monitor_id,
            screenshot_path=request.screenshot_path,
            spinner_template_name=request.spinner_template_name,
            spinner_template_path=request.spinner_template_path,
            spinner_text=request.spinner_text,
            spinner_element_name=request.spinner_element_name,
            spinner_element_role=request.spinner_element_role,
            progress_complete_text=request.progress_complete_text,
            progress_element_name=request.progress_element_name,
            progress_element_role=request.progress_element_role,
            progress_expected_value=request.progress_expected_value,
            required_signals=required_signals,
        )
