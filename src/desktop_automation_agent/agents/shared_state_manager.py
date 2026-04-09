from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from desktop_automation_agent.models import (
    SharedStateConflictPolicy,
    SharedStateField,
    SharedStateResult,
    SharedStateSnapshot,
    SharedStateWriteLog,
)


@dataclass(slots=True)
class SharedStateManager:
    storage_path: str
    conflict_policy: SharedStateConflictPolicy = SharedStateConflictPolicy.LAST_WRITE_WINS
    agent_priorities: dict[str, int] = field(default_factory=dict)
    manual_resolution_callback: Callable[[SharedStateField, Any, str], bool] | None = None

    def define_field(
        self,
        *,
        field_name: str,
        field_type: str,
        initial_value: Any = None,
        read_agents: list[str] | None = None,
        write_agents: list[str] | None = None,
    ) -> SharedStateResult:
        snapshot = self._load_snapshot()
        existing = self._find_field(snapshot["fields"], field_name)
        record = SharedStateField(
            field_name=field_name,
            field_type=field_type,
            value=deepcopy(initial_value),
            read_agents=list(read_agents or []),
            write_agents=list(write_agents or []),
            updated_by=None,
            updated_at=None,
        )
        if existing is None:
            snapshot["fields"].append(self._serialize_field(record))
        else:
            snapshot["fields"] = [
                self._serialize_field(record) if item["field_name"] == field_name else item
                for item in snapshot["fields"]
            ]
        self._save_snapshot(snapshot)
        return SharedStateResult(succeeded=True, state_field=record)

    def read_field(
        self,
        *,
        agent_id: str,
        field_name: str,
    ) -> SharedStateResult:
        snapshot = self._load_snapshot()
        record = self._find_field(snapshot["fields"], field_name)
        if record is None:
            return SharedStateResult(succeeded=False, reason="Shared state field not found.")
        field = self._deserialize_field(record)
        if not self._can_read(field, agent_id):
            return SharedStateResult(succeeded=False, reason="Agent does not have read access to the field.")
        return SharedStateResult(succeeded=True, state_field=field)

    def write_field(
        self,
        *,
        agent_id: str,
        field_name: str,
        value: Any,
    ) -> SharedStateResult:
        snapshot = self._load_snapshot()
        record = self._find_field(snapshot["fields"], field_name)
        if record is None:
            return SharedStateResult(succeeded=False, reason="Shared state field not found.")
        field = self._deserialize_field(record)
        if not self._can_write(field, agent_id):
            log = SharedStateWriteLog(
                field_name=field_name,
                agent_id=agent_id,
                value=deepcopy(value),
                accepted=False,
                reason="Agent does not have write access to the field.",
            )
            self._append_write_log(snapshot, log)
            self._save_snapshot(snapshot)
            return SharedStateResult(succeeded=False, state_field=field, write_log=log, reason=log.reason)

        accepted, reason = self._resolve_conflict(field, agent_id, value)
        updated_field = field
        if accepted:
            updated_field = SharedStateField(
                field_name=field.field_name,
                field_type=field.field_type,
                value=deepcopy(value),
                read_agents=list(field.read_agents),
                write_agents=list(field.write_agents),
                updated_by=agent_id,
                updated_at=utc_now(),
            )
            snapshot["fields"] = [
                self._serialize_field(updated_field) if item["field_name"] == field_name else item
                for item in snapshot["fields"]
            ]

        log = SharedStateWriteLog(
            field_name=field_name,
            agent_id=agent_id,
            value=deepcopy(value),
            accepted=accepted,
            reason=reason,
        )
        self._append_write_log(snapshot, log)
        self._save_snapshot(snapshot)
        return SharedStateResult(
            succeeded=accepted,
            state_field=updated_field,
            write_log=log,
            reason=reason if not accepted else None,
        )

    def snapshot(self) -> SharedStateResult:
        snapshot = self._load_snapshot()
        state_snapshot = SharedStateSnapshot(
            fields=[self._deserialize_field(item) for item in snapshot["fields"]],
            captured_at=utc_now(),
        )
        return SharedStateResult(succeeded=True, snapshot=state_snapshot, fields=list(state_snapshot.fields))

    def list_write_logs(self) -> list[SharedStateWriteLog]:
        snapshot = self._load_snapshot()
        return [self._deserialize_write_log(item) for item in snapshot["write_logs"]]

    def _resolve_conflict(
        self,
        field: SharedStateField,
        agent_id: str,
        value: Any,
    ) -> tuple[bool, str | None]:
        if field.updated_by is None or field.updated_at is None:
            return True, None
        if self.conflict_policy is SharedStateConflictPolicy.LAST_WRITE_WINS:
            return True, None
        if self.conflict_policy is SharedStateConflictPolicy.PRIORITY_BASED:
            current_priority = self.agent_priorities.get(field.updated_by, 0)
            incoming_priority = self.agent_priorities.get(agent_id, 0)
            if incoming_priority >= current_priority:
                return True, None
            return False, "Write rejected by priority-based conflict resolution."
        if self.conflict_policy is SharedStateConflictPolicy.MANUAL_RESOLUTION:
            if self.manual_resolution_callback is None:
                return False, "Manual conflict resolution requested but no callback is configured."
            accepted = self.manual_resolution_callback(field, value, agent_id)
            if accepted:
                return True, None
            return False, "Write rejected during manual conflict resolution."
        return True, None

    def _can_read(self, field: SharedStateField, agent_id: str) -> bool:
        return not field.read_agents or agent_id in field.read_agents

    def _can_write(self, field: SharedStateField, agent_id: str) -> bool:
        return not field.write_agents or agent_id in field.write_agents

    def _find_field(self, fields: list[dict], field_name: str) -> dict | None:
        for item in fields:
            if item["field_name"] == field_name:
                return item
        return None

    def _append_write_log(self, snapshot: dict, log: SharedStateWriteLog) -> None:
        snapshot.setdefault("write_logs", []).append(self._serialize_write_log(log))

    def _load_snapshot(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {"fields": [], "write_logs": []}
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("fields", [])
        payload.setdefault("write_logs", [])
        return payload

    def _save_snapshot(self, snapshot: dict) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    def _serialize_field(self, field: SharedStateField) -> dict:
        return {
            "field_name": field.field_name,
            "field_type": field.field_type,
            "value": field.value,
            "read_agents": list(field.read_agents),
            "write_agents": list(field.write_agents),
            "updated_by": field.updated_by,
            "updated_at": None if field.updated_at is None else field.updated_at.isoformat(),
        }

    def _deserialize_field(self, payload: dict) -> SharedStateField:
        return SharedStateField(
            field_name=payload["field_name"],
            field_type=payload["field_type"],
            value=payload.get("value"),
            read_agents=list(payload.get("read_agents", [])),
            write_agents=list(payload.get("write_agents", [])),
            updated_by=payload.get("updated_by"),
            updated_at=None if payload.get("updated_at") is None else datetime.fromisoformat(payload["updated_at"]),
        )

    def _serialize_write_log(self, log: SharedStateWriteLog) -> dict:
        return {
            "field_name": log.field_name,
            "agent_id": log.agent_id,
            "value": log.value,
            "timestamp": log.timestamp.isoformat(),
            "accepted": log.accepted,
            "reason": log.reason,
        }

    def _deserialize_write_log(self, payload: dict) -> SharedStateWriteLog:
        return SharedStateWriteLog(
            field_name=payload["field_name"],
            agent_id=payload["agent_id"],
            value=payload.get("value"),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            accepted=bool(payload.get("accepted", True)),
            reason=payload.get("reason"),
        )


