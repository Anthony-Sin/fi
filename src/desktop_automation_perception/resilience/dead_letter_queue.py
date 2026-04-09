from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from desktop_automation_perception.models import (
    DeadLetterItem,
    DeadLetterOperationResult,
    DeadLetterSnapshot,
    RetryAttemptLog,
    RetryFailureResult,
)


@dataclass(slots=True)
class DeadLetterQueueHandler:
    storage_path: str
    alert_threshold: int = 10
    alert_callback: Callable[[int], None] | None = None

    def enqueue(
        self,
        *,
        action_type: str,
        inputs: dict,
        retry_failure: RetryFailureResult,
        item_id: str | None = None,
    ) -> DeadLetterOperationResult:
        snapshot = self._load_snapshot()
        item = DeadLetterItem(
            item_id=item_id or str(uuid4()),
            action_type=action_type,
            inputs=dict(inputs),
            retry_failure=self._copy_retry_failure(retry_failure),
        )
        snapshot.items.append(item)
        self._save_snapshot(snapshot)
        self._maybe_alert(len(snapshot.items))
        return DeadLetterOperationResult(succeeded=True, item=item)

    def inspect(self) -> DeadLetterOperationResult:
        snapshot = self._load_snapshot()
        return DeadLetterOperationResult(
            succeeded=True,
            items=list(snapshot.items),
        )

    def retry_item(
        self,
        item_id: str,
        retry_action: Callable[[DeadLetterItem], dict],
    ) -> DeadLetterOperationResult:
        snapshot = self._load_snapshot()
        item = self._find_item(snapshot, item_id)
        if item is None:
            return DeadLetterOperationResult(succeeded=False, reason="DLQ item not found.")
        retry_action(item)
        snapshot.items = [existing for existing in snapshot.items if existing.item_id != item_id]
        self._save_snapshot(snapshot)
        return DeadLetterOperationResult(succeeded=True, item=item)

    def bulk_reprocess(
        self,
        retry_action: Callable[[DeadLetterItem], dict],
    ) -> DeadLetterOperationResult:
        snapshot = self._load_snapshot()
        processed: list[DeadLetterItem] = []
        remaining: list[DeadLetterItem] = []
        for item in snapshot.items:
            try:
                retry_action(item)
                processed.append(item)
            except Exception:
                remaining.append(item)
        snapshot.items = remaining
        self._save_snapshot(snapshot)
        return DeadLetterOperationResult(
            succeeded=True,
            items=processed,
            reason=None if not remaining else f"{len(remaining)} item(s) remain in the DLQ.",
        )

    def export_report(
        self,
        output_path: str,
    ) -> DeadLetterOperationResult:
        snapshot = self._load_snapshot()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "item_count": len(snapshot.items),
            "items": [self._serialize_item(item) for item in snapshot.items],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return DeadLetterOperationResult(
            succeeded=True,
            items=list(snapshot.items),
            report_path=str(path),
        )

    def _maybe_alert(self, depth: int) -> None:
        if self.alert_callback is not None and depth > self.alert_threshold:
            self.alert_callback(depth)

    def _find_item(
        self,
        snapshot: DeadLetterSnapshot,
        item_id: str,
    ) -> DeadLetterItem | None:
        for item in snapshot.items:
            if item.item_id == item_id:
                return item
        return None

    def _load_snapshot(self) -> DeadLetterSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return DeadLetterSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DeadLetterSnapshot(
            items=[self._deserialize_item(item) for item in payload.get("items", [])]
        )

    def _save_snapshot(self, snapshot: DeadLetterSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [self._serialize_item(item) for item in snapshot.items]
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_item(self, item: DeadLetterItem) -> dict:
        return {
            "item_id": item.item_id,
            "action_type": item.action_type,
            "inputs": item.inputs,
            "retry_failure": None if item.retry_failure is None else self._serialize_retry_failure(item.retry_failure),
            "timestamp": item.timestamp.isoformat(),
        }

    def _deserialize_item(self, payload: dict) -> DeadLetterItem:
        return DeadLetterItem(
            item_id=payload["item_id"],
            action_type=payload["action_type"],
            inputs=dict(payload.get("inputs", {})),
            retry_failure=self._deserialize_retry_failure(payload.get("retry_failure")),
            timestamp=self._parse_timestamp(payload.get("timestamp")),
        )

    def _serialize_retry_failure(self, failure: RetryFailureResult) -> dict:
        return {
            "succeeded": failure.succeeded,
            "attempts": [
                {
                    "attempt_number": attempt.attempt_number,
                    "delay_seconds": attempt.delay_seconds,
                    "exception_type": attempt.exception_type,
                    "exception_message": attempt.exception_message,
                    "disposition": attempt.disposition.value if attempt.disposition is not None else None,
                }
                for attempt in failure.attempts
            ],
            "final_exception_type": failure.final_exception_type,
            "final_exception_message": failure.final_exception_message,
            "reason": failure.reason,
        }

    def _deserialize_retry_failure(self, payload: dict | None) -> RetryFailureResult | None:
        if payload is None:
            return None
        return RetryFailureResult(
            succeeded=bool(payload.get("succeeded", False)),
            attempts=[
                RetryAttemptLog(
                    attempt_number=int(item["attempt_number"]),
                    delay_seconds=float(item.get("delay_seconds", 0.0)),
                    exception_type=item.get("exception_type"),
                    exception_message=item.get("exception_message"),
                    disposition=None if item.get("disposition") is None else self._parse_disposition(item["disposition"]),
                )
                for item in payload.get("attempts", [])
            ],
            final_exception_type=payload.get("final_exception_type"),
            final_exception_message=payload.get("final_exception_message"),
            reason=payload.get("reason"),
        )

    def _copy_retry_failure(self, failure: RetryFailureResult) -> RetryFailureResult:
        return self._deserialize_retry_failure(self._serialize_retry_failure(failure)) or RetryFailureResult()

    def _parse_timestamp(self, value: str | None):
        from datetime import datetime

        return datetime.fromisoformat(value) if value is not None else utc_now()

    def _parse_disposition(self, value: str):
        from desktop_automation_perception.models import RetryDisposition

        return RetryDisposition(value)


