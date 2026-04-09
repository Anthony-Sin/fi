from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_agent.models import (
    ApprovalDecision,
    ApprovalGateAction,
    ApprovalRequest,
    ApprovalRiskLevel,
    HumanReviewDecisionType,
)
from desktop_automation_agent.resilience import HumanReviewInterface


def make_request(request_id: str = "req-1") -> ApprovalRequest:
    now = datetime(2026, 4, 8, 12, 0)
    return ApprovalRequest(
        request_id=request_id,
        action=ApprovalGateAction(
            workflow_id="wf-1",
            step_id="delete-users",
            action_type="delete_users",
            description="Delete inactive users older than 365 days",
            application_name="admin-portal",
            risk_level=ApprovalRiskLevel.IRREVERSIBLE,
            blast_radius=240,
            context_data={"batch_size": "240", "target_group": "inactive"},
            expected_consequences=["User records will be permanently removed", "Audit history remains intact"],
        ),
        reviewer_channel="ops-review",
        created_at=now,
        expires_at=now + timedelta(minutes=30),
        proposed_effects=["User records will be permanently removed", "Audit history remains intact"],
    )


def test_human_review_interface_renders_pending_requests_with_actionable_context(tmp_path):
    interface = HumanReviewInterface(storage_path=str(Path(tmp_path) / "review.json"))
    request = make_request()

    submitted = interface.submit_request(request, workflow_context={"account": "seller-a", "build_id": "build-12"})
    listed = interface.list_pending_requests()

    assert submitted.succeeded is True
    assert "Workflow: wf-1" in submitted.rendered_view
    assert "Action: delete_users" in submitted.rendered_view
    assert "Risk: irreversible" in submitted.rendered_view
    assert "Available Decisions: approve | reject | modify" in submitted.rendered_view
    assert listed.succeeded is True
    assert len(listed.items) == 1
    assert "build-12" in listed.rendered_view


def test_human_review_interface_approves_and_logs_reviewer_identity(tmp_path):
    interface = HumanReviewInterface(storage_path=str(Path(tmp_path) / "review.json"))
    interface.submit_request(make_request())

    result = interface.approve(request_id="req-1", reviewer_id="reviewer-a", reason="Validated scope.")
    snapshot = interface.list_pending_requests().snapshot

    assert result.succeeded is True
    assert result.response is not None and result.response.decision is ApprovalDecision.APPROVE
    assert result.decision_record is not None
    assert result.decision_record.reviewer_id == "reviewer-a"
    assert result.decision_record.decision is HumanReviewDecisionType.APPROVE
    assert snapshot is not None and snapshot.pending_items == []
    assert len(snapshot.decision_history) == 1


def test_human_review_interface_rejects_request(tmp_path):
    interface = HumanReviewInterface(storage_path=str(Path(tmp_path) / "review.json"))
    interface.submit_request(make_request())

    result = interface.reject(request_id="req-1", reviewer_id="reviewer-b", reason="Too many records affected.")

    assert result.succeeded is True
    assert result.response is not None and result.response.decision is ApprovalDecision.REJECT
    assert result.decision_record is not None
    assert result.decision_record.decision is HumanReviewDecisionType.REJECT
    assert result.decision_record.reason == "Too many records affected."


def test_human_review_interface_modify_option_updates_action_parameters_before_approval(tmp_path):
    interface = HumanReviewInterface(storage_path=str(Path(tmp_path) / "review.json"))
    interface.submit_request(make_request(), workflow_context={"account": "seller-a"})

    result = interface.modify_and_approve(
        request_id="req-1",
        reviewer_id="reviewer-c",
        modified_parameters={"batch_size": 25, "target_group": "inactive-test"},
        reason="Reduce blast radius before approval.",
    )
    snapshot = interface.list_pending_requests().snapshot

    assert result.succeeded is True
    assert result.response is not None and result.response.decision is ApprovalDecision.APPROVE
    assert result.response.modified_parameters == {"batch_size": 25, "target_group": "inactive-test"}
    assert result.decision_record is not None
    assert result.decision_record.decision is HumanReviewDecisionType.MODIFY
    assert result.decision_record.modified_parameters["batch_size"] == 25
    assert snapshot is not None
    assert len(snapshot.decision_history) == 1
    assert snapshot.decision_history[0].reviewer_id == "reviewer-c"
