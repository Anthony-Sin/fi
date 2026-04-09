from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    AccountExecutionMode,
    AccountRotationResult,
    RotationExecutionEvent,
    RotationExecutionSnapshot,
    RotationTask,
)


@dataclass(slots=True)
class AccountRotationOrchestrator:
    storage_path: str
    account_registry: object
    task_executor: Callable[[RotationTask, str], bool] | None = None

    def execute(
        self,
        tasks: list[RotationTask],
        *,
        mode: AccountExecutionMode = AccountExecutionMode.SEQUENTIAL,
        minimum_reuse_interval: timedelta = timedelta(minutes=1),
        unhealthy_threshold: float = 0.5,
    ) -> AccountRotationResult:
        snapshot = self._load_snapshot()
        accounts = {account.name: account for account in self.account_registry.list_accounts()}
        grouped = self._group_tasks(tasks)

        scheduled_batches: list[list[RotationTask]] = []
        executed_events: list[RotationExecutionEvent] = []
        skipped_events: list[RotationExecutionEvent] = []

        if mode is AccountExecutionMode.SEQUENTIAL:
            for account_name, batch in grouped.items():
                if self._is_unhealthy(accounts.get(account_name), unhealthy_threshold):
                    skipped_events.extend(self._skip_batch(batch, account_name, "Account is unhealthy or unavailable."))
                    continue
                if self._violates_reuse_interval(snapshot, accounts.get(account_name), account_name, minimum_reuse_interval):
                    skipped_events.extend(self._skip_batch(batch, account_name, "Minimum reuse interval has not elapsed."))
                    continue
                scheduled_batches.append(batch)
                executed_events.extend(self._execute_batch(batch, account_name))
        else:
            ready_batch: list[RotationTask] = []
            for account_name, batch in grouped.items():
                if self._is_unhealthy(accounts.get(account_name), unhealthy_threshold):
                    skipped_events.extend(self._skip_batch(batch, account_name, "Account is unhealthy or unavailable."))
                    continue
                if self._violates_reuse_interval(snapshot, accounts.get(account_name), account_name, minimum_reuse_interval):
                    skipped_events.extend(self._skip_batch(batch, account_name, "Minimum reuse interval has not elapsed."))
                    continue
                scheduled_batches.append(batch)
                ready_batch.extend(batch)

            if ready_batch:
                executed_events.extend(self._execute_parallel(ready_batch))

        snapshot.events.extend(executed_events)
        snapshot.events.extend(skipped_events)
        self._save_snapshot(snapshot)

        return AccountRotationResult(
            succeeded=bool(executed_events) or not tasks,
            mode=mode,
            scheduled_batches=scheduled_batches,
            executed_events=executed_events,
            skipped_tasks=skipped_events,
            reason=None if executed_events or not tasks else "No eligible tasks could be executed.",
        )

    def get_execution_log(self) -> list[RotationExecutionEvent]:
        return self._load_snapshot().events

    def _group_tasks(self, tasks: list[RotationTask]) -> OrderedDict[str, list[RotationTask]]:
        grouped: OrderedDict[str, list[RotationTask]] = OrderedDict()
        for task in tasks:
            grouped.setdefault(task.required_account, []).append(task)
        return grouped

    def _execute_batch(self, tasks: list[RotationTask], account_name: str) -> list[RotationExecutionEvent]:
        events: list[RotationExecutionEvent] = []
        for task in tasks:
            succeeded = self.task_executor(task, account_name) if self.task_executor is not None else True
            events.append(
                RotationExecutionEvent(
                    task_id=task.task_id,
                    account_name=account_name,
                    timestamp=datetime.now(timezone.utc),
                    status="executed" if succeeded else "failed",
                    detail="Sequential execution.",
                )
            )
            self.account_registry.log_account_usage(account_name, "task_execution", task.task_id)
            if succeeded:
                self.account_registry.update_last_used(account_name)
        return events

    def _execute_parallel(self, tasks: list[RotationTask]) -> list[RotationExecutionEvent]:
        events: list[RotationExecutionEvent] = []
        for task in tasks:
            succeeded = self.task_executor(task, task.required_account) if self.task_executor is not None else True
            events.append(
                RotationExecutionEvent(
                    task_id=task.task_id,
                    account_name=task.required_account,
                    timestamp=datetime.now(timezone.utc),
                    status="executed" if succeeded else "failed",
                    detail="Parallel execution batch.",
                )
            )
            self.account_registry.log_account_usage(task.required_account, "task_execution", task.task_id)
            if succeeded:
                self.account_registry.update_last_used(task.required_account)
        return events

    def _skip_batch(self, tasks: list[RotationTask], account_name: str, detail: str) -> list[RotationExecutionEvent]:
        return [
            RotationExecutionEvent(
                task_id=task.task_id,
                account_name=account_name,
                timestamp=datetime.now(timezone.utc),
                status="skipped",
                detail=detail,
            )
            for task in tasks
        ]

    def _is_unhealthy(self, account, threshold: float) -> bool:
        if account is None:
            return True
        return (not account.active) or account.health_score < threshold

    def _violates_reuse_interval(
        self,
        snapshot: RotationExecutionSnapshot,
        account,
        account_name: str,
        minimum_reuse_interval: timedelta,
    ) -> bool:
        latest = None
        for event in reversed(snapshot.events):
            if event.account_name.casefold() == account_name.casefold() and event.status == "executed":
                latest = event
                break
        latest_timestamp = latest.timestamp if latest is not None else None
        if account is not None and getattr(account, "last_used_at", None) is not None:
            account_last_used = account.last_used_at
            if latest_timestamp is None or account_last_used > latest_timestamp:
                latest_timestamp = account_last_used
        if latest_timestamp is None:
            return False
        return latest_timestamp + minimum_reuse_interval > datetime.now(timezone.utc)

    def _load_snapshot(self) -> RotationExecutionSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return RotationExecutionSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RotationExecutionSnapshot(
            events=[
                RotationExecutionEvent(
                    task_id=item["task_id"],
                    account_name=item["account_name"],
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    status=item["status"],
                    detail=item.get("detail"),
                )
                for item in payload.get("events", [])
            ]
        )

    def _save_snapshot(self, snapshot: RotationExecutionSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "events": [
                {
                    "task_id": event.task_id,
                    "account_name": event.account_name,
                    "timestamp": event.timestamp.isoformat(),
                    "status": event.status,
                    "detail": event.detail,
                }
                for event in snapshot.events
            ]
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
