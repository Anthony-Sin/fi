from datetime import datetime
from pathlib import Path

from desktop_automation_perception.models import FailureArchiveRecord, PromptPerformanceRecord
from desktop_automation_perception.self_critique_improvement_loop import SelfCritiqueImprovementLoop


def make_failure(record_id, workflow_id, step_name, message):
    return FailureArchiveRecord(
        record_id=record_id,
        workflow_id=workflow_id,
        step_name=step_name,
        timestamp=datetime.utcnow(),
        exception_type="RuntimeError",
        exception_message=message,
    )


def test_self_critique_generates_step_improvement_proposal(tmp_path):
    applied = []
    loop = SelfCritiqueImprovementLoop(
        storage_path=str(Path(tmp_path) / "improvements.json"),
        step_failure_threshold=2,
        apply_callback=lambda proposal: applied.append(proposal.proposal_id),
    )

    result = loop.review_failures(
        failure_records=[
            make_failure("1", "wf-a", "submit", "loading timeout"),
            make_failure("2", "wf-a", "submit", "loading timeout"),
        ]
    )

    assert result.succeeded is True
    assert result.proposal is not None
    assert result.proposal.target_identifier == "submit"
    assert "wait" in (result.proposal.proposed_modification or "").casefold()
    assert applied == [result.proposal.proposal_id]


def test_self_critique_generates_workflow_level_proposal(tmp_path):
    loop = SelfCritiqueImprovementLoop(
        storage_path=str(Path(tmp_path) / "improvements.json"),
        workflow_failure_threshold=3,
    )

    result = loop.review_failures(
        failure_records=[
            make_failure("1", "wf-a", "s1", "a"),
            make_failure("2", "wf-a", "s2", "b"),
            make_failure("3", "wf-a", "s3", "c"),
        ]
    )

    workflow_proposals = [item for item in result.proposals if item.target_identifier == "wf-a"]
    assert workflow_proposals
    assert workflow_proposals[0].target_type.value == "workflow"


def test_self_critique_generates_prompt_template_proposal_for_low_success(tmp_path):
    loop = SelfCritiqueImprovementLoop(
        storage_path=str(Path(tmp_path) / "improvements.json"),
        prompt_failure_threshold=3,
        low_prompt_success_rate_threshold=0.5,
    )

    result = loop.review_failures(
        failure_records=[],
        prompt_performance_records=[
            PromptPerformanceRecord(
                record_id="1",
                template_name="json-template",
                variables={},
                response_text="bad",
                expected_format_met=False,
                execution_time_seconds=1.0,
                succeeded=False,
            ),
            PromptPerformanceRecord(
                record_id="2",
                template_name="json-template",
                variables={},
                response_text="bad",
                expected_format_met=False,
                execution_time_seconds=1.1,
                succeeded=False,
            ),
            PromptPerformanceRecord(
                record_id="3",
                template_name="json-template",
                variables={},
                response_text="ok",
                expected_format_met=True,
                execution_time_seconds=1.2,
                succeeded=True,
            ),
        ],
    )

    proposal = next(item for item in result.proposals if item.target_identifier == "json-template")
    assert proposal.target_type.value == "prompt_template"
    assert "output format" in (proposal.proposed_modification or "").casefold()


def test_self_critique_can_require_human_review_before_applying(tmp_path):
    reviewed = []
    applied = []
    loop = SelfCritiqueImprovementLoop(
        storage_path=str(Path(tmp_path) / "improvements.json"),
        step_failure_threshold=2,
        human_review_callback=lambda proposal: reviewed.append(proposal.proposal_id) or False,
        apply_callback=lambda proposal: applied.append(proposal.proposal_id),
    )

    result = loop.review_failures(
        failure_records=[
            make_failure("1", "wf-a", "login", "session expired"),
            make_failure("2", "wf-a", "login", "session expired"),
        ],
        require_human_review=True,
    )

    assert result.proposal is not None
    assert result.proposal.status.value == "rejected"
    assert reviewed == [result.proposal.proposal_id]
    assert applied == []


def test_self_critique_tracks_post_apply_outcomes(tmp_path):
    loop = SelfCritiqueImprovementLoop(
        storage_path=str(Path(tmp_path) / "improvements.json"),
        step_failure_threshold=2,
    )
    proposal = loop.review_failures(
        failure_records=[
            make_failure("1", "wf-a", "submit", "timeout"),
            make_failure("2", "wf-a", "submit", "timeout"),
        ],
    ).proposal

    updated = loop.record_improvement_outcome(proposal_id=proposal.proposal_id, succeeded=True)
    updated = loop.record_improvement_outcome(proposal_id=proposal.proposal_id, succeeded=False)

    assert updated.proposal is not None
    assert updated.proposal.post_apply_success_count == 1
    assert updated.proposal.post_apply_failure_count == 1
