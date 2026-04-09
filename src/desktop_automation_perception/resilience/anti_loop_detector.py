from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Callable

from desktop_automation_perception.models import (
    AntiLoopDetectionResult,
    AntiLoopEventRecord,
    AntiLoopStepExecution,
    AntiLoopTriggerType,
    EscalationTriggerType,
)


@dataclass(slots=True)
class AntiLoopDetector:
    storage_path: str
    workflow_id: str
    max_step_execution_count: int = 3
    max_pipeline_duration_seconds: float = 300.0
    audit_logger: object | None = None
    escalation_manager: object | None = None
    now_fn: Callable[[], datetime] = utc_now
    monotonic_fn: Callable[[], float] = monotonic
    _started_at: float = field(default=0.0, init=False, repr=False)
    _step_counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _step_history: list[AntiLoopStepExecution] = field(default_factory=list, init=False, repr=False)
    _last_record: AntiLoopEventRecord | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._started_at = self.monotonic_fn()
        self._step_counts.clear()
        self._step_history.clear()
        self._last_record = None

    def before_step(
        self,
        step_id: str,
        *,
        metadata: dict | None = None,
    ) -> AntiLoopDetectionResult:
        elapsed_before_step = self.monotonic_fn() - self._started_at
        if elapsed_before_step > float(self.max_pipeline_duration_seconds):
            return self._trigger(
                trigger_type=AntiLoopTriggerType.PIPELINE_TIMEOUT,
                escalation_trigger=EscalationTriggerType.PIPELINE_TIMEOUT,
                step_id=step_id,
                elapsed_seconds=elapsed_before_step,
                step_execution_count=self._step_counts.get(step_id, 0),
                detail=(
                    f"Workflow exceeded the maximum runtime of {self.max_pipeline_duration_seconds:.2f} seconds."
                ),
            )

        execution_count = self._step_counts.get(step_id, 0) + 1
        self._step_counts[step_id] = execution_count
        execution = AntiLoopStepExecution(
            step_id=step_id,
            timestamp=self.now_fn(),
            execution_count=execution_count,
            elapsed_seconds=elapsed_before_step,
            metadata={} if metadata is None else dict(metadata),
        )
        self._step_history.append(execution)

        if execution_count > int(self.max_step_execution_count):
            return self._trigger(
                trigger_type=AntiLoopTriggerType.STEP_EXECUTION_LIMIT,
                escalation_trigger=EscalationTriggerType.LOOP_DETECTED,
                step_id=step_id,
                elapsed_seconds=elapsed_before_step,
                step_execution_count=execution_count,
                detail=(
                    f"Step {step_id!r} exceeded the maximum execution count of "
                    f"{self.max_step_execution_count}."
                ),
            )

        return AntiLoopDetectionResult(succeeded=True, triggered=False)

    def list_events(self) -> list[AntiLoopEventRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_record(item) for item in payload.get("events", [])]

    def last_record(self) -> AntiLoopEventRecord | None:
        return self._last_record

    def _trigger(
        self,
        *,
        trigger_type: AntiLoopTriggerType,
        escalation_trigger: EscalationTriggerType,
        step_id: str | None,
        elapsed_seconds: float,
        step_execution_count: int,
        detail: str,
    ) -> AntiLoopDetectionResult:
        record = AntiLoopEventRecord(
            workflow_id=self.workflow_id,
            trigger_type=trigger_type,
            step_id=step_id,
            detail=detail,
            max_step_execution_count=int(self.max_step_execution_count),
            max_pipeline_duration_seconds=float(self.max_pipeline_duration_seconds),
            elapsed_seconds=float(elapsed_seconds),
            step_execution_count=int(step_execution_count),
            step_history=[
                AntiLoopStepExecution(
                    step_id=item.step_id,
                    timestamp=item.timestamp,
                    execution_count=item.execution_count,
                    elapsed_seconds=item.elapsed_seconds,
                    metadata=dict(item.metadata),
                )
                for item in self._step_history
            ],
            timestamp=self.now_fn(),
        )
        self._append_record(record)
        self._log_event(record)
        escalation_result = self._escalate(record, escalation_trigger)
        self._last_record = record
        return AntiLoopDetectionResult(
            succeeded=False,
            triggered=True,
            record=record,
            escalation_result=escalation_result,
            reason=detail,
        )

    def _append_record(self, record: AntiLoopEventRecord) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("events", []).append(self._serialize_record(record))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _log_event(self, record: AntiLoopEventRecord) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log_action(
            workflow_id=self.workflow_id,
            step_name=record.step_id or "workflow",
            action_type="anti_loop_detected",
            target_element=record.trigger_type.value,
            input_data={
                "step_id": record.step_id,
                "max_step_execution_count": record.max_step_execution_count,
                "max_pipeline_duration_seconds": record.max_pipeline_duration_seconds,
            },
            output_data={
                "detail": record.detail,
                "elapsed_seconds": record.elapsed_seconds,
                "step_execution_count": record.step_execution_count,
                "step_history": [
                    {
                        "step_id": item.step_id,
                        "timestamp": item.timestamp.isoformat(),
                        "execution_count": item.execution_count,
                        "elapsed_seconds": item.elapsed_seconds,
                        "metadata": dict(item.metadata),
                    }
                    for item in record.step_history
                ],
            },
            success=False,
        )

    def _escalate(
        self,
        record: AntiLoopEventRecord,
        trigger_type: EscalationTriggerType,
    ):
        if self.escalation_manager is None:
            return None
        return self.escalation_manager.trigger(
            workflow_id=self.workflow_id,
            step_id=record.step_id,
            trigger_type=trigger_type,
            detail=record.detail,
            context_data={
                "anti_loop_trigger_type": record.trigger_type.value,
                "elapsed_seconds": record.elapsed_seconds,
                "step_execution_count": record.step_execution_count,
                "max_step_execution_count": record.max_step_execution_count,
                "max_pipeline_duration_seconds": record.max_pipeline_duration_seconds,
                "step_history": [
                    {
                        "step_id": item.step_id,
                        "timestamp": item.timestamp.isoformat(),
                        "execution_count": item.execution_count,
                        "elapsed_seconds": item.elapsed_seconds,
                        "metadata": dict(item.metadata),
                    }
                    for item in record.step_history
                ],
            },
        )

    def _serialize_record(self, record: AntiLoopEventRecord) -> dict:
        return {
            "workflow_id": record.workflow_id,
            "trigger_type": record.trigger_type.value,
            "step_id": record.step_id,
            "detail": record.detail,
            "max_step_execution_count": record.max_step_execution_count,
            "max_pipeline_duration_seconds": record.max_pipeline_duration_seconds,
            "elapsed_seconds": record.elapsed_seconds,
            "step_execution_count": record.step_execution_count,
            "step_history": [
                {
                    "step_id": item.step_id,
                    "timestamp": item.timestamp.isoformat(),
                    "execution_count": item.execution_count,
                    "elapsed_seconds": item.elapsed_seconds,
                    "metadata": dict(item.metadata),
                }
                for item in record.step_history
            ],
            "timestamp": record.timestamp.isoformat(),
        }

    def _deserialize_record(self, payload: dict) -> AntiLoopEventRecord:
        return AntiLoopEventRecord(
            workflow_id=payload["workflow_id"],
            trigger_type=AntiLoopTriggerType(payload["trigger_type"]),
            step_id=payload.get("step_id"),
            detail=payload.get("detail"),
            max_step_execution_count=int(payload.get("max_step_execution_count", 0)),
            max_pipeline_duration_seconds=float(payload.get("max_pipeline_duration_seconds", 0.0)),
            elapsed_seconds=float(payload.get("elapsed_seconds", 0.0)),
            step_execution_count=int(payload.get("step_execution_count", 0)),
            step_history=[
                AntiLoopStepExecution(
                    step_id=item["step_id"],
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    execution_count=int(item.get("execution_count", 0)),
                    elapsed_seconds=float(item.get("elapsed_seconds", 0.0)),
                    metadata=dict(item.get("metadata", {})),
                )
                for item in payload.get("step_history", [])
            ],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )


