from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Callable

from desktop_automation_perception.contracts import (
    AccessibilityReader,
    InputRunner,
    OCRExtractor,
    PromptInjector,
    ScreenshotBackend,
    TemplateMatcher,
    WindowManager,
)
from desktop_automation_perception.models import (
    AIInterfaceConfiguration,
    AIInterfaceElementMatch,
    AIInterfaceElementSelector,
    AIInterfaceNavigationResult,
    AIInterfaceObservation,
    AIInterfaceStatus,
    AIInterfaceSubmitMode,
    AccessibilityElement,
    InputAction,
    InputActionType,
    InputTarget,
    OCRTextBlock,
    PromptInjectionMethod,
    PromptInjectionTarget,
    SelectorStrategy,
    TemplateSearchRequest,
    WindowReference,
)


@dataclass(slots=True)
class AIInterfaceNavigator:
    prompt_injector: PromptInjector
    input_runner: InputRunner
    screenshot_backend: ScreenshotBackend
    accessibility_reader: AccessibilityReader | None = None
    ocr_extractor: OCRExtractor | None = None
    template_matcher: TemplateMatcher | None = None
    window_manager: WindowManager | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic

    def navigate(
        self,
        *,
        prompt: str,
        interface: AIInterfaceConfiguration,
        injection_method: PromptInjectionMethod = PromptInjectionMethod.CLIPBOARD,
    ) -> AIInterfaceNavigationResult:
        observations = [
            AIInterfaceObservation(
                status=AIInterfaceStatus.READY,
                detail=f"Starting navigation for {interface.interface_name}.",
            )
        ]

        input_match = self._resolve_selector(
            interface.input_selector,
            ocr_language=interface.ocr_language,
            minimum_ocr_confidence=interface.minimum_ocr_confidence,
        )
        if input_match is None:
            return AIInterfaceNavigationResult(
                succeeded=False,
                interface_name=interface.interface_name,
                status=AIInterfaceStatus.ERROR,
                prompt=prompt,
                observations=observations,
                reason="Unable to locate the AI interface input field.",
            )

        injection_result = self.prompt_injector.inject_prompt(
            prompt=prompt,
            target=self._to_prompt_target(interface.input_selector),
            method=injection_method,
            ocr_language=interface.ocr_language,
            minimum_ocr_confidence=interface.minimum_ocr_confidence,
        )
        if not injection_result.succeeded:
            observations.append(
                AIInterfaceObservation(
                    status=AIInterfaceStatus.ERROR,
                    detail=injection_result.reason or "Prompt injection failed.",
                )
            )
            return AIInterfaceNavigationResult(
                succeeded=False,
                interface_name=interface.interface_name,
                status=AIInterfaceStatus.ERROR,
                prompt=prompt,
                input_match=input_match,
                observations=observations,
                reason=injection_result.reason or "Prompt injection failed.",
            )

        submit_match = self._submit_prompt(interface, input_match)
        if interface.submit_mode is AIInterfaceSubmitMode.BUTTON and submit_match is None:
            observations.append(
                AIInterfaceObservation(
                    status=AIInterfaceStatus.ERROR,
                    detail="Configured submit button was not found.",
                )
            )
            return AIInterfaceNavigationResult(
                succeeded=False,
                interface_name=interface.interface_name,
                status=AIInterfaceStatus.ERROR,
                prompt=prompt,
                input_match=input_match,
                observations=observations,
                reason="Configured submit button was not found.",
            )

        observations.append(
            AIInterfaceObservation(
                status=AIInterfaceStatus.SUBMITTED,
                detail="Prompt submitted to the AI interface.",
            )
        )
        self.sleep_fn(interface.submit_settle_seconds)

        streaming_seen = False
        stable_polls = 0
        deadline = self.monotonic_fn() + interface.response_timeout_seconds
        response_match: AIInterfaceElementMatch | None = None

        while self.monotonic_fn() <= deadline:
            timeout_match = self._resolve_any(
                interface.session_timeout_selectors,
                ocr_language=interface.ocr_language,
                minimum_ocr_confidence=interface.minimum_ocr_confidence,
            )
            if timeout_match is not None:
                observations.append(
                    AIInterfaceObservation(
                        status=AIInterfaceStatus.SESSION_TIMEOUT,
                        detail=timeout_match.text or timeout_match.detail or "Session timeout detected.",
                    )
                )
                return AIInterfaceNavigationResult(
                    succeeded=False,
                    interface_name=interface.interface_name,
                    status=AIInterfaceStatus.SESSION_TIMEOUT,
                    prompt=prompt,
                    input_match=input_match,
                    submit_match=submit_match,
                    observations=observations,
                    reason="Session timeout or re-authentication prompt detected.",
                )

            error_match = self._resolve_any(
                interface.error_dialog_selectors,
                ocr_language=interface.ocr_language,
                minimum_ocr_confidence=interface.minimum_ocr_confidence,
            )
            if error_match is not None:
                observations.append(
                    AIInterfaceObservation(
                        status=AIInterfaceStatus.ERROR,
                        detail=error_match.text or error_match.detail or "Interface error detected.",
                    )
                )
                return AIInterfaceNavigationResult(
                    succeeded=False,
                    interface_name=interface.interface_name,
                    status=AIInterfaceStatus.ERROR,
                    prompt=prompt,
                    input_match=input_match,
                    submit_match=submit_match,
                    observations=observations,
                    reason="AI interface reported an error dialog or failure state.",
                )

            loading_match = self._resolve_any(
                interface.loading_state_selectors,
                ocr_language=interface.ocr_language,
                minimum_ocr_confidence=interface.minimum_ocr_confidence,
            )
            streaming_match = self._resolve_any(
                interface.streaming_indicator_selectors,
                ocr_language=interface.ocr_language,
                minimum_ocr_confidence=interface.minimum_ocr_confidence,
            )

            if streaming_match is not None or loading_match is not None:
                streaming_seen = True
                stable_polls = 0
                observations.append(
                    AIInterfaceObservation(
                        status=AIInterfaceStatus.STREAMING,
                        detail=(streaming_match or loading_match).text
                        or (streaming_match or loading_match).detail
                        or "Response is still streaming.",
                    )
                )
            else:
                stable_polls += 1
                if not interface.streaming_indicator_selectors:
                    streaming_seen = True

            if streaming_seen and stable_polls >= max(1, interface.stable_polls_required):
                response_match = (
                    self._resolve_selector(
                        interface.response_selector,
                        ocr_language=interface.ocr_language,
                        minimum_ocr_confidence=interface.minimum_ocr_confidence,
                    )
                    if interface.response_selector is not None
                    else None
                )
                response_text = self._extract_response_text(
                    response_match=response_match,
                    interface=interface,
                )
                if response_text:
                    observations.append(
                        AIInterfaceObservation(
                            status=AIInterfaceStatus.COMPLETED,
                            detail="Response streaming completed and text was extracted.",
                        )
                    )
                    return AIInterfaceNavigationResult(
                        succeeded=True,
                        interface_name=interface.interface_name,
                        status=AIInterfaceStatus.COMPLETED,
                        prompt=prompt,
                        response_text=response_text,
                        input_match=input_match,
                        submit_match=submit_match,
                        response_match=response_match,
                        observations=observations,
                    )

            self.sleep_fn(interface.polling_interval_seconds)

        observations.append(
            AIInterfaceObservation(
                status=AIInterfaceStatus.TIMEOUT,
                detail="Timed out while waiting for the AI response to finish.",
            )
        )
        return AIInterfaceNavigationResult(
            succeeded=False,
            interface_name=interface.interface_name,
            status=AIInterfaceStatus.TIMEOUT,
            prompt=prompt,
            input_match=input_match,
            submit_match=submit_match,
            response_match=response_match,
            observations=observations,
            reason="Timed out while waiting for the AI response to complete.",
        )

    def _submit_prompt(
        self,
        interface: AIInterfaceConfiguration,
        input_match: AIInterfaceElementMatch,
    ) -> AIInterfaceElementMatch | None:
        if interface.submit_mode in {AIInterfaceSubmitMode.AUTO, AIInterfaceSubmitMode.ENTER}:
            enter_result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.KEYPRESS,
                        target=InputTarget(
                            window=self._resolve_window(interface.input_selector),
                            element_bounds=input_match.bounds,
                        ),
                        key="enter",
                    )
                ]
            )
            if getattr(enter_result, "succeeded", True):
                return AIInterfaceElementMatch(
                    selector=interface.input_selector,
                    strategy=SelectorStrategy.DIRECT_BOUNDS,
                    bounds=input_match.bounds,
                    center=input_match.center,
                    detail="Submitted via Enter key.",
                )
            if interface.submit_mode is AIInterfaceSubmitMode.ENTER:
                return None

        if interface.submit_button_selector is None:
            return None

        button_match = self._resolve_selector(
            interface.submit_button_selector,
            ocr_language=interface.ocr_language,
            minimum_ocr_confidence=interface.minimum_ocr_confidence,
        )
        if button_match is None or button_match.center is None:
            return None

        click_result = self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.CLICK,
                    target=InputTarget(
                        window=self._resolve_window(interface.submit_button_selector),
                        element_bounds=button_match.bounds,
                    ),
                    position=button_match.center,
                )
            ]
        )
        if getattr(click_result, "succeeded", True):
            return button_match
        return None

    def _extract_response_text(
        self,
        *,
        response_match: AIInterfaceElementMatch | None,
        interface: AIInterfaceConfiguration,
    ) -> str | None:
        if response_match is not None and response_match.text:
            return self._normalize_text(response_match.text)

        if (
            response_match is not None
            and response_match.element is not None
            and self.accessibility_reader is not None
        ):
            text = self.accessibility_reader.get_element_text(response_match.element)
            if text:
                return self._normalize_text(text)

        region = None if response_match is None else response_match.bounds
        if self.ocr_extractor is not None:
            extraction = self.ocr_extractor.extract_text(
                region_of_interest=region,
                language=interface.ocr_language,
                minimum_confidence=interface.minimum_ocr_confidence,
            )
            text = self._combine_ocr_blocks(getattr(extraction, "blocks", []))
            if text:
                return self._normalize_text(text)

        return None

    def _resolve_any(
        self,
        selectors: list[AIInterfaceElementSelector],
        *,
        ocr_language: str,
        minimum_ocr_confidence: float,
    ) -> AIInterfaceElementMatch | None:
        for selector in selectors:
            match = self._resolve_selector(
                selector,
                ocr_language=ocr_language,
                minimum_ocr_confidence=minimum_ocr_confidence,
            )
            if match is not None:
                return match
        return None

    def _resolve_selector(
        self,
        selector: AIInterfaceElementSelector | None,
        *,
        ocr_language: str,
        minimum_ocr_confidence: float,
    ) -> AIInterfaceElementMatch | None:
        if selector is None:
            return None

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
                match = self._resolve_ocr(
                    selector,
                    ocr_language=ocr_language,
                    minimum_ocr_confidence=minimum_ocr_confidence,
                )
            elif strategy is SelectorStrategy.TEMPLATE_MATCH:
                match = self._resolve_template(selector)
            elif strategy is SelectorStrategy.DIRECT_BOUNDS:
                match = None
            else:
                match = None

            if match is not None:
                return match

        return None

    def _resolve_accessibility(
        self,
        selector: AIInterfaceElementSelector,
    ) -> AIInterfaceElementMatch | None:
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
        text = self.accessibility_reader.get_element_text(element)
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.ACCESSIBILITY,
            bounds=element.bounds,
            center=self._center(element.bounds),
            text=text,
            confidence=1.0,
            element=element,
            detail="Matched with accessibility tree.",
        )

    def _resolve_ocr(
        self,
        selector: AIInterfaceElementSelector,
        *,
        ocr_language: str,
        minimum_ocr_confidence: float,
    ) -> AIInterfaceElementMatch | None:
        if self.ocr_extractor is None or not selector.target_text:
            return None
        result = self.ocr_extractor.find_text(
            target=selector.target_text,
            region_of_interest=selector.region_of_interest,
            language=ocr_language,
            minimum_confidence=minimum_ocr_confidence,
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
            detail="Matched with OCR.",
        )

    def _resolve_template(
        self,
        selector: AIInterfaceElementSelector,
    ) -> AIInterfaceElementMatch | None:
        if self.template_matcher is None or not selector.template_path:
            return None
        screenshot_path = self.screenshot_backend.capture_screenshot_to_path()
        results = self.template_matcher.search(
            screenshot_path=screenshot_path,
            requests=[
                TemplateSearchRequest(
                    template_name=selector.template_name or "template",
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
            detail="Matched with template image search.",
        )

    def _to_prompt_target(
        self,
        selector: AIInterfaceElementSelector,
    ) -> PromptInjectionTarget:
        return PromptInjectionTarget(
            window_title=selector.window_title,
            process_name=selector.process_name,
            element_name=selector.name,
            element_role=selector.role,
            element_bounds=selector.bounds,
        )

    def _resolve_window(
        self,
        selector: AIInterfaceElementSelector,
    ) -> WindowReference | None:
        if selector.window_title is None and selector.process_name is None:
            return None
        if self.window_manager is None:
            return WindowReference(title=selector.window_title)
        for window in self.window_manager.list_windows():
            if selector.window_title and selector.window_title.casefold() not in window.title.casefold():
                continue
            if selector.process_name and (window.process_name or "").casefold() != selector.process_name.casefold():
                continue
            return WindowReference(title=window.title, handle=window.handle)
        return WindowReference(title=selector.window_title)

    def _combine_ocr_blocks(self, blocks: list[OCRTextBlock]) -> str:
        if not blocks:
            return ""
        ordered = sorted(blocks, key=lambda block: (block.bounds[1], block.bounds[0]))
        return "\n".join(block.text.strip() for block in ordered if block.text.strip())

    def _normalize_text(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
