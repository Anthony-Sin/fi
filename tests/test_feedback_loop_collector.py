from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_perception.knowledge import FeedbackLoopCollector, SelfCritiqueImprovementLoop
from desktop_automation_perception.models import (
    ApprovalDecision,
    ApprovalGateAction,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalRiskLevel,
    HumanReviewDecisionRecord,
    HumanReviewDecisionType,
    HumanReviewPendingItem,
    ImprovementProposalStatus,
)


def make_request(request_id: str, workflow_id: str = "wf-1") -> ApprovalRequest:
    now = datetime(2026, 4, 8, 12, 0)
    return ApprovalRequest(
        request_id=request_id,
        action=ApprovalGateAction(
            workflow_id=workflow_id,
            step_id="delete-users",
            action_type="delete_users",
            description="Delete inactive users",
            application_name="admin-portal",
            risk_level=ApprovalRiskLevel.IRREVERSIBLE,
            blast_radius=250,
            context_data={"batch_size": "250", "target_group": "inactive"},
            expected_consequences=["User records will be permanently removed"],
        ),
        reviewer_channel="ops-review",
        created_at=now,
        expires_at=now + timedelta(minutes=30),
        proposed_effects=["User records will be permanently removed"],
    )


def test_feedback_loop_collector_records_approval_rejection_and_modification(tmp_path):
    loop = SelfCritiqueImprovementLoop(storage_path=str(Path(tmp_path) / "improvements.json"))
    collector = FeedbackLoopCollector(
        storage_path=str(Path(tmp_path) / "feedback.json"),
        self_improvement_module=loop,
    )
    request = make_request("req-1")

    rejected = collector.record_approval_feedback(
        request=request,
        response=ApprovalResponse(
            request_id="req-1",
            decision=ApprovalDecision.REJECT,
            reviewer_id="reviewer-a",
            reason="Scope is too broad.",
            responded_at=datetime(2026, 4, 8, 12, 5),
        ),
        workflow_context={"account": "seller-a"},
    )
    modified = collector.record_approval_feedback(
        request=make_request("req-2"),
        response=ApprovalResponse(
            request_id="req-2",
            decision=ApprovalDecision.APPROVE,
            reviewer_id="reviewer-b",
            reason="Reduce batch size first.",
            modified_parameters={"batch_size": 25},
            responded_at=datetime(2026, 4, 8, 12, 10),
        ),
        workflow_context={"account": "seller-b"},
    )

    assert rejected.succeeded is True
    assert rejected.event is not None and rejected.event.event_type.value == "approval_rejected"
    assert modified.succeeded is True
    assert modified.event is not None and modified.event.event_type.value == "approval_modified"
    assert modified.event.modified_action["batch_size"] == "25"


def test_feedback_loop_collector_records_human_review_modifications(tmp_path):
    loop = SelfCritiqueImprovementLoop(storage_path=str(Path(tmp_path) / "improvements.json"))
    collector = FeedbackLoopCollector(
        storage_path=str(Path(tmp_path) / "feedback.json"),
        self_improvement_module=loop,
    )
    pending_item = HumanReviewPendingItem(
        request=make_request("req-3"),
        workflow_context={"build_id": "build-7"},
    )
    decision = HumanReviewDecisionRecord(
        request_id="req-3",
        reviewer_id="reviewer-c",
        decision=HumanReviewDecisionType.MODIFY,
        decided_at=datetime(2026, 4, 8, 12, 15),
        reason="Use a smaller pilot group.",
        modified_parameters={"target_group": "inactive-pilot", "batch_size": 10},
    )

    result = collector.record_human_review_feedback(
        pending_item=pending_item,
        decision_record=decision,
    )

    assert result.succeeded is True
    assert result.event is not None
    assert result.event.event_type.value == "human_review_modified"
    assert result.event.context_data["build_id"] == "build-7"


def test_feedback_loop_collector_aggregates_patterns_and_generates_improvement_suggestions(tmp_path):
    loop = SelfCritiqueImprovementLoop(storage_path=str(Path(tmp_path) / "improvements.json"))
    collector = FeedbackLoopCollector(
        storage_path=str(Path(tmp_path) / "feedback.json"),
        self_improvement_module=loop,
        recurring_pattern_threshold=2,
    )

    collector.record_approval_feedback(
        request=make_request("req-4", workflow_id="wf-2"),
        response=ApprovalResponse(
            request_id="req-4",
            decision=ApprovalDecision.REJECT,
            reviewer_id="reviewer-a",
            reason="Scope is too broad.",
            responded_at=datetime(2026, 4, 8, 12, 20),
        ),
        workflow_context={"account": "seller-a"},
    )
    collector.record_approval_feedback(
        request=make_request("req-5", workflow_id="wf-3"),
        response=ApprovalResponse(
            request_id="req-5",
            decision=ApprovalDecision.REJECT,
            reviewer_id="reviewer-b",
            reason="Scope is too broad.",
            responded_at=datetime(2026, 4, 8, 12, 25),
        ),
        workflow_context={"account": "seller-b"},
    )

    result = collector.generate_improvement_suggestions(require_human_review=True)
    proposals = loop.list_proposals()

    assert result.succeeded is True
    assert len(result.patterns) >= 1
    assert len(result.proposals) == 1
    assert proposals[0].target_identifier == "delete_users"
    assert proposals[0].status is ImprovementProposalStatus.REVIEW_PENDING
    assert "scope" in (proposals[0].failure_summary or "").casefold()
