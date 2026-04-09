from datetime import datetime
from pathlib import Path

from desktop_automation_agent.models import (
    EscalationResolution,
    EscalationResponse,
    EscalationTriggerType,
    NotificationChannel,
    NotificationChannelType,
)
from desktop_automation_agent.notification_dispatcher import NotificationDispatcher
from desktop_automation_agent.resilience import EscalationManager


class FakeTransport:
    def __init__(self):
        self.calls = []

    def send(self, *, endpoint, headers, payload):
        self.calls.append({"endpoint": endpoint, "payload": payload})
        return type("Response", (), {"status_code": 200})()


def build_dispatcher(tmp_path, transport):
    return NotificationDispatcher(
        storage_path=str(Path(tmp_path) / "notifications.json"),
        channels=[
            NotificationChannel(
                channel_id="operators",
                channel_type=NotificationChannelType.GENERIC_WEBHOOK,
                endpoint="https://ops.example/escalations",
                batch_non_urgent=False,
            )
        ],
        transport_backend=transport,
    )


def test_escalation_manager_notifies_and_resumes_on_operator_response(tmp_path):
    now = datetime(2026, 4, 8, 12, 0)
    transport = FakeTransport()
    manager = EscalationManager(
        storage_path=str(Path(tmp_path) / "escalations.json"),
        operator_channel="tier-2-ops",
        notification_dispatcher=build_dispatcher(tmp_path, transport),
        response_callback=lambda request: EscalationResponse(
            escalation_id=request.escalation_id,
            resolution=EscalationResolution.RESUME,
            operator_id="operator-1",
            responded_at=now,
            reason="Handled manually; safe to continue.",
        ),
        now_fn=lambda: now,
    )

    result = manager.trigger(
        workflow_id="wf-1",
        step_id="submit-order",
        trigger_type=EscalationTriggerType.CAPTCHA_DETECTED,
        detail="CAPTCHA challenge blocked submission.",
        context_data={"page_title": "Checkout"},
    )

    assert result.succeeded is True
    assert result.paused is True
    assert result.resumed is True
    assert result.aborted is False
    assert result.record is not None and result.record.resolution is EscalationResolution.RESUME
    assert transport.calls[0]["payload"]["notifications"][0]["context_data"]["trigger_type"] == "captcha_detected"


def test_escalation_manager_aborts_on_operator_abort_response(tmp_path):
    manager = EscalationManager(
        storage_path=str(Path(tmp_path) / "escalations.json"),
        operator_channel="tier-2-ops",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: EscalationResponse(
            escalation_id=request.escalation_id,
            resolution=EscalationResolution.ABORT,
            operator_id="operator-2",
            reason="Security verification requires separate handling.",
        ),
    )

    result = manager.trigger(
        workflow_id="wf-2",
        step_id="login",
        trigger_type=EscalationTriggerType.SECURITY_VERIFICATION,
        detail="Unexpected MFA prompt detected.",
    )

    assert result.succeeded is False
    assert result.aborted is True
    assert result.response is not None and result.response.operator_id == "operator-2"
    assert result.reason == "Security verification requires separate handling."


def test_escalation_manager_times_out_and_uses_default_abort_policy(tmp_path):
    manager = EscalationManager(
        storage_path=str(Path(tmp_path) / "escalations.json"),
        operator_channel="tier-2-ops",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: None,
        timeout_resolution=EscalationResolution.ABORT,
    )

    result = manager.trigger(
        workflow_id="wf-3",
        step_id="approve-change",
        trigger_type=EscalationTriggerType.APPROVAL_TIMEOUT,
        detail="Reviewer did not respond before approval timeout.",
    )

    assert result.succeeded is False
    assert result.timed_out is True
    assert result.aborted is True
    assert result.response is not None and result.response.resolution is EscalationResolution.ABORT


def test_escalation_manager_records_repeated_failure_and_novel_ui_state_triggers(tmp_path):
    manager = EscalationManager(
        storage_path=str(Path(tmp_path) / "escalations.json"),
        operator_channel="tier-2-ops",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: EscalationResponse(
            escalation_id=request.escalation_id,
            resolution=EscalationResolution.RESUME,
            operator_id="operator-3",
            reason="Context captured.",
        ),
    )

    manager.trigger(
        workflow_id="wf-4",
        step_id="sync",
        trigger_type=EscalationTriggerType.REPEATED_STEP_FAILURE,
        detail="Step failed three times in a row.",
    )
    manager.trigger(
        workflow_id="wf-4",
        step_id="screen-check",
        trigger_type=EscalationTriggerType.NOVEL_UI_STATE,
        detail="Observed a UI state not matching any known pattern.",
    )
    snapshot = manager.inspect().snapshot

    assert snapshot is not None
    assert len(snapshot.records) == 2
    assert snapshot.records[0].trigger_type is EscalationTriggerType.REPEATED_STEP_FAILURE
    assert snapshot.records[1].trigger_type is EscalationTriggerType.NOVEL_UI_STATE
