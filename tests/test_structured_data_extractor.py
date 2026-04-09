from desktop_automation_perception.models import (
    AIInterfaceElementSelector,
    AccessibilityElement,
    AccessibilityElementState,
    AccessibilityTree,
    OCRTextBlock,
    PaginationConfiguration,
    StructuredDataExtractionConfiguration,
    StructuredDataExtractionMode,
    StructuredDataFieldSchema,
    StructuredDataFieldType,
    StructuredDataSchema,
)
from desktop_automation_perception.structured_data_extractor import StructuredDataExtractor


class FakeAccessibilityReader:
    def __init__(self, root=None, mapping=None):
        self.root = root
        self.mapping = mapping or {}

    def read_active_application_tree(self):
        return AccessibilityTree(application_name="App", root=self.root)

    def find_elements(self, *, name=None, role=None, value=None):
        return type("Query", (), {"matches": self.mapping.get((name, role, value), [])})()

    def get_element_text(self, element):
        return element.state.text or element.value or element.name


class FakeOCRExtractor:
    def __init__(self, blocks=None, find_mapping=None):
        self.blocks = list(blocks or [])
        self.find_mapping = find_mapping or {}

    def extract_text(self, *, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        blocks = self.blocks.pop(0) if self.blocks else []
        return type("Extraction", (), {"blocks": blocks})()

    def find_text(self, *, target, screenshot_path=None, region_of_interest=None, language="eng", minimum_confidence=0.0):
        result = self.find_mapping.get(target)
        if result is None:
            return type("OCRMatch", (), {"succeeded": False, "bounds": None, "matched_text": None, "confidence": 0.0})()
        return type(
            "OCRMatch",
            (),
            {
                "succeeded": True,
                "bounds": result["bounds"],
                "matched_text": result.get("matched_text", target),
                "confidence": result.get("confidence", 0.9),
            },
        )()


class FakeInputRunner:
    def __init__(self):
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        return type("RunResult", (), {"succeeded": True, "failure_reason": None})()


def make_element(name, role, bounds, *, text=None, value=None, children=None):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        bounds=bounds,
        value=value,
        state=AccessibilityElementState(text=text, enabled=True),
        children=list(children or []),
    )


def test_structured_data_extractor_extracts_table_rows_from_ocr():
    extractor = StructuredDataExtractor(
        ocr_extractor=FakeOCRExtractor(
            blocks=[
                [
                    OCRTextBlock(text="Name", confidence=0.9, bounds=(10, 10, 60, 30)),
                    OCRTextBlock(text="Age", confidence=0.9, bounds=(90, 10, 130, 30)),
                    OCRTextBlock(text="Alice", confidence=0.9, bounds=(10, 40, 70, 60)),
                    OCRTextBlock(text="31", confidence=0.9, bounds=(90, 40, 110, 60)),
                    OCRTextBlock(text="Bob", confidence=0.9, bounds=(10, 70, 50, 90)),
                    OCRTextBlock(text="27", confidence=0.9, bounds=(90, 70, 110, 90)),
                ]
            ]
        )
    )

    result = extractor.extract(
        StructuredDataExtractionConfiguration(
            mode=StructuredDataExtractionMode.TABLE,
            schema=StructuredDataSchema(
                schema_name="people",
                fields=[
                    StructuredDataFieldSchema(field_name="name"),
                    StructuredDataFieldSchema(field_name="age", field_type=StructuredDataFieldType.INTEGER),
                ],
            ),
            table_selector=AIInterfaceElementSelector(bounds=(0, 0, 300, 200)),
        )
    )

    assert result.succeeded is True
    assert [record.values for record in result.records] == [
        {"name": "Alice", "age": 31},
        {"name": "Bob", "age": 27},
    ]


def test_structured_data_extractor_reads_form_values_from_accessibility():
    name_field = make_element("Name", "edit", (100, 10, 220, 40), text="Alice")
    active_field = make_element("Active", "checkbox", (100, 50, 220, 80), text="Yes")
    root = make_element("Form", "window", (0, 0, 400, 300), children=[name_field, active_field])
    extractor = StructuredDataExtractor(
        accessibility_reader=FakeAccessibilityReader(
            root=root,
            mapping={
                ("Name", None, None): [name_field],
                ("Active", None, None): [active_field],
            },
        )
    )

    result = extractor.extract(
        StructuredDataExtractionConfiguration(
            mode=StructuredDataExtractionMode.FORM,
            schema=StructuredDataSchema(
                schema_name="account",
                fields=[
                    StructuredDataFieldSchema(field_name="Name"),
                    StructuredDataFieldSchema(field_name="Active", field_type=StructuredDataFieldType.BOOLEAN),
                ],
            ),
        )
    )

    assert result.succeeded is True
    assert result.records[0].values == {"Name": "Alice", "Active": True}


def test_structured_data_extractor_maps_text_block_to_schema():
    summary = make_element(
        "Summary",
        "document",
        (10, 10, 300, 200),
        text="Status: Ready\nCount: 2",
    )
    extractor = StructuredDataExtractor(
        accessibility_reader=FakeAccessibilityReader(
            root=summary,
            mapping={
                ("Summary", "document", None): [summary],
            },
        )
    )

    result = extractor.extract(
        StructuredDataExtractionConfiguration(
            mode=StructuredDataExtractionMode.TEXT_BLOCK,
            schema=StructuredDataSchema(
                schema_name="summary",
                fields=[
                    StructuredDataFieldSchema(field_name="status", aliases=("Status",)),
                    StructuredDataFieldSchema(
                        field_name="count",
                        aliases=("Count",),
                        field_type=StructuredDataFieldType.INTEGER,
                    ),
                ],
            ),
            text_block_selector=AIInterfaceElementSelector(name="Summary", role="document"),
        )
    )

    assert result.succeeded is True
    assert result.records[0].values == {"status": "Ready", "count": 2}


def test_structured_data_extractor_advances_through_pages():
    next_button = make_element("Next", "button", (250, 220, 300, 250), text="Next")
    extractor = StructuredDataExtractor(
        accessibility_reader=FakeAccessibilityReader(
            root=next_button,
            mapping={
                ("Next", "button", None): [next_button],
            },
        ),
        ocr_extractor=FakeOCRExtractor(
            blocks=[
                [
                    OCRTextBlock(text="Name", confidence=0.9, bounds=(10, 10, 60, 30)),
                    OCRTextBlock(text="Age", confidence=0.9, bounds=(90, 10, 130, 30)),
                    OCRTextBlock(text="Alice", confidence=0.9, bounds=(10, 40, 70, 60)),
                    OCRTextBlock(text="31", confidence=0.9, bounds=(90, 40, 110, 60)),
                ],
                [
                    OCRTextBlock(text="Name", confidence=0.9, bounds=(10, 10, 60, 30)),
                    OCRTextBlock(text="Age", confidence=0.9, bounds=(90, 10, 130, 30)),
                    OCRTextBlock(text="Bob", confidence=0.9, bounds=(10, 40, 50, 60)),
                    OCRTextBlock(text="27", confidence=0.9, bounds=(90, 40, 110, 60)),
                ],
            ]
        ),
        input_runner=FakeInputRunner(),
    )

    result = extractor.extract(
        StructuredDataExtractionConfiguration(
            mode=StructuredDataExtractionMode.TABLE,
            schema=StructuredDataSchema(
                schema_name="people",
                fields=[
                    StructuredDataFieldSchema(field_name="name"),
                    StructuredDataFieldSchema(field_name="age", field_type=StructuredDataFieldType.INTEGER),
                ],
            ),
            pagination=PaginationConfiguration(
                next_page_selector=AIInterfaceElementSelector(name="Next", role="button"),
                max_pages=2,
            ),
        )
    )

    assert result.succeeded is True
    assert [record.values["name"] for record in result.records] == ["Alice", "Bob"]
    assert len(extractor.input_runner.calls) == 1
