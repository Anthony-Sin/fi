from desktop_automation_agent.models import (
    AIInterfaceConfiguration,
    AIInterfaceElementSelector,
    AccessibilityElement,
    AccessibilityElementState,
    PromptInjectionMethod,
    ResponseExtractionConfiguration,
    ResponseProcessingMode,
    ResponseValidationMode,
    SelectorStrategy,
)
from desktop_automation_agent.response_extractor_parser import ResponseExtractorParser


class FakeAccessibilityReader:
    def __init__(self, mapping):
        self.mapping = mapping

    def find_elements(self, *, name=None, role=None, value=None):
        return type("Query", (), {"matches": self.mapping.get((name, role, value), [])})()

    def get_element_text(self, element):
        return element.state.text or element.value or element.name


class FakeOCRExtractor:
    def __init__(self, extraction_text=None):
        self.extraction_text = extraction_text or []

    def find_text(self, *, target, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        return type("OCRMatch", (), {"succeeded": False})()

    def extract_text(self, *, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        blocks = self.extraction_text.pop(0) if self.extraction_text else []
        return type("Extraction", (), {"blocks": blocks})()


class FakeTemplateMatcher:
    def search(self, *, screenshot_path, requests):
        return []


class FakeScreenshotBackend:
    def capture_screenshot_to_path(self, path=None):
        return "screen.png"


class FakeNavigator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def navigate(self, *, prompt, interface, injection_method=PromptInjectionMethod.CLIPBOARD):
        self.calls.append((prompt, interface.interface_name, injection_method))
        response_text, succeeded, reason = self.responses.pop(0)
        return type("NavigationResult", (), {"response_text": response_text, "succeeded": succeeded, "reason": reason})()


def make_element(name, role, bounds, text):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        bounds=bounds,
        state=AccessibilityElementState(text=text, enabled=True),
    )


def make_interface():
    return AIInterfaceConfiguration(
        interface_name="chatgpt",
        input_selector=AIInterfaceElementSelector(name="Message", role="edit"),
        response_selector=AIInterfaceElementSelector(name="Answer", role="document"),
    )


def test_response_extractor_captures_raw_text_from_response_container():
    response = make_element("Answer", "document", (10, 10, 200, 200), "Plain response")
    extractor = ResponseExtractorParser(
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader({("Answer", "document", None): [response]}),
        ocr_extractor=FakeOCRExtractor(),
        template_matcher=FakeTemplateMatcher(),
    )

    result = extractor.extract_response(
        configuration=ResponseExtractionConfiguration(
            interface_name="chatgpt",
            response_selector=AIInterfaceElementSelector(name="Answer", role="document"),
        ),
        interface=make_interface(),
    )

    assert result.succeeded is True
    assert result.raw_response == "Plain response"
    assert result.processed_response == "Plain response"


def test_response_extractor_strips_formatting_extracts_json_and_validates_schema():
    response = make_element(
        "Answer",
        "document",
        (10, 10, 200, 200),
        "```json\n{\"status\":\"ok\",\"count\":2}\n```",
    )
    extractor = ResponseExtractorParser(
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader({("Answer", "document", None): [response]}),
        ocr_extractor=FakeOCRExtractor(),
        template_matcher=FakeTemplateMatcher(),
    )

    result = extractor.extract_response(
        configuration=ResponseExtractionConfiguration(
            interface_name="chatgpt",
            response_selector=AIInterfaceElementSelector(name="Answer", role="document"),
            processing_modes=(
                ResponseProcessingMode.STRIP_FORMATTING,
                ResponseProcessingMode.EXTRACT_JSON_BLOCK,
            ),
            validation_mode=ResponseValidationMode.JSON_SCHEMA_LITE,
            expected_schema={"status": "string", "count": "integer"},
        ),
        interface=make_interface(),
    )

    assert result.succeeded is True
    assert result.processed_response == "{\"status\":\"ok\",\"count\":2}"
    assert result.parsed_payload == {"status": "ok", "count": 2}


def test_response_extractor_splits_sections():
    response = make_element("Answer", "document", (10, 10, 200, 200), "Intro\n\nBody\n\nConclusion")
    extractor = ResponseExtractorParser(
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader({("Answer", "document", None): [response]}),
    )

    result = extractor.extract_response(
        configuration=ResponseExtractionConfiguration(
            interface_name="chatgpt",
            response_selector=AIInterfaceElementSelector(name="Answer", role="document"),
            processing_modes=(ResponseProcessingMode.SPLIT_SECTIONS,),
        ),
        interface=make_interface(),
    )

    assert result.succeeded is True
    assert result.sections == ["Intro", "Body", "Conclusion"]


def test_response_extractor_logs_invalid_raw_response_and_retries_with_format_instruction():
    response = make_element("Answer", "document", (10, 10, 200, 200), "not json")
    navigator = FakeNavigator([('{"status":"ok"}', True, None)])
    extractor = ResponseExtractorParser(
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader({("Answer", "document", None): [response]}),
        navigator=navigator,
    )

    result = extractor.extract_response(
        configuration=ResponseExtractionConfiguration(
            interface_name="chatgpt",
            response_selector=AIInterfaceElementSelector(name="Answer", role="document"),
            processing_modes=(ResponseProcessingMode.EXTRACT_JSON_BLOCK,),
            validation_mode=ResponseValidationMode.JSON,
            retry_on_validation_failure=True,
            max_retry_attempts=1,
            retry_instruction_suffix=" Please return valid JSON only.",
        ),
        interface=make_interface(),
        prompt="Summarize this",
    )

    assert result.succeeded is True
    assert result.attempts[0].raw_response == "not json"
    assert result.attempts[0].validation is not None
    assert result.attempts[0].validation.succeeded is False
    assert navigator.calls[0][0] == "Summarize this Please return valid JSON only."
    assert result.parsed_payload == {"status": "ok"}


def test_response_extractor_fails_without_retry_when_validation_does_not_match():
    response = make_element("Answer", "document", (10, 10, 200, 200), "alpha")
    extractor = ResponseExtractorParser(
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader({("Answer", "document", None): [response]}),
    )

    result = extractor.extract_response(
        configuration=ResponseExtractionConfiguration(
            interface_name="chatgpt",
            response_selector=AIInterfaceElementSelector(
                name="Answer",
                role="document",
                strategies=(SelectorStrategy.ACCESSIBILITY,),
            ),
            validation_mode=ResponseValidationMode.REGEX,
            expected_pattern=r"^\d+$",
        ),
        interface=make_interface(),
    )

    assert result.succeeded is False
    assert result.reason == "Response did not match the expected pattern."
