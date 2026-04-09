from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from desktop_automation_agent.agents import ParallelWorkerPool
from desktop_automation_agent.models import (
    AutomationTask,
    TaskPriority,
    WorkerExecutionMode,
)
from desktop_automation_agent.sandbox_isolation_manager import SandboxIsolationManager


class FakeRuntime:
    def __init__(self, worker_id, session_context):
        self.worker_id = worker_id
        self.session_context = session_context
        self.alive = True
        self.started = 0
        self.stopped = 0
        self.assigned_tasks = []

    def start(self):
        self.started += 1
        self.alive = True

    def stop(self):
        self.stopped += 1
        self.alive = False

    def is_alive(self):
        return self.alive

    def assign_task(self, task):
        self.assigned_tasks.append(task.task_id)


def make_task(task_id: str, *, account: str, priority: TaskPriority, deadline=None, retries=1):
    return AutomationTask(
        task_id=task_id,
        priority=priority,
        required_account=account,
        required_module="automation.module",
        input_payload={"task": task_id},
        deadline=deadline,
        max_retry_count=retries,
        enqueued_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )


def test_parallel_worker_pool_assigns_tasks_to_multiple_idle_workers(tmp_path):
    runtimes = {}

    def runtime_factory(worker_id, session_context):
        runtime = FakeRuntime(worker_id, session_context)
        runtimes[worker_id] = runtime
        return runtime

    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool.json"),
        worker_count=2,
        runtime_factory=runtime_factory,
    )
    pool.enqueue(make_task("task-1", account="acct-a", priority=TaskPriority.HIGH))
    pool.enqueue(make_task("task-2", account="acct-b", priority=TaskPriority.MEDIUM))

    result = pool.dispatch()

    assert result.succeeded is True
    assert [assignment.task_id for assignment in result.assignments] == ["task-1", "task-2"]
    assert [assignment.worker_id for assignment in result.assignments] == ["worker-1", "worker-2"]
    assert runtimes["worker-1"].assigned_tasks == ["task-1"]
    assert runtimes["worker-2"].assigned_tasks == ["task-2"]


def test_parallel_worker_pool_enforces_per_account_concurrency_limits(tmp_path):
    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool_limits.json"),
        worker_count=3,
        max_concurrency_per_account=1,
        runtime_factory=lambda worker_id, session_context: FakeRuntime(worker_id, session_context),
    )
    pool.enqueue(make_task("task-1", account="acct-a", priority=TaskPriority.HIGH))
    pool.enqueue(make_task("task-2", account="acct-a", priority=TaskPriority.MEDIUM))
    pool.enqueue(make_task("task-3", account="acct-b", priority=TaskPriority.MEDIUM))

    result = pool.dispatch()
    snapshot = pool.inspect().snapshot

    assert result.succeeded is True
    assert [assignment.task_id for assignment in result.assignments] == ["task-1", "task-3"]
    assert snapshot is not None
    assert [task.task_id for task in snapshot.pending_tasks] == ["task-2"]


def test_parallel_worker_pool_restarts_crashed_worker_and_requeues_task(tmp_path):
    runtimes = {}

    def runtime_factory(worker_id, session_context):
        runtime = FakeRuntime(worker_id, session_context)
        runtimes.setdefault(worker_id, []).append(runtime)
        return runtime

    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool_restart.json"),
        worker_count=1,
        runtime_factory=runtime_factory,
        now_fn=lambda: now,
    )
    pool.enqueue(make_task("task-1", account="acct-a", priority=TaskPriority.HIGH, retries=2))
    pool.dispatch()
    runtimes["worker-1"][-1].alive = False

    result = pool.monitor_health()
    snapshot = result.snapshot

    assert result.succeeded is True
    assert len(result.workers) == 1
    assert result.workers[0].restart_count == 1
    assert snapshot is not None
    assert [task.task_id for task in snapshot.pending_tasks] == ["task-1"]
    assert snapshot.active_assignments == []
    assert len(runtimes["worker-1"]) == 2
    assert runtimes["worker-1"][0].stopped == 1
    assert runtimes["worker-1"][1].started == 1


def test_parallel_worker_pool_restarts_worker_when_heartbeat_times_out(tmp_path):
    runtimes = {}
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}

    def runtime_factory(worker_id, session_context):
        runtime = FakeRuntime(worker_id, session_context)
        runtimes.setdefault(worker_id, []).append(runtime)
        return runtime

    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool_heartbeat.json"),
        worker_count=1,
        heartbeat_timeout_seconds=10.0,
        runtime_factory=runtime_factory,
        now_fn=lambda: current_time["value"],
    )
    inspected = pool.inspect()
    first_session_id = inspected.snapshot.workers[0].session_context.session_id if inspected.snapshot else None
    current_time["value"] = current_time["value"] + timedelta(seconds=20)

    result = pool.monitor_health()
    restarted = result.workers[0]

    assert result.succeeded is True
    assert restarted.session_context.session_id != first_session_id
    assert restarted.status.name == "IDLE"


def test_parallel_worker_pool_creates_isolated_session_contexts_for_workers(tmp_path):
    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool_sessions.json"),
        worker_count=2,
        execution_mode=WorkerExecutionMode.PROCESS,
        runtime_factory=lambda worker_id, session_context: FakeRuntime(worker_id, session_context),
    )

    snapshot = pool.inspect().snapshot

    assert snapshot is not None
    assert len(snapshot.workers) == 2
    assert snapshot.workers[0].session_context.session_id != snapshot.workers[1].session_context.session_id
    assert all(worker.session_context.execution_mode is WorkerExecutionMode.PROCESS for worker in snapshot.workers)


def test_parallel_worker_pool_dispatch_prefers_urgent_deadline(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool_deadline.json"),
        worker_count=1,
        runtime_factory=lambda worker_id, session_context: FakeRuntime(worker_id, session_context),
        now_fn=lambda: now,
    )
    pool.enqueue(
        make_task(
            "standard-high",
            account="acct-a",
            priority=TaskPriority.HIGH,
            deadline=now + timedelta(hours=8),
        )
    )
    pool.enqueue(
        make_task(
            "urgent-medium",
            account="acct-b",
            priority=TaskPriority.MEDIUM,
            deadline=now + timedelta(minutes=10),
        )
    )

    result = pool.dispatch()

    assert result.succeeded is True
    assert [assignment.task_id for assignment in result.assignments] == ["urgent-medium"]


def test_parallel_worker_pool_stops_worker_when_sandbox_violation_is_reported(tmp_path):
    policy_path = Path(tmp_path) / "sandbox_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "hostname_allowlist": ["api.safe.example"],
                "writable_directories": ["C:/sandbox/work"],
                "restricted_resources": [],
                "os_user_accounts": ["sandbox-user"],
                "virtualenv_paths": ["C:/venvs/worker-a"],
                "default_isolation_mode": "virtual_environment",
            }
        ),
        encoding="utf-8",
    )
    manager = SandboxIsolationManager(
        policy_path=str(policy_path),
        violation_log_path=str(Path(tmp_path) / "sandbox_violations.json"),
    )
    pool = ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool_sandbox.json"),
        worker_count=1,
        execution_mode=WorkerExecutionMode.PROCESS,
        isolation_manager=manager,
    )
    pool.enqueue(make_task("task-1", account="acct-a", priority=TaskPriority.HIGH))
    pool.dispatch()

    result = pool.report_network_access("worker-1", "api.blocked.example")
    snapshot = result.snapshot

    assert result.succeeded is False
    assert snapshot is not None
    assert snapshot.workers[0].status.name == "STOPPED"
    assert snapshot.failed_task_ids == ["task-1"]
