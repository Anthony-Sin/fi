import json
from pathlib import Path

from desktop_automation_agent.resilience import SensitiveDataProtector
from desktop_automation_agent.models import (
    AccessibilityElement,
    AccessibilityElementState,
    AccessibilityTree,
    FailureArchiveQuery,
    InputAction,
    InputActionType,
    ActionLogEntry,
)
from desktop_automation_agent.screenshot_failure_recorder import ScreenshotOnFailureRecorder


class FakeScreenshotBackend:
    def __init__(self):
        self.paths = []

    def capture_screenshot_to_path(self, path=None):
        self.paths.append(path)
        if path is not None:
            Path(path).write_text("image", encoding="utf-8")
        return path


class FakeAccessibilityReader:
    def read_active_application_tree(self):
        return AccessibilityTree(
            application_name="Chat App",
            root=AccessibilityElement(
                element_id="root",
                name="Main Window",
                role="window",
                state=AccessibilityElementState(text="Main Window", enabled=True),
                children=[
                    AccessibilityElement(
                        element_id="input",
                        name="Message",
                        role="edit",
                        state=AccessibilityElementState(text="hello", enabled=True),
                    )
                ],
            ),
        )


def make_action_log(index):
    return ActionLogEntry(
        action=InputAction(
            action_type=InputActionType.TYPE_TEXT,
            text=f"message-{index}",
        ),
        executed=True,
        delay_seconds=0.1 * index,
    )


def test_failure_recorder_captures_screenshot_tree_and_actions(tmp_path):
    archive_path = Path(tmp_path) / "archive.json"
    artifacts = Path(tmp_path) / "artifacts"
    recorder = ScreenshotOnFailureRecorder(
        archive_path=str(archive_path),
        artifact_directory=str(artifacts),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(),
    )

    result = recorder.record_failure(
        workflow_id="ops-workflow",
        step_name="submit prompt",
        error=RuntimeError("submission failed"),
        recent_actions=[make_action_log(index) for index in range(1, 4)],
        application_name="Chat App",
    )

    assert result.succeeded is True
    assert result.record is not None
    assert "ops_workflow__submit_prompt__" in (result.record.screenshot_path or "")
    assert Path(result.record.screenshot_path).exists()
    assert Path(result.record.accessibility_tree_path).exists()
    payload = json.loads(Path(result.record.accessibility_tree_path).read_text(encoding="utf-8"))
    assert payload["application_name"] == "Chat App"
    assert payload["root"]["children"][0]["name"] == "Message"
    assert len(result.record.last_actions) == 3
    assert result.record.exception_type == "RuntimeError"


def test_failure_recorder_keeps_only_last_five_actions(tmp_path):
    recorder = ScreenshotOnFailureRecorder(
        archive_path=str(Path(tmp_path) / "archive.json"),
        artifact_directory=str(Path(tmp_path) / "artifacts"),
    )

    result = recorder.record_failure(
        workflow_id="wf",
        step_name="step",
        error=RuntimeError("boom"),
        recent_actions=[make_action_log(index) for index in range(1, 8)],
    )

    assert result.record is not None
    assert len(result.record.last_actions) == 5
    assert "message-3" in result.record.last_actions[0]
    assert "message-7" in result.record.last_actions[-1]


def test_failure_recorder_supports_querying_archive(tmp_path):
    recorder = ScreenshotOnFailureRecorder(
        archive_path=str(Path(tmp_path) / "archive.json"),
        artifact_directory=str(Path(tmp_path) / "artifacts"),
    )
    recorder.record_failure(
        workflow_id="workflow-a",
        step_name="login",
        error=RuntimeError("first"),
    )
    recorder.record_failure(
        workflow_id="workflow-b",
        step_name="submit",
        error=ValueError("second"),
    )

    queried = recorder.query_records(FailureArchiveQuery(workflow_id="workflow-b"))

    assert queried.succeeded is True
    assert len(queried.records) == 1
    assert queried.records[0].step_name == "submit"


def test_failure_recorder_persists_archive_records(tmp_path):
    recorder = ScreenshotOnFailureRecorder(
        archive_path=str(Path(tmp_path) / "archive.json"),
        artifact_directory=str(Path(tmp_path) / "artifacts"),
    )

    recorder.record_failure(
        workflow_id="workflow-a",
        step_name="compose",
        error=ValueError("invalid prompt"),
    )
    records = recorder.list_records()

    assert len(records) == 1
    assert records[0].exception_type == "ValueError"
    assert records[0].exception_message == "invalid prompt"


def test_failure_recorder_masks_sensitive_values_in_screenshot_tree_and_actions(tmp_path):
    archive_path = Path(tmp_path) / "archive.json"
    artifacts = Path(tmp_path) / "artifacts"
    recorder = ScreenshotOnFailureRecorder(
        archive_path=str(archive_path),
        artifact_directory=str(artifacts),
        screenshot_backend=FakeScreenshotBackend(),
        accessibility_reader=FakeAccessibilityReader(),
        sensitive_data_protector=SensitiveDataProtector(
            sensitive_value_patterns=(r"hello", r"message-1"),
        ),
    )

    result = recorder.record_failure(
        workflow_id="ops-workflow",
        step_name="submit prompt",
        error=RuntimeError("submission failed"),
        recent_actions=[make_action_log(1)],
        application_name="Chat App",
    )

    screenshot_text = Path(result.record.screenshot_path).read_text(encoding="utf-8")
    tree_payload = json.loads(Path(result.record.accessibility_tree_path).read_text(encoding="utf-8"))

    assert screenshot_text == "image"
    assert tree_payload["root"]["children"][0]["state"]["text"] == "***SENSITIVE***"
    assert "***SENSITIVE***" in result.record.last_actions[0]
