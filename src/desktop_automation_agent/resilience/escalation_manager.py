from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4

from desktop_automation_agent.models import (
    EscalationManagerResult,
    EscalationRecord,
    EscalationRequest,
    EscalationResolution,
    EscalationResponse,
    EscalationSnapshot,
    EscalationTriggerType,
    NotificationEventType,
)


@dataclass(slots=True)
class EscalationManager:
    storage_path: str
    operator_channel: str
    notification_dispatcher: object | None = None
    response_callback: Callable[[EscalationRequest], EscalationResponse | None] | None = None
    response_timeout_seconds: float = 600.0
    timeout_resolution: EscalationResolution = EscalationResolution.ABORT
    now_fn: Callable[[], datetime] = utc_now

    def trigger(
        self,
        *,
        workflow_id: str,
        trigger_type: EscalationTriggerType,
        step_id: str | None = None,
        detail: str | None = None,
        context_data: dict | None = None,
    ) -> EscalationManagerResult:
        snapshot = self._load_snapshot()
        request = EscalationRequest(
            escalation_id=str(uuid4()),
            workflow_id=workflow_id,
            step_id=step_id,
            trigger_type=trigger_type,
            created_at=self.now_fn(),
            expires_at=self.now_fn() + timedelta(seconds=self.response_timeout_seconds),
            context_data={} if context_data is None else dict(context_data),
            detail=detail,
        )
        record = EscalationRecord(
            escalation_id=request.escalation_id,
            workflow_id=workflow_id,
            step_id=step_id,
            trigger_type=trigger_type,
            paused=True,
            resolved=False,
            detail=detail,
            created_at=request.created_at,
        )
        snapshot.active_requests.append(self._copy_request(request))
        snapshot.records.append(record)
        self._save_snapshot(snapshot)

        self._notify_operator(request)
        response = self.response_callback(request) if self.response_callback is not None else None
        if response is None:
            response = EscalationResponse(
                escalation_id=request.escalation_id,
                resolution=self.timeout_resolution,
                operator_id=None,
                responded_at=self.now_fn(),
                reason=f"Escalation timed out; defaulted to {self.timeout_resolution.value}.",
            )
            return self._finalize(snapshot=snapshot, request=request, response=response, timed_out=True)

        return self._finalize(snapshot=snapshot, request=request, response=response, timed_out=False)

    def inspect(self) -> EscalationManagerResult:
        snapshot = self._load_snapshot()
        return EscalationManagerResult(succeeded=True, snapshot=snapshot)

    def _finalize(
        self,
        *,
        snapshot: EscalationSnapshot,
        request: EscalationRequest,
        response: EscalationResponse,
        timed_out: bool,
    ) -> EscalationManagerResult:
        snapshot.active_requests = [item for item in snapshot.active_requests if item.escalation_id != request.escalation_id]
        record = next(item for item in snapshot.records if item.escalation_id == request.escalation_id)
        record.resolved = True
        record.resolution = response.resolution
        record.operator_id = response.operator_id
        record.responded_at = response.responded_at
        self._save_snapshot(snapshot)

        resumed = response.resolution is EscalationResolution.RESUME
        aborted = response.resolution is EscalationResolution.ABORT
        return EscalationManagerResult(
            succeeded=resumed,
            request=request,
            response=response,
            record=record,
            snapshot=snapshot,
            paused=True,
            resumed=resumed,
            aborted=aborted,
            timed_out=timed_out,
            reason=response.reason,
        )

    def _notify_operator(self, request: EscalationRequest) -> None:
        if self.notification_dispatcher is None:
            return
        self.notification_dispatcher.dispatch(
            workflow_id=request.workflow_id,
            event_type=NotificationEventType.ESCALATION,
            description=self._notification_description(request),
            context_data={
                "operator_channel": self.operator_channel,
                "escalation_id": request.escalation_id,
                "trigger_type": request.trigger_type.value,
                "step_id": request.step_id,
                "detail": request.detail,
                "context_data": dict(request.context_data),
                "expires_at": request.expires_at.isoformat(),
            },
            step_name="escalation_manager_notify",
        )

    def _notification_description(self, request: EscalationRequest) -> str:
        step = request.step_id or "workflow"
        detail = request.detail or "Automation cannot proceed autonomously."
        return f"Escalation triggered for {step}: {request.trigger_type.value}. {detail}"

    def _load_snapshot(self) -> EscalationSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return EscalationSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EscalationSnapshot(
            active_requests=[self._deserialize_request(item) for item in payload.get("active_requests", [])],
            records=[self._deserialize_record(item) for item in payload.get("records", [])],
        )

    def _save_snapshot(self, snapshot: EscalationSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_requests": [self._serialize_request(item) for item in snapshot.active_requests],
            "records": [self._serialize_record(item) for item in snapshot.records],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_request(self, request: EscalationRequest) -> EscalationRequest:
        return self._deserialize_request(self._serialize_request(request))

    def _serialize_request(self, request: EscalationRequest) -> dict:
        return {
            "escalation_id": request.escalation_id,
            "workflow_id": request.workflow_id,
            "step_id": request.step_id,
            "trigger_type": request.trigger_type.value,
            "created_at": request.created_at.isoformat(),
            "expires_at": request.expires_at.isoformat(),
            "context_data": dict(request.context_data),
            "detail": request.detail,
        }

    def _deserialize_request(self, payload: dict) -> EscalationRequest:
        return EscalationRequest(
            escalation_id=payload["escalation_id"],
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            trigger_type=EscalationTriggerType(payload["trigger_type"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
            context_data=dict(payload.get("context_data", {})),
            detail=payload.get("detail"),
        )

    def _serialize_record(self, record: EscalationRecord) -> dict:
        return {
            "escalation_id": record.escalation_id,
            "workflow_id": record.workflow_id,
            "step_id": record.step_id,
            "trigger_type": record.trigger_type.value,
            "paused": record.paused,
            "resolved": record.resolved,
            "resolution": None if record.resolution is None else record.resolution.value,
            "operator_id": record.operator_id,
            "created_at": record.created_at.isoformat(),
            "responded_at": None if record.responded_at is None else record.responded_at.isoformat(),
            "detail": record.detail,
        }

    def _deserialize_record(self, payload: dict) -> EscalationRecord:
        return EscalationRecord(
            escalation_id=payload["escalation_id"],
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            trigger_type=EscalationTriggerType(payload["trigger_type"]),
            paused=bool(payload.get("paused", False)),
            resolved=bool(payload.get("resolved", False)),
            resolution=None if payload.get("resolution") is None else EscalationResolution(payload["resolution"]),
            operator_id=payload.get("operator_id"),
            created_at=datetime.fromisoformat(payload["created_at"]),
            responded_at=None if payload.get("responded_at") is None else datetime.fromisoformat(payload["responded_at"]),
            detail=payload.get("detail"),
        )


