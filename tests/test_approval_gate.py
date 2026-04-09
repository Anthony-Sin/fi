from datetime import datetime
from pathlib import Path

from desktop_automation_perception.models import (
    ApprovalDecision,
    ApprovalGateAction,
    ApprovalResponse,
    ApprovalRiskLevel,
    ApprovalTimeoutPolicy,
    NotificationChannel,
    NotificationChannelType,
)
from desktop_automation_perception.notification_dispatcher import NotificationDispatcher
from desktop_automation_perception.resilience import ApprovalGateModule


class FakeTransport:
    def __init__(self):
        self.calls = []

    def send(self, *, endpoint, headers, payload):
        self.calls.append({"endpoint": endpoint, "headers": headers, "payload": payload})
        return type("Response", (), {"status_code": 200})()


def build_dispatcher(tmp_path, transport):
    return NotificationDispatcher(
        storage_path=str(Path(tmp_path) / "notifications.json"),
        channels=[
            NotificationChannel(
                channel_id="reviewers",
                channel_type=NotificationChannelType.GENERIC_WEBHOOK,
                endpoint="https://reviewers.example/webhook",
                batch_non_urgent=False,
            )
        ],
        transport_backend=transport,
    )


def test_approval_gate_allows_low_risk_actions_without_pause(tmp_path):
    gate = ApprovalGateModule(
        reviewer_channel="ops-review",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
    )
    action = ApprovalGateAction(
        workflow_id="wf-1",
        step_id="inspect",
        action_type="read_record",
        description="Read current customer status",
        risk_level=ApprovalRiskLevel.READ_ONLY,
        blast_radius=1,
    )

    result = gate.evaluate(action)

    assert result.succeeded is True
    assert result.triggered_gate is False


def test_approval_gate_notifies_and_waits_for_reviewer_approval(tmp_path):
    transport = FakeTransport()
    dispatcher = build_dispatcher(tmp_path, transport)
    now = datetime(2026, 4, 8, 12, 0)
    gate = ApprovalGateModule(
        reviewer_channel="ops-review",
        notification_dispatcher=dispatcher,
        response_callback=lambda request: ApprovalResponse(
            request_id=request.request_id,
            decision=ApprovalDecision.APPROVE,
            reviewer_id="reviewer-1",
            responded_at=now,
            reason="Looks safe.",
        ),
        now_fn=lambda: now,
    )
    action = ApprovalGateAction(
        workflow_id="wf-2",
        step_id="submit-payment",
        action_type="submit_payment",
        description="Submit final payment to vendor",
        risk_level=ApprovalRiskLevel.IRREVERSIBLE,
        blast_radius=2,
        expected_consequences=["Funds will be transferred", "Vendor record will be updated"],
    )

    result = gate.evaluate(action)

    assert result.succeeded is True
    assert result.triggered_gate is True
    assert result.response is not None and result.response.decision is ApprovalDecision.APPROVE
    assert result.notification_result is not None and result.notification_result.succeeded is True
    assert transport.calls[0]["payload"]["notifications"][0]["context_data"]["reviewer_channel"] == "ops-review"
    assert "Funds will be transferred" in transport.calls[0]["payload"]["notifications"][0]["context_data"]["expected_consequences"]


def test_approval_gate_rejects_when_reviewer_rejects(tmp_path):
    gate = ApprovalGateModule(
        reviewer_channel="ops-review",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: ApprovalResponse(
            request_id=request.request_id,
            decision=ApprovalDecision.REJECT,
            reviewer_id="reviewer-2",
            reason="Action exceeds approved scope.",
        ),
    )
    action = ApprovalGateAction(
        workflow_id="wf-3",
        step_id="delete-users",
        action_type="delete_users",
        description="Delete inactive users",
        risk_level=ApprovalRiskLevel.IRREVERSIBLE,
        blast_radius=100,
    )

    result = gate.evaluate(action)

    assert result.succeeded is False
    assert result.response is not None and result.response.decision is ApprovalDecision.REJECT
    assert result.reason == "Action exceeds approved scope."


def test_approval_gate_applies_timeout_policy_when_no_response(tmp_path):
    action = ApprovalGateAction(
        workflow_id="wf-4",
        step_id="bulk-update",
        action_type="bulk_update",
        description="Bulk update account settings",
        risk_level=ApprovalRiskLevel.REVERSIBLE,
        blast_radius=50,
    )

    reject_gate = ApprovalGateModule(
        reviewer_channel="ops-review",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: None,
        timeout_policy=ApprovalTimeoutPolicy.REJECT,
    )
    escalate_gate = ApprovalGateModule(
        reviewer_channel="ops-review",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: None,
        timeout_policy=ApprovalTimeoutPolicy.ESCALATE,
    )
    caution_gate = ApprovalGateModule(
        reviewer_channel="ops-review",
        notification_dispatcher=build_dispatcher(tmp_path, FakeTransport()),
        response_callback=lambda request: None,
        timeout_policy=ApprovalTimeoutPolicy.PROCEED_WITH_CAUTION,
    )

    reject_result = reject_gate.evaluate(action)
    escalate_result = escalate_gate.evaluate(action)
    caution_result = caution_gate.evaluate(action)

    assert reject_result.succeeded is False
    assert reject_result.timed_out is True
    assert reject_result.response is not None and reject_result.response.decision is ApprovalDecision.REJECT
    assert escalate_result.succeeded is False
    assert escalate_result.response is not None and escalate_result.response.decision is ApprovalDecision.ESCALATE
    assert caution_result.succeeded is True
    assert caution_result.response is not None and caution_result.response.decision is ApprovalDecision.PROCEED_WITH_CAUTION
