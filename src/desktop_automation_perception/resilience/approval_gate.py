from __future__ import annotations

from desktop_automation_perception._time import utc_now

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from desktop_automation_perception.models import (
    ApprovalDecision,
    ApprovalGateAction,
    ApprovalGateResult,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalRiskLevel,
    ApprovalTimeoutPolicy,
    NotificationEventType,
)


@dataclass(slots=True)
class ApprovalGateModule:
    reviewer_channel: str
    notification_dispatcher: object | None = None
    response_callback: Callable[[ApprovalRequest], ApprovalResponse | None] | None = None
    timeout_seconds: float = 300.0
    timeout_policy: ApprovalTimeoutPolicy = ApprovalTimeoutPolicy.REJECT
    high_blast_radius_threshold: int = 10
    now_fn: Callable[[], datetime] = utc_now

    def evaluate(self, action: ApprovalGateAction) -> ApprovalGateResult:
        risk_level = self._classify_risk(action)
        triggered_gate = self._should_gate(action, risk_level)
        if not triggered_gate:
            return ApprovalGateResult(
                succeeded=True,
                action=action,
                triggered_gate=False,
                risk_level=risk_level,
            )

        request = ApprovalRequest(
            request_id=str(uuid4()),
            action=action,
            reviewer_channel=self.reviewer_channel,
            created_at=self.now_fn(),
            expires_at=self.now_fn() + timedelta(seconds=self.timeout_seconds),
            proposed_effects=list(action.expected_consequences),
        )
        notification_result = self._notify_reviewer(request)
        response = self.response_callback(request) if self.response_callback is not None else None

        if response is None:
            response = self._timeout_response(request)
            return self._result_from_response(
                action=action,
                risk_level=risk_level,
                request=request,
                response=response,
                notification_result=notification_result,
                timed_out=True,
            )

        return self._result_from_response(
            action=action,
            risk_level=risk_level,
            request=request,
            response=response,
            notification_result=notification_result,
            timed_out=False,
        )

    def _classify_risk(self, action: ApprovalGateAction) -> ApprovalRiskLevel:
        if action.risk_level is not None:
            return action.risk_level
        lowered = action.action_type.casefold()
        if any(token in lowered for token in ("delete", "submit", "transfer", "purchase", "close", "terminate")):
            return ApprovalRiskLevel.IRREVERSIBLE
        if any(token in lowered for token in ("update", "edit", "write", "create", "send")):
            return ApprovalRiskLevel.REVERSIBLE
        return ApprovalRiskLevel.READ_ONLY

    def _should_gate(self, action: ApprovalGateAction, risk_level: ApprovalRiskLevel) -> bool:
        if risk_level is ApprovalRiskLevel.IRREVERSIBLE:
            return True
        return action.blast_radius >= self.high_blast_radius_threshold

    def _notify_reviewer(self, request: ApprovalRequest):
        if self.notification_dispatcher is None:
            return None
        return self.notification_dispatcher.dispatch(
            workflow_id=request.action.workflow_id,
            event_type=NotificationEventType.ESCALATION,
            description=self._notification_description(request),
            context_data={
                "reviewer_channel": request.reviewer_channel,
                "request_id": request.request_id,
                "step_id": request.action.step_id,
                "action_type": request.action.action_type,
                "application_name": request.action.application_name,
                "risk_level": request.action.risk_level.value,
                "blast_radius": request.action.blast_radius,
                "expected_consequences": list(request.proposed_effects),
                "context_data": dict(request.action.context_data),
                "expires_at": request.expires_at.isoformat(),
            },
            step_name="approval_gate_notification",
        )

    def _notification_description(self, request: ApprovalRequest) -> str:
        consequences = "; ".join(request.proposed_effects) if request.proposed_effects else "No explicit consequences provided."
        return (
            f"Approval required for {request.action.action_type} in step {request.action.step_id}. "
            f"Risk={request.action.risk_level.value}, blast_radius={request.action.blast_radius}. "
            f"Description: {request.action.description}. Expected consequences: {consequences}"
        )

    def _timeout_response(self, request: ApprovalRequest) -> ApprovalResponse:
        decision_map = {
            ApprovalTimeoutPolicy.REJECT: ApprovalDecision.REJECT,
            ApprovalTimeoutPolicy.ESCALATE: ApprovalDecision.ESCALATE,
            ApprovalTimeoutPolicy.PROCEED_WITH_CAUTION: ApprovalDecision.PROCEED_WITH_CAUTION,
        }
        decision = decision_map[self.timeout_policy]
        return ApprovalResponse(
            request_id=request.request_id,
            decision=decision,
            reviewer_id=None,
            responded_at=self.now_fn(),
            reason=f"Approval timed out; applied default policy {self.timeout_policy.value}.",
        )

    def _result_from_response(
        self,
        *,
        action: ApprovalGateAction,
        risk_level: ApprovalRiskLevel,
        request: ApprovalRequest,
        response: ApprovalResponse,
        notification_result,
        timed_out: bool,
    ) -> ApprovalGateResult:
        succeeded = response.decision in (ApprovalDecision.APPROVE, ApprovalDecision.PROCEED_WITH_CAUTION)
        reason = response.reason
        if response.decision is ApprovalDecision.REJECT and reason is None:
            reason = "Action rejected during approval review."
        if response.decision is ApprovalDecision.ESCALATE and reason is None:
            reason = "Action timed out or was escalated for additional review."
        return ApprovalGateResult(
            succeeded=succeeded,
            action=action,
            triggered_gate=True,
            request=request,
            response=response,
            notification_result=notification_result,
            risk_level=risk_level,
            timed_out=timed_out,
            reason=reason,
        )


