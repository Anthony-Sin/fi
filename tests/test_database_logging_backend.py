from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_agent.database_logging_backend import (
    DatabaseLoggingBackend,
    SQLiteConnectionPool,
)
from desktop_automation_agent.models import (
    DatabaseCheckpointRecord,
    DatabaseErrorRecord,
    DatabaseExtractedDataRecord,
    DatabaseStepRecord,
    DatabaseMetricRecord,
    DatabaseWorkflowEventRecord,
    DatabaseWorkflowRecord,
    WorkflowCheckpoint,
    WorkflowContext,
    WorkflowStepResult,
)


class FlakyConnectionPool:
    def __init__(self, wrapped_pool):
        self.wrapped_pool = wrapped_pool
        self.available = True

    def acquire(self):
        if not self.available:
            raise RuntimeError("database unavailable")
        return self.wrapped_pool.acquire()

    def release(self, connection):
        self.wrapped_pool.release(connection)

    def close(self):
        self.wrapped_pool.close()


def test_database_logging_backend_persists_workflow_records_and_queries_by_workflow_id(tmp_path):
    now = datetime(2026, 4, 8, 12, 0)
    backend = DatabaseLoggingBackend(
        database_path=str(Path(tmp_path) / "automation.sqlite"),
        buffer_path=str(Path(tmp_path) / "buffer.jsonl"),
        now_fn=lambda: now,
    )
    backend.initialize_schema()

    backend.insert_workflow(
        DatabaseWorkflowRecord(
            workflow_id="wf-1",
            workflow_name="Import contacts",
            started_at=now,
            status="running",
            metadata={"account": "seller-a"},
        )
    )
    backend.insert_workflow_event(
        DatabaseWorkflowEventRecord(
            workflow_id="wf-1",
            event_type="started",
            detail="Workflow launched",
            payload={"source": "scheduler"},
            recorded_at=now,
        )
    )
    backend.insert_step_result(
        workflow_id="wf-1",
        step_result=WorkflowStepResult(
            step_id="step-1",
            application_name="chrome",
            succeeded=True,
            reason=None,
            context_snapshot=WorkflowContext(current_application="chrome", step_number=1),
        ),
    )
    backend.insert_error(
        DatabaseErrorRecord(
            workflow_id="wf-1",
            step_id="step-2",
            error_type="TimeoutError",
            message="Step timed out",
            recorded_at=now + timedelta(minutes=1),
        )
    )
    backend.insert_checkpoint(
        checkpoint=WorkflowCheckpoint(
            workflow_id="wf-1",
            saved_at=now + timedelta(minutes=2),
            step_index=2,
            workflow_context=WorkflowContext(current_application="crm", step_number=2, shared_data={"cursor": "abc"}),
            collected_data={"rows": "10"},
        )
    )
    backend.insert_extracted_data(
        DatabaseExtractedDataRecord(
            workflow_id="wf-1",
            step_id="step-1",
            data_key="customer_id",
            data_value="12345",
            source_application="chrome",
            recorded_at=now + timedelta(minutes=2),
        )
    )
    backend.insert_metric(
        DatabaseMetricRecord(
            workflow_id="wf-1",
            step_id="step-1",
            metric_name="latency_seconds",
            metric_value=1.25,
            dimensions={"browser": "chrome"},
            recorded_at=now + timedelta(minutes=2),
        )
    )

    result = backend.query_by_workflow_id("wf-1")

    assert result.succeeded is True
    assert result.snapshot is not None
    assert result.snapshot.workflows[0].workflow_name == "Import contacts"
    assert result.snapshot.events[0].event_type == "started"
    assert result.snapshot.steps[0].status == "completed"
    assert result.snapshot.errors[0].error_type == "TimeoutError"
    assert result.snapshot.checkpoints[0].step_index == 2
    assert result.snapshot.extracted_data[0].data_value == "12345"
    assert result.snapshot.metrics[0].metric_value == 1.25


def test_database_logging_backend_queries_time_range_and_updates_step_status(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0)}
    backend = DatabaseLoggingBackend(
        database_path=str(Path(tmp_path) / "automation.sqlite"),
        buffer_path=str(Path(tmp_path) / "buffer.jsonl"),
        now_fn=lambda: current_time["value"],
    )
    backend.initialize_schema()

    backend.insert_workflow(
        DatabaseWorkflowRecord(workflow_id="wf-old", workflow_name="Old", started_at=current_time["value"], status="running")
    )
    backend.insert_step_result(
        record=DatabaseStepRecord(
            workflow_id="wf-old",
            step_id="step-a",
            application_name="app",
            status="running",
            succeeded=None,
            recorded_at=current_time["value"],
        ),
        workflow_id="wf-old",
    )
    backend.update_step_status(workflow_id="wf-old", step_id="step-a", status="failed", succeeded=False, reason="validation")
    current_time["value"] = current_time["value"] + timedelta(hours=2)
    backend.insert_workflow(
        DatabaseWorkflowRecord(workflow_id="wf-new", workflow_name="New", started_at=current_time["value"], status="running")
    )

    result = backend.query_by_time_range(
        started_at=datetime(2026, 4, 8, 13, 0),
        ended_at=datetime(2026, 4, 8, 15, 0),
    )
    old_result = backend.query_by_workflow_id("wf-old")

    assert result.succeeded is True
    assert [record.workflow_id for record in result.snapshot.workflows] == ["wf-new"]
    assert old_result.snapshot is not None
    assert old_result.snapshot.steps[0].status == "failed"
    assert old_result.snapshot.steps[0].reason == "validation"


def test_database_logging_backend_deletes_records_past_retention_period(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0)}
    backend = DatabaseLoggingBackend(
        database_path=str(Path(tmp_path) / "automation.sqlite"),
        buffer_path=str(Path(tmp_path) / "buffer.jsonl"),
        now_fn=lambda: current_time["value"],
    )
    backend.initialize_schema()

    backend.insert_workflow(
        DatabaseWorkflowRecord(workflow_id="wf-keep", started_at=current_time["value"], status="running")
    )
    backend.insert_workflow_event(
        DatabaseWorkflowEventRecord(workflow_id="wf-keep", event_type="keep", recorded_at=current_time["value"])
    )

    old_time = current_time["value"] - timedelta(days=10)
    backend.insert_workflow(DatabaseWorkflowRecord(workflow_id="wf-drop", started_at=old_time, status="completed"))
    backend.insert_workflow_event(DatabaseWorkflowEventRecord(workflow_id="wf-drop", event_type="drop", recorded_at=old_time))

    deleted = backend.delete_old_records(retention_period=timedelta(days=5))
    remaining = backend.query_by_time_range(
        started_at=current_time["value"] - timedelta(days=30),
        ended_at=current_time["value"] + timedelta(days=1),
    )

    assert deleted.succeeded is True
    assert deleted.deleted_count >= 2
    assert [record.workflow_id for record in remaining.snapshot.workflows] == ["wf-keep"]


def test_database_logging_backend_buffers_when_unavailable_and_flushes_when_restored(tmp_path):
    now = datetime(2026, 4, 8, 12, 0)
    wrapped_pool = SQLiteConnectionPool(str(Path(tmp_path) / "automation.sqlite"), pool_size=2)
    flaky_pool = FlakyConnectionPool(wrapped_pool)
    backend = DatabaseLoggingBackend(
        database_path=str(Path(tmp_path) / "automation.sqlite"),
        buffer_path=str(Path(tmp_path) / "buffer.jsonl"),
        connection_pool=flaky_pool,
        now_fn=lambda: now,
    )
    backend.initialize_schema()

    flaky_pool.available = False
    buffered = backend.insert_workflow(
        DatabaseWorkflowRecord(workflow_id="wf-buffered", workflow_name="Buffered", started_at=now, status="running")
    )
    buffered_event = backend.insert_workflow_event(
        DatabaseWorkflowEventRecord(workflow_id="wf-buffered", event_type="queued", recorded_at=now)
    )

    assert buffered.succeeded is True
    assert buffered.buffered_count == 1
    assert buffered_event.buffered_count == 2

    flaky_pool.available = True
    flushed = backend.flush_buffer()
    result = backend.query_by_workflow_id("wf-buffered")

    assert flushed.succeeded is True
    assert flushed.flushed_count == 2
    assert flushed.buffered_count == 0
    assert result.snapshot is not None
    assert result.snapshot.workflows[0].workflow_id == "wf-buffered"
    assert result.snapshot.events[0].event_type == "queued"
