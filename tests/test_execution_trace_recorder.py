from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_agent.models import (
    ActionLogEntry,
    ApprovalGateAction,
    ApprovalRequest,
    BranchConditionType,
    BranchEvaluationRecord,
    BranchValueSource,
    ExecutionTraceEventType,
    HumanReviewDecisionRecord,
    HumanReviewDecisionType,
    HumanReviewPendingItem,
    InputAction,
    InputActionType,
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
    WorkflowContext,
    WorkflowStepResult,
)
from desktop_automation_agent.observability import ExecutionTraceRecorder


class FakeScreenshotBackend:
    def __init__(self):
        self.captured_paths = []

    def capture_screenshot_to_path(self, path: str) -> str:
        self.captured_paths.append(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("png-bytes", encoding="utf-8")
        return path


def test_execution_trace_recorder_captures_replayable_trace_and_archive(tmp_path):
    screenshot_backend = FakeScreenshotBackend()
    recorder = ExecutionTraceRecorder(
        storage_directory=str(tmp_path / "traces"),
        archive_directory=str(tmp_path / "archives"),
        screenshot_backend=screenshot_backend,
    )
    started = recorder.start_trace(
        workflow_id="wf-42",
        workflow_version_number=3,
        metadata={"account": "acct-a"},
        trace_id="trace-42",
    )
    assert started.succeeded is True
    assert started.trace is not None

    recorder.record_step_state(
        "trace-42",
        step_id="step-1",
        step_index=1,
        pre_state=WorkflowContext(current_application="Billing", step_number=1),
        capture_pre_screenshot=True,
    )
    recorder.record_perception(
        "trace-42",
        step_id="step-1",
        step_index=1,
        perception_results=[
            PerceptionResult(
                source=PerceptionSource.OCR,
                confidence=0.91,
                artifacts=[PerceptionArtifact(kind="text", confidence=0.91, payload={"text": "Submit"})],
            )
        ],
        capture_screenshot=True,
    )
    recorder.record_action_decision(
        "trace-42",
        step_id="step-1",
        step_index=1,
        decision_summary="Click submit",
        rationale={"target": "submit-button"},
    )
    recorder.record_action_executed(
        "trace-42",
        step_id="step-1",
        step_index=1,
        action=ActionLogEntry(
            action=InputAction(action_type=InputActionType.CLICK, position=(100, 200)),
            executed=True,
            delay_seconds=0.2,
        ),
        pre_state=WorkflowContext(current_application="Billing", step_number=1),
        post_state=WorkflowContext(current_application="Billing", step_number=2),
        capture_post_screenshot=True,
    )
    recorder.record_branch_decision(
        "trace-42",
        step_id="step-1",
        step_index=1,
        selected_branch="success-path",
        records=[
            BranchEvaluationRecord(
                condition_id="status-ok",
                condition_type=BranchConditionType.STRING_MATCH,
                source=BranchValueSource.STEP_OUTPUT,
                field_path="status",
                actual_value="ok",
                expected_value="ok",
                matched=True,
                selected_branch_id="success-path",
                selected_next_step_id="step-2",
            )
        ],
        workflow_data={"status": "ok"},
    )
    recorder.record_human_interaction(
        "trace-42",
        step_id="step-1",
        step_index=1,
        pending_item=HumanReviewPendingItem(
            request=ApprovalRequest(
                request_id="approval-1",
                action=ApprovalGateAction(
                    workflow_id="wf-42",
                    step_id="step-1",
                    action_type="click",
                    description="Submit invoice",
                    application_name="Billing",
                ),
                reviewer_channel="ops",
                created_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(minutes=10),
                proposed_effects=["submit invoice"],
            ),
            workflow_context={"workflow_id": "wf-42"},
            action_summary="Approve invoice submission",
        ),
        decision_record=HumanReviewDecisionRecord(
            request_id="approval-1",
            reviewer_id="reviewer-7",
            decision=HumanReviewDecisionType.APPROVE,
            decided_at=datetime.utcnow(),
            reason="Looks correct",
        ),
    )
    completed = recorder.complete_trace(
        "trace-42",
        succeeded=True,
        final_outcome="workflow completed",
        final_result={"status": "done"},
    )

    assert completed.succeeded is True
    assert completed.trace is not None
    assert completed.archive_path is not None
    assert Path(completed.archive_path).exists()

    replayed = recorder.replay_trace(completed.archive_path)

    assert replayed.succeeded is True
    assert [event.event_type for event in replayed.replay_events] == [
        ExecutionTraceEventType.STEP_STATE,
        ExecutionTraceEventType.PERCEPTION,
        ExecutionTraceEventType.ACTION_DECISION,
        ExecutionTraceEventType.ACTION_EXECUTED,
        ExecutionTraceEventType.BRANCH_DECISION,
        ExecutionTraceEventType.HUMAN_INTERACTION,
        ExecutionTraceEventType.FINAL_OUTCOME,
    ]
    assert replayed.replay_events[1].screenshot_path is not None
    assert replayed.replay_events[-1].payload["final_outcome"] == "workflow completed"

    with zipfile.ZipFile(completed.archive_path, "r") as handle:
        names = set(handle.namelist())
        assert "trace.json" in names
        assert any(name.startswith("artifacts/") for name in names)
        manifest = json.loads(handle.read("trace.json").decode("utf-8"))
        assert manifest["workflow_id"] == "wf-42"
        assert manifest["archive_path"] == completed.archive_path


def test_execution_trace_recorder_can_load_directory_trace_without_archiving(tmp_path):
    screenshot_file = tmp_path / "provided.png"
    screenshot_file.write_text("image", encoding="utf-8")
    recorder = ExecutionTraceRecorder(storage_directory=str(tmp_path / "traces"))
    recorder.start_trace(workflow_id="wf-dir", trace_id="trace-dir")
    recorder.record_step_state(
        "trace-dir",
        step_id="step-a",
        post_state=WorkflowContext(current_application="Portal", step_number=3),
        post_screenshot_path=str(screenshot_file),
        step_result=WorkflowStepResult(
            step_id="step-a",
            application_name="Portal",
            succeeded=True,
            context_snapshot=WorkflowContext(current_application="Portal", step_number=3),
        ),
    )
    completed = recorder.complete_trace(
        "trace-dir",
        succeeded=False,
        final_outcome="step failed",
        final_result={"error": "not found"},
        archive=False,
    )

    assert completed.trace is not None
    loaded = recorder.load_trace(str(Path(completed.manifest_path).parent))

    assert loaded.succeeded is True
    assert loaded.trace is not None
    assert loaded.trace.succeeded is False
    assert loaded.trace.events[0].payload["post_screenshot_path"].startswith("artifacts/")
    assert loaded.trace.events[0].payload["step_result"]["application_name"] == "Portal"
