from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from desktop_automation_perception.models import (
    ActionLogEntry,
    AccessibilityElement,
    AccessibilityTree,
    FailureArchiveQuery,
    FailureArchiveRecord,
    FailureArchiveResult,
)


@dataclass(slots=True)
class ScreenshotOnFailureRecorder:
    archive_path: str
    artifact_directory: str
    screenshot_backend: object | None = None
    accessibility_reader: object | None = None
    sensitive_data_protector: object | None = None

    def record_failure(
        self,
        *,
        workflow_id: str,
        step_name: str,
        error: Exception | object,
        recent_actions: list[ActionLogEntry | str] | None = None,
        application_name: str | None = None,
    ) -> FailureArchiveResult:
        timestamp = utc_now()
        record_id = str(uuid4())
        artifact_directory = Path(self.artifact_directory)
        artifact_directory.mkdir(parents=True, exist_ok=True)

        base_name = self._build_base_name(workflow_id, step_name, timestamp)
        screenshot_path = self._capture_screenshot(artifact_directory / f"{base_name}.png")
        accessibility_tree_path = self._dump_accessibility_tree(artifact_directory / f"{base_name}.accessibility.json")
        exception_type, exception_message = self._normalize_error(error)

        record = FailureArchiveRecord(
            record_id=record_id,
            workflow_id=workflow_id,
            step_name=step_name,
            timestamp=timestamp,
            screenshot_path=screenshot_path,
            accessibility_tree_path=accessibility_tree_path,
            exception_type=exception_type,
            exception_message=exception_message,
            last_actions=self._serialize_actions(recent_actions or []),
            application_name=application_name,
        )
        self._append_record(record)
        return FailureArchiveResult(succeeded=True, record=record)

    def query_records(
        self,
        query: FailureArchiveQuery | None = None,
    ) -> FailureArchiveResult:
        query = query or FailureArchiveQuery()
        records = [
            record
            for record in self.list_records()
            if self._matches_query(record, query)
        ]
        return FailureArchiveResult(succeeded=True, records=records)

    def list_records(self) -> list[FailureArchiveRecord]:
        path = Path(self.archive_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_record(item) for item in payload.get("records", [])]

    def _append_record(self, record: FailureArchiveRecord) -> None:
        path = Path(self.archive_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("records", []).append(self._serialize_record(record))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _build_base_name(
        self,
        workflow_id: str,
        step_name: str,
        timestamp: datetime,
    ) -> str:
        safe_workflow = self._slugify(workflow_id)
        safe_step = self._slugify(step_name)
        return f"{safe_workflow}__{safe_step}__{timestamp.strftime('%Y%m%dT%H%M%S')}"

    def _capture_screenshot(self, path: Path) -> str | None:
        if self.screenshot_backend is None:
            return None
        captured_path = self.screenshot_backend.capture_screenshot_to_path(str(path))
        final_path = captured_path if isinstance(captured_path, str) and captured_path else str(path)
        if self.sensitive_data_protector is not None and final_path is not None:
            self.sensitive_data_protector.protect_screenshot_file(final_path)
        return final_path

    def _dump_accessibility_tree(self, path: Path) -> str | None:
        if self.accessibility_reader is None:
            return None
        tree = self.accessibility_reader.read_active_application_tree()
        payload = self._serialize_tree(tree)
        if self.sensitive_data_protector is not None:
            payload = self.sensitive_data_protector.sanitize_payload(payload, location="failure_accessibility_tree")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)

    def _serialize_tree(self, tree: AccessibilityTree | object) -> dict:
        application_name = getattr(tree, "application_name", None)
        root = getattr(tree, "root", None)
        return {
            "application_name": application_name,
            "root": self._serialize_element(root),
        }

    def _serialize_element(self, element: AccessibilityElement | object | None) -> dict | None:
        if element is None:
            return None
        state = getattr(element, "state", None)
        return {
            "element_id": getattr(element, "element_id", None),
            "name": getattr(element, "name", None),
            "role": getattr(element, "role", None),
            "value": getattr(element, "value", None),
            "bounds": getattr(element, "bounds", None),
            "source": getattr(element, "source", None),
            "handle": getattr(element, "handle", None),
            "state": {
                "text": getattr(state, "text", None),
                "enabled": getattr(state, "enabled", None),
                "selected": getattr(state, "selected", None),
            },
            "children": [
                self._serialize_element(child)
                for child in getattr(element, "children", [])
            ],
        }

    def _normalize_error(self, error: Exception | object) -> tuple[str, str]:
        if isinstance(error, BaseException):
            return type(error).__name__, str(error)
        exception_type = getattr(error, "exception_type", None) or getattr(error, "error_type", None)
        message = getattr(error, "message", None) or getattr(error, "error_message", None) or str(error)
        return exception_type or type(error).__name__, message

    def _serialize_actions(self, actions: list[ActionLogEntry | str]) -> list[str]:
        serialized: list[str] = []
        for item in actions[-5:]:
            if isinstance(item, str):
                text = item
                if self.sensitive_data_protector is not None:
                    text = self.sensitive_data_protector.mask_text(text, location="failure_actions").text or text
                serialized.append(text)
                continue
            action = getattr(item, "action", None)
            action_type = getattr(action, "action_type", None)
            action_value = getattr(action_type, "value", None) if action_type is not None else None
            payload = {
                "action_type": action_value,
                "executed": getattr(item, "executed", None),
                "delay_seconds": getattr(item, "delay_seconds", None),
                "reason": getattr(item, "reason", None),
                "text": getattr(action, "text", None),
                "key": getattr(action, "key", None),
                "scroll_amount": getattr(action, "scroll_amount", None),
            }
            if self.sensitive_data_protector is not None:
                payload = self.sensitive_data_protector.sanitize_payload(payload, location="failure_actions")
            serialized.append(json.dumps(payload))
        return serialized

    def _matches_query(
        self,
        record: FailureArchiveRecord,
        query: FailureArchiveQuery,
    ) -> bool:
        if query.workflow_id is not None and record.workflow_id != query.workflow_id:
            return False
        if query.step_name is not None and record.step_name != query.step_name:
            return False
        if query.exception_type is not None and record.exception_type != query.exception_type:
            return False
        return True

    def _serialize_record(self, record: FailureArchiveRecord) -> dict:
        return {
            "record_id": record.record_id,
            "workflow_id": record.workflow_id,
            "step_name": record.step_name,
            "timestamp": record.timestamp.isoformat(),
            "screenshot_path": record.screenshot_path,
            "accessibility_tree_path": record.accessibility_tree_path,
            "exception_type": record.exception_type,
            "exception_message": record.exception_message,
            "last_actions": list(record.last_actions),
            "application_name": record.application_name,
        }

    def _deserialize_record(self, payload: dict) -> FailureArchiveRecord:
        return FailureArchiveRecord(
            record_id=payload["record_id"],
            workflow_id=payload["workflow_id"],
            step_name=payload["step_name"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            screenshot_path=payload.get("screenshot_path"),
            accessibility_tree_path=payload.get("accessibility_tree_path"),
            exception_type=payload.get("exception_type"),
            exception_message=payload.get("exception_message"),
            last_actions=list(payload.get("last_actions", [])),
            application_name=payload.get("application_name"),
        )

    def _slugify(self, value: str) -> str:
        normalized = "".join(character.lower() if character.isalnum() else "_" for character in value.strip())
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized.strip("_") or "unknown"


