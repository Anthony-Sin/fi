from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from desktop_automation_perception.models import (
    AgentHandoffContext,
    AgentHandoffReason,
    AgentHandoffRecord,
    AgentHandoffResult,
)


@dataclass(slots=True)
class AgentHandoffProtocol:
    storage_path: str
    message_bus: object | None = None

    def create_handoff(
        self,
        *,
        sender_agent_id: str,
        receiver_agent_id: str,
        context: AgentHandoffContext,
        reason: AgentHandoffReason,
        special_instructions: str | None = None,
        overlap_seconds: float = 0.0,
        correlation_id: str | None = None,
    ) -> AgentHandoffResult:
        overlap_until = None
        if overlap_seconds > 0:
            overlap_until = utc_now() + timedelta(seconds=float(overlap_seconds))

        handoff = AgentHandoffRecord(
            handoff_id=str(uuid4()),
            sender_agent_id=sender_agent_id,
            receiver_agent_id=receiver_agent_id,
            context=AgentHandoffContext(
                current_step=context.current_step,
                collected_data=dict(context.collected_data),
                plan_description=context.plan_description,
            ),
            reason=reason,
            special_instructions=special_instructions,
            overlap_until=overlap_until,
        )
        self._append_handoff(handoff)
        self._notify_receiver(handoff, correlation_id or handoff.handoff_id)
        return AgentHandoffResult(succeeded=True, handoff=handoff)

    def list_handoffs(self) -> list[AgentHandoffRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_handoff(item) for item in payload.get("handoffs", [])]

    def active_overlap_handoffs(
        self,
        *,
        agent_id: str | None = None,
        as_of: datetime | None = None,
    ) -> AgentHandoffResult:
        as_of = as_of or utc_now()
        handoffs = []
        for handoff in self.list_handoffs():
            if handoff.overlap_until is None or handoff.overlap_until < as_of:
                continue
            if agent_id is not None and handoff.sender_agent_id != agent_id:
                continue
            handoffs.append(handoff)
        return AgentHandoffResult(succeeded=True, handoffs=handoffs)

    def _notify_receiver(
        self,
        handoff: AgentHandoffRecord,
        correlation_id: str,
    ) -> None:
        if self.message_bus is None:
            return
        self.message_bus.send_direct(
            sender_id=handoff.sender_agent_id,
            recipient_id=handoff.receiver_agent_id,
            message_type="agent_handoff",
            payload={
                "handoff_id": handoff.handoff_id,
                "reason": handoff.reason.value,
                "current_step": handoff.context.current_step,
                "collected_data": handoff.context.collected_data,
                "plan_description": handoff.context.plan_description,
                "special_instructions": handoff.special_instructions,
                "overlap_until": None if handoff.overlap_until is None else handoff.overlap_until.isoformat(),
            },
            correlation_id=correlation_id,
        )

    def _append_handoff(
        self,
        handoff: AgentHandoffRecord,
    ) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"handoffs": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("handoffs", []).append(self._serialize_handoff(handoff))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_handoff(
        self,
        handoff: AgentHandoffRecord,
    ) -> dict:
        return {
            "handoff_id": handoff.handoff_id,
            "sender_agent_id": handoff.sender_agent_id,
            "receiver_agent_id": handoff.receiver_agent_id,
            "context": {
                "current_step": handoff.context.current_step,
                "collected_data": handoff.context.collected_data,
                "plan_description": handoff.context.plan_description,
            },
            "reason": handoff.reason.value,
            "special_instructions": handoff.special_instructions,
            "overlap_until": None if handoff.overlap_until is None else handoff.overlap_until.isoformat(),
            "timestamp": handoff.timestamp.isoformat(),
        }

    def _deserialize_handoff(
        self,
        payload: dict,
    ) -> AgentHandoffRecord:
        return AgentHandoffRecord(
            handoff_id=payload["handoff_id"],
            sender_agent_id=payload["sender_agent_id"],
            receiver_agent_id=payload["receiver_agent_id"],
            context=AgentHandoffContext(
                current_step=payload.get("context", {}).get("current_step"),
                collected_data=dict(payload.get("context", {}).get("collected_data", {})),
                plan_description=payload.get("context", {}).get("plan_description"),
            ),
            reason=AgentHandoffReason(payload["reason"]),
            special_instructions=payload.get("special_instructions"),
            overlap_until=None
            if payload.get("overlap_until") is None
            else datetime.fromisoformat(payload["overlap_until"]),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )


