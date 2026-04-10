from pathlib import Path

from desktop_automation_agent.models import (
    AntiLoopTriggerType,
    EscalationResolution,
    EscalationResponse,
    EscalationTriggerType,
)
from desktop_automation_agent.resilience import AntiLoopDetector, EscalationManager


class FakeAuditLogger:
    def __init__(self):
        self.calls = []

    def log_action(self, **kwargs):
        self.calls.append(kwargs)


def test_anti_loop_detector_triggers_on_step_execution_limit_and_logs_full_history(tmp_path):
    """Verifies that AntiLoopDetector correctly identifies when a single step
    exceeds its allowed execution limit and accurately logs the entire step history."""
    audit_logger = FakeAuditLogger()
    detector = AntiLoopDetector(
        storage_path=str(Path(tmp_path) / "anti_loop.json"),
        workflow_id="wf-loop",
        max_step_execution_count=1,
        max_pipeline_duration_seconds=30.0,
        audit_logger=audit_logger,
        monotonic_fn=iter([0.0, 0.1, 0.2]).__next__,
    )

    first = detector.before_step("submit", metadata={"action_type": "click"})
    second = detector.before_step("submit", metadata={"action_type": "click"})
    events = detector.list_events()

    assert first.succeeded is True
    assert first.triggered is False
    assert second.succeeded is False
    assert second.triggered is True
    assert second.record is not None
    assert second.record.trigger_type is AntiLoopTriggerType.STEP_EXECUTION_LIMIT
    assert [item.step_id for item in second.record.step_history] == ["submit", "submit"]
    assert second.record.step_history[-1].execution_count == 2
    assert events[0].step_history[-1].execution_count == 2
    assert audit_logger.calls[0]["action_type"] == "anti_loop_detected"
    assert audit_logger.calls[0]["output_data"]["step_history"][-1]["execution_count"] == 2


def test_anti_loop_detector_triggers_pipeline_timeout_and_escalation(tmp_path):
    """Verifies that the detector triggers a PIPELINE_TIMEOUT when the workflow
    exceeds its maximum duration and correctly escalates the issue."""
    manager = EscalationManager(
        storage_path=str(Path(tmp_path) / "escalations.json"),
        operator_channel="ops",
        response_callback=lambda request: EscalationResponse(
            escalation_id=request.escalation_id,
            resolution=EscalationResolution.ABORT,
            operator_id="operator-timeout",
            reason="Timed out safely.",
        ),
    )
    detector = AntiLoopDetector(
        storage_path=str(Path(tmp_path) / "anti_loop.json"),
        workflow_id="wf-timeout",
        max_step_execution_count=5,
        max_pipeline_duration_seconds=0.05,
        escalation_manager=manager,
        monotonic_fn=iter([0.0, 0.2]).__next__,
    )

    result = detector.before_step("publish", metadata={"application_name": "Writer"})
    snapshot = manager.inspect().snapshot

    assert result.succeeded is False
    assert result.triggered is True
    assert result.record is not None
    assert result.record.trigger_type is AntiLoopTriggerType.PIPELINE_TIMEOUT
    assert result.escalation_result is not None
    assert result.escalation_result.record is not None
    assert result.escalation_result.record.trigger_type is EscalationTriggerType.PIPELINE_TIMEOUT
    assert snapshot is not None
    assert snapshot.records[0].trigger_type is EscalationTriggerType.PIPELINE_TIMEOUT
