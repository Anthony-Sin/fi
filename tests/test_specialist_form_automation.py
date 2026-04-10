import unittest.mock as mock
from desktop_automation_agent.automation.form_automation import FormAutomationModule
from desktop_automation_agent.desktop.input_simulator import SafeInputSimulator, PyAutoGUIBackend
from desktop_automation_agent.models import (
    AccessibilityElement,
    AccessibilityElementState,
    FormFieldType,
    FormFieldValue,
    ScreenBounds
)

class FakeAccessibilityReader:
    def __init__(self, element):
        self.element = element
    def find_elements(self, **kwargs):
        return mock.MagicMock(matches=[self.element])
    def get_element_text(self, element):
        return element.state.text
    def is_element_selected(self, element):
        return element.state.selected
    def read_active_application_tree(self):
        return mock.MagicMock(root=self.element)

def test_form_automation_pyautogui_mocked():
    """Verifies that FormAutomationModule, using SafeInputSimulator and PyAutoGUIBackend,
    triggers the correct sequence of mocked pyautogui calls (click, write, press)
    when filling a text field."""

    mock_pyautogui = mock.MagicMock()
    backend = PyAutoGUIBackend(_module=mock_pyautogui)

    # Setup mocks for input runner dependencies
    mock_window_manager = mock.MagicMock()
    mock_window_manager.get_focused_window.return_value = mock.MagicMock(focused=True)

    mock_screen_inspector = mock.MagicMock()
    mock_screen_inspector.get_screen_bounds.return_value = ScreenBounds(width=1920, height=1080)

    input_runner = SafeInputSimulator(
        backend=backend,
        window_manager=mock_window_manager,
        screen_inspector=mock_screen_inspector
    )

    # Setup accessibility element for the form field
    field_element = AccessibilityElement(
        element_id="name-field",
        name="Name",
        role="edit",
        bounds=(100, 100, 300, 140),
        state=AccessibilityElementState(text="Alice", enabled=True)
    )

    reader = FakeAccessibilityReader(field_element)
    module = FormAutomationModule(input_runner=input_runner, accessibility_reader=reader)

    field_value = FormFieldValue(
        label="Name",
        value="Alice",
        field_type=FormFieldType.TEXT
    )

    result = module.fill_form([field_value])

    assert result.succeeded is True

    # Verify pyautogui calls
    # 1. Click to focus (center of bounds 100,100,300,140 is 200,120)
    mock_pyautogui.click.assert_called_with(x=200, y=120, button="left")

    # 2. Ctrl+A to select all (from clear logic)
    mock_pyautogui.hotkey.assert_called_with("ctrl", "a")

    # 3. Delete key (from clear logic)
    mock_pyautogui.press.assert_called_with("delete")

    # 4. Write "Alice"
    mock_pyautogui.write.assert_called_with("Alice")
