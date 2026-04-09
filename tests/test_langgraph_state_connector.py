from datetime import datetime
from pathlib import Path

from desktop_automation_perception.automation import LangGraphStateConnector
from desktop_automation_perception.checkpoint_manager import CheckpointManager
from desktop_automation_perception.context import CaptureContext
from desktop_automation_perception.models import (
    DesktopState,
    LocatorResult,
    LocatorStrategy,
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
    UIStateFingerprint,
    WorkflowContext,
)


class FakePerceptionEngine:
    def capture_state(self, context: CaptureContext | None = None) -> DesktopState:
        _ = context
        return DesktopState(
            captured_at=datetime(2026, 4, 9),
            results=[
                PerceptionResult(
                    source=PerceptionSource.OCR,
                    confidence=0.93,
                    artifacts=[
                        PerceptionArtifact(
                            kind="text",
                            confidence=0.93,
                            payload={"text": "Continue"},
                            bounds=(20, 30, 120, 80),
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
            confidence=0.93,
            threshold=0.8,
            strategy=LocatorStrategy.OCR,
            bounds=(20, 30, 120, 80),
            center=(70, 55),
        )


class FakeInputRunner:
    def __init__(self):
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        return type("RunResult", (), {"succeeded": True, "logs": [], "failure_reason": None})()


def test_langgraph_connector_builds_nodes_and_updates_state():
    connector = LangGraphStateConnector(
        perception_engine=FakePerceptionEngine(),
        element_locator=FakeLocator(),
        input_runner=FakeInputRunner(),
        workflow_id="wf-langgraph",
    )
    state = connector.create_initial_state(
        workflow_context=WorkflowContext(current_application="Editor", step_number=0),
        extra_state={
            "automation_inputs": {
                "find_element": {"text": "Continue"},
                "type": {"text": "hello world", "application_name": "Editor"},
            }
        },
    )

    nodes = connector.build_nodes()
    after_find = nodes["find_element"](state)
    after_type = nodes["type"](after_find)

    assert after_find["automation_outputs"]["find_element"]["result"]["center"] == [70, 55]
    assert after_type["automation_outputs"]["type"]["succeeded"] is True
    assert after_type["automation_pipeline"]["workflow_context"]["step_number"] == 2
    assert after_type["automation_pipeline"]["step_results"][1]["step_id"] == "type"


def test_langgraph_connector_serializes_and_restores_state():
    connector = LangGraphStateConnector(workflow_id="wf-serialize")
    state = connector.create_initial_state(
        workflow_context=WorkflowContext(
            current_application="Browser",
            step_number=3,
            shared_data={"draft": "hello"},
        ),
        ui_state_fingerprint=UIStateFingerprint(
            window_title_hash="hash-1",
            landmark_positions={"submit": (0.5, 0.75)},
            pixel_histogram=(0.1, 0.2),
            screen_size=(1920, 1080),
            window_count=2,
        ),
    )
    state["automation_outputs"]["navigate"] = {"succeeded": True, "outcomes": []}

    payload = connector.serialize_state(state)
    restored = connector.restore_state(payload)

    assert payload["automation_pipeline"]["workflow_context"]["current_application"] == "Browser"
    assert restored["automation_outputs"]["navigate"]["succeeded"] is True
    assert restored["automation_pipeline"]["ui_state_fingerprint"]["window_title_hash"] == "hash-1"


def test_langgraph_connector_saves_and_restores_checkpoint(tmp_path):
    checkpoint_manager = CheckpointManager(storage_path=str(Path(tmp_path) / "langgraph-checkpoints.json"))
    connector = LangGraphStateConnector(
        checkpoint_manager=checkpoint_manager,
        workflow_id="wf-checkpoint",
    )
    state = connector.create_initial_state(
        workflow_context=WorkflowContext(
            current_application="Sheets",
            step_number=4,
            shared_data={"report": "ready"},
            active_applications=["Sheets"],
            application_signatures={"Sheets": "sheets.exe"},
        ),
        collected_data={"report": "ready"},
    )
    state["automation_pipeline"]["step_results"] = [
        {
            "step_id": "find_element",
            "application_name": "Sheets",
            "succeeded": True,
            "dry_run": False,
            "context_snapshot": state["automation_pipeline"]["workflow_context"],
            "reason": None,
        }
    ]

    checkpoint_payload = connector.save_checkpoint(state)
    restored_state = connector.restore_checkpoint()

    assert checkpoint_payload["workflow_id"] == "wf-checkpoint"
    assert restored_state["automation_pipeline"]["workflow_context"]["current_application"] == "Sheets"
    assert restored_state["automation_pipeline"]["step_results"][0]["step_id"] == "find_element"
