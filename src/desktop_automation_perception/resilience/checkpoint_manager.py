from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    CheckpointDecision,
    CheckpointRestoreResult,
    CheckpointResumePolicy,
    UIStateFingerprint,
    WorkflowCheckpoint,
    WorkflowContext,
    WorkflowStepResult,
)


@dataclass(slots=True)
class CheckpointManager:
    storage_path: str
    decision_callback: Callable[[WorkflowCheckpoint], CheckpointDecision] | None = None

    def save_checkpoint(
        self,
        *,
        workflow_id: str,
        step_index: int,
        workflow_context: WorkflowContext,
        account_context: dict[str, str] | None = None,
        collected_data: dict[str, str] | None = None,
        step_outcomes: list[WorkflowStepResult] | None = None,
        ui_state_fingerprint: UIStateFingerprint | None = None,
    ) -> WorkflowCheckpoint:
        checkpoint = WorkflowCheckpoint(
            workflow_id=workflow_id,
            saved_at=utc_now(),
            step_index=step_index,
            workflow_context=self._copy_context(workflow_context),
            account_context=dict(account_context or {}),
            collected_data=dict(collected_data or {}),
            step_outcomes=[self._copy_step_outcome(item) for item in (step_outcomes or [])],
            ui_state_fingerprint=self._copy_ui_state_fingerprint(ui_state_fingerprint),
        )
        payload = self._load_payload()
        payload[workflow_id] = self._serialize_checkpoint(checkpoint)
        self._write_payload(payload)
        return checkpoint

    def get_checkpoint(self, workflow_id: str) -> WorkflowCheckpoint | None:
        payload = self._load_payload()
        data = payload.get(workflow_id)
        if data is None:
            return None
        return self._deserialize_checkpoint(data)

    def restore_or_restart(
        self,
        *,
        workflow_id: str,
        policy: CheckpointResumePolicy = CheckpointResumePolicy.AUTO_RESUME,
    ) -> CheckpointRestoreResult:
        checkpoint = self.get_checkpoint(workflow_id)
        if checkpoint is None:
            return CheckpointRestoreResult(
                succeeded=False,
                decision=CheckpointDecision.RESTART,
                checkpoint=None,
                reason="No checkpoint found for the workflow.",
            )

        if policy is CheckpointResumePolicy.AUTO_RESTART:
            self.clear_checkpoint(workflow_id)
            return CheckpointRestoreResult(
                succeeded=True,
                decision=CheckpointDecision.RESTART,
                checkpoint=None,
            )

        if policy is CheckpointResumePolicy.CALLBACK:
            if self.decision_callback is None:
                return CheckpointRestoreResult(
                    succeeded=False,
                    decision=CheckpointDecision.RESTART,
                    checkpoint=None,
                    reason="Checkpoint callback policy requested but no decision callback is configured.",
                )
            decision = self.decision_callback(checkpoint)
            if decision is CheckpointDecision.RESTART:
                self.clear_checkpoint(workflow_id)
                return CheckpointRestoreResult(
                    succeeded=True,
                    decision=CheckpointDecision.RESTART,
                    checkpoint=None,
                )
            return CheckpointRestoreResult(
                succeeded=True,
                decision=CheckpointDecision.RESUME,
                checkpoint=checkpoint,
            )

        return CheckpointRestoreResult(
            succeeded=True,
            decision=CheckpointDecision.RESUME,
            checkpoint=checkpoint,
        )

    def clear_checkpoint(self, workflow_id: str) -> None:
        payload = self._load_payload()
        if workflow_id in payload:
            del payload[workflow_id]
            self._write_payload(payload)

    def _load_payload(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_payload(self, payload: dict) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_checkpoint(self, checkpoint: WorkflowCheckpoint) -> dict:
        return {
            "workflow_id": checkpoint.workflow_id,
            "saved_at": checkpoint.saved_at.isoformat(),
            "step_index": checkpoint.step_index,
            "workflow_context": self._serialize_context(checkpoint.workflow_context),
            "account_context": checkpoint.account_context,
            "collected_data": checkpoint.collected_data,
            "step_outcomes": [self._serialize_step_outcome(item) for item in checkpoint.step_outcomes],
            "ui_state_fingerprint": self._serialize_ui_state_fingerprint(checkpoint.ui_state_fingerprint),
        }

    def _deserialize_checkpoint(self, payload: dict) -> WorkflowCheckpoint:
        return WorkflowCheckpoint(
            workflow_id=payload["workflow_id"],
            saved_at=datetime.fromisoformat(payload["saved_at"]),
            step_index=int(payload["step_index"]),
            workflow_context=self._deserialize_context(payload["workflow_context"]),
            account_context=dict(payload.get("account_context", {})),
            collected_data=dict(payload.get("collected_data", {})),
            step_outcomes=[self._deserialize_step_outcome(item) for item in payload.get("step_outcomes", [])],
            ui_state_fingerprint=self._deserialize_ui_state_fingerprint(payload.get("ui_state_fingerprint")),
        )

    def _serialize_context(self, context: WorkflowContext) -> dict:
        return {
            "current_application": context.current_application,
            "step_number": context.step_number,
            "shared_data": context.shared_data,
            "active_applications": context.active_applications,
            "application_signatures": context.application_signatures,
        }

    def _deserialize_context(self, payload: dict) -> WorkflowContext:
        return WorkflowContext(
            current_application=payload.get("current_application"),
            step_number=int(payload.get("step_number", 0)),
            shared_data=dict(payload.get("shared_data", {})),
            active_applications=list(payload.get("active_applications", [])),
            application_signatures=dict(payload.get("application_signatures", {})),
        )

    def _serialize_step_outcome(self, outcome: WorkflowStepResult) -> dict:
        return {
            "step_id": outcome.step_id,
            "application_name": outcome.application_name,
            "succeeded": outcome.succeeded,
            "dry_run": outcome.dry_run,
            "context_snapshot": None
            if outcome.context_snapshot is None
            else self._serialize_context(outcome.context_snapshot),
            "reason": outcome.reason,
        }

    def _deserialize_step_outcome(self, payload: dict) -> WorkflowStepResult:
        return WorkflowStepResult(
            step_id=payload["step_id"],
            application_name=payload["application_name"],
            succeeded=bool(payload["succeeded"]),
            dry_run=bool(payload.get("dry_run", False)),
            context_snapshot=self._deserialize_context(payload["context_snapshot"])
            if payload.get("context_snapshot") is not None
            else None,
            reason=payload.get("reason"),
        )

    def _copy_context(self, context: WorkflowContext) -> WorkflowContext:
        return WorkflowContext(
            current_application=context.current_application,
            step_number=context.step_number,
            shared_data=dict(context.shared_data),
            active_applications=list(context.active_applications),
            application_signatures=dict(context.application_signatures),
        )

    def _copy_step_outcome(self, outcome: WorkflowStepResult) -> WorkflowStepResult:
        return WorkflowStepResult(
            step_id=outcome.step_id,
            application_name=outcome.application_name,
            succeeded=outcome.succeeded,
            dry_run=outcome.dry_run,
            context_snapshot=self._copy_context(outcome.context_snapshot)
            if outcome.context_snapshot is not None
            else None,
            reason=outcome.reason,
        )

    def _serialize_ui_state_fingerprint(self, fingerprint: UIStateFingerprint | None) -> dict | None:
        if fingerprint is None:
            return None
        return {
            "window_title_hash": fingerprint.window_title_hash,
            "landmark_positions": {
                name: [position[0], position[1]] for name, position in fingerprint.landmark_positions.items()
            },
            "pixel_histogram": list(fingerprint.pixel_histogram),
            "screen_size": [fingerprint.screen_size[0], fingerprint.screen_size[1]],
            "window_count": fingerprint.window_count,
            "captured_at": fingerprint.captured_at.isoformat(),
        }

    def _deserialize_ui_state_fingerprint(self, payload: dict | None) -> UIStateFingerprint | None:
        if payload is None:
            return None
        landmark_positions = {
            name: (float(position[0]), float(position[1]))
            for name, position in dict(payload.get("landmark_positions", {})).items()
        }
        screen_size = payload.get("screen_size", [0, 0])
        return UIStateFingerprint(
            window_title_hash=payload.get("window_title_hash", ""),
            landmark_positions=landmark_positions,
            pixel_histogram=tuple(float(value) for value in payload.get("pixel_histogram", [])),
            screen_size=(int(screen_size[0]), int(screen_size[1])),
            window_count=int(payload.get("window_count", 0)),
            captured_at=datetime.fromisoformat(payload["captured_at"])
            if payload.get("captured_at")
            else utc_now(),
        )

    def _copy_ui_state_fingerprint(self, fingerprint: UIStateFingerprint | None) -> UIStateFingerprint | None:
        if fingerprint is None:
            return None
        return UIStateFingerprint(
            window_title_hash=fingerprint.window_title_hash,
            landmark_positions=dict(fingerprint.landmark_positions),
            pixel_histogram=tuple(fingerprint.pixel_histogram),
            screen_size=tuple(fingerprint.screen_size),
            window_count=fingerprint.window_count,
            captured_at=fingerprint.captured_at,
        )


