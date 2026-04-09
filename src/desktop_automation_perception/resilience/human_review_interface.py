from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from desktop_automation_perception.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    HumanReviewDecisionRecord,
    HumanReviewDecisionType,
    HumanReviewInterfaceResult,
    HumanReviewInterfaceSnapshot,
    HumanReviewPendingItem,
)


@dataclass(slots=True)
class HumanReviewInterface:
    storage_path: str

    def submit_request(
        self,
        request: ApprovalRequest,
        *,
        workflow_context: dict | None = None,
    ) -> HumanReviewInterfaceResult:
        snapshot = self._load_snapshot()
        item = HumanReviewPendingItem(
            request=self._copy_request(request),
            workflow_context={} if workflow_context is None else dict(workflow_context),
            action_summary=self._action_summary(request),
        )
        snapshot.pending_items = [existing for existing in snapshot.pending_items if existing.request.request_id != request.request_id]
        snapshot.pending_items.append(item)
        self._save_snapshot(snapshot)
        return HumanReviewInterfaceResult(
            succeeded=True,
            item=item,
            snapshot=snapshot,
            rendered_view=self.render_request(item),
        )

    def list_pending_requests(self) -> HumanReviewInterfaceResult:
        snapshot = self._load_snapshot()
        return HumanReviewInterfaceResult(
            succeeded=True,
            items=[self._copy_item(item) for item in snapshot.pending_items],
            snapshot=snapshot,
            rendered_view=self.render_pending(snapshot.pending_items),
        )

    def approve(
        self,
        *,
        request_id: str,
        reviewer_id: str,
        reason: str | None = None,
    ) -> HumanReviewInterfaceResult:
        return self._decide(
            request_id=request_id,
            reviewer_id=reviewer_id,
            decision=HumanReviewDecisionType.APPROVE,
            approval_decision=ApprovalDecision.APPROVE,
            reason=reason,
        )

    def reject(
        self,
        *,
        request_id: str,
        reviewer_id: str,
        reason: str | None = None,
    ) -> HumanReviewInterfaceResult:
        return self._decide(
            request_id=request_id,
            reviewer_id=reviewer_id,
            decision=HumanReviewDecisionType.REJECT,
            approval_decision=ApprovalDecision.REJECT,
            reason=reason,
        )

    def modify_and_approve(
        self,
        *,
        request_id: str,
        reviewer_id: str,
        modified_parameters: dict[str, object],
        reason: str | None = None,
    ) -> HumanReviewInterfaceResult:
        return self._decide(
            request_id=request_id,
            reviewer_id=reviewer_id,
            decision=HumanReviewDecisionType.MODIFY,
            approval_decision=ApprovalDecision.APPROVE,
            reason=reason,
            modified_parameters=modified_parameters,
        )

    def render_pending(self, items: list[HumanReviewPendingItem] | None = None) -> str:
        pending = [self._copy_item(item) for item in (items if items is not None else self._load_snapshot().pending_items)]
        if not pending:
            return "No pending approval requests."
        return "\n\n".join(self.render_request(item) for item in pending)

    def render_request(self, item: HumanReviewPendingItem) -> str:
        action = item.request.action
        consequences = (
            "\n".join(f"- {value}" for value in item.request.proposed_effects)
            if item.request.proposed_effects
            else "- No explicit consequences provided"
        )
        workflow_context = json.dumps(item.workflow_context, sort_keys=True) if item.workflow_context else "{}"
        action_context = json.dumps(action.context_data, sort_keys=True) if action.context_data else "{}"
        return (
            f"Request ID: {item.request.request_id}\n"
            f"Workflow: {action.workflow_id}\n"
            f"Step: {action.step_id}\n"
            f"Application: {action.application_name or 'Unknown'}\n"
            f"Action: {action.action_type}\n"
            f"Description: {action.description}\n"
            f"Risk: {action.risk_level.value}\n"
            f"Blast Radius: {action.blast_radius}\n"
            f"Workflow Context: {workflow_context}\n"
            f"Action Parameters: {action_context}\n"
            f"Expected Outcome If Approved:\n{consequences}\n"
            f"Available Decisions: approve | reject | modify"
        )

    def _decide(
        self,
        *,
        request_id: str,
        reviewer_id: str,
        decision: HumanReviewDecisionType,
        approval_decision: ApprovalDecision,
        reason: str | None = None,
        modified_parameters: dict[str, object] | None = None,
    ) -> HumanReviewInterfaceResult:
        snapshot = self._load_snapshot()
        item = next((pending for pending in snapshot.pending_items if pending.request.request_id == request_id), None)
        if item is None:
            return HumanReviewInterfaceResult(succeeded=False, reason="Approval request was not found.", snapshot=snapshot)

        modified_payload = {} if modified_parameters is None else dict(modified_parameters)
        response = ApprovalResponse(
            request_id=request_id,
            decision=approval_decision,
            reviewer_id=reviewer_id,
            responded_at=utc_now(),
            reason=reason,
            modified_parameters=modified_payload,
        )
        if modified_payload:
            item.request.action.context_data = {**item.request.action.context_data, **{key: str(value) for key, value in modified_payload.items()}}

        record = HumanReviewDecisionRecord(
            request_id=request_id,
            reviewer_id=reviewer_id,
            decision=decision,
            decided_at=response.responded_at,
            reason=reason,
            modified_parameters=modified_payload,
        )
        snapshot.pending_items = [pending for pending in snapshot.pending_items if pending.request.request_id != request_id]
        snapshot.decision_history.append(record)
        self._save_snapshot(snapshot)
        return HumanReviewInterfaceResult(
            succeeded=True,
            decision_record=record,
            response=response,
            snapshot=snapshot,
        )

    def _load_snapshot(self) -> HumanReviewInterfaceSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return HumanReviewInterfaceSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return HumanReviewInterfaceSnapshot(
            pending_items=[self._deserialize_item(item) for item in payload.get("pending_items", [])],
            decision_history=[self._deserialize_decision(item) for item in payload.get("decision_history", [])],
        )

    def _save_snapshot(self, snapshot: HumanReviewInterfaceSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pending_items": [self._serialize_item(item) for item in snapshot.pending_items],
            "decision_history": [self._serialize_decision(item) for item in snapshot.decision_history],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_request(self, request: ApprovalRequest) -> ApprovalRequest:
        return self._deserialize_request(self._serialize_request(request))

    def _copy_item(self, item: HumanReviewPendingItem) -> HumanReviewPendingItem:
        return self._deserialize_item(self._serialize_item(item))

    def _action_summary(self, request: ApprovalRequest) -> str:
        return f"{request.action.action_type} on {request.action.application_name or 'unknown'} ({request.action.risk_level.value})"

    def _serialize_request(self, request: ApprovalRequest) -> dict:
        return {
            "request_id": request.request_id,
            "action": {
                "workflow_id": request.action.workflow_id,
                "step_id": request.action.step_id,
                "action_type": request.action.action_type,
                "description": request.action.description,
                "application_name": request.action.application_name,
                "risk_level": request.action.risk_level.value,
                "blast_radius": request.action.blast_radius,
                "context_data": dict(request.action.context_data),
                "expected_consequences": list(request.action.expected_consequences),
            },
            "reviewer_channel": request.reviewer_channel,
            "created_at": request.created_at.isoformat(),
            "expires_at": request.expires_at.isoformat(),
            "proposed_effects": list(request.proposed_effects),
        }

    def _deserialize_request(self, payload: dict) -> ApprovalRequest:
        from desktop_automation_perception.models import ApprovalGateAction, ApprovalRiskLevel

        return ApprovalRequest(
            request_id=payload["request_id"],
            action=ApprovalGateAction(
                workflow_id=payload["action"]["workflow_id"],
                step_id=payload["action"]["step_id"],
                action_type=payload["action"]["action_type"],
                description=payload["action"]["description"],
                application_name=payload["action"].get("application_name"),
                risk_level=ApprovalRiskLevel(payload["action"]["risk_level"]),
                blast_radius=int(payload["action"].get("blast_radius", 1)),
                context_data={key: value for key, value in payload["action"].get("context_data", {}).items()},
                expected_consequences=list(payload["action"].get("expected_consequences", [])),
            ),
            reviewer_channel=payload["reviewer_channel"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
            proposed_effects=list(payload.get("proposed_effects", [])),
        )

    def _serialize_item(self, item: HumanReviewPendingItem) -> dict:
        return {
            "request": self._serialize_request(item.request),
            "workflow_context": dict(item.workflow_context),
            "action_summary": item.action_summary,
        }

    def _deserialize_item(self, payload: dict) -> HumanReviewPendingItem:
        return HumanReviewPendingItem(
            request=self._deserialize_request(payload["request"]),
            workflow_context=dict(payload.get("workflow_context", {})),
            action_summary=payload.get("action_summary"),
        )

    def _serialize_decision(self, record: HumanReviewDecisionRecord) -> dict:
        return {
            "request_id": record.request_id,
            "reviewer_id": record.reviewer_id,
            "decision": record.decision.value,
            "decided_at": record.decided_at.isoformat(),
            "reason": record.reason,
            "modified_parameters": dict(record.modified_parameters),
        }

    def _deserialize_decision(self, payload: dict) -> HumanReviewDecisionRecord:
        return HumanReviewDecisionRecord(
            request_id=payload["request_id"],
            reviewer_id=payload["reviewer_id"],
            decision=HumanReviewDecisionType(payload["decision"]),
            decided_at=datetime.fromisoformat(payload["decided_at"]),
            reason=payload.get("reason"),
            modified_parameters=dict(payload.get("modified_parameters", {})),
        )


