from __future__ import annotations

from desktop_automation_agent._time import utc_now

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from desktop_automation_agent.models import (
    WorkflowAuditLogEntry,
    WorkflowAuditOutcome,
    WorkflowAuditQuery,
    WorkflowAuditResult,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkflowAuditLogger:
    storage_path: str
    credential_key_patterns: tuple[str, ...] = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credential",
        "auth",
        "cookie",
    )
    redaction_text: str = "***REDACTED***"
    sensitive_data_protector: object | None = None

    def log_action(
        self,
        *,
        workflow_id: str,
        workflow_version_number: int | None = None,
        step_name: str,
        action_type: str,
        target_element: str | None = None,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        duration_seconds: float = 0.0,
        success: bool = True,
        timestamp: datetime | None = None,
    ) -> WorkflowAuditResult:
        entry = WorkflowAuditLogEntry(
            timestamp=timestamp or utc_now(),
            workflow_id=workflow_id,
            workflow_version_number=workflow_version_number,
            step_name=step_name,
            action_type=action_type,
            target_element=target_element,
            input_data=self._sanitize_payload(input_data or {}),
            output_data=self._sanitize_payload(output_data or {}),
            duration_seconds=float(duration_seconds),
            outcome=WorkflowAuditOutcome.SUCCESS if success else WorkflowAuditOutcome.FAILURE,
            success=bool(success),
        )
        self._append_entry(entry)
        return WorkflowAuditResult(succeeded=True, entry=entry)

    def query_logs(
        self,
        query: WorkflowAuditQuery | None = None,
    ) -> WorkflowAuditResult:
        query = query or WorkflowAuditQuery()
        entries = [entry for entry in self.list_logs() if self._matches_query(entry, query)]
        return WorkflowAuditResult(succeeded=True, entries=entries)

    def list_logs(self) -> list[WorkflowAuditLogEntry]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        entries: list[WorkflowAuditLogEntry] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    normalized = line.strip()
                    if not normalized:
                        continue
                    try:
                        entries.append(self._deserialize_entry(json.loads(normalized)))
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.warning(f"Skipping malformed audit entry in {self.storage_path}: {e}")
        except Exception as e:
            logger.warning(f"Failed to read audit logs from {self.storage_path}: {e}")
        return entries

    def export_json(
        self,
        output_path: str,
        query: WorkflowAuditQuery | None = None,
    ) -> WorkflowAuditResult:
        entries = self.query_logs(query).entries
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [self._serialize_entry(entry) for entry in entries]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return WorkflowAuditResult(succeeded=True, entries=entries, export_path=str(path))

    def export_csv(
        self,
        output_path: str,
        query: WorkflowAuditQuery | None = None,
    ) -> WorkflowAuditResult:
        entries = self.query_logs(query).entries
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "timestamp",
                    "workflow_id",
                    "step_name",
                    "action_type",
                    "target_element",
                    "input_data",
                    "output_data",
                    "duration_seconds",
                    "outcome",
                    "success",
                ],
            )
            writer.writeheader()
            for entry in entries:
                writer.writerow(
                    {
                        "timestamp": entry.timestamp.isoformat(),
                        "workflow_id": entry.workflow_id,
                        "step_name": entry.step_name,
                        "action_type": entry.action_type,
                        "target_element": entry.target_element,
                        "input_data": json.dumps(entry.input_data, sort_keys=True),
                        "output_data": json.dumps(entry.output_data, sort_keys=True),
                        "duration_seconds": entry.duration_seconds,
                        "outcome": entry.outcome.value,
                        "success": entry.success,
                    }
                )
        return WorkflowAuditResult(succeeded=True, entries=entries, export_path=str(path))

    def _append_entry(self, entry: WorkflowAuditLogEntry) -> None:
        try:
            path = Path(self.storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self._serialize_entry(entry), sort_keys=True))
                handle.write("\n")
        except Exception as e:
            logger.warning(f"Failed to append audit entry to {self.storage_path}: {e}")

    def _matches_query(
        self,
        entry: WorkflowAuditLogEntry,
        query: WorkflowAuditQuery,
    ) -> bool:
        if query.workflow_id is not None and entry.workflow_id != query.workflow_id:
            return False
        if query.workflow_version_number is not None and entry.workflow_version_number != query.workflow_version_number:
            return False
        if query.action_type is not None and entry.action_type != query.action_type:
            return False
        if query.outcome is not None and entry.outcome is not query.outcome:
            return False
        if query.started_at is not None and entry.timestamp < query.started_at:
            return False
        if query.ended_at is not None and entry.timestamp > query.ended_at:
            return False
        return True

    def _sanitize_payload(self, payload: Any) -> Any:
        if self.sensitive_data_protector is not None:
            return self.sensitive_data_protector.sanitize_payload(payload, location="workflow_audit_logger")
        if isinstance(payload, dict):
            return {
                key: (
                    self.redaction_text
                    if self._should_redact_key(key)
                    else self._sanitize_payload(value)
                )
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self._sanitize_payload(item) for item in payload]
        if isinstance(payload, tuple):
            return [self._sanitize_payload(item) for item in payload]
        return payload

    def _should_redact_key(self, key: str) -> bool:
        lowered = key.casefold()
        return any(pattern in lowered for pattern in self.credential_key_patterns)

    def _serialize_entry(self, entry: WorkflowAuditLogEntry) -> dict[str, Any]:
        return {
            "timestamp": entry.timestamp.isoformat(),
            "workflow_id": entry.workflow_id,
            "workflow_version_number": entry.workflow_version_number,
            "step_name": entry.step_name,
            "action_type": entry.action_type,
            "target_element": entry.target_element,
            "input_data": entry.input_data,
            "output_data": entry.output_data,
            "duration_seconds": entry.duration_seconds,
            "outcome": entry.outcome.value,
            "success": entry.success,
        }

    def _deserialize_entry(self, payload: dict[str, Any]) -> WorkflowAuditLogEntry:
        return WorkflowAuditLogEntry(
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            workflow_id=payload["workflow_id"],
            workflow_version_number=payload.get("workflow_version_number"),
            step_name=payload["step_name"],
            action_type=payload["action_type"],
            target_element=payload.get("target_element"),
            input_data=dict(payload.get("input_data", {})),
            output_data=dict(payload.get("output_data", {})),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            outcome=WorkflowAuditOutcome(payload.get("outcome", WorkflowAuditOutcome.SUCCESS.value)),
            success=bool(payload.get("success", True)),
        )


