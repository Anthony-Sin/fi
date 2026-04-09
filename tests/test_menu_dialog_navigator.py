from desktop_automation_agent.menu_dialog_navigator import MenuDialogNavigator
from desktop_automation_agent.models import (
    AIInterfaceElementSelector,
    AccessibilityElement,
    AccessibilityElementState,
    DialogClassification,
    DialogHandlingRequest,
    DialogResponse,
    MenuNavigationRequest,
    SelectorStrategy,
    UnexpectedDialogPolicy,
)
from itertools import count


class FakeInputRunner:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        succeeded = self.results.pop(0) if self.results else True
        return type("RunResult", (), {"succeeded": succeeded, "failure_reason": None if succeeded else "click failed"})()


class FakeAccessibilityReader:
    def __init__(self, mapping):
        self.mapping = mapping

    def find_elements(self, *, name=None, role=None, value=None):
        return type("Query", (), {"matches": self.mapping.get((name, role, value), [])})()

    def get_element_text(self, element):
        return element.state.text or element.value or element.name


class FakeOCRExtractor:
    def __init__(self, extraction_blocks=None):
        self.extraction_blocks = list(extraction_blocks or [])

    def find_text(self, *, target, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        return type("OCRMatch", (), {"succeeded": False})()

    def extract_text(self, *, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        blocks = self.extraction_blocks.pop(0) if self.extraction_blocks else []
        return type("Extraction", (), {"blocks": blocks})()


class FakeTemplateMatcher:
    def search(self, *, screenshot_path, requests):
        return []


class FakeScreenshotBackend:
    def __init__(self):
        self.count = 0

    def capture_screenshot_to_path(self, path=None):
        self.count += 1
        return f"screen-{self.count}.png"


class FakeWindowManager:
    def __init__(self, windows=None):
        self.windows = windows or []

    def list_windows(self):
        return self.windows


def make_element(name, role, bounds=(10, 10, 40, 40), *, text=None):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        bounds=bounds,
        state=AccessibilityElementState(text=text, enabled=True),
    )


def make_clock(step=0.1):
    ticks = count()
    return lambda: next(ticks) * step


def test_menu_dialog_navigator_selects_native_menu_item_by_label():
    menu = make_element("File", "menu item")
    navigator = MenuDialogNavigator(
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader({("File", "menu item", None): [menu]}),
        window_manager=FakeWindowManager([type("Window", (), {"title": "Editor", "focused": True})()]),
        sleep_fn=lambda _: None,
        monotonic_fn=make_clock(),
    )

    result = navigator.select_menu_item(MenuNavigationRequest(menu_label="File"))

    assert result.succeeded is True
    assert result.logs[0].before_state is not None
    assert result.logs[0].after_state is not None
    assert navigator.input_runner.calls[0][0].position == (25, 25)


def test_menu_dialog_navigator_falls_back_to_custom_menu_selector():
    custom = make_element("Open Project", "button")
    navigator = MenuDialogNavigator(
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Open Project", "button", None): [custom],
            }
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=make_clock(),
    )

    result = navigator.select_menu_item(
        MenuNavigationRequest(
            menu_label="Open",
            menu_selector=AIInterfaceElementSelector(name="Open", role="menu item"),
            custom_menu_selector=AIInterfaceElementSelector(name="Open Project", role="button"),
        )
    )

    assert result.succeeded is True
    assert navigator.input_runner.calls[0][0].position == (25, 25)


def test_menu_dialog_navigator_accepts_dialog():
    dialog = make_element("Confirm Save", "dialog", text="Are you sure you want to save?")
    ok_button = make_element("OK", "button")
    navigator = MenuDialogNavigator(
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Confirm Save", "dialog", None): [dialog],
                ("OK", "button", None): [ok_button],
            }
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=make_clock(),
    )

    result = navigator.handle_dialog(
        DialogHandlingRequest(
            dialog_selector=AIInterfaceElementSelector(name="Confirm Save", role="dialog"),
            response=DialogResponse.ACCEPT,
        )
    )

    assert result.succeeded is True
    assert result.logs[0].classification is DialogClassification.CONFIRMATION
    assert result.logs[0].response is DialogResponse.ACCEPT


def test_menu_dialog_navigator_supports_custom_dialog_response():
    dialog = make_element("Export Options", "dialog", text="Choose export target")
    custom_button = make_element("Overwrite", "button")
    navigator = MenuDialogNavigator(
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Export Options", "dialog", None): [dialog],
                ("Overwrite", "button", None): [custom_button],
            }
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=make_clock(),
    )

    result = navigator.handle_dialog(
        DialogHandlingRequest(
            dialog_selector=AIInterfaceElementSelector(name="Export Options", role="dialog"),
            response=DialogResponse.CUSTOM,
            custom_response_selector=AIInterfaceElementSelector(name="Overwrite", role="button"),
        )
    )

    assert result.succeeded is True
    assert result.logs[0].response is DialogResponse.CUSTOM


def test_menu_dialog_navigator_handles_unexpected_dialog_with_default_policy():
    dialog = make_element("Unsaved Changes", "dialog", text="Warning: unsaved changes")
    cancel_button = make_element("Cancel", "button")
    navigator = MenuDialogNavigator(
        input_runner=FakeInputRunner(),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(
            {
                ("Unsaved Changes", "dialog", None): [dialog],
                ("Cancel", "button", None): [cancel_button],
            }
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=make_clock(),
    )

    result = navigator.handle_unexpected_dialogs(
        dialog_selectors=[
            AIInterfaceElementSelector(
                name="Unsaved Changes",
                role="dialog",
                strategies=(SelectorStrategy.ACCESSIBILITY,),
            )
        ],
        policy=UnexpectedDialogPolicy(
            default_response=DialogResponse.CANCEL,
            response_by_classification={DialogClassification.WARNING: DialogResponse.CANCEL},
        ),
    )

    assert result.succeeded is True
    assert result.logs[0].classification is DialogClassification.WARNING
    assert result.logs[0].response is DialogResponse.CANCEL
