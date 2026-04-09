from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from desktop_automation_agent.models import (
    AutomationTask,
    TaskPriority,
    WorkerAssignment,
    WorkerExecutionMode,
    WorkerPoolOperationResult,
    WorkerPoolSnapshot,
    WorkerRecord,
    WorkerSessionContext,
    WorkerStatus,
)


class InMemoryWorkerRuntime:
    def __init__(self, worker_id: str, session_context: WorkerSessionContext):
        self.worker_id = worker_id
        self.session_context = session_context
        self.alive = True
        self.started_tasks: list[str] = []

    def start(self) -> None:
        self.alive = True

    def stop(self) -> None:
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    def assign_task(self, task: AutomationTask) -> None:
        self.started_tasks.append(task.task_id)


@dataclass(slots=True)
class ParallelWorkerPool:
    storage_path: str
    worker_count: int
    execution_mode: WorkerExecutionMode = WorkerExecutionMode.THREAD
    heartbeat_timeout_seconds: float = 30.0
    max_concurrency_per_account: int = 1
    account_concurrency_limits: dict[str, int] = field(default_factory=dict)
    assignment_fn: Callable[
        [list[AutomationTask], list[WorkerRecord], dict[str, int]],
        tuple[str, str] | None,
    ] | None = None
    runtime_factory: Callable[[str, WorkerSessionContext], object] | None = None
    isolation_manager: object | None = None
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _runtimes: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    def enqueue(self, task: AutomationTask) -> WorkerPoolOperationResult:
        snapshot = self._load_snapshot()
        self._ensure_workers(snapshot)
        snapshot.pending_tasks.append(self._copy_task(task))
        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(
            succeeded=True,
            task=task,
            tasks=list(snapshot.pending_tasks),
            workers=list(snapshot.workers),
            snapshot=snapshot,
        )

    def dispatch(self) -> WorkerPoolOperationResult:
        snapshot = self._load_snapshot()
        self._ensure_workers(snapshot)
        assignments: list[WorkerAssignment] = []

        while True:
            idle_workers = [worker for worker in snapshot.workers if worker.status is WorkerStatus.IDLE]
            if not idle_workers or not snapshot.pending_tasks:
                break

            active_counts = self._active_account_counts(snapshot)
            eligible_tasks = [task for task in snapshot.pending_tasks if self._can_run_account(task.required_account, active_counts)]
            if not eligible_tasks:
                break

            task, worker = self._select_assignment(eligible_tasks, idle_workers, active_counts)
            if task is None or worker is None:
                break

            assignment = WorkerAssignment(
                worker_id=worker.worker_id,
                task_id=task.task_id,
                account_name=task.required_account,
                module_name=task.required_module,
                assigned_at=self.now_fn(),
            )
            assignments.append(assignment)
            snapshot.active_assignments.append(assignment)
            snapshot.pending_tasks = [item for item in snapshot.pending_tasks if item.task_id != task.task_id]
            snapshot.active_tasks.append(self._copy_task(task))

            worker.status = WorkerStatus.BUSY
            worker.current_task_id = task.task_id
            worker.current_account = task.required_account
            worker.last_heartbeat_at = self.now_fn()

            runtime = self._runtimes[worker.worker_id]
            assign_task = getattr(runtime, "assign_task", None)
            if callable(assign_task):
                assign_task(task)

        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(
            succeeded=True,
            assignments=assignments,
            workers=list(snapshot.workers),
            tasks=list(snapshot.pending_tasks),
            snapshot=snapshot,
        )

    def heartbeat(self, worker_id: str) -> WorkerPoolOperationResult:
        snapshot = self._load_snapshot()
        worker = self._find_worker(snapshot, worker_id)
        if worker is None:
            return WorkerPoolOperationResult(succeeded=False, reason="Worker not found.")
        worker.last_heartbeat_at = self.now_fn()
        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(succeeded=True, worker=worker, snapshot=snapshot)

    def complete_task(self, worker_id: str, *, succeeded: bool = True) -> WorkerPoolOperationResult:
        snapshot = self._load_snapshot()
        worker = self._find_worker(snapshot, worker_id)
        if worker is None:
            return WorkerPoolOperationResult(succeeded=False, reason="Worker not found.")
        if worker.current_task_id is None:
            return WorkerPoolOperationResult(succeeded=False, worker=worker, reason="Worker has no assigned task.")

        task_id = worker.current_task_id
        snapshot.active_assignments = [item for item in snapshot.active_assignments if item.worker_id != worker_id]
        snapshot.active_tasks = [item for item in snapshot.active_tasks if item.task_id != task_id]
        if succeeded:
            snapshot.completed_task_ids.append(task_id)
        else:
            snapshot.failed_task_ids.append(task_id)

        worker.status = WorkerStatus.IDLE
        worker.current_task_id = None
        worker.current_account = None
        worker.last_heartbeat_at = self.now_fn()

        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(succeeded=True, worker=worker, snapshot=snapshot)

    def monitor_health(self) -> WorkerPoolOperationResult:
        snapshot = self._load_snapshot()
        self._ensure_workers(snapshot)
        restarted: list[WorkerRecord] = []

        for worker in snapshot.workers:
            runtime = self._runtimes.get(worker.worker_id)
            heartbeat_stale = self._heartbeat_stale(worker)
            runtime_crashed = runtime is not None and callable(getattr(runtime, "is_alive", None)) and not runtime.is_alive()
            if not heartbeat_stale and not runtime_crashed:
                continue

            worker.status = WorkerStatus.CRASHED
            if worker.current_task_id is not None:
                task = self._remove_active_task(snapshot, worker.worker_id, worker.current_task_id)
                if task is not None:
                    task.retry_count += 1
                    if task.retry_count <= task.max_retry_count:
                        snapshot.pending_tasks.append(task)
                    else:
                        snapshot.failed_task_ids.append(task.task_id)

            worker.status = WorkerStatus.RESTARTING
            self._restart_worker(worker)
            worker.status = WorkerStatus.IDLE
            worker.current_task_id = None
            worker.current_account = None
            worker.last_heartbeat_at = self.now_fn()
            restarted.append(self._copy_worker(worker))

        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(
            succeeded=True,
            workers=restarted,
            tasks=list(snapshot.pending_tasks),
            assignments=list(snapshot.active_assignments),
            snapshot=snapshot,
        )

    def inspect(self) -> WorkerPoolOperationResult:
        snapshot = self._load_snapshot()
        self._ensure_workers(snapshot)
        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(
            succeeded=True,
            workers=list(snapshot.workers),
            tasks=list(snapshot.pending_tasks),
            assignments=list(snapshot.active_assignments),
            snapshot=snapshot,
        )

    def report_network_access(self, worker_id: str, hostname: str) -> WorkerPoolOperationResult:
        return self._handle_sandbox_access(worker_id=worker_id, access_kind="network", target=hostname)

    def report_file_write(self, worker_id: str, file_path: str) -> WorkerPoolOperationResult:
        return self._handle_sandbox_access(worker_id=worker_id, access_kind="file_write", target=file_path)

    def report_resource_access(self, worker_id: str, resource_name: str) -> WorkerPoolOperationResult:
        return self._handle_sandbox_access(worker_id=worker_id, access_kind="resource", target=resource_name)

    def _ensure_workers(self, snapshot: WorkerPoolSnapshot) -> None:
        existing_ids = {worker.worker_id for worker in snapshot.workers}
        for index in range(1, self.worker_count + 1):
            worker_id = f"worker-{index}"
            if worker_id not in existing_ids:
                worker = WorkerRecord(
                    worker_id=worker_id,
                    status=WorkerStatus.IDLE,
                    session_context=self._new_session_context(worker_id),
                    last_heartbeat_at=self.now_fn(),
                )
                snapshot.workers.append(worker)
            self._ensure_runtime(next(worker for worker in snapshot.workers if worker.worker_id == worker_id))

    def _ensure_runtime(self, worker: WorkerRecord) -> None:
        if worker.worker_id in self._runtimes:
            return
        runtime = self._create_runtime(worker.worker_id, worker.session_context)
        start = getattr(runtime, "start", None)
        if callable(start):
            start()
        self._runtimes[worker.worker_id] = runtime

    def _restart_worker(self, worker: WorkerRecord) -> None:
        runtime = self._runtimes.get(worker.worker_id)
        if runtime is not None:
            stop = getattr(runtime, "stop", None)
            if callable(stop):
                stop()
        worker.restart_count += 1
        worker.session_context = self._new_session_context(worker.worker_id)
        runtime = self._create_runtime(worker.worker_id, worker.session_context)
        start = getattr(runtime, "start", None)
        if callable(start):
            start()
        self._runtimes[worker.worker_id] = runtime

    def _create_runtime(self, worker_id: str, session_context: WorkerSessionContext) -> object:
        if self.isolation_manager is not None and hasattr(self.isolation_manager, "launch_worker"):
            result = self.isolation_manager.launch_worker(
                worker_id=worker_id,
                session_context=session_context,
                execution_mode=self.execution_mode,
            )
            runtime = getattr(result, "runtime", None)
            if runtime is not None:
                return runtime
        factory = self.runtime_factory or (lambda wid, ctx: InMemoryWorkerRuntime(wid, ctx))
        return factory(worker_id, session_context)

    def _handle_sandbox_access(
        self,
        *,
        worker_id: str,
        access_kind: str,
        target: str,
    ) -> WorkerPoolOperationResult:
        if self.isolation_manager is None:
            return WorkerPoolOperationResult(succeeded=False, reason="No sandbox isolation manager is configured.")
        snapshot = self._load_snapshot()
        worker = self._find_worker(snapshot, worker_id)
        if worker is None:
            return WorkerPoolOperationResult(succeeded=False, reason="Worker not found.")
        if access_kind == "network":
            result = self.isolation_manager.check_network_access(worker_id, target)
        elif access_kind == "file_write":
            result = self.isolation_manager.check_file_write(worker_id, target)
        else:
            result = self.isolation_manager.check_resource_access(worker_id, target)
        if getattr(result, "succeeded", False):
            return WorkerPoolOperationResult(succeeded=True, worker=worker, snapshot=snapshot)

        if worker.current_task_id is not None:
            task = self._remove_active_task(snapshot, worker.worker_id, worker.current_task_id)
            if task is not None:
                snapshot.failed_task_ids.append(task.task_id)
        worker.status = WorkerStatus.STOPPED
        worker.current_task_id = None
        worker.current_account = None
        worker.last_heartbeat_at = self.now_fn()
        self._save_snapshot(snapshot)
        return WorkerPoolOperationResult(
            succeeded=False,
            worker=worker,
            snapshot=snapshot,
            reason=getattr(result, "reason", "Sandbox restriction violation detected."),
        )

    def _new_session_context(self, worker_id: str) -> WorkerSessionContext:
        return WorkerSessionContext(
            worker_id=worker_id,
            session_id=f"{worker_id}-session-{uuid4().hex}",
            execution_mode=self.execution_mode,
            created_at=self.now_fn(),
        )

    def _active_account_counts(self, snapshot: WorkerPoolSnapshot) -> dict[str, int]:
        counts: dict[str, int] = {}
        for assignment in snapshot.active_assignments:
            counts[assignment.account_name] = counts.get(assignment.account_name, 0) + 1
        return counts

    def _can_run_account(self, account_name: str, active_counts: dict[str, int]) -> bool:
        limit = self.account_concurrency_limits.get(account_name, self.max_concurrency_per_account)
        return active_counts.get(account_name, 0) < limit

    def _select_assignment(
        self,
        tasks: list[AutomationTask],
        workers: list[WorkerRecord],
        active_counts: dict[str, int],
    ) -> tuple[AutomationTask | None, WorkerRecord | None]:
        if not tasks or not workers:
            return None, None

        if self.assignment_fn is not None:
            selected = self.assignment_fn(list(tasks), list(workers), dict(active_counts))
            if selected is not None:
                task_id, worker_id = selected
                task = next((item for item in tasks if item.task_id == task_id), None)
                worker = next((item for item in workers if item.worker_id == worker_id), None)
                return task, worker

        ordered_tasks = sorted(
            tasks,
            key=lambda task: (
                -self._effective_priority_score(task),
                self._deadline_value(task),
                task.enqueued_at,
                task.task_id,
            ),
        )
        return ordered_tasks[0], sorted(workers, key=lambda worker: worker.worker_id)[0]

    def _priority_score(self, priority: TaskPriority) -> int:
        return {
            TaskPriority.LOW: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.HIGH: 3,
            TaskPriority.CRITICAL: 4,
        }[priority]

    def _effective_priority_score(self, task: AutomationTask) -> int:
        return self._priority_score(task.priority) + self._deadline_escalation_score(task)

    def _deadline_escalation_score(self, task: AutomationTask) -> int:
        if task.deadline is None:
            return 0
        remaining = task.deadline - self.now_fn()
        if remaining <= timedelta(0):
            return 4
        if remaining <= timedelta(minutes=15):
            return 3
        if remaining <= timedelta(hours=1):
            return 2
        if remaining <= timedelta(hours=6):
            return 1
        return 0

    def _deadline_value(self, task: AutomationTask) -> datetime:
        return task.deadline or datetime.max.replace(tzinfo=timezone.utc)

    def _heartbeat_stale(self, worker: WorkerRecord) -> bool:
        if worker.last_heartbeat_at is None:
            return True
        delta = self.now_fn() - worker.last_heartbeat_at
        return delta.total_seconds() > self.heartbeat_timeout_seconds

    def _remove_active_task(
        self,
        snapshot: WorkerPoolSnapshot,
        worker_id: str,
        task_id: str,
    ) -> AutomationTask | None:
        snapshot.active_assignments = [item for item in snapshot.active_assignments if item.worker_id != worker_id]
        for index, task in enumerate(snapshot.active_tasks):
            if task.task_id == task_id:
                return snapshot.active_tasks.pop(index)
        return None

    def _find_worker(self, snapshot: WorkerPoolSnapshot, worker_id: str) -> WorkerRecord | None:
        return next((worker for worker in snapshot.workers if worker.worker_id == worker_id), None)

    def _copy_task(self, task: AutomationTask) -> AutomationTask:
        return self._deserialize_task(self._serialize_task(task))

    def _copy_worker(self, worker: WorkerRecord) -> WorkerRecord:
        return self._deserialize_worker(self._serialize_worker(worker))

    def _load_snapshot(self) -> WorkerPoolSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return WorkerPoolSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return WorkerPoolSnapshot(
            workers=[self._deserialize_worker(item) for item in payload.get("workers", [])],
            pending_tasks=[self._deserialize_task(item) for item in payload.get("pending_tasks", [])],
            active_tasks=[self._deserialize_task(item) for item in payload.get("active_tasks", [])],
            active_assignments=[self._deserialize_assignment(item) for item in payload.get("active_assignments", [])],
            completed_task_ids=list(payload.get("completed_task_ids", [])),
            failed_task_ids=list(payload.get("failed_task_ids", [])),
        )

    def _save_snapshot(self, snapshot: WorkerPoolSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "workers": [self._serialize_worker(worker) for worker in snapshot.workers],
            "pending_tasks": [self._serialize_task(task) for task in snapshot.pending_tasks],
            "active_tasks": [self._serialize_task(task) for task in snapshot.active_tasks],
            "active_assignments": [self._serialize_assignment(item) for item in snapshot.active_assignments],
            "completed_task_ids": list(snapshot.completed_task_ids),
            "failed_task_ids": list(snapshot.failed_task_ids),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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
            deadline=None if payload.get("deadline") is None else datetime.fromisoformat(payload["deadline"]),
            max_retry_count=int(payload.get("max_retry_count", 0)),
            retry_count=int(payload.get("retry_count", 0)),
            enqueued_at=datetime.fromisoformat(payload["enqueued_at"]),
        )

    def _serialize_assignment(self, assignment: WorkerAssignment) -> dict:
        return {
            "worker_id": assignment.worker_id,
            "task_id": assignment.task_id,
            "account_name": assignment.account_name,
            "module_name": assignment.module_name,
            "assigned_at": assignment.assigned_at.isoformat(),
        }

    def _deserialize_assignment(self, payload: dict) -> WorkerAssignment:
        return WorkerAssignment(
            worker_id=payload["worker_id"],
            task_id=payload["task_id"],
            account_name=payload["account_name"],
            module_name=payload["module_name"],
            assigned_at=datetime.fromisoformat(payload["assigned_at"]),
        )

    def _serialize_worker(self, worker: WorkerRecord) -> dict:
        return {
            "worker_id": worker.worker_id,
            "status": worker.status.value,
            "session_context": {
                "worker_id": worker.session_context.worker_id,
                "session_id": worker.session_context.session_id,
                "execution_mode": worker.session_context.execution_mode.value,
                "created_at": worker.session_context.created_at.isoformat(),
                "metadata": dict(worker.session_context.metadata),
            },
            "last_heartbeat_at": None if worker.last_heartbeat_at is None else worker.last_heartbeat_at.isoformat(),
            "current_task_id": worker.current_task_id,
            "current_account": worker.current_account,
            "restart_count": worker.restart_count,
        }

    def _deserialize_worker(self, payload: dict) -> WorkerRecord:
        session = payload["session_context"]
        return WorkerRecord(
            worker_id=payload["worker_id"],
            status=WorkerStatus(payload["status"]),
            session_context=WorkerSessionContext(
                worker_id=session["worker_id"],
                session_id=session["session_id"],
                execution_mode=WorkerExecutionMode(session["execution_mode"]),
                created_at=datetime.fromisoformat(session["created_at"]),
                metadata=dict(session.get("metadata", {})),
            ),
            last_heartbeat_at=None
            if payload.get("last_heartbeat_at") is None
            else datetime.fromisoformat(payload["last_heartbeat_at"]),
            current_task_id=payload.get("current_task_id"),
            current_account=payload.get("current_account"),
            restart_count=int(payload.get("restart_count", 0)),
        )
