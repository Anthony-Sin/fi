from datetime import datetime, timedelta, timezone
from pathlib import Path

from desktop_automation_perception.agents import TaskQueueManager
from desktop_automation_perception.models import AutomationTask, TaskPriority


def make_task(
    task_id: str,
    *,
    priority: TaskPriority,
    account: str,
    module: str = "automation",
    payload: dict | None = None,
    deadline: datetime | None = None,
    max_retry_count: int = 3,
    enqueued_at: datetime | None = None,
) -> AutomationTask:
    return AutomationTask(
        task_id=task_id,
        priority=priority,
        required_account=account,
        required_module=module,
        input_payload={} if payload is None else dict(payload),
        deadline=deadline,
        max_retry_count=max_retry_count,
        enqueued_at=enqueued_at or datetime.now(timezone.utc),
    )


def test_task_queue_manager_enqueues_peeks_and_dequeues_by_priority(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    manager = TaskQueueManager(
        storage_path=str(Path(tmp_path) / "task_queue.json"),
        now_fn=lambda: now,
    )
    manager.enqueue(make_task("low", priority=TaskPriority.LOW, account="acct-a"))
    manager.enqueue(make_task("high", priority=TaskPriority.HIGH, account="acct-b"))
    manager.enqueue(make_task("medium", priority=TaskPriority.MEDIUM, account="acct-c"))

    peeked = manager.peek()
    first = manager.dequeue()
    second = manager.dequeue()
    third = manager.dequeue()

    assert peeked.succeeded is True
    assert peeked.task is not None and peeked.task.task_id == "high"
    assert [first.task.task_id, second.task.task_id, third.task.task_id] == ["high", "medium", "low"]


def test_task_queue_manager_escalates_urgent_deadlines(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    manager = TaskQueueManager(
        storage_path=str(Path(tmp_path) / "deadline_queue.json"),
        now_fn=lambda: now,
    )
    manager.enqueue(
        make_task(
            "standard-high",
            priority=TaskPriority.HIGH,
            account="acct-a",
            deadline=now + timedelta(hours=8),
        )
    )
    manager.enqueue(
        make_task(
            "urgent-medium",
            priority=TaskPriority.MEDIUM,
            account="acct-b",
            deadline=now + timedelta(minutes=10),
        )
    )

    result = manager.dequeue()

    assert result.succeeded is True
    assert result.task is not None
    assert result.task.task_id == "urgent-medium"


def test_task_queue_manager_groups_equal_priority_tasks_by_account(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    manager = TaskQueueManager(
        storage_path=str(Path(tmp_path) / "grouped_queue.json"),
        now_fn=lambda: now,
    )
    manager.enqueue(make_task("acct-a-1", priority=TaskPriority.HIGH, account="acct-a"))
    manager.enqueue(make_task("acct-b-1", priority=TaskPriority.HIGH, account="acct-b"))
    manager.enqueue(make_task("acct-a-2", priority=TaskPriority.HIGH, account="acct-a"))

    first = manager.dequeue()
    second = manager.dequeue()
    third = manager.dequeue()

    assert [first.task.task_id, second.task.task_id, third.task.task_id] == [
        "acct-a-1",
        "acct-a-2",
        "acct-b-1",
    ]


def test_task_queue_manager_logs_depth_metrics_and_emits_threshold_alerts(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    metrics = []
    alerts = []
    manager = TaskQueueManager(
        storage_path=str(Path(tmp_path) / "alerts_queue.json"),
        depth_alert_thresholds=(1, 3),
        depth_metric_callback=metrics.append,
        alert_callback=alerts.append,
        now_fn=lambda: now,
    )

    manager.enqueue(make_task("t1", priority=TaskPriority.LOW, account="acct-a"))
    manager.enqueue(make_task("t2", priority=TaskPriority.LOW, account="acct-a"))
    manager.enqueue(make_task("t3", priority=TaskPriority.LOW, account="acct-a"))
    manager.enqueue(make_task("t4", priority=TaskPriority.LOW, account="acct-a"))
    dequeued = manager.dequeue()
    snapshot = manager.get_snapshot()

    assert dequeued.succeeded is True
    assert [metric.depth for metric in metrics] == [1, 2, 3, 4, 3]
    assert [alert.threshold for alert in alerts] == [1, 3]
    assert [alert.depth for alert in alerts] == [2, 4]
    assert len(snapshot.depth_metrics) == 5
    assert len(snapshot.alerts) == 2


def test_task_queue_manager_returns_failure_for_empty_queue(tmp_path):
    manager = TaskQueueManager(storage_path=str(Path(tmp_path) / "empty_queue.json"))

    peeked = manager.peek()
    dequeued = manager.dequeue()

    assert peeked.succeeded is False
    assert peeked.reason == "Task queue is empty."
    assert dequeued.succeeded is False
    assert dequeued.reason == "Task queue is empty."


def test_task_queue_manager_can_clear_pending_tasks(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    manager = TaskQueueManager(
        storage_path=str(Path(tmp_path) / "clear_queue.json"),
        now_fn=lambda: now,
    )
    manager.enqueue(make_task("one", priority=TaskPriority.HIGH, account="acct-a"))
    manager.enqueue(make_task("two", priority=TaskPriority.MEDIUM, account="acct-b"))

    result = manager.clear_pending_tasks(reason="Stopped by fail-safe.")

    assert result.succeeded is True
    assert [task.task_id for task in result.removed_tasks] == ["one", "two"]
    assert manager.get_snapshot().tasks == []
    assert result.reason == "Stopped by fail-safe."
