from desktop_automation_agent.form_automation import FormAutomationModule
from desktop_automation_agent.models import (
    AccessibilityElement,
    AccessibilityElementState,
    AccessibilityTree,
    FormFieldType,
    FormFieldValue,
    OCRTextBlock,
)


class FakeInputRunner:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        succeeded = self.results.pop(0) if self.results else True
        return type("RunResult", (), {"succeeded": succeeded, "failure_reason": None if succeeded else "input failed"})()


class FakeAccessibilityReader:
    def __init__(self, root, mapping=None):
        self.root = root
        self.mapping = mapping or {}

    def read_active_application_tree(self):
        return AccessibilityTree(application_name="Form App", root=self.root)

    def find_elements(self, *, name=None, role=None, value=None):
        matches = self.mapping.get((name, role, value), [])
        return type("Query", (), {"matches": matches})()

    def get_element_text(self, element):
        return element.state.text or element.value or element.name

    def is_element_selected(self, element):
        return element.state.selected


class FakeOCRExtractor:
    def __init__(self, blocks=None):
        self.blocks = list(blocks or [])

    def extract_text(self, *, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        blocks = self.blocks.pop(0) if self.blocks else []
        return type("Extraction", (), {"blocks": blocks})()


def make_element(name, role, bounds, *, value=None, text=None, selected=None, children=None):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        value=value,
        bounds=bounds,
        state=AccessibilityElementState(text=text, enabled=True, selected=selected),
        children=list(children or []),
    )


def test_form_automation_fills_text_field_and_verifies_value():
    """Verifies that the form automation module can locate a text field by its associated label,
    type the specified value, and confirm the value was correctly entered using accessibility text."""
    field = make_element("First Name", "edit", (100, 10, 240, 40), text="Alice")
    label = make_element("First Name", "text", (10, 10, 80, 40))
    root = make_element("Form", "window", (0, 0, 400, 400), children=[label, field])
    module = FormAutomationModule(
        input_runner=FakeInputRunner(),
        accessibility_reader=FakeAccessibilityReader(root),
    )

    result = module.fill_form([FormFieldValue(label="First Name", value="Alice", field_type=FormFieldType.TEXT)])

    assert result.succeeded is True
    assert result.field_results[0].actual_value == "Alice"
    assert module.input_runner.calls[1][2].text == "Alice"


def test_form_automation_handles_dropdown_field():
    """Verifies that the module can interact with dropdown (combo box) elements by expanding
    them and selecting the matching list item."""
    option = make_element("Canada", "list item", (120, 80, 220, 110), text="Canada")
    dropdown = make_element("Country", "combo box", (100, 10, 240, 40), text="Canada")
    label = make_element("Country", "text", (10, 10, 80, 40))
    root = make_element("Form", "window", (0, 0, 400, 400), children=[label, dropdown, option])
    module = FormAutomationModule(
        input_runner=FakeInputRunner(),
        accessibility_reader=FakeAccessibilityReader(
            root,
            mapping={
                ("Canada", "list item", None): [option],
            },
        ),
    )

    result = module.fill_form([FormFieldValue(label="Country", value="Canada", field_type=FormFieldType.DROPDOWN)])

    assert result.succeeded is True
    assert result.field_results[0].actual_value == "Canada"


def test_form_automation_handles_checkbox_and_radio_fields():
    """Verifies that the module can correctly toggle checkboxes and select radio buttons,
    validating their 'selected' state via accessibility APIs."""
    checkbox = make_element("Subscribe", "checkbox", (100, 10, 140, 40), selected=True)
    radio = make_element("Premium", "radio button", (100, 50, 140, 80), selected=True)
    label_checkbox = make_element("Subscribe", "text", (10, 10, 80, 40))
    label_radio = make_element("Premium", "text", (10, 50, 80, 80))
    root = make_element("Form", "window", (0, 0, 400, 400), children=[label_checkbox, checkbox, label_radio, radio])
    module = FormAutomationModule(
        input_runner=FakeInputRunner(),
        accessibility_reader=FakeAccessibilityReader(root),
    )

    result = module.fill_form(
        [
            FormFieldValue(label="Subscribe", value=True, field_type=FormFieldType.CHECKBOX),
            FormFieldValue(label="Premium", value=True, field_type=FormFieldType.RADIO),
        ]
    )

    assert result.succeeded is True
    assert result.field_results[0].actual_value is True
    assert result.field_results[1].actual_value is True


def test_form_automation_handles_date_field_as_text_entry():
    """Verifies that date picker fields can be treated as text entry fields for direct
    input of formatted date strings."""
    field = make_element("Start Date", "date picker", (100, 10, 240, 40), text="2026-04-08")
    label = make_element("Start Date", "text", (10, 10, 80, 40))
    root = make_element("Form", "window", (0, 0, 400, 400), children=[label, field])
    module = FormAutomationModule(
        input_runner=FakeInputRunner(),
        accessibility_reader=FakeAccessibilityReader(root),
    )

    result = module.fill_form([FormFieldValue(label="Start Date", value="2026-04-08", field_type=FormFieldType.DATE)])

    assert result.succeeded is True
    assert result.field_results[0].actual_value == "2026-04-08"


def test_form_automation_uses_ocr_for_verification_when_accessibility_text_missing():
    """Verifies that the module falls back to OCR-based verification if a field's
    content cannot be read through the standard accessibility tree."""
    field = make_element("Notes", "edit", (100, 10, 240, 60), text=None, value=None)
    label = make_element("Notes", "text", (10, 10, 80, 40))
    root = make_element("Form", "window", (0, 0, 400, 400), children=[label, field])
    module = FormAutomationModule(
        input_runner=FakeInputRunner(),
        accessibility_reader=FakeAccessibilityReader(root),
        ocr_extractor=FakeOCRExtractor(
            blocks=[[OCRTextBlock(text="Filled note", confidence=0.9, bounds=(1, 1, 40, 20))]]
        ),
    )

    result = module.fill_form([FormFieldValue(label="Notes", value="Filled note", field_type=FormFieldType.TEXT)])

    assert result.succeeded is True
    assert result.field_results[0].actual_value == "Filled note"


def test_form_automation_reports_verification_failure():
    """Verifies that the module correctly identifies and reports a failure if the value
    actually present in the field after entry does not match the requested value."""
    field = make_element("Email", "edit", (100, 10, 240, 40), text="wrong@example.com")
    label = make_element("Email", "text", (10, 10, 80, 40))
    root = make_element("Form", "window", (0, 0, 400, 400), children=[label, field])
    module = FormAutomationModule(
        input_runner=FakeInputRunner(),
        accessibility_reader=FakeAccessibilityReader(root),
    )

    result = module.fill_form([FormFieldValue(label="Email", value="user@example.com", field_type=FormFieldType.TEXT)])

    assert result.succeeded is False
    assert result.field_results[0].reason == "Field verification failed after entry."
