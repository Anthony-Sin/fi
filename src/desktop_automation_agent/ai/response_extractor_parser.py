from __future__ import annotations

import json
import re
from dataclasses import dataclass

from desktop_automation_agent.contracts import (
    AIInterfaceNavigationExecutor,
    AccessibilityReader,
    OCRExtractor,
    ScreenshotBackend,
    TemplateMatcher,
)
from desktop_automation_agent.models import (
    AIInterfaceConfiguration,
    AIInterfaceElementMatch,
    AIInterfaceElementSelector,
    AccessibilityElement,
    OCRTextBlock,
    PromptInjectionMethod,
    ResponseExtractionAttempt,
    ResponseExtractionConfiguration,
    ResponseExtractionResult,
    ResponseProcessingMode,
    ResponseValidationMode,
    ResponseValidationResult,
    SelectorStrategy,
    TemplateSearchRequest,
)


@dataclass(slots=True)
class ResponseExtractorParser:
    screenshot_backend: ScreenshotBackend
    accessibility_reader: AccessibilityReader | None = None
    ocr_extractor: OCRExtractor | None = None
    template_matcher: TemplateMatcher | None = None
    navigator: AIInterfaceNavigationExecutor | None = None

    def extract_response(
        self,
        *,
        configuration: ResponseExtractionConfiguration,
        interface: AIInterfaceConfiguration,
        prompt: str | None = None,
        injection_method: PromptInjectionMethod = PromptInjectionMethod.CLIPBOARD,
    ) -> ResponseExtractionResult:
        attempts: list[ResponseExtractionAttempt] = []
        raw_response: str | None = None

        total_attempts = 1 + (
            configuration.max_retry_attempts if configuration.retry_on_validation_failure else 0
        )
        current_prompt = prompt

        for attempt_number in range(1, total_attempts + 1):
            if attempt_number > 1:
                if self.navigator is None or current_prompt is None:
                    break
                retry_navigation = self.navigator.navigate(
                    prompt=current_prompt + configuration.retry_instruction_suffix,
                    interface=interface,
                    injection_method=injection_method,
                )
                if not retry_navigation.succeeded:
                    attempts.append(
                        ResponseExtractionAttempt(
                            attempt_number=attempt_number,
                            prompt=current_prompt + configuration.retry_instruction_suffix,
                            retried=True,
                            reason=retry_navigation.reason or "Retry prompt submission failed.",
                        )
                    )
                    return ResponseExtractionResult(
                        succeeded=False,
                        interface_name=configuration.interface_name,
                        attempts=attempts,
                        reason=retry_navigation.reason or "Retry prompt submission failed.",
                    )
                raw_response = retry_navigation.response_text
                current_prompt = current_prompt + configuration.retry_instruction_suffix
            else:
                raw_response = self._extract_raw_response(configuration)

            processed_response, sections = self._post_process(
                raw_response=raw_response,
                configuration=configuration,
            )
            validation = self._validate_response(
                processed_response=processed_response,
                configuration=configuration,
            )
            attempts.append(
                ResponseExtractionAttempt(
                    attempt_number=attempt_number,
                    prompt=current_prompt,
                    raw_response=raw_response,
                    processed_response=processed_response,
                    sections=sections,
                    validation=validation,
                    retried=attempt_number > 1,
                    reason=validation.reason,
                )
            )
            if validation.succeeded:
                return ResponseExtractionResult(
                    succeeded=True,
                    interface_name=configuration.interface_name,
                    raw_response=raw_response,
                    processed_response=processed_response,
                    sections=sections,
                    parsed_payload=validation.parsed_payload,
                    validation=validation,
                    attempts=attempts,
                )

        last_attempt = attempts[-1] if attempts else None
        return ResponseExtractionResult(
            succeeded=False,
            interface_name=configuration.interface_name,
            raw_response=last_attempt.raw_response if last_attempt else None,
            processed_response=last_attempt.processed_response if last_attempt else None,
            sections=last_attempt.sections if last_attempt else [],
            parsed_payload=last_attempt.validation.parsed_payload if last_attempt and last_attempt.validation else None,
            validation=last_attempt.validation if last_attempt else None,
            attempts=attempts,
            reason=(last_attempt.validation.reason if last_attempt and last_attempt.validation else "Response validation failed."),
        )

    def _extract_raw_response(
        self,
        configuration: ResponseExtractionConfiguration,
    ) -> str | None:
        match = self._resolve_selector(
            configuration.response_selector,
            ocr_language=configuration.ocr_language,
            minimum_ocr_confidence=configuration.minimum_ocr_confidence,
        )
        if match is None:
            return None
        if match.text:
            return self._normalize_text(match.text)
        if match.element is not None and self.accessibility_reader is not None:
            text = self.accessibility_reader.get_element_text(match.element)
            if text:
                return self._normalize_text(text)
        if self.ocr_extractor is not None and match.bounds is not None:
            extraction = self.ocr_extractor.extract_text(
                region_of_interest=match.bounds,
                language=configuration.ocr_language,
                minimum_confidence=configuration.minimum_ocr_confidence,
            )
            text = self._combine_ocr_blocks(getattr(extraction, "blocks", []))
            if text:
                return self._normalize_text(text)
        return None

    def _post_process(
        self,
        *,
        raw_response: str | None,
        configuration: ResponseExtractionConfiguration,
    ) -> tuple[str | None, list[str]]:
        processed = raw_response
        sections: list[str] = []

        for mode in configuration.processing_modes:
            if processed is None:
                break
            if mode is ResponseProcessingMode.RAW:
                continue
            if mode is ResponseProcessingMode.STRIP_FORMATTING:
                processed = self._strip_formatting(processed)
                continue
            if mode is ResponseProcessingMode.EXTRACT_JSON_BLOCK:
                processed = self._extract_json_block(processed)
                continue
            if mode is ResponseProcessingMode.SPLIT_SECTIONS:
                sections = self._split_sections(processed, configuration.section_delimiters)
                continue

        return processed, sections

    def _validate_response(
        self,
        *,
        processed_response: str | None,
        configuration: ResponseExtractionConfiguration,
    ) -> ResponseValidationResult:
        if configuration.validation_mode is ResponseValidationMode.NONE:
            return ResponseValidationResult(succeeded=processed_response is not None, mode=configuration.validation_mode)

        if processed_response is None:
            return ResponseValidationResult(
                succeeded=False,
                mode=configuration.validation_mode,
                reason="No response text was extracted from the interface.",
            )

        if configuration.validation_mode is ResponseValidationMode.REGEX:
            matched = (
                configuration.expected_pattern is not None
                and re.search(configuration.expected_pattern, processed_response, re.MULTILINE) is not None
            )
            return ResponseValidationResult(
                succeeded=bool(matched),
                mode=configuration.validation_mode,
                reason=None if matched else "Response did not match the expected pattern.",
            )

        if configuration.validation_mode is ResponseValidationMode.JSON:
            try:
                payload = json.loads(processed_response)
            except json.JSONDecodeError as exc:
                return ResponseValidationResult(
                    succeeded=False,
                    mode=configuration.validation_mode,
                    reason=f"Response is not valid JSON: {exc.msg}",
                )
            return ResponseValidationResult(
                succeeded=True,
                mode=configuration.validation_mode,
                parsed_payload=payload,
            )

        if configuration.validation_mode is ResponseValidationMode.JSON_SCHEMA_LITE:
            try:
                payload = json.loads(processed_response)
            except json.JSONDecodeError as exc:
                return ResponseValidationResult(
                    succeeded=False,
                    mode=configuration.validation_mode,
                    reason=f"Response is not valid JSON: {exc.msg}",
                )
            if not isinstance(payload, dict):
                return ResponseValidationResult(
                    succeeded=False,
                    mode=configuration.validation_mode,
                    reason="Expected a top-level JSON object for schema validation.",
                )
            schema_error = self._validate_json_schema_lite(payload, configuration.expected_schema)
            return ResponseValidationResult(
                succeeded=schema_error is None,
                mode=configuration.validation_mode,
                reason=schema_error,
                parsed_payload=payload,
            )

        return ResponseValidationResult(
            succeeded=False,
            mode=configuration.validation_mode,
            reason="Unsupported response validation mode.",
        )

    def _validate_json_schema_lite(
        self,
        payload: dict,
        schema: dict[str, str],
    ) -> str | None:
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        for key, expected_type in schema.items():
            if key not in payload:
                return f"Missing required field: {key}"
            python_type = type_map.get(expected_type.casefold())
            if python_type is None:
                return f"Unsupported schema type: {expected_type}"
            if not isinstance(payload[key], python_type):
                return f"Field {key!r} is not of expected type {expected_type}."
        return None

    def _resolve_selector(
        self,
        selector: AIInterfaceElementSelector,
        *,
        ocr_language: str,
        minimum_ocr_confidence: float,
    ) -> AIInterfaceElementMatch | None:
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
                match = self._resolve_ocr(selector, ocr_language, minimum_ocr_confidence)
            elif strategy is SelectorStrategy.TEMPLATE_MATCH:
                match = self._resolve_template(selector)
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
        )

    def _resolve_ocr(
        self,
        selector: AIInterfaceElementSelector,
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
                    template_name=selector.template_name or "response",
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

    def _strip_formatting(self, text: str) -> str:
        stripped = re.sub(r"```(?:json|python|text)?\s*([\s\S]*?)```", r"\1", text)
        stripped = re.sub(r"`([^`]+)`", r"\1", stripped)
        stripped = re.sub(r"[*_#>-]+", " ", stripped)
        stripped = re.sub(r"\n{3,}", "\n\n", stripped)
        return stripped.strip()

    def _extract_json_block(self, text: str) -> str:
        fenced_match = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fenced_match:
            return fenced_match.group(1).strip()
        inline_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if inline_match:
            return inline_match.group(1).strip()
        return text

    def _split_sections(self, text: str, delimiters: tuple[str, ...]) -> list[str]:
        if not delimiters:
            parts = re.split(r"\n\s*\n", text)
        else:
            pattern = "|".join(re.escape(delimiter) for delimiter in delimiters)
            parts = re.split(pattern, text)
        return [part.strip() for part in parts if part.strip()]

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
