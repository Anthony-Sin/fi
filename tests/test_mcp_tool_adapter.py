from datetime import datetime
from pathlib import Path

from desktop_automation_perception.allowlist_enforcer import ActionAllowlistEnforcer
from desktop_automation_perception.automation import MCPToolAdapter
from desktop_automation_perception.context import CaptureContext
from desktop_automation_perception.models import (
    AccessibilityElement,
    AccessibilityElementState,
    AccessibilityTree,
    DesktopState,
    LocatorResult,
    LocatorStrategy,
    NavigationStepActionType,
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
)
from desktop_automation_perception.navigation_step_sequencer import NavigationStepSequencer
from desktop_automation_perception.structured_data_extractor import StructuredDataExtractor


class FakePerceptionEngine:
    def capture_state(self, context: CaptureContext | None = None) -> DesktopState:
        _ = context
        return DesktopState(
            captured_at=datetime(2026, 4, 9),
            results=[
                PerceptionResult(
                    source=PerceptionSource.OCR,
                    confidence=0.92,
                    artifacts=[
                        PerceptionArtifact(
                            kind="text",
                            confidence=0.92,
                            payload={"text": "Submit"},
                            bounds=(10, 20, 110, 70),
                        )
                    ],
                )
            ],
        )


class FakeLocator:
    def locate(self, desktop_state, target, confidence_threshold=None, monitor_id=None):
        _ = desktop_state, target, confidence_threshold, monitor_id
        return LocatorResult(
            succeeded=True,
            confidence=0.92,
            threshold=0.8,
            strategy=LocatorStrategy.OCR,
            bounds=(10, 20, 110, 70),
            center=(60, 45),
        )


class FakeInputRunner:
    def __init__(self):
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        return type("RunResult", (), {"succeeded": True, "logs": [], "failure_reason": None})()


class FakeSwitcher:
    def __init__(self):
        self.calls = []

    def switch_profile(self, account_name):
        self.calls.append(account_name)
        return type("SwitchResult", (), {"succeeded": True, "reason": None, "account_name": account_name})()


class FakeVerifier:
    def verify(self, checks, screenshot_path=None):
        _ = checks, screenshot_path
        return type("VerifyResult", (), {"failed_checks": [], "passed_checks": []})()


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


class FakeLauncher:
    def __init__(self):
        self.calls = []

    def launch(self, request):
        self.calls.append(request)
        return type("LaunchResult", (), {"succeeded": True, "reason": None})()


def make_element(name, role, bounds, *, text=None):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        bounds=bounds,
        state=AccessibilityElementState(text=text, enabled=True),
        children=[],
    )


def test_mcp_tool_adapter_lists_expected_tools():
    adapter = MCPToolAdapter()

    tools = adapter.list_tools()

    assert [tool["name"] for tool in tools] == [
        "take_screenshot",
        "find_element",
        "click",
        "type",
        "switch_account",
        "read_data",
        "navigate",
    ]
    assert "outputSchema" in tools[0]


def test_mcp_tool_adapter_routes_find_element_calls():
    adapter = MCPToolAdapter(
        perception_engine=FakePerceptionEngine(),
        element_locator=FakeLocator(),
    )

    response = adapter.handle_tool_call(
        {
            "tool_name": "find_element",
            "arguments": {"text": "Submit", "confidence_threshold": 0.8},
            "call_id": "call-1",
        }
    )

    assert response["isError"] is False
    assert response["call_id"] == "call-1"
    assert response["structuredContent"]["result"]["center"] == [60, 45]
    assert response["structuredContent"]["desktop_state"]["source"] == "ocr"


def test_mcp_tool_adapter_blocks_disallowed_calls_via_allowlist(tmp_path):
    allowlist_path = Path(tmp_path) / "allowlist.json"
    allowlist_path.write_text(
        '{"action_types":["find_element"],"applications":["editor"],"urls":["https://safe.example/*"],"file_paths":["C:/safe/*"]}',
        encoding="utf-8",
    )
    adapter = MCPToolAdapter(
        input_runner=FakeInputRunner(),
        allowlist_enforcer=ActionAllowlistEnforcer(config_path=str(allowlist_path)),
        workflow_id="wf-mcp",
    )

    response = adapter.handle_tool_call(
        {
            "tool_name": "click",
            "arguments": {"window_title": "Editor", "position": [10, 20]},
        }
    )

    assert response["isError"] is True
    assert "allowlist" in response["structuredContent"]["reason"]


def test_mcp_tool_adapter_routes_read_data_calls():
    summary = make_element("Summary", "document", (10, 10, 300, 200), text="Status: Ready\nCount: 2")
    extractor = StructuredDataExtractor(
        accessibility_reader=FakeAccessibilityReader(
            root=summary,
            mapping={("Summary", "document", None): [summary]},
        )
    )
    adapter = MCPToolAdapter(data_extractor=extractor)

    response = adapter.handle_tool_call(
        {
            "tool_name": "read_data",
            "arguments": {
                "mode": "text_block",
                "schema": {
                    "schema_name": "summary",
                    "fields": [
                        {"field_name": "status", "aliases": ["Status"]},
                        {"field_name": "count", "aliases": ["Count"], "field_type": "integer"},
                    ],
                },
                "text_block_selector": {"name": "Summary", "role": "document"},
            },
        }
    )

    assert response["isError"] is False
    assert response["structuredContent"]["records"][0]["values"] == {"status": "Ready", "count": 2}


def test_mcp_tool_adapter_routes_navigate_and_switch_account_calls():
    switcher = FakeSwitcher()
    navigator = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(),
        launcher=FakeLauncher(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )
    adapter = MCPToolAdapter(
        account_switcher=switcher,
        navigator=navigator,
    )

    switch_response = adapter.handle_tool_call(
        {"tool_name": "switch_account", "arguments": {"account_name": "acct-2"}}
    )
    navigate_response = adapter.handle_tool_call(
        {
            "tool_name": "navigate",
            "arguments": {
                "steps": [
                    {
                        "step_id": "wait-step",
                        "action_type": NavigationStepActionType.WAIT.value,
                        "target_description": "Pause briefly",
                        "input_data": {"seconds": 0.1},
                    }
                ]
            },
        }
    )

    assert switch_response["isError"] is False
    assert switcher.calls == ["acct-2"]
    assert navigate_response["isError"] is False
    assert navigate_response["structuredContent"]["outcomes"][0]["step_id"] == "wait-step"
