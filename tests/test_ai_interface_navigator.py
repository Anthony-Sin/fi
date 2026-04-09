from desktop_automation_agent.ai_interface_navigator import AIInterfaceNavigator
from desktop_automation_agent.models import (
    AIInterfaceConfiguration,
    AIInterfaceElementSelector,
    AIInterfaceStatus,
    AIInterfaceSubmitMode,
    AccessibilityElement,
    AccessibilityElementState,
    InputAction,
    OCRTextBlock,
    PromptInjectionMethod,
)


class FakePromptInjector:
    def __init__(self, succeeded=True, reason=None):
        self.succeeded = succeeded
        self.reason = reason
        self.calls = []

    def inject_prompt(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "InjectionResult",
            (),
            {"succeeded": self.succeeded, "reason": self.reason},
        )()


class FakeInputRunner:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.runs: list[list[InputAction]] = []

    def run(self, actions):
        self.runs.append(actions)
        succeeded = self.results.pop(0) if self.results else True
        return type("RunResult", (), {"succeeded": succeeded, "failure_reason": None if succeeded else "failed"})()


class FakeAccessibilityReader:
    def __init__(self, mapping):
        self.mapping = mapping

    def find_elements(self, *, name=None, role=None, value=None):
        matches = self.mapping.get((name, role, value), [])
        return type("Query", (), {"matches": matches})()

    def get_element_text(self, element):
        return element.state.text or element.value or element.name


class FakeOCRExtractor:
    def __init__(self, text_matches=None, extraction_blocks=None):
        self.text_matches = list(text_matches or [])
        self.extraction_blocks = list(extraction_blocks or [])

    def find_text(
        self,
        *,
        target,
        screenshot_path=None,
        region_of_interest=None,
        language="eng",
        minimum_confidence=0.0,
    ):
        if self.text_matches:
            payload = self.text_matches.pop(0)
            return type("OCRMatch", (), payload)()
        return type("OCRMatch", (), {"succeeded": False, "bounds": None, "confidence": 0.0, "matched_text": None})()

    def extract_text(
        self,
        *,
        screenshot_path=None,
        region_of_interest=None,
        language="eng",
        minimum_confidence=0.0,
    ):
        blocks = self.extraction_blocks.pop(0) if self.extraction_blocks else []
        return type("Extraction", (), {"blocks": blocks})()


class FakeTemplateMatcher:
    def __init__(self, results=None):
        self.results = list(results or [])

    def search(self, *, screenshot_path, requests):
        return self.results.pop(0) if self.results else []


class FakeScreenshotBackend:
    def __init__(self):
        self.calls = 0

    def capture_screenshot_to_path(self, path=None):
        self.calls += 1
        return f"screen-{self.calls}.png"


class FakeWindowManager:
    def list_windows(self):
        return [
            type(
                "Window",
                (),
                {"title": "ChatGPT", "process_name": "chrome.exe", "handle": 22},
            )()
        ]


def make_element(name, role, bounds, *, text=None):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        bounds=bounds,
        state=AccessibilityElementState(text=text, enabled=True),
    )


def test_ai_interface_navigator_submits_and_extracts_completed_response():
    input_field = make_element("Message", "edit", (10, 10, 210, 80))
    response_panel = make_element("Conversation", "document", (20, 100, 500, 500), text="Final answer")
    navigator = AIInterfaceNavigator(
        prompt_injector=FakePromptInjector(),
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Message", "edit", None): [input_field],
                ("Conversation", "document", None): [response_panel],
            }
        ),
        ocr_extractor=FakeOCRExtractor(),
        template_matcher=FakeTemplateMatcher(),
        window_manager=FakeWindowManager(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2, 0.3]).__next__,
    )

    result = navigator.navigate(
        prompt="Summarize this.",
        interface=AIInterfaceConfiguration(
            interface_name="chatgpt",
            input_selector=AIInterfaceElementSelector(
                name="Message",
                role="edit",
                window_title="ChatGPT",
                process_name="chrome.exe",
            ),
            submit_mode=AIInterfaceSubmitMode.ENTER,
            response_selector=AIInterfaceElementSelector(name="Conversation", role="document"),
            streaming_indicator_selectors=[],
            stable_polls_required=1,
            response_timeout_seconds=1.0,
        ),
        injection_method=PromptInjectionMethod.TYPE,
    )

    assert result.succeeded is True
    assert result.status is AIInterfaceStatus.COMPLETED
    assert result.response_text == "Final answer"
    assert navigator.input_runner.runs[0][0].key == "enter"


def test_ai_interface_navigator_uses_submit_button_when_enter_fails():
    input_field = make_element("Prompt", "edit", (10, 10, 210, 80))
    button = make_element("Send", "button", (220, 20, 260, 60))
    navigator = AIInterfaceNavigator(
        prompt_injector=FakePromptInjector(),
        input_runner=FakeInputRunner(results=[False, True]),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Prompt", "edit", None): [input_field],
                ("Send", "button", None): [button],
            }
        ),
        ocr_extractor=FakeOCRExtractor(
            extraction_blocks=[[OCRTextBlock(text="Done", confidence=0.9, bounds=(1, 1, 40, 20))]]
        ),
        template_matcher=FakeTemplateMatcher(),
        window_manager=FakeWindowManager(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2, 0.3]).__next__,
    )

    result = navigator.navigate(
        prompt="Go",
        interface=AIInterfaceConfiguration(
            interface_name="claude",
            input_selector=AIInterfaceElementSelector(name="Prompt", role="edit"),
            submit_mode=AIInterfaceSubmitMode.AUTO,
            submit_button_selector=AIInterfaceElementSelector(name="Send", role="button"),
            stable_polls_required=1,
            response_timeout_seconds=1.0,
        ),
    )

    assert result.succeeded is True
    assert result.submit_match is not None
    assert result.submit_match.selector.name == "Send"
    assert navigator.input_runner.runs[1][0].action_type.value == "click"


def test_ai_interface_navigator_detects_streaming_error_dialog():
    input_field = make_element("Message", "edit", (10, 10, 210, 80))
    error = make_element("Something went wrong", "dialog", (50, 50, 250, 140), text="Something went wrong")
    navigator = AIInterfaceNavigator(
        prompt_injector=FakePromptInjector(),
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Message", "edit", None): [input_field],
                ("Something went wrong", "dialog", None): [error],
            }
        ),
        ocr_extractor=FakeOCRExtractor(),
        template_matcher=FakeTemplateMatcher(),
        window_manager=FakeWindowManager(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2]).__next__,
    )

    result = navigator.navigate(
        prompt="Retry",
        interface=AIInterfaceConfiguration(
            interface_name="chat-ui",
            input_selector=AIInterfaceElementSelector(name="Message", role="edit"),
            error_dialog_selectors=[AIInterfaceElementSelector(name="Something went wrong", role="dialog")],
            stable_polls_required=1,
            response_timeout_seconds=1.0,
        ),
    )

    assert result.succeeded is False
    assert result.status is AIInterfaceStatus.ERROR
    assert "error dialog" in (result.reason or "").casefold()


def test_ai_interface_navigator_detects_session_timeout():
    input_field = make_element("Message", "edit", (10, 10, 210, 80))
    expired = make_element("Sign in", "dialog", (50, 50, 250, 140), text="Please sign in")
    navigator = AIInterfaceNavigator(
        prompt_injector=FakePromptInjector(),
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Message", "edit", None): [input_field],
                ("Sign in", "dialog", None): [expired],
            }
        ),
        ocr_extractor=FakeOCRExtractor(),
        template_matcher=FakeTemplateMatcher(),
        window_manager=FakeWindowManager(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2]).__next__,
    )

    result = navigator.navigate(
        prompt="Continue",
        interface=AIInterfaceConfiguration(
            interface_name="gemini",
            input_selector=AIInterfaceElementSelector(name="Message", role="edit"),
            session_timeout_selectors=[AIInterfaceElementSelector(name="Sign in", role="dialog")],
            stable_polls_required=1,
            response_timeout_seconds=1.0,
        ),
    )

    assert result.succeeded is False
    assert result.status is AIInterfaceStatus.SESSION_TIMEOUT


def test_ai_interface_navigator_times_out_when_streaming_indicator_never_clears():
    input_field = make_element("Message", "edit", (10, 10, 210, 80))
    spinner = make_element("Stop generating", "button", (220, 20, 280, 60), text="Stop generating")
    navigator = AIInterfaceNavigator(
        prompt_injector=FakePromptInjector(),
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Message", "edit", None): [input_field],
                ("Stop generating", "button", None): [spinner],
            }
        ),
        ocr_extractor=FakeOCRExtractor(),
        template_matcher=FakeTemplateMatcher(),
        window_manager=FakeWindowManager(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5]).__next__,
    )

    result = navigator.navigate(
        prompt="Wait",
        interface=AIInterfaceConfiguration(
            interface_name="perplexity",
            input_selector=AIInterfaceElementSelector(name="Message", role="edit"),
            streaming_indicator_selectors=[AIInterfaceElementSelector(name="Stop generating", role="button")],
            stable_polls_required=1,
            response_timeout_seconds=0.35,
            polling_interval_seconds=0.1,
        ),
    )

    assert result.succeeded is False
    assert result.status is AIInterfaceStatus.TIMEOUT
