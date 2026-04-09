from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from typing import Any, Callable, Iterator

from desktop_automation_perception.models import (
    ActiveWorkflowStatus,
    DashboardAccountStatus,
    DashboardDataProviderResult,
    DashboardSnapshot,
    DashboardWorkerStatus,
    QueueDepthStatus,
    StepExecutionRateStatus,
    WorkflowAuditQuery,
)


@dataclass(slots=True)
class RealTimeDashboardDataProvider:
    update_interval_seconds: float = 5.0
    task_queue_manager: object | None = None
    dead_letter_queue_handler: object | None = None
    load_balancer: object | None = None
    worker_pool: object | None = None
    workflow_audit_logger: object | None = None
    active_workflow_provider: Callable[[], list[dict[str, Any]] | list[ActiveWorkflowStatus]] | None = None
    now_fn: Callable[[], datetime] = utc_now
    sleep_fn: Callable[[float], None] = sleep

    def get_dashboard_snapshot(self) -> DashboardDataProviderResult:
        snapshot = DashboardSnapshot(
            generated_at=self.now_fn(),
            active_workflows=self._active_workflows(),
            queue_depths=self._queue_depths(),
            account_statuses=self._account_statuses(),
            step_rates_last_hour=self._step_rates_last_hour(),
            worker_statuses=self._worker_statuses(),
        )
        return DashboardDataProviderResult(succeeded=True, snapshot=snapshot)

    def get_active_workflows(self) -> DashboardDataProviderResult:
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=DashboardSnapshot(generated_at=self.now_fn(), active_workflows=self._active_workflows()),
        )

    def get_queue_depths(self) -> DashboardDataProviderResult:
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=DashboardSnapshot(generated_at=self.now_fn(), queue_depths=self._queue_depths()),
        )

    def get_account_statuses(self) -> DashboardDataProviderResult:
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=DashboardSnapshot(generated_at=self.now_fn(), account_statuses=self._account_statuses()),
        )

    def get_step_rates_last_hour(self) -> DashboardDataProviderResult:
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=DashboardSnapshot(generated_at=self.now_fn(), step_rates_last_hour=self._step_rates_last_hour()),
        )

    def get_worker_statuses(self) -> DashboardDataProviderResult:
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=DashboardSnapshot(generated_at=self.now_fn(), worker_statuses=self._worker_statuses()),
        )

    def build_sse_event(self) -> DashboardDataProviderResult:
        result = self.get_dashboard_snapshot()
        if not result.succeeded or result.snapshot is None:
            return DashboardDataProviderResult(succeeded=False, reason=result.reason)
        payload = self._snapshot_payload(result.snapshot)
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=result.snapshot,
            sse_event=f"event: dashboard\n" f"data: {json.dumps(payload, sort_keys=True)}\n\n",
        )

    def build_websocket_payload(self) -> DashboardDataProviderResult:
        result = self.get_dashboard_snapshot()
        if not result.succeeded or result.snapshot is None:
            return DashboardDataProviderResult(succeeded=False, reason=result.reason)
        payload = {
            "type": "dashboard_snapshot",
            "data": self._snapshot_payload(result.snapshot),
        }
        return DashboardDataProviderResult(
            succeeded=True,
            snapshot=result.snapshot,
            websocket_payload=payload,
        )

    def stream_sse(self, *, iterations: int | None = None) -> Iterator[str]:
        emitted = 0
        while iterations is None or emitted < iterations:
            result = self.build_sse_event()
            if result.succeeded and result.sse_event is not None:
                yield result.sse_event
            emitted += 1
            if iterations is None or emitted < iterations:
                self.sleep_fn(self.update_interval_seconds)

    def stream_websocket_payloads(self, *, iterations: int | None = None) -> Iterator[dict[str, Any]]:
        emitted = 0
        while iterations is None or emitted < iterations:
            result = self.build_websocket_payload()
            if result.succeeded and result.websocket_payload is not None:
                yield result.websocket_payload
            emitted += 1
            if iterations is None or emitted < iterations:
                self.sleep_fn(self.update_interval_seconds)

    def _active_workflows(self) -> list[ActiveWorkflowStatus]:
        if self.active_workflow_provider is not None:
            provided = self.active_workflow_provider()
            return [item if isinstance(item, ActiveWorkflowStatus) else self._coerce_active_workflow(item) for item in provided]

        worker_snapshot = self._worker_snapshot()
        grouped: dict[str, dict[str, Any]] = {}
        active_tasks = getattr(worker_snapshot, "active_tasks", []) if worker_snapshot is not None else []
        assignments = getattr(worker_snapshot, "active_assignments", []) if worker_snapshot is not None else []

        task_by_id = {task.task_id: task for task in active_tasks}
        for task in active_tasks:
            workflow_id = str(task.input_payload.get("workflow_id") or task.required_application or "unknown_workflow")
            total_steps = int(task.input_payload.get("total_steps", 0) or 0)
            completed_steps = int(task.input_payload.get("completed_steps", 0) or 0)
            current_step_name = task.input_payload.get("current_step_name")
            workflow_version_number = task.input_payload.get("workflow_version_number")
            grouped.setdefault(
                workflow_id,
                {
                    "workflow_id": workflow_id,
                    "workflow_version_number": workflow_version_number,
                    "current_step_name": current_step_name,
                    "total_steps": total_steps,
                    "completed_steps": completed_steps,
                    "active_task_ids": [],
                    "assigned_worker_ids": [],
                },
            )
            grouped[workflow_id]["active_task_ids"].append(task.task_id)
            grouped[workflow_id]["total_steps"] = max(grouped[workflow_id]["total_steps"], total_steps)
            grouped[workflow_id]["completed_steps"] = max(grouped[workflow_id]["completed_steps"], completed_steps)
            if current_step_name:
                grouped[workflow_id]["current_step_name"] = current_step_name
            if workflow_version_number is not None:
                grouped[workflow_id]["workflow_version_number"] = workflow_version_number

        for assignment in assignments:
            task = task_by_id.get(assignment.task_id)
            workflow_id = (
                str(task.input_payload.get("workflow_id"))
                if task is not None and task.input_payload.get("workflow_id") is not None
                else str(task.required_application if task is not None else "unknown_workflow")
            )
            if workflow_id in grouped:
                grouped[workflow_id]["assigned_worker_ids"].append(assignment.worker_id)

        statuses: list[ActiveWorkflowStatus] = []
        for payload in grouped.values():
            total_steps = payload["total_steps"]
            completed_steps = payload["completed_steps"]
            percent_complete = 0.0 if total_steps <= 0 else min(completed_steps / total_steps, 1.0)
            statuses.append(
                ActiveWorkflowStatus(
                    workflow_id=payload["workflow_id"],
                    workflow_version_number=payload.get("workflow_version_number"),
                    current_step_name=payload.get("current_step_name"),
                    total_steps=total_steps,
                    completed_steps=completed_steps,
                    percent_complete=percent_complete,
                    active_task_ids=list(payload["active_task_ids"]),
                    assigned_worker_ids=sorted(set(payload["assigned_worker_ids"])),
                )
            )
        statuses.sort(key=lambda item: item.workflow_id)
        return statuses

    def _queue_depths(self) -> QueueDepthStatus:
        task_snapshot = self.task_queue_manager.get_snapshot() if self.task_queue_manager is not None else None
        dlq_result = self.dead_letter_queue_handler.inspect() if self.dead_letter_queue_handler is not None else None
        return QueueDepthStatus(
            task_queue_depth=len(getattr(task_snapshot, "tasks", []) or []),
            dead_letter_queue_depth=len(getattr(dlq_result, "items", []) or []),
        )

    def _account_statuses(self) -> list[DashboardAccountStatus]:
        if self.load_balancer is None:
            return []
        result = self.load_balancer.get_load_metrics()
        metrics = getattr(result, "metrics", []) or []
        statuses = [
            DashboardAccountStatus(
                account_name=metric.account_name,
                account_type=metric.account_type,
                application_name=metric.application_name,
                healthy=metric.healthy,
                active=metric.active,
                current_load=metric.current_load,
                capacity=metric.capacity,
                available_capacity=metric.available_capacity,
                load_ratio=metric.load_ratio,
                assigned_worker_ids=list(metric.assigned_worker_ids),
            )
            for metric in metrics
        ]
        statuses.sort(key=lambda item: item.account_name)
        return statuses

    def _step_rates_last_hour(self) -> list[StepExecutionRateStatus]:
        if self.workflow_audit_logger is None:
            return []
        cutoff = self.now_fn() - timedelta(hours=1)
        query = WorkflowAuditQuery(
            workflow_id=None,
            workflow_version_number=None,
            started_at=cutoff,
            ended_at=None,
            action_type=None,
            outcome=None,
        )
        result = self.workflow_audit_logger.query_logs(query)
        entries = getattr(result, "entries", []) or []
        counters: dict[str, dict[str, int]] = defaultdict(lambda: {"success": 0, "failure": 0})
        for entry in entries:
            bucket = counters[entry.step_name]
            if getattr(entry, "success", False):
                bucket["success"] += 1
            else:
                bucket["failure"] += 1
        rates = []
        for step_name, values in counters.items():
            total = values["success"] + values["failure"]
            rates.append(
                StepExecutionRateStatus(
                    step_name=step_name,
                    success_count=values["success"],
                    failure_count=values["failure"],
                    success_rate=(values["success"] / total) if total else 0.0,
                    failure_rate=(values["failure"] / total) if total else 0.0,
                )
            )
        rates.sort(key=lambda item: item.step_name)
        return rates

    def _worker_statuses(self) -> list[DashboardWorkerStatus]:
        worker_snapshot = self._worker_snapshot()
        if worker_snapshot is None:
            return []
        statuses = [
            DashboardWorkerStatus(
                worker_id=worker.worker_id,
                status=getattr(getattr(worker, "status", None), "value", getattr(worker, "status", "unknown")),
                current_task_id=worker.current_task_id,
                current_account=worker.current_account,
                last_heartbeat_at=worker.last_heartbeat_at,
                restart_count=worker.restart_count,
            )
            for worker in getattr(worker_snapshot, "workers", []) or []
        ]
        statuses.sort(key=lambda item: item.worker_id)
        return statuses

    def _worker_snapshot(self):
        if self.worker_pool is None:
            return None
        result = self.worker_pool.inspect()
        return getattr(result, "snapshot", None)

    def _coerce_active_workflow(self, payload: dict[str, Any]) -> ActiveWorkflowStatus:
        total_steps = int(payload.get("total_steps", 0) or 0)
        completed_steps = int(payload.get("completed_steps", 0) or 0)
        percent_complete = float(payload.get("percent_complete", 0.0))
        if percent_complete == 0.0 and total_steps > 0:
            percent_complete = min(completed_steps / total_steps, 1.0)
        return ActiveWorkflowStatus(
            workflow_id=str(payload["workflow_id"]),
            workflow_version_number=payload.get("workflow_version_number"),
            current_step_name=payload.get("current_step_name"),
            total_steps=total_steps,
            completed_steps=completed_steps,
            percent_complete=percent_complete,
            active_task_ids=list(payload.get("active_task_ids", [])),
            assigned_worker_ids=list(payload.get("assigned_worker_ids", [])),
            status=str(payload.get("status", "active")),
        )

    def _snapshot_payload(self, snapshot: DashboardSnapshot) -> dict[str, Any]:
        return {
            "generated_at": snapshot.generated_at.isoformat(),
            "active_workflows": [
                {
                    "workflow_id": item.workflow_id,
                    "workflow_version_number": item.workflow_version_number,
                    "current_step_name": item.current_step_name,
                    "total_steps": item.total_steps,
                    "completed_steps": item.completed_steps,
                    "percent_complete": item.percent_complete,
                    "active_task_ids": list(item.active_task_ids),
                    "assigned_worker_ids": list(item.assigned_worker_ids),
                    "status": item.status,
                }
                for item in snapshot.active_workflows
            ],
            "queue_depths": {
                "task_queue_depth": snapshot.queue_depths.task_queue_depth,
                "dead_letter_queue_depth": snapshot.queue_depths.dead_letter_queue_depth,
            },
            "account_statuses": [
                {
                    "account_name": item.account_name,
                    "account_type": item.account_type,
                    "application_name": item.application_name,
                    "healthy": item.healthy,
                    "active": item.active,
                    "current_load": item.current_load,
                    "capacity": item.capacity,
                    "available_capacity": item.available_capacity,
                    "load_ratio": item.load_ratio,
                    "assigned_worker_ids": list(item.assigned_worker_ids),
                }
                for item in snapshot.account_statuses
            ],
            "step_rates_last_hour": [
                {
                    "step_name": item.step_name,
                    "success_count": item.success_count,
                    "failure_count": item.failure_count,
                    "success_rate": item.success_rate,
                    "failure_rate": item.failure_rate,
                }
                for item in snapshot.step_rates_last_hour
            ],
            "worker_statuses": [
                {
                    "worker_id": item.worker_id,
                    "status": item.status,
                    "current_task_id": item.current_task_id,
                    "current_account": item.current_account,
                    "last_heartbeat_at": None if item.last_heartbeat_at is None else item.last_heartbeat_at.isoformat(),
                    "restart_count": item.restart_count,
                }
                for item in snapshot.worker_statuses
            ],
        }


