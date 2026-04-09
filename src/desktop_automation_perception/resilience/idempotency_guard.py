from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    IdempotencyRecord,
    IdempotencyResult,
    IdempotencySnapshot,
)


@dataclass(slots=True)
class IdempotencyGuard:
    storage_path: str

    def run_once(
        self,
        *,
        action_id: str,
        action: Callable[[], dict],
    ) -> IdempotencyResult:
        snapshot = self._load_snapshot()
        existing = self._find_record(snapshot, action_id)
        if existing is not None:
            return IdempotencyResult(
                succeeded=True,
                action_id=action_id,
                executed=False,
                cached=True,
                result_payload=dict(existing.result_payload),
            )

        result_payload = action()
        record = IdempotencyRecord(
            action_id=action_id,
            completed_at=utc_now(),
            result_payload=dict(result_payload),
        )
        snapshot.completed_actions.append(record)
        self._save_snapshot(snapshot)
        return IdempotencyResult(
            succeeded=True,
            action_id=action_id,
            executed=True,
            cached=False,
            result_payload=dict(result_payload),
        )

    def get_completed_action(self, action_id: str) -> IdempotencyRecord | None:
        return self._find_record(self._load_snapshot(), action_id)

    def reset_action(self, action_id: str) -> bool:
        snapshot = self._load_snapshot()
        before = len(snapshot.completed_actions)
        snapshot.completed_actions = [
            record for record in snapshot.completed_actions if record.action_id != action_id
        ]
        changed = len(snapshot.completed_actions) != before
        if changed:
            self._save_snapshot(snapshot)
        return changed

    def reset_all(self) -> None:
        self._save_snapshot(IdempotencySnapshot())

    def _find_record(
        self,
        snapshot: IdempotencySnapshot,
        action_id: str,
    ) -> IdempotencyRecord | None:
        for record in snapshot.completed_actions:
            if record.action_id == action_id:
                return record
        return None

    def _load_snapshot(self) -> IdempotencySnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return IdempotencySnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IdempotencySnapshot(
            completed_actions=[
                IdempotencyRecord(
                    action_id=item["action_id"],
                    completed_at=datetime.fromisoformat(item["completed_at"]),
                    result_payload=dict(item.get("result_payload", {})),
                )
                for item in payload.get("completed_actions", [])
            ]
        )

    def _save_snapshot(self, snapshot: IdempotencySnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "completed_actions": [
                {
                    "action_id": record.action_id,
                    "completed_at": record.completed_at.isoformat(),
                    "result_payload": record.result_payload,
                }
                for record in snapshot.completed_actions
            ]
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


