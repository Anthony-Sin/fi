from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
import shutil
import zipfile
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from desktop_automation_perception.models import (
    ActionLogEntry,
    BranchEvaluationRecord,
    ExecutionTraceEvent,
    ExecutionTraceEventType,
    ExecutionTraceRecord,
    ExecutionTraceResult,
    HumanReviewDecisionRecord,
    HumanReviewPendingItem,
    PerceptionResult,
    WorkflowContext,
    WorkflowStepResult,
)


@dataclass(slots=True)
class ExecutionTraceRecorder:
    storage_directory: str
    archive_directory: str | None = None
    screenshot_backend: object | None = None
    sensitive_data_protector: object | None = None
    delete_uncompressed_after_archive: bool = False
    _active_traces: dict[str, ExecutionTraceRecord] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._active_traces = {}

    def start_trace(
        self,
        *,
        workflow_id: str,
        workflow_version_number: int | None = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> ExecutionTraceResult:
        trace_identifier = trace_id or str(uuid4())
        trace = ExecutionTraceRecord(
            trace_id=trace_identifier,
            workflow_id=workflow_id,
            workflow_version_number=workflow_version_number,
            started_at=utc_now(),
            metadata=self._sanitize_payload(metadata or {}),
        )
        trace_dir = self._trace_directory(trace.trace_id)
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace.manifest_path = str(trace_dir / "trace.json")
        self._active_traces[trace.trace_id] = trace
        self._persist_trace(trace)
        return ExecutionTraceResult(
            succeeded=True,
            trace=trace,
            manifest_path=trace.manifest_path,
        )

    def record_step_state(
        self,
        trace_id: str,
        *,
        step_id: str | None = None,
        step_index: int | None = None,
        pre_state: WorkflowContext | dict[str, Any] | None = None,
        post_state: WorkflowContext | dict[str, Any] | None = None,
        step_result: WorkflowStepResult | None = None,
        pre_screenshot_path: str | None = None,
        post_screenshot_path: str | None = None,
        capture_pre_screenshot: bool = False,
        capture_post_screenshot: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionTraceResult:
        payload = {
            "pre_state": self._serialize_value(pre_state),
            "post_state": self._serialize_value(post_state),
            "step_result": self._serialize_value(step_result),
            "pre_screenshot_path": self._prepare_screenshot(trace_id, "step_pre", pre_screenshot_path, capture_pre_screenshot),
            "post_screenshot_path": self._prepare_screenshot(trace_id, "step_post", post_screenshot_path, capture_post_screenshot),
            "metadata": self._sanitize_payload(metadata or {}),
        }
        return self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.STEP_STATE,
            step_id=step_id,
            step_index=step_index,
            payload=payload,
        )

    def record_perception(
        self,
        trace_id: str,
        *,
        perception_results: list[PerceptionResult],
        step_id: str | None = None,
        step_index: int | None = None,
        screenshot_path: str | None = None,
        capture_screenshot: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionTraceResult:
        attached_screenshot = self._prepare_screenshot(trace_id, "perception", screenshot_path, capture_screenshot)
        payload = {
            "perception_results": self._serialize_value(perception_results),
            "metadata": self._sanitize_payload(metadata or {}),
        }
        return self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.PERCEPTION,
            step_id=step_id,
            step_index=step_index,
            payload=payload,
            screenshot_path=attached_screenshot,
        )

    def record_action_decision(
        self,
        trace_id: str,
        *,
        decision_summary: str,
        step_id: str | None = None,
        step_index: int | None = None,
        candidate_actions: list[ActionLogEntry | dict[str, Any]] | None = None,
        selected_action: ActionLogEntry | dict[str, Any] | None = None,
        rationale: dict[str, Any] | None = None,
    ) -> ExecutionTraceResult:
        payload = {
            "decision_summary": decision_summary,
            "candidate_actions": self._serialize_value(candidate_actions or []),
            "selected_action": self._serialize_value(selected_action),
            "rationale": self._sanitize_payload(rationale or {}),
        }
        return self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.ACTION_DECISION,
            step_id=step_id,
            step_index=step_index,
            payload=payload,
        )

    def record_action_executed(
        self,
        trace_id: str,
        *,
        action: ActionLogEntry,
        step_id: str | None = None,
        step_index: int | None = None,
        pre_state: WorkflowContext | dict[str, Any] | None = None,
        post_state: WorkflowContext | dict[str, Any] | None = None,
        pre_screenshot_path: str | None = None,
        post_screenshot_path: str | None = None,
        capture_pre_screenshot: bool = False,
        capture_post_screenshot: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionTraceResult:
        payload = {
            "action": self._serialize_value(action),
            "pre_state": self._serialize_value(pre_state),
            "post_state": self._serialize_value(post_state),
            "pre_screenshot_path": self._prepare_screenshot(trace_id, "action_pre", pre_screenshot_path, capture_pre_screenshot),
            "post_screenshot_path": self._prepare_screenshot(trace_id, "action_post", post_screenshot_path, capture_post_screenshot),
            "metadata": self._sanitize_payload(metadata or {}),
        }
        primary_screenshot = payload["post_screenshot_path"] or payload["pre_screenshot_path"]
        return self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.ACTION_EXECUTED,
            step_id=step_id,
            step_index=step_index,
            payload=payload,
            screenshot_path=primary_screenshot,
        )

    def record_branch_decision(
        self,
        trace_id: str,
        *,
        selected_branch: str,
        step_id: str | None = None,
        step_index: int | None = None,
        records: list[BranchEvaluationRecord] | None = None,
        workflow_data: dict[str, Any] | None = None,
        screen_observations: dict[str, Any] | None = None,
    ) -> ExecutionTraceResult:
        payload = {
            "selected_branch": selected_branch,
            "records": self._serialize_value(records or []),
            "workflow_data": self._sanitize_payload(workflow_data or {}),
            "screen_observations": self._sanitize_payload(screen_observations or {}),
        }
        return self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.BRANCH_DECISION,
            step_id=step_id,
            step_index=step_index,
            payload=payload,
        )

    def record_human_interaction(
        self,
        trace_id: str,
        *,
        step_id: str | None = None,
        step_index: int | None = None,
        pending_item: HumanReviewPendingItem | None = None,
        decision_record: HumanReviewDecisionRecord | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionTraceResult:
        payload = {
            "pending_item": self._serialize_value(pending_item),
            "decision_record": self._serialize_value(decision_record),
            "metadata": self._sanitize_payload(metadata or {}),
        }
        return self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.HUMAN_INTERACTION,
            step_id=step_id,
            step_index=step_index,
            payload=payload,
        )

    def complete_trace(
        self,
        trace_id: str,
        *,
        succeeded: bool,
        final_outcome: str,
        final_result: dict[str, Any] | object | None = None,
        archive: bool = True,
    ) -> ExecutionTraceResult:
        trace = self._require_trace(trace_id)
        trace.completed_at = utc_now()
        trace.succeeded = succeeded
        trace.final_outcome = final_outcome
        event_result = self._append_event(
            trace_id,
            event_type=ExecutionTraceEventType.FINAL_OUTCOME,
            payload={
                "succeeded": succeeded,
                "final_outcome": final_outcome,
                "final_result": self._serialize_value(final_result),
            },
        )
        trace = self._require_trace(trace_id)
        archive_path = None
        if archive:
            archive_path = self.archive_trace(trace_id).archive_path
        self._active_traces.pop(trace_id, None)
        return ExecutionTraceResult(
            succeeded=event_result.succeeded,
            trace=trace,
            event=event_result.event,
            manifest_path=trace.manifest_path,
            archive_path=archive_path,
        )

    def archive_trace(self, trace_id: str) -> ExecutionTraceResult:
        trace = self._require_trace(trace_id)
        trace_dir = self._trace_directory(trace.trace_id)
        archive_dir = Path(self.archive_directory or self.storage_directory)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{trace.trace_id}.zip"
        trace.archive_path = str(archive_path)
        self._persist_trace(trace)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for path in trace_dir.rglob("*"):
                if path.is_file():
                    handle.write(path, arcname=str(path.relative_to(trace_dir)))
        if self.delete_uncompressed_after_archive and trace_dir.exists():
            shutil.rmtree(trace_dir)
        return ExecutionTraceResult(
            succeeded=True,
            trace=trace,
            archive_path=str(archive_path),
            manifest_path=trace.manifest_path,
        )

    def load_trace(self, path: str) -> ExecutionTraceResult:
        source = Path(path)
        if not source.exists():
            return ExecutionTraceResult(succeeded=False, reason=f"Trace path not found: {path}")
        if source.is_dir():
            manifest = source / "trace.json"
            if not manifest.exists():
                return ExecutionTraceResult(succeeded=False, reason=f"Trace manifest not found: {manifest}")
            trace = self._deserialize_trace(json.loads(manifest.read_text(encoding="utf-8")))
            return ExecutionTraceResult(succeeded=True, trace=trace, manifest_path=str(manifest))
        if source.suffix.casefold() == ".zip":
            with zipfile.ZipFile(source, "r") as handle:
                trace = self._deserialize_trace(json.loads(handle.read("trace.json").decode("utf-8")))
            return ExecutionTraceResult(succeeded=True, trace=trace, manifest_path="trace.json", archive_path=str(source))
        return ExecutionTraceResult(succeeded=False, reason=f"Unsupported trace path: {path}")

    def replay_trace(self, path: str) -> ExecutionTraceResult:
        loaded = self.load_trace(path)
        if not loaded.succeeded or loaded.trace is None:
            return loaded
        return ExecutionTraceResult(
            succeeded=True,
            trace=loaded.trace,
            replay_events=list(loaded.trace.events),
            manifest_path=loaded.manifest_path,
            archive_path=loaded.archive_path,
        )

    def _append_event(
        self,
        trace_id: str,
        *,
        event_type: ExecutionTraceEventType,
        step_id: str | None = None,
        step_index: int | None = None,
        payload: dict[str, Any] | None = None,
        screenshot_path: str | None = None,
    ) -> ExecutionTraceResult:
        trace = self._require_trace(trace_id)
        event = ExecutionTraceEvent(
            sequence_number=len(trace.events) + 1,
            timestamp=utc_now(),
            event_type=event_type,
            step_id=step_id,
            step_index=step_index,
            payload=self._sanitize_payload(payload or {}),
            screenshot_path=screenshot_path,
        )
        trace.events.append(event)
        self._persist_trace(trace)
        return ExecutionTraceResult(
            succeeded=True,
            trace=trace,
            event=event,
            manifest_path=trace.manifest_path,
        )

    def _persist_trace(self, trace: ExecutionTraceRecord) -> None:
        manifest_path = Path(trace.manifest_path or self._trace_directory(trace.trace_id) / "trace.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(self._serialize_trace(trace), indent=2, sort_keys=True), encoding="utf-8")
        trace.manifest_path = str(manifest_path)

    def _prepare_screenshot(
        self,
        trace_id: str,
        stem: str,
        source_path: str | None,
        capture: bool,
    ) -> str | None:
        artifacts_dir = self._trace_directory(trace_id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".png"
        if source_path:
            source = Path(source_path)
            if not source.exists():
                return None
            suffix = source.suffix or ".png"
            destination = artifacts_dir / f"{stem}_{uuid4().hex}{suffix}"
            shutil.copy2(source, destination)
            final_path = destination
        elif capture and self.screenshot_backend is not None:
            destination = artifacts_dir / f"{stem}_{uuid4().hex}{suffix}"
            captured = self.screenshot_backend.capture_screenshot_to_path(str(destination))
            final_path = Path(captured) if isinstance(captured, str) and captured else destination
        else:
            return None
        if self.sensitive_data_protector is not None:
            self.sensitive_data_protector.protect_screenshot_file(str(final_path))
        return final_path.relative_to(self._trace_directory(trace_id)).as_posix()

    def _trace_directory(self, trace_id: str) -> Path:
        return Path(self.storage_directory) / trace_id

    def _require_trace(self, trace_id: str) -> ExecutionTraceRecord:
        trace = self._active_traces.get(trace_id)
        if trace is None:
            raise ValueError(f"Unknown trace id: {trace_id}")
        return trace

    def _serialize_trace(self, trace: ExecutionTraceRecord) -> dict[str, Any]:
        return {
            "trace_id": trace.trace_id,
            "workflow_id": trace.workflow_id,
            "workflow_version_number": trace.workflow_version_number,
            "started_at": trace.started_at.isoformat(),
            "completed_at": trace.completed_at.isoformat() if trace.completed_at is not None else None,
            "succeeded": trace.succeeded,
            "final_outcome": trace.final_outcome,
            "manifest_path": trace.manifest_path,
            "archive_path": trace.archive_path,
            "metadata": self._serialize_value(trace.metadata),
            "events": [self._serialize_event(event) for event in trace.events],
        }

    def _serialize_event(self, event: ExecutionTraceEvent) -> dict[str, Any]:
        return {
            "sequence_number": event.sequence_number,
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type.value,
            "step_id": event.step_id,
            "step_index": event.step_index,
            "payload": self._serialize_value(event.payload),
            "screenshot_path": event.screenshot_path,
        }

    def _deserialize_trace(self, payload: dict[str, Any]) -> ExecutionTraceRecord:
        return ExecutionTraceRecord(
            trace_id=payload["trace_id"],
            workflow_id=payload["workflow_id"],
            workflow_version_number=payload.get("workflow_version_number"),
            started_at=datetime.fromisoformat(payload["started_at"]),
            completed_at=datetime.fromisoformat(payload["completed_at"]) if payload.get("completed_at") else None,
            succeeded=payload.get("succeeded"),
            final_outcome=payload.get("final_outcome"),
            manifest_path=payload.get("manifest_path"),
            archive_path=payload.get("archive_path"),
            metadata=dict(payload.get("metadata", {})),
            events=[self._deserialize_event(item) for item in payload.get("events", [])],
        )

    def _deserialize_event(self, payload: dict[str, Any]) -> ExecutionTraceEvent:
        return ExecutionTraceEvent(
            sequence_number=int(payload["sequence_number"]),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            event_type=ExecutionTraceEventType(payload["event_type"]),
            step_id=payload.get("step_id"),
            step_index=payload.get("step_index"),
            payload=dict(payload.get("payload", {})),
            screenshot_path=payload.get("screenshot_path"),
        )

    def _serialize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if is_dataclass(value):
            return {
                field.name: self._serialize_value(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, dict):
            return {str(key): self._serialize_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)):
            return value
        return repr(value)

    def _sanitize_payload(self, payload: Any) -> Any:
        serialized = self._serialize_value(payload)
        if self.sensitive_data_protector is None:
            return serialized
        return self.sensitive_data_protector.sanitize_payload(serialized, location="execution_trace")


