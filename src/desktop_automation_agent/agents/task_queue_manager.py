from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_agent.models import (
    AutomationTask,
    TaskPriority,
    TaskQueueAlert,
    TaskQueueDepthMetric,
    TaskQueueOperationResult,
    TaskQueueSnapshot,
)


@dataclass(slots=True)
class TaskQueueManager:
    storage_path: str
    depth_alert_thresholds: tuple[int, ...] = (25, 50, 100)
    alert_callback: Callable[[TaskQueueAlert], None] | None = None
    depth_metric_callback: Callable[[TaskQueueDepthMetric], None] | None = None
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def enqueue(self, task: AutomationTask) -> TaskQueueOperationResult:
        snapshot = self._load_snapshot()
        previous_depth = len(snapshot.tasks)
        snapshot.tasks.append(self._copy_task(task))
        metric, alerts = self._record_depth(snapshot, previous_depth=previous_depth)
        self._save_snapshot(snapshot)
        self._emit_depth_metric(metric)
        self._emit_alerts(alerts)
        return TaskQueueOperationResult(
            succeeded=True,
            task=task,
            tasks=self._ordered_tasks(snapshot.tasks, snapshot.last_dequeued_account),
            metric=metric,
            alert=alerts[-1] if alerts else None,
        )

    def dequeue(self) -> TaskQueueOperationResult:
        snapshot = self._load_snapshot()
        task = self._select_next_task(snapshot.tasks, snapshot.last_dequeued_account)
        if task is None:
            return TaskQueueOperationResult(succeeded=False, reason="Task queue is empty.")

        snapshot.tasks = [item for item in snapshot.tasks if item.task_id != task.task_id]
        snapshot.last_dequeued_account = task.required_account
        metric, alerts = self._record_depth(snapshot, previous_depth=len(snapshot.tasks) + 1)
        self._save_snapshot(snapshot)
        self._emit_depth_metric(metric)
        self._emit_alerts(alerts)
        return TaskQueueOperationResult(
            succeeded=True,
            task=task,
            tasks=self._ordered_tasks(snapshot.tasks, snapshot.last_dequeued_account),
            metric=metric,
            alert=alerts[-1] if alerts else None,
        )

    def peek(self) -> TaskQueueOperationResult:
        snapshot = self._load_snapshot()
        task = self._select_next_task(snapshot.tasks, snapshot.last_dequeued_account)
        if task is None:
            return TaskQueueOperationResult(succeeded=False, reason="Task queue is empty.")
        return TaskQueueOperationResult(
            succeeded=True,
            task=task,
            tasks=self._ordered_tasks(snapshot.tasks, snapshot.last_dequeued_account),
        )

    def inspect(self) -> TaskQueueOperationResult:
        snapshot = self._load_snapshot()
        return TaskQueueOperationResult(
            succeeded=True,
            tasks=self._ordered_tasks(snapshot.tasks, snapshot.last_dequeued_account),
        )

    def clear_pending_tasks(self, *, reason: str | None = None) -> TaskQueueOperationResult:
        snapshot = self._load_snapshot()
        removed_tasks = self._ordered_tasks(snapshot.tasks, snapshot.last_dequeued_account)
        previous_depth = len(snapshot.tasks)
        snapshot.tasks = []
        metric, alerts = self._record_depth(snapshot, previous_depth=previous_depth)
        snapshot.last_dequeued_account = None
        self._save_snapshot(snapshot)
        self._emit_depth_metric(metric)
        self._emit_alerts(alerts)
        return TaskQueueOperationResult(
            succeeded=True,
            tasks=[],
            removed_tasks=removed_tasks,
            metric=metric,
            alert=alerts[-1] if alerts else None,
            reason=reason,
        )

    def get_snapshot(self) -> TaskQueueSnapshot:
        return self._load_snapshot()

    def _select_next_task(
        self,
        tasks: list[AutomationTask],
        last_dequeued_account: str | None,
    ) -> AutomationTask | None:
        if not tasks:
            return None

        ordered = self._ordered_tasks(tasks, None)
        selected = ordered[0]
        if last_dequeued_account is None:
            return selected

        same_account_tasks = [task for task in tasks if task.required_account == last_dequeued_account]
        if not same_account_tasks:
            return selected

        same_account_best = self._ordered_tasks(same_account_tasks, None)[0]
        if self._should_prefer_same_account(same_account_best, selected):
            return same_account_best
        return selected

    def _should_prefer_same_account(
        self,
        same_account_task: AutomationTask,
        selected_task: AutomationTask,
    ) -> bool:
        now = self.now_fn()
        same_priority = self._effective_priority_score(same_account_task, now) == self._effective_priority_score(
            selected_task,
            now,
        )
        if not same_priority:
            return False
        return self._deadline_sort_value(same_account_task) <= self._deadline_sort_value(selected_task) or (
            same_account_task.required_account != selected_task.required_account
        )

    def _ordered_tasks(
        self,
        tasks: list[AutomationTask],
        current_account: str | None,
    ) -> list[AutomationTask]:
        now = self.now_fn()
        return sorted(
            tasks,
            key=lambda task: (
                -self._effective_priority_score(task, now),
                self._account_group_penalty(task, current_account, now),
                self._deadline_sort_value(task),
                task.enqueued_at,
                task.task_id,
            ),
        )

    def _account_group_penalty(
        self,
        task: AutomationTask,
        current_account: str | None,
        now: datetime,
    ) -> int:
        if current_account is None or task.required_account != current_account:
            return 1
        if self._deadline_escalation_score(task, now) >= 3:
            return 1
        return 0

    def _effective_priority_score(
        self,
        task: AutomationTask,
        now: datetime,
    ) -> int:
        return self._base_priority_score(task.priority) + self._deadline_escalation_score(task, now)

    def _base_priority_score(self, priority: TaskPriority) -> int:
        mapping = {
            TaskPriority.LOW: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.HIGH: 3,
            TaskPriority.CRITICAL: 4,
        }
        return mapping[priority]

    def _deadline_escalation_score(
        self,
        task: AutomationTask,
        now: datetime,
    ) -> int:
        if task.deadline is None:
            return 0
        remaining = task.deadline - now
        if remaining <= timedelta(0):
            return 4
        if remaining <= timedelta(minutes=15):
            return 3
        if remaining <= timedelta(hours=1):
            return 2
        if remaining <= timedelta(hours=6):
            return 1
        return 0

    def _deadline_sort_value(self, task: AutomationTask) -> datetime:
        if task.deadline is not None:
            return task.deadline
        return datetime.max.replace(tzinfo=timezone.utc)

    def _record_depth(
        self,
        snapshot: TaskQueueSnapshot,
        *,
        previous_depth: int,
    ) -> tuple[TaskQueueDepthMetric, list[TaskQueueAlert]]:
        depth = len(snapshot.tasks)
        exceeded = [threshold for threshold in self._normalized_thresholds() if depth > threshold]
        metric = TaskQueueDepthMetric(
            timestamp=self.now_fn(),
            depth=depth,
            threshold_exceeded=bool(exceeded),
            exceeded_thresholds=exceeded,
        )
        snapshot.depth_metrics.append(metric)

        alerts: list[TaskQueueAlert] = []
        for threshold in self._normalized_thresholds():
            if previous_depth <= threshold < depth:
                alert = TaskQueueAlert(
                    timestamp=metric.timestamp,
                    depth=depth,
                    threshold=threshold,
                    message=f"Task queue depth {depth} exceeded threshold {threshold}.",
                )
                snapshot.alerts.append(alert)
                alerts.append(alert)
        return metric, alerts

    def _normalized_thresholds(self) -> tuple[int, ...]:
        return tuple(sorted({threshold for threshold in self.depth_alert_thresholds if threshold >= 0}))

    def _emit_depth_metric(self, metric: TaskQueueDepthMetric) -> None:
        if self.depth_metric_callback is not None:
            self.depth_metric_callback(metric)

    def _emit_alerts(self, alerts: list[TaskQueueAlert]) -> None:
        if self.alert_callback is None:
            return
        for alert in alerts:
            self.alert_callback(alert)

    def _load_snapshot(self) -> TaskQueueSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return TaskQueueSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TaskQueueSnapshot(
            tasks=[self._deserialize_task(item) for item in payload.get("tasks", [])],
            depth_metrics=[self._deserialize_metric(item) for item in payload.get("depth_metrics", [])],
            alerts=[self._deserialize_alert(item) for item in payload.get("alerts", [])],
            last_dequeued_account=payload.get("last_dequeued_account"),
        )

    def _save_snapshot(self, snapshot: TaskQueueSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tasks": [self._serialize_task(task) for task in snapshot.tasks],
            "depth_metrics": [self._serialize_metric(metric) for metric in snapshot.depth_metrics],
            "alerts": [self._serialize_alert(alert) for alert in snapshot.alerts],
            "last_dequeued_account": snapshot.last_dequeued_account,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_task(self, task: AutomationTask) -> AutomationTask:
        return self._deserialize_task(self._serialize_task(task))

    def _serialize_task(self, task: AutomationTask) -> dict:
        return {
            "task_id": task.task_id,
            "priority": task.priority.value,
            "required_account": task.required_account,
            "required_account_type": task.required_account_type,
            "required_application": task.required_application,
            "required_module": task.required_module,
            "input_payload": dict(task.input_payload),
            "deadline": None if task.deadline is None else task.deadline.isoformat(),
            "max_retry_count": task.max_retry_count,
            "retry_count": task.retry_count,
            "enqueued_at": task.enqueued_at.isoformat(),
        }

    def _deserialize_task(self, payload: dict) -> AutomationTask:
        return AutomationTask(
            task_id=payload["task_id"],
            priority=TaskPriority(payload["priority"]),
            required_account=payload.get("required_account"),
            required_account_type=payload.get("required_account_type"),
            required_application=payload.get("required_application"),
            required_module=payload["required_module"],
            input_payload=dict(payload.get("input_payload", {})),
            deadline=self._parse_datetime(payload.get("deadline")),
            max_retry_count=int(payload.get("max_retry_count", 0)),
            retry_count=int(payload.get("retry_count", 0)),
            enqueued_at=self._parse_datetime(payload.get("enqueued_at")) or self.now_fn(),
        )

    def _serialize_metric(self, metric: TaskQueueDepthMetric) -> dict:
        return {
            "timestamp": metric.timestamp.isoformat(),
            "depth": metric.depth,
            "threshold_exceeded": metric.threshold_exceeded,
            "exceeded_thresholds": list(metric.exceeded_thresholds),
        }

    def _deserialize_metric(self, payload: dict) -> TaskQueueDepthMetric:
        return TaskQueueDepthMetric(
            timestamp=self._parse_datetime(payload.get("timestamp")) or self.now_fn(),
            depth=int(payload.get("depth", 0)),
            threshold_exceeded=bool(payload.get("threshold_exceeded", False)),
            exceeded_thresholds=[int(item) for item in payload.get("exceeded_thresholds", [])],
        )

    def _serialize_alert(self, alert: TaskQueueAlert) -> dict:
        return {
            "timestamp": alert.timestamp.isoformat(),
            "depth": alert.depth,
            "threshold": alert.threshold,
            "message": alert.message,
        }

    def _deserialize_alert(self, payload: dict) -> TaskQueueAlert:
        return TaskQueueAlert(
            timestamp=self._parse_datetime(payload.get("timestamp")) or self.now_fn(),
            depth=int(payload.get("depth", 0)),
            threshold=int(payload.get("threshold", 0)),
            message=payload.get("message", ""),
        )

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)
