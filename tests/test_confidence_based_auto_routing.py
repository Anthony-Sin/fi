from datetime import datetime
from pathlib import Path

from desktop_automation_perception.models import (
    ApprovalDecision,
    ApprovalGateAction,
    ApprovalGateResult,
    ApprovalRiskLevel,
    ConfidenceRoutingDisposition,
)
from desktop_automation_perception.resilience import ConfidenceBasedAutoRouting


class FakeApprovalGate:
    def __init__(self, result=None):
        self.result = result or ApprovalGateResult(
            succeeded=True,
            action=ApprovalGateAction(
                workflow_id="wf-default",
                step_id="default",
                action_type="default_action",
                description="Default",
            ),
            triggered_gate=True,
        )
        self.calls = []

    def evaluate(self, action):
        self.calls.append(action)
        return self.result


def make_action(action_type: str = "submit_order") -> ApprovalGateAction:
    return ApprovalGateAction(
        workflow_id="wf-1",
        step_id="step-1",
        action_type=action_type,
        description="Submit the order to the external system",
        application_name="erp",
        risk_level=ApprovalRiskLevel.IRREVERSIBLE,
        blast_radius=2,
    )


def test_confidence_router_auto_proceeds_when_score_exceeds_threshold(tmp_path):
    router = ConfidenceBasedAutoRouting(
        storage_path=str(Path(tmp_path) / "confidence.json"),
        approval_gate=FakeApprovalGate(),
        default_threshold=0.75,
        now_fn=lambda: datetime(2026, 4, 8, 12, 0),
    )

    result = router.route_action(action=make_action("read_invoice"), confidence_score=0.92)

    assert result.succeeded is True
    assert result.decision is not None
    assert result.decision.disposition is ConfidenceRoutingDisposition.AUTO_PROCEED
    assert result.approval_result is None


def test_confidence_router_routes_low_confidence_actions_to_approval_gate(tmp_path):
    approval_result = ApprovalGateResult(
        succeeded=False,
        action=make_action(),
        triggered_gate=True,
        response=type(
            "ApprovalResponseLike",
            (),
            {"decision": ApprovalDecision.REJECT, "reason": "Needs human review."},
        )(),
        reason="Needs human review.",
    )
    gate = FakeApprovalGate(result=approval_result)
    router = ConfidenceBasedAutoRouting(
        storage_path=str(Path(tmp_path) / "confidence.json"),
        approval_gate=gate,
        default_threshold=0.85,
        now_fn=lambda: datetime(2026, 4, 8, 12, 0),
    )

    result = router.route_action(action=make_action(), confidence_score=0.5)

    assert result.succeeded is False
    assert result.decision is not None
    assert result.decision.disposition is ConfidenceRoutingDisposition.ROUTE_TO_APPROVAL
    assert len(gate.calls) == 1
    assert result.approval_result is approval_result


def test_confidence_router_adjusts_thresholds_based_on_error_rates(tmp_path):
    router = ConfidenceBasedAutoRouting(
        storage_path=str(Path(tmp_path) / "confidence.json"),
        approval_gate=FakeApprovalGate(),
        default_threshold=0.7,
        error_rate_sensitivity=0.3,
    )

    router.adjust_thresholds_from_error_rates({"submit_order": 0.4})
    result = router.inspect()
    rule = next(rule for rule in result.snapshot.threshold_rules if rule.action_type == "submit_order")
    decision = router.route_action(action=make_action("submit_order"), confidence_score=0.81).decision

    assert rule.minimum_confidence == 0.7
    assert rule.observed_error_rate == 0.4
    assert decision is not None
    assert round(decision.threshold_used, 2) == 0.82


def test_confidence_router_logs_decisions_and_supports_manual_threshold_updates(tmp_path):
    router = ConfidenceBasedAutoRouting(
        storage_path=str(Path(tmp_path) / "confidence.json"),
        approval_gate=FakeApprovalGate(),
        default_threshold=0.7,
        now_fn=lambda: datetime(2026, 4, 8, 12, 0),
    )

    router.update_threshold(action_type="bulk_update", minimum_confidence=0.9, observed_error_rate=0.1)
    router.route_action(action=make_action("bulk_update"), confidence_score=0.88)
    snapshot = router.inspect().snapshot

    assert snapshot is not None
    rule = next(rule for rule in snapshot.threshold_rules if rule.action_type == "bulk_update")
    assert rule.minimum_confidence == 0.9
    assert rule.observed_error_rate == 0.1
    assert len(snapshot.decisions) == 1
    assert snapshot.decisions[0].action_type == "bulk_update"
