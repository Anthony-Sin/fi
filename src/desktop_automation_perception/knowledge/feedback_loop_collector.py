from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from desktop_automation_perception.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    FeedbackEventRecord,
    FeedbackEventType,
    FeedbackLoopResult,
    FeedbackPatternSummary,
    HumanReviewDecisionRecord,
    HumanReviewDecisionType,
    HumanReviewPendingItem,
    ImprovementProposalRecord,
    ImprovementProposalStatus,
    ImprovementTargetType,
)


@dataclass(slots=True)
class FeedbackLoopCollector:
    storage_path: str
    self_improvement_module: object
    recurring_pattern_threshold: int = 2

    def record_approval_feedback(
        self,
        *,
        request: ApprovalRequest,
        response: ApprovalResponse,
        workflow_context: dict[str, Any] | None = None,
    ) -> FeedbackLoopResult:
        event_type = {
            ApprovalDecision.REJECT: FeedbackEventType.APPROVAL_REJECTED,
            ApprovalDecision.APPROVE: FeedbackEventType.APPROVAL_MODIFIED if response.modified_parameters else None,
            ApprovalDecision.PROCEED_WITH_CAUTION: FeedbackEventType.APPROVAL_MODIFIED if response.modified_parameters else None,
        }.get(response.decision)
        if event_type is None:
            return FeedbackLoopResult(succeeded=False, reason="Approval response did not contain corrective feedback.")

        event = FeedbackEventRecord(
            feedback_id=str(uuid4()),
            workflow_id=request.action.workflow_id,
            step_id=request.action.step_id,
            action_type=request.action.action_type,
            event_type=event_type,
            reviewer_id=response.reviewer_id,
            original_action=self._serialize_action(request.action),
            modified_action={**request.action.context_data, **{key: str(value) for key, value in response.modified_parameters.items()}},
            reason=response.reason,
            context_data={} if workflow_context is None else dict(workflow_context),
            recorded_at=response.responded_at,
        )
        return self._append_event(event)

    def record_human_review_feedback(
        self,
        *,
        pending_item: HumanReviewPendingItem,
        decision_record: HumanReviewDecisionRecord,
    ) -> FeedbackLoopResult:
        event_type = {
            HumanReviewDecisionType.REJECT: FeedbackEventType.HUMAN_REVIEW_REJECTED,
            HumanReviewDecisionType.MODIFY: FeedbackEventType.HUMAN_REVIEW_MODIFIED,
        }.get(decision_record.decision)
        if event_type is None:
            return FeedbackLoopResult(succeeded=False, reason="Human review decision did not contain corrective feedback.")

        event = FeedbackEventRecord(
            feedback_id=str(uuid4()),
            workflow_id=pending_item.request.action.workflow_id,
            step_id=pending_item.request.action.step_id,
            action_type=pending_item.request.action.action_type,
            event_type=event_type,
            reviewer_id=decision_record.reviewer_id,
            original_action=self._serialize_action(pending_item.request.action),
            modified_action={**pending_item.request.action.context_data, **{key: str(value) for key, value in decision_record.modified_parameters.items()}},
            reason=decision_record.reason,
            context_data=dict(pending_item.workflow_context),
            recorded_at=decision_record.decided_at,
        )
        return self._append_event(event)

    def generate_improvement_suggestions(
        self,
        *,
        require_human_review: bool = True,
    ) -> FeedbackLoopResult:
        events = self._load_events()
        patterns = self._aggregate_patterns(events)
        proposals: list[ImprovementProposalRecord] = []

        for pattern in patterns:
            if pattern.event_count < self.recurring_pattern_threshold:
                continue
            proposals.append(
                ImprovementProposalRecord(
                    proposal_id=str(uuid4()),
                    target_type=ImprovementTargetType.STEP,
                    target_identifier=pattern.action_type or pattern.pattern_key,
                    workflow_id=pattern.workflow_ids[0] if pattern.workflow_ids else None,
                    failure_count=pattern.event_count,
                    failure_summary="; ".join(pattern.common_reasons) if pattern.common_reasons else "Recurring reviewer correction pattern.",
                    proposed_modification=pattern.suggested_change,
                    status=ImprovementProposalStatus.REVIEW_PENDING if require_human_review else ImprovementProposalStatus.PROPOSED,
                    human_review_required=require_human_review,
                    baseline_failure_count=pattern.event_count,
                )
            )

        if not proposals:
            return FeedbackLoopResult(
                succeeded=False,
                events=events,
                patterns=patterns,
                reason="No recurring feedback pattern met the suggestion threshold.",
            )

        ingest_result = self.self_improvement_module.ingest_proposals(proposals)
        return FeedbackLoopResult(
            succeeded=ingest_result.succeeded,
            events=events,
            patterns=patterns,
            proposals=list(getattr(ingest_result, "proposals", [])),
            reason=getattr(ingest_result, "reason", None),
        )

    def list_feedback_events(self) -> FeedbackLoopResult:
        events = self._load_events()
        patterns = self._aggregate_patterns(events)
        return FeedbackLoopResult(succeeded=True, events=events, patterns=patterns)

    def _append_event(self, event: FeedbackEventRecord) -> FeedbackLoopResult:
        events = self._load_events()
        events.append(event)
        self._save_events(events)
        return FeedbackLoopResult(succeeded=True, event=event, events=events)

    def _aggregate_patterns(self, events: list[FeedbackEventRecord]) -> list[FeedbackPatternSummary]:
        grouped: dict[tuple[str, FeedbackEventType], list[FeedbackEventRecord]] = defaultdict(list)
        for event in events:
            grouped[(event.action_type.casefold(), event.event_type)].append(event)

        patterns: list[FeedbackPatternSummary] = []
        for (action_key, event_type), items in grouped.items():
            reasons = [item.reason.strip() for item in items if item.reason]
            common_reasons = [message for message, _ in Counter(reasons).most_common(3)]
            action_type = items[0].action_type if items else action_key
            patterns.append(
                FeedbackPatternSummary(
                    pattern_key=f"{action_key}:{event_type.value}",
                    event_count=len(items),
                    workflow_ids=sorted({item.workflow_id for item in items}),
                    action_type=action_type,
                    event_type=event_type,
                    common_reasons=common_reasons,
                    suggested_change=self._suggest_change(action_type, event_type, common_reasons),
                )
            )
        patterns.sort(key=lambda item: (-item.event_count, item.pattern_key))
        return patterns

    def _suggest_change(
        self,
        action_type: str,
        event_type: FeedbackEventType,
        common_reasons: list[str],
    ) -> str:
        summary = " ".join(common_reasons).casefold()
        if event_type in (FeedbackEventType.APPROVAL_MODIFIED, FeedbackEventType.HUMAN_REVIEW_MODIFIED):
            if "batch" in summary or "blast" in summary or "too many" in summary:
                return f"Reduce default blast radius and parameter ranges for action '{action_type}', and require narrower scoped inputs."
            return f"Update planning for action '{action_type}' to pre-fill safer parameter defaults and expose reviewer-adjustable controls earlier."
        if "scope" in summary or "wrong" in summary:
            return f"Add stronger pre-condition validation and target verification before executing action '{action_type}'."
        if "security" in summary or "sensitive" in summary:
            return f"Raise the approval threshold and add explicit security checks before action '{action_type}'."
        return f"Add a reviewer-informed safeguard and clearer action preview for '{action_type}' before execution."

    def _serialize_action(self, action) -> dict[str, Any]:
        return {
            "workflow_id": action.workflow_id,
            "step_id": action.step_id,
            "action_type": action.action_type,
            "description": action.description,
            "application_name": action.application_name,
            "risk_level": action.risk_level.value,
            "blast_radius": action.blast_radius,
            "context_data": dict(action.context_data),
            "expected_consequences": list(action.expected_consequences),
        }

    def _load_events(self) -> list[FeedbackEventRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_event(item) for item in payload.get("events", [])]

    def _save_events(self, events: list[FeedbackEventRecord]) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": [self._serialize_event(item) for item in events]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_event(self, event: FeedbackEventRecord) -> dict[str, Any]:
        return {
            "feedback_id": event.feedback_id,
            "workflow_id": event.workflow_id,
            "step_id": event.step_id,
            "action_type": event.action_type,
            "event_type": event.event_type.value,
            "reviewer_id": event.reviewer_id,
            "original_action": dict(event.original_action),
            "modified_action": dict(event.modified_action),
            "reason": event.reason,
            "context_data": dict(event.context_data),
            "recorded_at": event.recorded_at.isoformat(),
        }

    def _deserialize_event(self, payload: dict[str, Any]) -> FeedbackEventRecord:
        return FeedbackEventRecord(
            feedback_id=payload["feedback_id"],
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            action_type=payload["action_type"],
            event_type=FeedbackEventType(payload["event_type"]),
            reviewer_id=payload.get("reviewer_id"),
            original_action=dict(payload.get("original_action", {})),
            modified_action=dict(payload.get("modified_action", {})),
            reason=payload.get("reason"),
            context_data=dict(payload.get("context_data", {})),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )
