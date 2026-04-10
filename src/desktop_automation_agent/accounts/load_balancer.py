from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_agent.models import (
    AccountLoadMetric,
    AutomationTask,
    LoadBalancerDecisionRecord,
    LoadBalancerResult,
    LoadBalancerSnapshot,
    RateLimitRequest,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MultiAccountLoadBalancer:
    storage_path: str
    account_registry: object
    worker_pool: object
    rate_limiter: object | None = None
    default_account_capacity: int = 1
    account_capacities: dict[str, int] | None = None
    health_threshold: float = 0.5
    orchestrator_notification_callback: Callable[[AutomationTask, str], None] | None = None
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def route_task(self, task: AutomationTask) -> LoadBalancerResult:
        snapshot = self._load_snapshot()
        metrics = self.get_load_metrics().metrics
        pool_snapshot = self._pool_snapshot()

        if not self._idle_workers(pool_snapshot):
            return self._queue_task(
                snapshot,
                task,
                metrics,
                "No idle workers are currently available.",
            )

        candidates = [metric for metric in metrics if self._meets_requirements(metric, task) and self._is_available(metric)]
        if not candidates:
            return self._queue_task(
                snapshot,
                task,
                metrics,
                "All matching healthy accounts are at capacity or rate-limited.",
            )

        selected = self._select_account(candidates)
        routed_task = self._copy_task(task)
        routed_task.required_account = selected.account_name
        if routed_task.required_application is None:
            routed_task.required_application = selected.application_name

        enqueue_result = self.worker_pool.enqueue(routed_task)
        dispatch_result = self.worker_pool.dispatch()
        selected_assignment = next(
            (assignment for assignment in dispatch_result.assignments if assignment.task_id == routed_task.task_id),
            None,
        )
        if selected_assignment is None:
            return self._queue_task(
                snapshot,
                task,
                metrics,
                "Task could not be assigned to a worker after account selection.",
            )

        decision = LoadBalancerDecisionRecord(
            task_id=routed_task.task_id,
            selected_account=selected.account_name,
            selected_worker_id=selected_assignment.worker_id,
            queued=False,
            reason="Routed to least-loaded healthy account.",
            timestamp=self.now_fn(),
        )
        snapshot.decision_history.append(decision)
        self._save_snapshot(snapshot)
        if hasattr(self.account_registry, "log_account_usage"):
            self.account_registry.log_account_usage(selected.account_name, "load_balancer_route", routed_task.task_id)
        if hasattr(self.account_registry, "update_last_used"):
            self.account_registry.update_last_used(selected.account_name, timestamp=self.now_fn())
        return LoadBalancerResult(
            succeeded=True,
            task=enqueue_result.task or routed_task,
            metrics=self.get_load_metrics().metrics,
            decision=decision,
            decisions=list(snapshot.decision_history),
        )

    def rebalance_queued_tasks(self) -> LoadBalancerResult:
        snapshot = self._load_snapshot()
        routed: list[LoadBalancerDecisionRecord] = []
        remaining: list[AutomationTask] = []

        for task in snapshot.queued_tasks:
            metrics = self.get_load_metrics().metrics
            pool_snapshot = self._pool_snapshot()
            if not self._idle_workers(pool_snapshot):
                remaining.append(task)
                continue

            candidates = [metric for metric in metrics if self._meets_requirements(metric, task) and self._is_available(metric)]
            if not candidates:
                remaining.append(task)
                continue

            selected = self._select_account(candidates)
            routed_task = self._copy_task(task)
            routed_task.required_account = selected.account_name
            self.worker_pool.enqueue(routed_task)
            dispatch_result = self.worker_pool.dispatch()
            selected_assignment = next(
                (assignment for assignment in dispatch_result.assignments if assignment.task_id == routed_task.task_id),
                None,
            )
            if selected_assignment is None:
                remaining.append(task)
                continue

            decision = LoadBalancerDecisionRecord(
                task_id=routed_task.task_id,
                selected_account=selected.account_name,
                selected_worker_id=selected_assignment.worker_id,
                queued=False,
                reason="Queued task resumed and routed.",
                timestamp=self.now_fn(),
            )
            snapshot.decision_history.append(decision)
            routed.append(decision)

        snapshot.queued_tasks = remaining
        self._save_snapshot(snapshot)
        return LoadBalancerResult(
            succeeded=True,
            tasks=list(snapshot.queued_tasks),
            metrics=self.get_load_metrics().metrics,
            decisions=routed,
        )

    def inspect_queue(self) -> LoadBalancerResult:
        snapshot = self._load_snapshot()
        return LoadBalancerResult(
            succeeded=True,
            tasks=[self._copy_task(task) for task in snapshot.queued_tasks],
            decisions=list(snapshot.decision_history),
        )

    def get_load_metrics(self) -> LoadBalancerResult:
        accounts = self.account_registry.list_accounts() if hasattr(self.account_registry, "list_accounts") else []
        pool_snapshot = self._pool_snapshot()
        active_counts = self._active_account_counts(pool_snapshot)
        worker_ids_by_account = self._worker_ids_by_account(pool_snapshot)
        metrics: list[AccountLoadMetric] = []

        for account in accounts:
            capacity = self._capacity_for_account(account.name)
            current_load = active_counts.get(account.name, 0)
            usage = self._rate_limit_usage(account.name)
            metrics.append(
                AccountLoadMetric(
                    account_name=account.name,
                    account_type=account.account_type,
                    application_name=account.application,
                    healthy=bool(account.active and account.health_score >= self.health_threshold),
                    active=bool(account.active),
                    current_load=current_load,
                    capacity=capacity,
                    available_capacity=max(capacity - current_load, 0),
                    load_ratio=(current_load / capacity) if capacity > 0 else 1.0,
                    rate_limit_used=usage["used"],
                    rate_limit_limit=usage["limit"],
                    rate_limit_utilization=usage["utilization"],
                    assigned_worker_ids=worker_ids_by_account.get(account.name, []),
                )
            )

        metrics.sort(key=lambda item: (item.load_ratio, item.current_load, item.rate_limit_utilization, item.account_name))
        return LoadBalancerResult(succeeded=True, metrics=metrics)

    def _queue_task(
        self,
        snapshot: LoadBalancerSnapshot,
        task: AutomationTask,
        metrics: list[AccountLoadMetric],
        reason: str,
    ) -> LoadBalancerResult:
        queued_task = self._copy_task(task)
        snapshot.queued_tasks.append(queued_task)
        decision = LoadBalancerDecisionRecord(
            task_id=queued_task.task_id,
            queued=True,
            reason=reason,
            timestamp=self.now_fn(),
        )
        snapshot.decision_history.append(decision)
        self._save_snapshot(snapshot)
        if self.orchestrator_notification_callback is not None:
            self.orchestrator_notification_callback(queued_task, reason)
        return LoadBalancerResult(
            succeeded=True,
            task=queued_task,
            tasks=[self._copy_task(item) for item in snapshot.queued_tasks],
            metrics=metrics,
            decision=decision,
            decisions=list(snapshot.decision_history),
            reason=reason,
        )

    def _select_account(self, candidates: list[AccountLoadMetric]) -> AccountLoadMetric:
        return min(
            candidates,
            key=lambda item: (
                item.load_ratio,
                item.current_load,
                item.rate_limit_utilization,
                item.account_name,
            ),
        )

    def _meets_requirements(self, metric: AccountLoadMetric, task: AutomationTask) -> bool:
        if task.required_account and metric.account_name.casefold() != task.required_account.casefold():
            return False
        if task.required_account_type and metric.account_type.casefold() != task.required_account_type.casefold():
            return False
        if task.required_application and metric.application_name.casefold() != task.required_application.casefold():
            return False
        return True

    def _is_available(self, metric: AccountLoadMetric) -> bool:
        if not metric.healthy:
            return False
        if metric.available_capacity <= 0:
            return False
        if metric.rate_limit_limit > 0 and metric.rate_limit_used >= metric.rate_limit_limit:
            return False
        return True

    def _pool_snapshot(self):
        inspect = self.worker_pool.inspect()
        return getattr(inspect, "snapshot", None)

    def _idle_workers(self, pool_snapshot) -> list[object]:
        if pool_snapshot is None:
            return []
        idle_workers: list[object] = []
        for worker in pool_snapshot.workers:
            status = getattr(worker, "status", None)
            status_value = getattr(status, "value", status)
            if status_value == "idle":
                idle_workers.append(worker)
        return idle_workers

    def _active_account_counts(self, pool_snapshot) -> dict[str, int]:
        counts: dict[str, int] = {}
        if pool_snapshot is None:
            return counts
        for assignment in pool_snapshot.active_assignments:
            counts[assignment.account_name] = counts.get(assignment.account_name, 0) + 1
        return counts

    def _worker_ids_by_account(self, pool_snapshot) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        if pool_snapshot is None:
            return mapping
        for assignment in pool_snapshot.active_assignments:
            mapping.setdefault(assignment.account_name, []).append(assignment.worker_id)
        return mapping

    def _capacity_for_account(self, account_name: str) -> int:
        capacities = self.account_capacities or {}
        return max(1, int(capacities.get(account_name, self.default_account_capacity)))

    def _rate_limit_usage(self, account_name: str) -> dict[str, float | int]:
        if self.rate_limiter is None:
            return {"used": 0, "limit": 0, "utilization": 0.0}
        result = self.rate_limiter.get_usage_metrics(
            RateLimitRequest(request_id=f"usage-{account_name}", account_name=account_name)
        )
        metrics = [metric for metric in getattr(result, "metrics", []) if getattr(metric, "key", "").casefold() == account_name.casefold()]
        if not metrics:
            return {"used": 0, "limit": 0, "utilization": 0.0}
        most_constrained = max(
            metrics,
            key=lambda metric: ((metric.used_count / metric.limit) if metric.limit else 0.0, metric.used_count),
        )
        utilization = (most_constrained.used_count / most_constrained.limit) if most_constrained.limit else 0.0
        return {
            "used": most_constrained.used_count,
            "limit": most_constrained.limit,
            "utilization": utilization,
        }

    def _load_snapshot(self) -> LoadBalancerSnapshot:
        try:
            path = Path(self.storage_path)
            if not path.exists():
                return LoadBalancerSnapshot()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load load balancer state from {self.storage_path}: {e}")
            return LoadBalancerSnapshot()

        return LoadBalancerSnapshot(
            queued_tasks=[self._deserialize_task(item) for item in payload.get("queued_tasks", [])],
            decision_history=[self._deserialize_decision(item) for item in payload.get("decision_history", [])],
        )

    def _save_snapshot(self, snapshot: LoadBalancerSnapshot) -> None:
        try:
            path = Path(self.storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "queued_tasks": [self._serialize_task(task) for task in snapshot.queued_tasks],
                "decision_history": [self._serialize_decision(item) for item in snapshot.decision_history],
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save load balancer state to {self.storage_path}: {e}")

    def _copy_task(self, task: AutomationTask) -> AutomationTask:
        return self._deserialize_task(self._serialize_task(task))

    def _serialize_task(self, task: AutomationTask) -> dict:
        return {
            "task_id": task.task_id,
            "priority": task.priority.value,
            "required_module": task.required_module,
            "required_account": task.required_account,
            "required_account_type": task.required_account_type,
            "required_application": task.required_application,
            "input_payload": dict(task.input_payload),
            "deadline": None if task.deadline is None else task.deadline.isoformat(),
            "max_retry_count": task.max_retry_count,
            "retry_count": task.retry_count,
            "enqueued_at": task.enqueued_at.isoformat(),
        }

    def _deserialize_task(self, payload: dict) -> AutomationTask:
        from desktop_automation_agent.models import TaskPriority

        return AutomationTask(
            task_id=payload["task_id"],
            priority=TaskPriority(payload["priority"]),
            required_module=payload["required_module"],
            required_account=payload.get("required_account"),
            required_account_type=payload.get("required_account_type"),
            required_application=payload.get("required_application"),
            input_payload=dict(payload.get("input_payload", {})),
            deadline=None if payload.get("deadline") is None else datetime.fromisoformat(payload["deadline"]),
            max_retry_count=int(payload.get("max_retry_count", 0)),
            retry_count=int(payload.get("retry_count", 0)),
            enqueued_at=datetime.fromisoformat(payload["enqueued_at"]),
        )

    def _serialize_decision(self, decision: LoadBalancerDecisionRecord) -> dict:
        return {
            "task_id": decision.task_id,
            "selected_account": decision.selected_account,
            "selected_worker_id": decision.selected_worker_id,
            "queued": decision.queued,
            "reason": decision.reason,
            "timestamp": decision.timestamp.isoformat(),
        }

    def _deserialize_decision(self, payload: dict) -> LoadBalancerDecisionRecord:
        return LoadBalancerDecisionRecord(
            task_id=payload["task_id"],
            selected_account=payload.get("selected_account"),
            selected_worker_id=payload.get("selected_worker_id"),
            queued=bool(payload.get("queued", False)),
            reason=payload.get("reason"),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )
