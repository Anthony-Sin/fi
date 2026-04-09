from datetime import datetime, timezone
from pathlib import Path

from desktop_automation_perception.accounts import AccountRegistry
from desktop_automation_perception.agents import ParallelWorkerPool
from desktop_automation_perception.load_balancer import MultiAccountLoadBalancer
from desktop_automation_perception.models import (
    AccountRecord,
    AutomationTask,
    RateLimitRequest,
    RateLimitRule,
    RateLimitScope,
    RateLimitWindow,
    TaskPriority,
)
from desktop_automation_perception.rate_limiter import RateLimiter


class FakeRuntime:
    def __init__(self, worker_id, session_context):
        self.worker_id = worker_id
        self.session_context = session_context
        self.alive = True
        self.assigned_tasks = []

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False

    def is_alive(self):
        return self.alive

    def assign_task(self, task):
        self.assigned_tasks.append(task.task_id)


def make_account(name: str, *, account_type: str = "seller", application: str = "chrome", health_score: float = 1.0):
    return AccountRecord(
        name=name,
        credential_reference=f"cred-{name}",
        account_type=account_type,
        application=application,
        active=True,
        health_score=health_score,
    )


def make_task(
    task_id: str,
    *,
    priority: TaskPriority = TaskPriority.MEDIUM,
    account: str | None = None,
    account_type: str | None = "seller",
    application: str | None = "chrome",
):
    return AutomationTask(
        task_id=task_id,
        priority=priority,
        required_module="automation.module",
        required_account=account,
        required_account_type=account_type,
        required_application=application,
        input_payload={"task_id": task_id},
        enqueued_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )


def build_registry(tmp_path, *accounts: AccountRecord) -> AccountRegistry:
    registry = AccountRegistry(storage_path=str(Path(tmp_path) / "accounts.json"))
    for account in accounts:
        registry.upsert_account(account)
    return registry


def build_pool(tmp_path, *, worker_count: int = 3) -> ParallelWorkerPool:
    return ParallelWorkerPool(
        storage_path=str(Path(tmp_path) / "pool.json"),
        worker_count=worker_count,
        runtime_factory=lambda worker_id, session_context: FakeRuntime(worker_id, session_context),
    )


def test_load_balancer_routes_to_least_loaded_healthy_account(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    registry = build_registry(tmp_path, make_account("acct-a"), make_account("acct-b"))
    pool = build_pool(tmp_path)
    pool.enqueue(make_task("existing-a", account="acct-a", account_type=None, application=None))
    pool.dispatch()

    balancer = MultiAccountLoadBalancer(
        storage_path=str(Path(tmp_path) / "balancer.json"),
        account_registry=registry,
        worker_pool=pool,
        account_capacities={"acct-a": 2, "acct-b": 2},
        now_fn=lambda: now,
    )

    result = balancer.route_task(make_task("new-task"))
    history = registry.get_usage_history("acct-b")

    assert result.succeeded is True
    assert result.task is not None
    assert result.task.required_account == "acct-b"
    assert result.decision is not None
    assert result.decision.selected_account == "acct-b"
    assert any(event.action == "load_balancer_route" and event.detail == "new-task" for event in history)


def test_load_balancer_queues_and_notifies_orchestrator_when_accounts_are_at_capacity(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    registry = build_registry(tmp_path, make_account("acct-a"), make_account("acct-b"))
    pool = build_pool(tmp_path)
    pool.enqueue(make_task("active-a", account="acct-a", account_type=None, application=None))
    pool.enqueue(make_task("active-b", account="acct-b", account_type=None, application=None))
    pool.dispatch()
    notifications = []

    balancer = MultiAccountLoadBalancer(
        storage_path=str(Path(tmp_path) / "balancer_queue.json"),
        account_registry=registry,
        worker_pool=pool,
        orchestrator_notification_callback=lambda task, reason: notifications.append((task.task_id, reason)),
        now_fn=lambda: now,
    )

    result = balancer.route_task(make_task("queued-task"))
    queued = balancer.inspect_queue()

    assert result.succeeded is True
    assert result.decision is not None
    assert result.decision.queued is True
    assert result.reason == "All matching healthy accounts are at capacity or rate-limited."
    assert [task.task_id for task in queued.tasks] == ["queued-task"]
    assert notifications == [("queued-task", "All matching healthy accounts are at capacity or rate-limited.")]


def test_load_balancer_avoids_accounts_that_have_hit_rate_limits(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    registry = build_registry(tmp_path, make_account("acct-a"), make_account("acct-b"))
    pool = build_pool(tmp_path)
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate_limiter.json"),
        rules=[RateLimitRule(scope=RateLimitScope.ACCOUNT, key="acct-a", limit=1, window=RateLimitWindow.MINUTE)],
        now_fn=lambda: now,
    )
    limiter.submit_request(RateLimitRequest(request_id="acct-a-1", account_name="acct-a"))

    balancer = MultiAccountLoadBalancer(
        storage_path=str(Path(tmp_path) / "balancer_rate.json"),
        account_registry=registry,
        worker_pool=pool,
        rate_limiter=limiter,
        account_capacities={"acct-a": 2, "acct-b": 2},
        now_fn=lambda: now,
    )

    result = balancer.route_task(make_task("rate-aware-task"))

    assert result.succeeded is True
    assert result.decision is not None
    assert result.decision.selected_account == "acct-b"
    assert result.task is not None
    assert result.task.required_account == "acct-b"


def test_load_balancer_reports_metrics_and_rebalances_queued_tasks_when_capacity_returns(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    registry = build_registry(tmp_path, make_account("acct-a"), make_account("acct-b", health_score=0.3))
    pool = build_pool(tmp_path)
    pool.enqueue(make_task("active-a", account="acct-a", account_type=None, application=None))
    dispatch_result = pool.dispatch()

    balancer = MultiAccountLoadBalancer(
        storage_path=str(Path(tmp_path) / "balancer_rebalance.json"),
        account_registry=registry,
        worker_pool=pool,
        now_fn=lambda: now,
    )

    queued_result = balancer.route_task(make_task("queued-task", account_type="seller", application="chrome"))
    metrics = balancer.get_load_metrics().metrics
    pool.complete_task(dispatch_result.assignments[0].worker_id)
    rebalanced = balancer.rebalance_queued_tasks()
    remaining = balancer.inspect_queue()

    acct_a_metric = next(metric for metric in metrics if metric.account_name == "acct-a")
    acct_b_metric = next(metric for metric in metrics if metric.account_name == "acct-b")

    assert acct_a_metric.current_load == 1
    assert acct_a_metric.capacity == 1
    assert acct_a_metric.available_capacity == 0
    assert acct_b_metric.healthy is False
    assert queued_result.decision is not None and queued_result.decision.queued is True
    assert len(rebalanced.decisions) == 1
    assert rebalanced.decisions[0].selected_account == "acct-a"
    assert remaining.tasks == []
