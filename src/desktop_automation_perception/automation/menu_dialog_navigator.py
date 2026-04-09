from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Callable

from desktop_automation_perception.contracts import (
    AccessibilityReader,
    InputRunner,
    OCRExtractor,
    ScreenshotBackend,
    TemplateMatcher,
    WindowManager,
)
from desktop_automation_perception.models import (
    AIInterfaceElementMatch,
    AIInterfaceElementSelector,
    AccessibilityElement,
    DialogClassification,
    DialogHandlingRequest,
    DialogResponse,
    InputAction,
    InputActionType,
    InputTarget,
    InteractionStateSnapshot,
    MenuDialogInteractionLog,
    MenuDialogNavigationResult,
    MenuNavigationRequest,
    OCRTextBlock,
    SelectorStrategy,
    TemplateSearchRequest,
    UnexpectedDialogPolicy,
    WindowReference,
)


@dataclass(slots=True)
class MenuDialogNavigator:
    input_runner: InputRunner
    screenshot_backend: ScreenshotBackend
    accessibility_reader: AccessibilityReader | None = None
    ocr_extractor: OCRExtractor | None = None
    template_matcher: TemplateMatcher | None = None
    window_manager: WindowManager | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic

    def select_menu_item(
        self,
        request: MenuNavigationRequest,
    ) -> MenuDialogNavigationResult:
        before = self._capture_state()
        selector = request.menu_selector or AIInterfaceElementSelector(
            name=request.menu_label,
            role="menu item",
            window_title=request.window_title,
            process_name=request.process_name,
            strategies=(
                SelectorStrategy.ACCESSIBILITY,
                SelectorStrategy.OCR,
                SelectorStrategy.TEMPLATE_MATCH,
            ),
        )

        match = self._wait_for_selector(selector, timeout_seconds=request.timeout_seconds)
        if match is None and request.custom_menu_selector is not None:
            match = self._wait_for_selector(request.custom_menu_selector, timeout_seconds=request.timeout_seconds)
        if match is None or match.center is None:
            after = self._capture_state()
            return MenuDialogNavigationResult(
                succeeded=False,
                logs=[
                    MenuDialogInteractionLog(
                        interaction_type="menu",
                        target_label=request.menu_label,
                        succeeded=False,
                        reason="Menu item could not be located.",
                        before_state=before,
                        after_state=after,
                    )
                ],
                reason="Menu item could not be located.",
            )

        action_result = self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.CLICK,
                    target=InputTarget(
                        window=self._resolve_window(selector),
                        element_bounds=match.bounds,
                    ),
                    position=match.center,
                )
            ]
        )
        after = self._capture_state()
        succeeded = getattr(action_result, "succeeded", False)
        reason = None if succeeded else getattr(action_result, "failure_reason", "Menu click failed.")
        return MenuDialogNavigationResult(
            succeeded=succeeded,
            logs=[
                MenuDialogInteractionLog(
                    interaction_type="menu",
                    target_label=request.menu_label,
                    succeeded=succeeded,
                    reason=reason,
                    before_state=before,
                    after_state=after,
                )
            ],
            reason=reason,
        )

    def handle_dialog(
        self,
        request: DialogHandlingRequest,
    ) -> MenuDialogNavigationResult:
        before = self._capture_state(dialog_selector=request.dialog_selector)
        dialog_match = self._wait_for_selector(request.dialog_selector, timeout_seconds=request.timeout_seconds)
        if dialog_match is None:
            after = self._capture_state(dialog_selector=request.dialog_selector)
            return MenuDialogNavigationResult(
                succeeded=False,
                logs=[
                    MenuDialogInteractionLog(
                        interaction_type="dialog",
                        target_label=request.dialog_selector.name or "dialog",
                        succeeded=False,
                        reason="Dialog could not be located.",
                        before_state=before,
                        after_state=after,
                    )
                ],
                reason="Dialog could not be located.",
            )

        response_selector = self._resolve_response_selector(request)
        response_match = self._wait_for_selector(response_selector, timeout_seconds=request.timeout_seconds)
        if response_match is None or response_match.center is None:
            after = self._capture_state(dialog_selector=request.dialog_selector)
            return MenuDialogNavigationResult(
                succeeded=False,
                logs=[
                    MenuDialogInteractionLog(
                        interaction_type="dialog",
                        target_label=request.dialog_selector.name or "dialog",
                        succeeded=False,
                        response=request.response,
                        reason="Dialog response control could not be located.",
                        before_state=before,
                        after_state=after,
                    )
                ],
                reason="Dialog response control could not be located.",
            )

        action_result = self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.CLICK,
                    target=InputTarget(
                        window=self._resolve_window(response_selector),
                        element_bounds=response_match.bounds,
                    ),
                    position=response_match.center,
                )
            ]
        )
        after = self._capture_state(dialog_selector=request.dialog_selector)
        succeeded = getattr(action_result, "succeeded", False)
        reason = None if succeeded else getattr(action_result, "failure_reason", "Dialog interaction failed.")
        classification = self._classify_dialog(before.dialog_text)
        return MenuDialogNavigationResult(
            succeeded=succeeded,
            logs=[
                MenuDialogInteractionLog(
                    interaction_type="dialog",
                    target_label=request.dialog_selector.name or "dialog",
                    succeeded=succeeded,
                    classification=classification,
                    response=request.response,
                    reason=reason,
                    before_state=before,
                    after_state=after,
                )
            ],
            reason=reason,
        )

    def handle_unexpected_dialogs(
        self,
        *,
        dialog_selectors: list[AIInterfaceElementSelector],
        policy: UnexpectedDialogPolicy,
        timeout_seconds: float = 1.0,
    ) -> MenuDialogNavigationResult:
        logs: list[MenuDialogInteractionLog] = []
        for selector in dialog_selectors:
            before = self._capture_state(dialog_selector=selector)
            dialog_match = self._wait_for_selector(selector, timeout_seconds=timeout_seconds)
            if dialog_match is None:
                continue
            classification = self._classify_dialog(before.dialog_text)
            response = policy.response_by_classification.get(classification, policy.default_response)
            request = DialogHandlingRequest(
                dialog_selector=selector,
                response=response,
                custom_response_selector=policy.custom_response_selector,
                timeout_seconds=timeout_seconds,
            )
            result = self.handle_dialog(request)
            log = result.logs[0]
            log.classification = classification
            logs.append(log)
            if not result.succeeded:
                return MenuDialogNavigationResult(
                    succeeded=False,
                    logs=logs,
                    reason=result.reason,
                )
        return MenuDialogNavigationResult(
            succeeded=True,
            logs=logs,
        )

    def _resolve_response_selector(
        self,
        request: DialogHandlingRequest,
    ) -> AIInterfaceElementSelector:
        if request.response is DialogResponse.CUSTOM and request.custom_response_selector is not None:
            return request.custom_response_selector
        if request.response is DialogResponse.ACCEPT:
            return AIInterfaceElementSelector(name="OK", role="button")
        if request.response is DialogResponse.CANCEL:
            return AIInterfaceElementSelector(name="Cancel", role="button")
        return AIInterfaceElementSelector(name="Close", role="button")

    def _wait_for_selector(
        self,
        selector: AIInterfaceElementSelector,
        *,
        timeout_seconds: float,
    ) -> AIInterfaceElementMatch | None:
        deadline = self.monotonic_fn() + timeout_seconds
        while self.monotonic_fn() <= deadline:
            match = self._resolve_selector(selector)
            if match is not None:
                return match
            self.sleep_fn(0.25)
        return None

    def _resolve_selector(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if selector.bounds is not None:
            return AIInterfaceElementMatch(
                selector=selector,
                strategy=SelectorStrategy.DIRECT_BOUNDS,
                bounds=selector.bounds,
                center=self._center(selector.bounds),
                confidence=1.0,
            )
        for strategy in selector.strategies:
            if strategy is SelectorStrategy.ACCESSIBILITY:
                match = self._resolve_accessibility(selector)
            elif strategy is SelectorStrategy.OCR:
                match = self._resolve_ocr(selector)
            elif strategy is SelectorStrategy.TEMPLATE_MATCH:
                match = self._resolve_template(selector)
            else:
                match = None
            if match is not None:
                return match
        return None

    def _resolve_accessibility(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.accessibility_reader is None:
            return None
        query = self.accessibility_reader.find_elements(
            name=selector.name,
            role=selector.role,
            value=selector.value,
        )
        matches = getattr(query, "matches", [])
        if not matches:
            return None
        element = matches[0]
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.ACCESSIBILITY,
            bounds=element.bounds,
            center=self._center(element.bounds),
            text=self.accessibility_reader.get_element_text(element),
            element=element,
            confidence=1.0,
        )

    def _resolve_ocr(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.ocr_extractor is None or not selector.target_text:
            return None
        result = self.ocr_extractor.find_text(
            target=selector.target_text,
            region_of_interest=selector.region_of_interest,
        )
        if not getattr(result, "succeeded", False):
            return None
        bounds = getattr(result, "bounds", None)
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.OCR,
            bounds=bounds,
            center=self._center(bounds),
            text=getattr(result, "matched_text", None),
            confidence=float(getattr(result, "confidence", 0.0)),
        )

    def _resolve_template(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.template_matcher is None or not selector.template_path:
            return None
        screenshot_path = self.screenshot_backend.capture_screenshot_to_path()
        results = self.template_matcher.search(
            screenshot_path=screenshot_path,
            requests=[
                TemplateSearchRequest(
                    template_name=selector.template_name or "ui",
                    template_path=selector.template_path,
                    threshold=selector.threshold,
                    region_of_interest=selector.region_of_interest,
                )
            ],
        )
        if not results or not results[0].matches:
            return None
        match = results[0].matches[0]
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.TEMPLATE_MATCH,
            bounds=match.bounds,
            center=match.center,
            confidence=match.confidence,
        )

    def _capture_state(
        self,
        *,
        dialog_selector: AIInterfaceElementSelector | None = None,
    ) -> InteractionStateSnapshot:
        screenshot_path = self.screenshot_backend.capture_screenshot_to_path()
        active_window_title = None
        if self.window_manager is not None:
            windows = self.window_manager.list_windows()
            focused = next((window for window in windows if getattr(window, "focused", False)), None)
            active_window_title = focused.title if focused is not None else None

        dialog_text = None
        if dialog_selector is not None:
            match = self._resolve_selector(dialog_selector)
            dialog_text = match.text if match is not None else None
            if dialog_text is None and self.ocr_extractor is not None and match is not None and match.bounds is not None:
                extraction = self.ocr_extractor.extract_text(region_of_interest=match.bounds)
                dialog_text = self._combine_ocr_blocks(getattr(extraction, "blocks", []))

        return InteractionStateSnapshot(
            screenshot_path=screenshot_path,
            active_window_title=active_window_title,
            dialog_text=dialog_text,
        )

    def _classify_dialog(self, dialog_text: str | None) -> DialogClassification:
        normalized = (dialog_text or "").casefold()
        if any(token in normalized for token in ("are you sure", "confirm", "continue")):
            return DialogClassification.CONFIRMATION
        if any(token in normalized for token in ("warning", "unsaved", "caution")):
            return DialogClassification.WARNING
        if any(token in normalized for token in ("error", "failed", "cannot")):
            return DialogClassification.ERROR
        if normalized:
            return DialogClassification.INFO
        return DialogClassification.UNKNOWN

    def _resolve_window(self, selector: AIInterfaceElementSelector) -> WindowReference | None:
        if selector.window_title is None and selector.process_name is None:
            return None
        if self.window_manager is None:
            return WindowReference(title=selector.window_title)
        for window in self.window_manager.list_windows():
            if selector.window_title and selector.window_title.casefold() not in window.title.casefold():
                continue
            if selector.process_name and (window.process_name or "").casefold() != selector.process_name.casefold():
                continue
            return WindowReference(title=window.title, handle=getattr(window, "handle", None))
        return WindowReference(title=selector.window_title)

    def _combine_ocr_blocks(self, blocks: list[OCRTextBlock]) -> str:
        if not blocks:
            return ""
        ordered = sorted(blocks, key=lambda block: (block.bounds[1], block.bounds[0]))
        return "\n".join(block.text.strip() for block in ordered if block.text.strip())

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
