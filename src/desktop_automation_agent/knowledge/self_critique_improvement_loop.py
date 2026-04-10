from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .exceptions import ConfigurationError
from desktop_automation_agent.models import (
    FailureArchiveRecord,
    ImprovementProposalRecord,
    ImprovementProposalStatus,
    ImprovementTargetType,
    PromptPerformanceRecord,
    SelfCritiqueResult,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SelfCritiqueImprovementLoop:
    storage_path: str
    step_failure_threshold: int = 3
    workflow_failure_threshold: int = 5
    prompt_failure_threshold: int = 3
    low_prompt_success_rate_threshold: float = 0.6
    human_review_callback: Callable[[ImprovementProposalRecord], bool] | None = None
    apply_callback: Callable[[ImprovementProposalRecord], object] | None = None
    rules_path: str = "src/desktop_automation_agent/knowledge/improvement_rules.json"

    def __post_init__(self) -> None:
        """Validate storage and rules on startup."""
        try:
            self._load_proposals()
            self._load_rules()
        except ConfigurationError as e:
            logger.error(f"Failed to initialize SelfCritiqueImprovementLoop: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during SelfCritiqueImprovementLoop initialization: {e}")
            raise ConfigurationError(f"Unexpected error during initialization: {e}") from e

    def review_failures(
        self,
        *,
        failure_records: list[FailureArchiveRecord],
        prompt_performance_records: list[PromptPerformanceRecord] | None = None,
        require_human_review: bool = False,
    ) -> SelfCritiqueResult:
        proposals: list[ImprovementProposalRecord] = []
        proposals.extend(self._proposals_for_steps(failure_records, require_human_review))
        proposals.extend(self._proposals_for_workflows(failure_records, require_human_review))
        proposals.extend(self._proposals_for_prompts(prompt_performance_records or [], require_human_review))

        if not proposals:
            return SelfCritiqueResult(
                succeeded=False,
                reason="No repeated failure pattern met the improvement threshold.",
            )

        saved: list[ImprovementProposalRecord] = []
        for proposal in proposals:
            finalized = self._finalize_proposal(proposal)
            self._append_proposal(finalized)
            saved.append(finalized)

        return SelfCritiqueResult(
            succeeded=True,
            proposal=saved[0],
            proposals=saved,
        )

    def record_improvement_outcome(
        self,
        *,
        proposal_id: str,
        succeeded: bool,
    ) -> SelfCritiqueResult:
        try:
            proposals = self._load_proposals()
        except Exception as e:
            logger.warning(f"Failed to load proposals for outcome recording: {e}")
            return SelfCritiqueResult(succeeded=False, reason=str(e))

        updated: ImprovementProposalRecord | None = None
        new_proposals: list[ImprovementProposalRecord] = []
        for proposal in proposals:
            if proposal.proposal_id == proposal_id:
                if succeeded:
                    proposal = ImprovementProposalRecord(
                        proposal_id=proposal.proposal_id,
                        target_type=proposal.target_type,
                        target_identifier=proposal.target_identifier,
                        workflow_id=proposal.workflow_id,
                        failure_count=proposal.failure_count,
                        failure_summary=proposal.failure_summary,
                        proposed_modification=proposal.proposed_modification,
                        status=proposal.status,
                        human_review_required=proposal.human_review_required,
                        baseline_success_count=proposal.baseline_success_count,
                        baseline_failure_count=proposal.baseline_failure_count,
                        post_apply_success_count=proposal.post_apply_success_count + 1,
                        post_apply_failure_count=proposal.post_apply_failure_count,
                        created_at=proposal.created_at,
                        applied_at=proposal.applied_at,
                        review_note=proposal.review_note,
                    )
                else:
                    proposal = ImprovementProposalRecord(
                        proposal_id=proposal.proposal_id,
                        target_type=proposal.target_type,
                        target_identifier=proposal.target_identifier,
                        workflow_id=proposal.workflow_id,
                        failure_count=proposal.failure_count,
                        failure_summary=proposal.failure_summary,
                        proposed_modification=proposal.proposed_modification,
                        status=proposal.status,
                        human_review_required=proposal.human_review_required,
                        baseline_success_count=proposal.baseline_success_count,
                        baseline_failure_count=proposal.baseline_failure_count,
                        post_apply_success_count=proposal.post_apply_success_count,
                        post_apply_failure_count=proposal.post_apply_failure_count + 1,
                        created_at=proposal.created_at,
                        applied_at=proposal.applied_at,
                        review_note=proposal.review_note,
                    )
                updated = proposal
            new_proposals.append(proposal)

        if updated is None:
            logger.warning(f"Improvement proposal not found: {proposal_id}")
            return SelfCritiqueResult(succeeded=False, reason=f"Improvement proposal not found: {proposal_id}")

        self._save_proposals(new_proposals)
        return SelfCritiqueResult(succeeded=True, proposal=updated)

    def list_proposals(self) -> list[ImprovementProposalRecord]:
        return self._load_proposals()

    def ingest_proposals(
        self,
        proposals: list[ImprovementProposalRecord],
    ) -> SelfCritiqueResult:
        if not proposals:
            return SelfCritiqueResult(succeeded=False, reason="No improvement proposals were supplied.")

        saved: list[ImprovementProposalRecord] = []
        for proposal in proposals:
            finalized = self._finalize_proposal(proposal)
            self._append_proposal(finalized)
            saved.append(finalized)

        return SelfCritiqueResult(
            succeeded=True,
            proposal=saved[0],
            proposals=saved,
        )

    def _proposals_for_steps(
        self,
        failure_records: list[FailureArchiveRecord],
        require_human_review: bool,
    ) -> list[ImprovementProposalRecord]:
        grouped: dict[tuple[str | None, str], list[FailureArchiveRecord]] = defaultdict(list)
        for record in failure_records:
            grouped[(record.workflow_id, record.step_name)].append(record)

        proposals: list[ImprovementProposalRecord] = []
        for (workflow_id, step_name), records in grouped.items():
            if len(records) < self.step_failure_threshold:
                continue
            summary = self._summarize_failure_messages(record.exception_message for record in records)
            proposals.append(
                ImprovementProposalRecord(
                    proposal_id=str(uuid4()),
                    target_type=ImprovementTargetType.STEP,
                    target_identifier=step_name,
                    workflow_id=workflow_id,
                    failure_count=len(records),
                    failure_summary=summary,
                    proposed_modification=self._propose_step_change(step_name, summary),
                    status=ImprovementProposalStatus.REVIEW_PENDING if require_human_review else ImprovementProposalStatus.PROPOSED,
                    human_review_required=require_human_review,
                    baseline_failure_count=len(records),
                )
            )
        return proposals

    def _proposals_for_workflows(
        self,
        failure_records: list[FailureArchiveRecord],
        require_human_review: bool,
    ) -> list[ImprovementProposalRecord]:
        grouped: dict[str, list[FailureArchiveRecord]] = defaultdict(list)
        for record in failure_records:
            grouped[record.workflow_id].append(record)

        proposals: list[ImprovementProposalRecord] = []
        for workflow_id, records in grouped.items():
            if len(records) < self.workflow_failure_threshold:
                continue
            summary = self._summarize_failure_messages(record.exception_message for record in records)
            proposals.append(
                ImprovementProposalRecord(
                    proposal_id=str(uuid4()),
                    target_type=ImprovementTargetType.WORKFLOW,
                    target_identifier=workflow_id,
                    workflow_id=workflow_id,
                    failure_count=len(records),
                    failure_summary=summary,
                    proposed_modification=(
                        f"Revise workflow '{workflow_id}' to add stronger pre-condition checks and a recovery branch "
                        f"before the most failure-prone steps."
                    ),
                    status=ImprovementProposalStatus.REVIEW_PENDING if require_human_review else ImprovementProposalStatus.PROPOSED,
                    human_review_required=require_human_review,
                    baseline_failure_count=len(records),
                )
            )
        return proposals

    def _proposals_for_prompts(
        self,
        prompt_records: list[PromptPerformanceRecord],
        require_human_review: bool,
    ) -> list[ImprovementProposalRecord]:
        grouped: dict[str, list[PromptPerformanceRecord]] = defaultdict(list)
        for record in prompt_records:
            grouped[record.template_name].append(record)

        proposals: list[ImprovementProposalRecord] = []
        for template_name, records in grouped.items():
            if len(records) < self.prompt_failure_threshold:
                continue
            success_count = sum(1 for record in records if record.succeeded)
            success_rate = success_count / len(records)
            if success_rate >= self.low_prompt_success_rate_threshold:
                continue
            summary = self._summarize_failure_messages(
                "expected format not met" if not record.expected_format_met else "submission failed"
                for record in records
                if not record.succeeded or not record.expected_format_met
            )
            proposals.append(
                ImprovementProposalRecord(
                    proposal_id=str(uuid4()),
                    target_type=ImprovementTargetType.PROMPT_TEMPLATE,
                    target_identifier=template_name,
                    failure_count=len(records) - success_count,
                    failure_summary=summary,
                    proposed_modification=(
                        f"Revise template '{template_name}' to make the required output format explicit and add a "
                        f"short corrective example."
                    ),
                    status=ImprovementProposalStatus.REVIEW_PENDING if require_human_review else ImprovementProposalStatus.PROPOSED,
                    human_review_required=require_human_review,
                    baseline_success_count=success_count,
                    baseline_failure_count=len(records) - success_count,
                )
            )
        return proposals

    def _finalize_proposal(
        self,
        proposal: ImprovementProposalRecord,
    ) -> ImprovementProposalRecord:
        if proposal.human_review_required:
            if self.human_review_callback is None:
                return proposal
            approved = self.human_review_callback(proposal)
            if not approved:
                return ImprovementProposalRecord(
                    proposal_id=proposal.proposal_id,
                    target_type=proposal.target_type,
                    target_identifier=proposal.target_identifier,
                    workflow_id=proposal.workflow_id,
                    failure_count=proposal.failure_count,
                    failure_summary=proposal.failure_summary,
                    proposed_modification=proposal.proposed_modification,
                    status=ImprovementProposalStatus.REJECTED,
                    human_review_required=True,
                    baseline_success_count=proposal.baseline_success_count,
                    baseline_failure_count=proposal.baseline_failure_count,
                    post_apply_success_count=proposal.post_apply_success_count,
                    post_apply_failure_count=proposal.post_apply_failure_count,
                    created_at=proposal.created_at,
                    applied_at=None,
                    review_note="Rejected during human review.",
                )

        if self.apply_callback is None:
            return proposal

        self.apply_callback(proposal)
        return ImprovementProposalRecord(
            proposal_id=proposal.proposal_id,
            target_type=proposal.target_type,
            target_identifier=proposal.target_identifier,
            workflow_id=proposal.workflow_id,
            failure_count=proposal.failure_count,
            failure_summary=proposal.failure_summary,
            proposed_modification=proposal.proposed_modification,
            status=ImprovementProposalStatus.APPLIED,
            human_review_required=proposal.human_review_required,
            baseline_success_count=proposal.baseline_success_count,
            baseline_failure_count=proposal.baseline_failure_count,
            post_apply_success_count=proposal.post_apply_success_count,
            post_apply_failure_count=proposal.post_apply_failure_count,
            created_at=proposal.created_at,
            applied_at=utc_now(),
            review_note=proposal.review_note,
        )

    def _summarize_failure_messages(
        self,
        messages,
    ) -> str:
        counter = Counter(message.strip() for message in messages if message)
        if not counter:
            return "Repeated failures with no detailed message."
        most_common = [message for message, _ in counter.most_common(3)]
        return "; ".join(most_common)

    def _propose_step_change(
        self,
        step_name: str,
        summary: str,
    ) -> str:
        normalized = summary.casefold()
        rules = self._load_rules()
        sc_rules = rules.get("self_critique", {})

        for entry in sc_rules.get("step_changes", []):
            if any(kw in normalized for kw in entry.get("keywords", [])):
                return entry["template"].format(step_name=step_name)

        return sc_rules.get("default_template", "Revise step '{step_name}'.").format(step_name=step_name)

    def _load_rules(self) -> dict[str, Any]:
        path = Path(self.rules_path)
        if not path.exists():
            logger.warning(f"Improvement rules file not found at {self.rules_path}")
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load improvement rules: {e}")
            return {}

    def _append_proposal(
        self,
        proposal: ImprovementProposalRecord,
    ) -> None:
        proposals = self._load_proposals()
        proposals.append(proposal)
        self._save_proposals(proposals)

    def _load_proposals(self) -> list[ImprovementProposalRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"Malformed JSON in improvement proposals at {self.storage_path}: {e}")
            raise ConfigurationError(f"Malformed JSON in improvement proposals: {e}") from e

        if not isinstance(payload, dict):
            raise ConfigurationError(f"Proposals payload must be a JSON object, got {type(payload).__name__}")

        return [self._deserialize_proposal(item) for item in payload.get("proposals", [])]

    def _save_proposals(
        self,
        proposals: list[ImprovementProposalRecord],
    ) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"proposals": [self._serialize_proposal(item) for item in proposals]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_proposal(
        self,
        proposal: ImprovementProposalRecord,
    ) -> dict:
        return {
            "proposal_id": proposal.proposal_id,
            "target_type": proposal.target_type.value,
            "target_identifier": proposal.target_identifier,
            "workflow_id": proposal.workflow_id,
            "failure_count": proposal.failure_count,
            "failure_summary": proposal.failure_summary,
            "proposed_modification": proposal.proposed_modification,
            "status": proposal.status.value,
            "human_review_required": proposal.human_review_required,
            "baseline_success_count": proposal.baseline_success_count,
            "baseline_failure_count": proposal.baseline_failure_count,
            "post_apply_success_count": proposal.post_apply_success_count,
            "post_apply_failure_count": proposal.post_apply_failure_count,
            "created_at": proposal.created_at.isoformat(),
            "applied_at": None if proposal.applied_at is None else proposal.applied_at.isoformat(),
            "review_note": proposal.review_note,
        }

    def _deserialize_proposal(
        self,
        payload: dict,
    ) -> ImprovementProposalRecord:
        required_fields = ("proposal_id", "target_type", "target_identifier", "created_at")
        for field in required_fields:
            if field not in payload:
                raise ConfigurationError(f"Missing required field '{field}' in proposal payload")

        try:
            return ImprovementProposalRecord(
                proposal_id=str(payload["proposal_id"]),
                target_type=ImprovementTargetType(payload["target_type"]),
                target_identifier=str(payload["target_identifier"]),
                workflow_id=payload.get("workflow_id"),
                failure_count=int(payload.get("failure_count", 0)),
                failure_summary=payload.get("failure_summary"),
                proposed_modification=payload.get("proposed_modification"),
                status=ImprovementProposalStatus(payload.get("status", ImprovementProposalStatus.PROPOSED.value)),
                human_review_required=bool(payload.get("human_review_required", False)),
                baseline_success_count=int(payload.get("baseline_success_count", 0)),
                baseline_failure_count=int(payload.get("baseline_failure_count", 0)),
                post_apply_success_count=int(payload.get("post_apply_success_count", 0)),
                post_apply_failure_count=int(payload.get("post_apply_failure_count", 0)),
                created_at=datetime.fromisoformat(payload["created_at"]),
                applied_at=None if payload.get("applied_at") is None else datetime.fromisoformat(payload["applied_at"]),
                review_note=payload.get("review_note"),
            )
        except (ValueError, TypeError) as e:
            raise ConfigurationError(f"Malformed proposal record for '{payload.get('proposal_id', 'unknown')}': {e}") from e


