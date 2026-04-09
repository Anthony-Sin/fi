from __future__ import annotations

import json
from datetime import datetime, timedelta

from desktop_automation_agent.models import (
    AccountLoadMetric,
    AutomationTask,
    DeadLetterItem,
    DeadLetterOperationResult,
    LoadBalancerResult,
    TaskPriority,
    TaskQueueSnapshot,
    WorkerAssignment,
    WorkerExecutionMode,
    WorkerPoolOperationResult,
    WorkerPoolSnapshot,
    WorkerRecord,
    WorkerSessionContext,
    WorkerStatus,
    WorkflowAuditLogEntry,
    WorkflowAuditOutcome,
    WorkflowAuditResult,
)
from desktop_automation_agent.observability import RealTimeDashboardDataProvider


class FakeTaskQueueManager:
    def __init__(self, tasks):
        self._tasks = list(tasks)

    def get_snapshot(self):
        return TaskQueueSnapshot(tasks=list(self._tasks))


class FakeDeadLetterQueueHandler:
    def __init__(self, items):
        self._items = list(items)

    def inspect(self):
        return DeadLetterOperationResult(succeeded=True, items=list(self._items))


class FakeLoadBalancer:
    def __init__(self, metrics):
        self._metrics = list(metrics)

    def get_load_metrics(self):
        return LoadBalancerResult(succeeded=True, metrics=list(self._metrics))


class FakeWorkerPool:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def inspect(self):
        return WorkerPoolOperationResult(succeeded=True, snapshot=self._snapshot)


class FakeWorkflowAuditLogger:
    def __init__(self, entries):
        self._entries = list(entries)
        self.last_query = None

    def query_logs(self, query):
        self.last_query = query
        filtered = [entry for entry in self._entries if query.started_at is None or entry.timestamp >= query.started_at]
        return WorkflowAuditResult(succeeded=True, entries=filtered)


def test_dashboard_data_provider_aggregates_live_snapshot():
    now = datetime(2026, 4, 9, 14, 30, 0)
    active_task = AutomationTask(
        task_id="task-1",
        priority=TaskPriority.HIGH,
        required_module="workflow_runner",
        required_application="billing-app",
        input_payload={
            "workflow_id": "workflow-1",
            "workflow_version_number": 7,
            "current_step_name": "Submit invoice",
            "total_steps": 5,
            "completed_steps": 2,
        },
    )
    worker_snapshot = WorkerPoolSnapshot(
        workers=[
            WorkerRecord(
                worker_id="worker-a",
                status=WorkerStatus.BUSY,
                session_context=WorkerSessionContext(
                    worker_id="worker-a",
                    session_id="session-a",
                    execution_mode=WorkerExecutionMode.THREAD,
                ),
                last_heartbeat_at=now,
                current_task_id="task-1",
                current_account="acct-a",
                restart_count=1,
            )
        ],
        active_tasks=[active_task],
        active_assignments=[
            WorkerAssignment(
                worker_id="worker-a",
                task_id="task-1",
                account_name="acct-a",
                module_name="workflow_runner",
                assigned_at=now,
            )
        ],
    )
    audit_logger = FakeWorkflowAuditLogger(
        [
            WorkflowAuditLogEntry(
                timestamp=now - timedelta(minutes=20),
                workflow_id="workflow-1",
                workflow_version_number=7,
                step_name="Submit invoice",
                action_type="click",
                outcome=WorkflowAuditOutcome.SUCCESS,
                success=True,
            ),
            WorkflowAuditLogEntry(
                timestamp=now - timedelta(minutes=10),
                workflow_id="workflow-1",
                workflow_version_number=7,
                step_name="Submit invoice",
                action_type="click",
                outcome=WorkflowAuditOutcome.FAILURE,
                success=False,
            ),
            WorkflowAuditLogEntry(
                timestamp=now - timedelta(minutes=5),
                workflow_id="workflow-1",
                workflow_version_number=7,
                step_name="Open dashboard",
                action_type="navigate",
                outcome=WorkflowAuditOutcome.SUCCESS,
                success=True,
            ),
        ]
    )

    provider = RealTimeDashboardDataProvider(
        task_queue_manager=FakeTaskQueueManager(
            [
                active_task,
                AutomationTask(task_id="task-2", priority=TaskPriority.MEDIUM, required_module="workflow_runner"),
            ]
        ),
        dead_letter_queue_handler=FakeDeadLetterQueueHandler(
            [DeadLetterItem(item_id="dlq-1", action_type="click")]
        ),
        load_balancer=FakeLoadBalancer(
            [
                AccountLoadMetric(
                    account_name="acct-a",
                    account_type="shared",
                    application_name="Billing",
                    healthy=True,
                    active=True,
                    current_load=3,
                    capacity=5,
                    available_capacity=2,
                    load_ratio=0.6,
                    assigned_worker_ids=["worker-a"],
                )
            ]
        ),
        worker_pool=FakeWorkerPool(worker_snapshot),
        workflow_audit_logger=audit_logger,
        now_fn=lambda: now,
    )

    result = provider.get_dashboard_snapshot()

    assert result.succeeded is True
    assert result.snapshot is not None
    assert result.snapshot.generated_at == now
    assert result.snapshot.queue_depths.task_queue_depth == 2
    assert result.snapshot.queue_depths.dead_letter_queue_depth == 1
    assert len(result.snapshot.active_workflows) == 1
    workflow = result.snapshot.active_workflows[0]
    assert workflow.workflow_id == "workflow-1"
    assert workflow.workflow_version_number == 7
    assert workflow.current_step_name == "Submit invoice"
    assert workflow.completed_steps == 2
    assert workflow.total_steps == 5
    assert workflow.percent_complete == 0.4
    assert workflow.assigned_worker_ids == ["worker-a"]
    assert result.snapshot.account_statuses[0].account_name == "acct-a"
    assert result.snapshot.worker_statuses[0].status == "busy"
    assert result.snapshot.worker_statuses[0].current_task_id == "task-1"
    step_rates = {item.step_name: item for item in result.snapshot.step_rates_last_hour}
    assert step_rates["Submit invoice"].success_count == 1
    assert step_rates["Submit invoice"].failure_count == 1
    assert step_rates["Submit invoice"].success_rate == 0.5
    assert step_rates["Open dashboard"].success_rate == 1.0
    assert audit_logger.last_query is not None
    assert audit_logger.last_query.started_at == now - timedelta(hours=1)


def test_dashboard_data_provider_builds_sse_and_websocket_payloads():
    now = datetime(2026, 4, 9, 15, 0, 0)
    provider = RealTimeDashboardDataProvider(
        active_workflow_provider=lambda: [
            {
                "workflow_id": "workflow-9",
                "workflow_version_number": 2,
                "current_step_name": "Validate",
                "total_steps": 4,
                "completed_steps": 1,
                "active_task_ids": ["task-9"],
                "assigned_worker_ids": ["worker-z"],
            }
        ],
        now_fn=lambda: now,
    )

    sse_result = provider.build_sse_event()
    websocket_result = provider.build_websocket_payload()

    assert sse_result.succeeded is True
    assert sse_result.sse_event is not None
    assert sse_result.sse_event.startswith("event: dashboard\n")
    payload = json.loads(sse_result.sse_event.split("data: ", 1)[1])
    assert payload["generated_at"] == now.isoformat()
    assert payload["active_workflows"][0]["workflow_id"] == "workflow-9"
    assert payload["active_workflows"][0]["percent_complete"] == 0.25

    assert websocket_result.succeeded is True
    assert websocket_result.websocket_payload is not None
    assert websocket_result.websocket_payload["type"] == "dashboard_snapshot"
    assert websocket_result.websocket_payload["data"]["active_workflows"][0]["assigned_worker_ids"] == ["worker-z"]


def test_dashboard_data_provider_streams_at_configured_interval():
    sleeps = []
    now = datetime(2026, 4, 9, 16, 0, 0)
    provider = RealTimeDashboardDataProvider(
        update_interval_seconds=2.5,
        active_workflow_provider=lambda: [{"workflow_id": "workflow-stream"}],
        now_fn=lambda: now,
        sleep_fn=sleeps.append,
    )

    sse_events = list(provider.stream_sse(iterations=3))
    websocket_payloads = list(provider.stream_websocket_payloads(iterations=2))

    assert len(sse_events) == 3
    assert all(event.startswith("event: dashboard\n") for event in sse_events)
    assert len(websocket_payloads) == 2
    assert all(payload["type"] == "dashboard_snapshot" for payload in websocket_payloads)
    assert sleeps == [2.5, 2.5, 2.5]
