from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from queue import LifoQueue
from typing import Any, Callable

from desktop_automation_agent.models import (
    DatabaseBufferedOperation,
    DatabaseCheckpointRecord,
    DatabaseErrorRecord,
    DatabaseExtractedDataRecord,
    DatabaseLoggingResult,
    DatabaseLoggingSnapshot,
    DatabaseLogOperationType,
    DatabaseMetricRecord,
    DatabaseStepRecord,
    DatabaseWorkflowEventRecord,
    DatabaseWorkflowRecord,
    WorkflowCheckpoint,
    WorkflowStepResult,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SQLiteConnectionPool:
    database_path: str
    pool_size: int = 3
    timeout_seconds: float = 5.0
    _pool: LifoQueue | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._pool = LifoQueue(maxsize=max(1, self.pool_size))
        for _ in range(max(1, self.pool_size)):
            self._pool.put(self._create_connection())

    def acquire(self):
        if self._pool is None:
            logger.warning("Attempted to acquire from a closed connection pool.")
            return None
        return self._pool.get()

    def release(self, connection) -> None:
        if self._pool is None:
            try:
                connection.close()
            except Exception:
                return
            return
        self._pool.put(connection)

    def close(self) -> None:
        if self._pool is None:
            return
        while not self._pool.empty():
            try:
                self._pool.get_nowait().close()
            except Exception:
                pass
        self._pool = None

    def _create_connection(self):
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=self.timeout_seconds, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection


@dataclass(slots=True)
class DatabaseLoggingBackend:
    database_path: str
    buffer_path: str
    connection_pool: object | None = None
    pool_size: int = 3
    now_fn: Callable[[], datetime] = utc_now

    def __post_init__(self) -> None:
        if self.connection_pool is None:
            self.connection_pool = SQLiteConnectionPool(self.database_path, pool_size=self.pool_size)

    def initialize_schema(self) -> DatabaseLoggingResult:
        statements = [
            "CREATE TABLE IF NOT EXISTS workflows (workflow_id TEXT PRIMARY KEY, workflow_name TEXT, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL, metadata_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS workflow_events (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, step_id TEXT, event_type TEXT NOT NULL, detail TEXT, payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS steps (workflow_id TEXT NOT NULL, step_id TEXT NOT NULL, application_name TEXT NOT NULL, status TEXT NOT NULL, succeeded INTEGER, reason TEXT, payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (workflow_id, step_id))",
            "CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, step_id TEXT, error_type TEXT NOT NULL, message TEXT NOT NULL, detail TEXT, recorded_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS checkpoints (workflow_id TEXT NOT NULL, step_index INTEGER NOT NULL, saved_at TEXT NOT NULL, context_json TEXT NOT NULL, account_context_json TEXT NOT NULL, collected_data_json TEXT NOT NULL, PRIMARY KEY (workflow_id, step_index, saved_at))",
            "CREATE TABLE IF NOT EXISTS extracted_data (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, step_id TEXT, data_key TEXT NOT NULL, data_value TEXT NOT NULL, source_application TEXT, recorded_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, step_id TEXT, metric_name TEXT NOT NULL, metric_value REAL NOT NULL, dimensions_json TEXT NOT NULL, recorded_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow_id ON workflow_events(workflow_id, recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_steps_workflow_id ON steps(workflow_id, recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_errors_workflow_id ON errors(workflow_id, recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_workflow_id ON checkpoints(workflow_id, saved_at)",
            "CREATE INDEX IF NOT EXISTS idx_extracted_data_workflow_id ON extracted_data(workflow_id, recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_metrics_workflow_id ON metrics(workflow_id, recorded_at)",
        ]
        return self._execute_write(lambda cursor: [cursor.execute(statement) for statement in statements])

    def insert_workflow(self, record: DatabaseWorkflowRecord) -> DatabaseLoggingResult:
        payload = self._serialize_workflow(record)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_WORKFLOW,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO workflows (workflow_id, workflow_name, started_at, ended_at, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET workflow_name=excluded.workflow_name, started_at=excluded.started_at, "
                "ended_at=excluded.ended_at, status=excluded.status, metadata_json=excluded.metadata_json",
                (
                    payload["workflow_id"],
                    payload.get("workflow_name"),
                    payload["started_at"],
                    payload.get("ended_at"),
                    payload["status"],
                    json.dumps(payload.get("metadata", {}), sort_keys=True),
                ),
            ),
        )
        result.workflow = record
        return result

    def insert_workflow_event(self, record: DatabaseWorkflowEventRecord) -> DatabaseLoggingResult:
        payload = self._serialize_event(record)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_EVENT,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO workflow_events (workflow_id, step_id, event_type, detail, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload["workflow_id"],
                    payload.get("step_id"),
                    payload["event_type"],
                    payload.get("detail"),
                    json.dumps(payload.get("payload", {}), sort_keys=True),
                    payload["recorded_at"],
                ),
            ),
        )
        result.event = record
        return result

    def insert_step_result(
        self,
        *,
        workflow_id: str,
        step_result: WorkflowStepResult | None = None,
        record: DatabaseStepRecord | None = None,
    ) -> DatabaseLoggingResult:
        resolved = record or self._step_record_from_result(workflow_id, step_result)
        if resolved is None:
            return DatabaseLoggingResult(succeeded=False, reason="Missing step result data.")
        payload = self._serialize_step(resolved)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_STEP,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO steps (workflow_id, step_id, application_name, status, succeeded, reason, payload_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workflow_id, step_id) DO UPDATE SET application_name=excluded.application_name, status=excluded.status, "
                "succeeded=excluded.succeeded, reason=excluded.reason, payload_json=excluded.payload_json, recorded_at=excluded.recorded_at",
                (
                    payload["workflow_id"],
                    payload["step_id"],
                    payload["application_name"],
                    payload["status"],
                    payload.get("succeeded"),
                    payload.get("reason"),
                    json.dumps(payload.get("payload", {}), sort_keys=True),
                    payload["recorded_at"],
                ),
            ),
        )
        result.step = resolved
        return result

    def insert_error(self, record: DatabaseErrorRecord) -> DatabaseLoggingResult:
        payload = self._serialize_error(record)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_ERROR,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO errors (workflow_id, step_id, error_type, message, detail, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload["workflow_id"],
                    payload.get("step_id"),
                    payload["error_type"],
                    payload["message"],
                    payload.get("detail"),
                    payload["recorded_at"],
                ),
            ),
        )
        result.error = record
        return result

    def insert_checkpoint(
        self,
        *,
        checkpoint: WorkflowCheckpoint | None = None,
        record: DatabaseCheckpointRecord | None = None,
    ) -> DatabaseLoggingResult:
        resolved = record or self._checkpoint_record_from_checkpoint(checkpoint)
        if resolved is None:
            return DatabaseLoggingResult(succeeded=False, reason="Missing checkpoint data.")
        payload = self._serialize_checkpoint(resolved)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_CHECKPOINT,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO checkpoints (workflow_id, step_index, saved_at, context_json, account_context_json, collected_data_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload["workflow_id"],
                    payload["step_index"],
                    payload["saved_at"],
                    json.dumps(payload.get("context_snapshot", {}), sort_keys=True),
                    json.dumps(payload.get("account_context", {}), sort_keys=True),
                    json.dumps(payload.get("collected_data", {}), sort_keys=True),
                ),
            ),
        )
        result.checkpoint = resolved
        return result

    def insert_extracted_data(self, record: DatabaseExtractedDataRecord) -> DatabaseLoggingResult:
        payload = self._serialize_extracted_data(record)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_EXTRACTED_DATA,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO extracted_data (workflow_id, step_id, data_key, data_value, source_application, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload["workflow_id"],
                    payload.get("step_id"),
                    payload["data_key"],
                    payload["data_value"],
                    payload.get("source_application"),
                    payload["recorded_at"],
                ),
            ),
        )
        result.extracted_data_record = record
        return result

    def insert_metric(self, record: DatabaseMetricRecord) -> DatabaseLoggingResult:
        payload = self._serialize_metric(record)
        result = self._write_or_buffer(
            operation_type=DatabaseLogOperationType.INSERT_METRIC,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "INSERT INTO metrics (workflow_id, step_id, metric_name, metric_value, dimensions_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload["workflow_id"],
                    payload.get("step_id"),
                    payload["metric_name"],
                    payload["metric_value"],
                    json.dumps(payload.get("dimensions", {}), sort_keys=True),
                    payload["recorded_at"],
                ),
            ),
        )
        result.metric = record
        return result

    def update_step_status(
        self,
        *,
        workflow_id: str,
        step_id: str,
        status: str,
        succeeded: bool | None = None,
        reason: str | None = None,
    ) -> DatabaseLoggingResult:
        payload = {
            "workflow_id": workflow_id,
            "step_id": step_id,
            "status": status,
            "succeeded": succeeded,
            "reason": reason,
            "recorded_at": self.now_fn().isoformat(),
        }
        return self._write_or_buffer(
            operation_type=DatabaseLogOperationType.UPDATE_STEP_STATUS,
            payload=payload,
            writer=lambda cursor: cursor.execute(
                "UPDATE steps SET status = ?, succeeded = ?, reason = ?, recorded_at = ? WHERE workflow_id = ? AND step_id = ?",
                (
                    payload["status"],
                    payload.get("succeeded"),
                    payload.get("reason"),
                    payload["recorded_at"],
                    payload["workflow_id"],
                    payload["step_id"],
                ),
            ),
            updated_count=1,
        )

    def query_by_workflow_id(self, workflow_id: str) -> DatabaseLoggingResult:
        try:
            snapshot = self._execute_read(lambda cursor: self._build_workflow_snapshot(cursor, workflow_id=workflow_id))
            snapshot.buffered_operations = self._load_buffered_operations()
            return DatabaseLoggingResult(succeeded=True, snapshot=snapshot)
        except Exception as exc:
            return DatabaseLoggingResult(succeeded=False, reason=str(exc), snapshot=DatabaseLoggingSnapshot())

    def query_by_time_range(self, *, started_at: datetime, ended_at: datetime) -> DatabaseLoggingResult:
        try:
            snapshot = self._execute_read(
                lambda cursor: self._build_time_range_snapshot(
                    cursor,
                    started_at=started_at.isoformat(),
                    ended_at=ended_at.isoformat(),
                )
            )
            snapshot.buffered_operations = self._load_buffered_operations()
            return DatabaseLoggingResult(succeeded=True, snapshot=snapshot)
        except Exception as exc:
            return DatabaseLoggingResult(succeeded=False, reason=str(exc), snapshot=DatabaseLoggingSnapshot())

    def delete_old_records(self, *, retention_period: timedelta) -> DatabaseLoggingResult:
        cutoff = self.now_fn() - retention_period
        total_deleted = 0

        def writer(cursor):
            nonlocal total_deleted
            for statement in (
                ("DELETE FROM workflow_events WHERE recorded_at < ?", cutoff.isoformat()),
                ("DELETE FROM errors WHERE recorded_at < ?", cutoff.isoformat()),
                ("DELETE FROM checkpoints WHERE saved_at < ?", cutoff.isoformat()),
                ("DELETE FROM extracted_data WHERE recorded_at < ?", cutoff.isoformat()),
                ("DELETE FROM metrics WHERE recorded_at < ?", cutoff.isoformat()),
                ("DELETE FROM steps WHERE recorded_at < ?", cutoff.isoformat()),
                ("DELETE FROM workflows WHERE started_at < ?", cutoff.isoformat()),
            ):
                cursor.execute(statement[0], (statement[1],))
                total_deleted += max(cursor.rowcount, 0)

        result = self._execute_write(writer)
        result.deleted_count = total_deleted
        return result

    def flush_buffer(self) -> DatabaseLoggingResult:
        operations = self._load_buffered_operations()
        if not operations:
            return DatabaseLoggingResult(succeeded=True, flushed_count=0, buffered_count=0)

        flushed = 0
        remaining: list[DatabaseBufferedOperation] = []
        for index, operation in enumerate(operations):
            try:
                self._replay_buffered_operation(operation)
                flushed += 1
            except Exception:
                remaining.append(operation)
                remaining.extend(operations[index + 1 :])
                break

        self._write_buffered_operations(remaining)
        return DatabaseLoggingResult(
            succeeded=len(remaining) == 0,
            flushed_count=flushed,
            buffered_count=len(remaining),
            reason=None if not remaining else "Some buffered operations could not be flushed.",
        )

    def close(self) -> None:
        if hasattr(self.connection_pool, "close"):
            self.connection_pool.close()

    def _write_or_buffer(
        self,
        *,
        operation_type: DatabaseLogOperationType,
        payload: dict[str, Any],
        writer: Callable[[Any], Any],
        updated_count: int = 0,
    ) -> DatabaseLoggingResult:
        try:
            result = self._execute_write(writer)
            result.updated_count = updated_count
            result.buffered_count = len(self._load_buffered_operations())
            return result
        except Exception as exc:
            operation = DatabaseBufferedOperation(operation_type=operation_type, payload=payload, buffered_at=self.now_fn())
            buffered_operations = self._load_buffered_operations()
            buffered_operations.append(operation)
            self._write_buffered_operations(buffered_operations)
            return DatabaseLoggingResult(
                succeeded=True,
                buffered_count=len(buffered_operations),
                updated_count=updated_count,
                reason=f"Database unavailable; operation buffered locally: {exc}",
            )

    def _execute_write(self, writer: Callable[[Any], Any]) -> DatabaseLoggingResult:
        connection = self.connection_pool.acquire()
        if connection is None:
            return DatabaseLoggingResult(succeeded=False, reason="Could not acquire database connection.")
        try:
            cursor = connection.cursor()
            writer(cursor)
            connection.commit()
            return DatabaseLoggingResult(succeeded=True)
        except Exception as e:
            connection.rollback()
            logger.warning(f"Database write operation failed: {e}")
            return DatabaseLoggingResult(succeeded=False, reason=str(e))
        finally:
            self.connection_pool.release(connection)

    def _execute_read(self, reader: Callable[[Any], DatabaseLoggingSnapshot]) -> DatabaseLoggingSnapshot:
        connection = self.connection_pool.acquire()
        if connection is None:
            return DatabaseLoggingSnapshot()
        try:
            cursor = connection.cursor()
            return reader(cursor)
        except Exception as e:
            logger.warning(f"Database read operation failed: {e}")
            return DatabaseLoggingSnapshot()
        finally:
            self.connection_pool.release(connection)

    def _build_workflow_snapshot(self, cursor, *, workflow_id: str) -> DatabaseLoggingSnapshot:
        snapshot = DatabaseLoggingSnapshot()
        row = cursor.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is not None:
            snapshot.workflows.append(self._deserialize_workflow_row(row))
        snapshot.events = [
            self._deserialize_event_row(row)
            for row in cursor.execute(
                "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY recorded_at ASC",
                (workflow_id,),
            ).fetchall()
        ]
        snapshot.steps = [
            self._deserialize_step_row(row)
            for row in cursor.execute(
                "SELECT * FROM steps WHERE workflow_id = ? ORDER BY recorded_at ASC",
                (workflow_id,),
            ).fetchall()
        ]
        snapshot.errors = [
            self._deserialize_error_row(row)
            for row in cursor.execute(
                "SELECT * FROM errors WHERE workflow_id = ? ORDER BY recorded_at ASC",
                (workflow_id,),
            ).fetchall()
        ]
        snapshot.checkpoints = [
            self._deserialize_checkpoint_row(row)
            for row in cursor.execute(
                "SELECT * FROM checkpoints WHERE workflow_id = ? ORDER BY saved_at ASC",
                (workflow_id,),
            ).fetchall()
        ]
        snapshot.extracted_data = [
            self._deserialize_extracted_data_row(row)
            for row in cursor.execute(
                "SELECT * FROM extracted_data WHERE workflow_id = ? ORDER BY recorded_at ASC",
                (workflow_id,),
            ).fetchall()
        ]
        snapshot.metrics = [
            self._deserialize_metric_row(row)
            for row in cursor.execute(
                "SELECT * FROM metrics WHERE workflow_id = ? ORDER BY recorded_at ASC",
                (workflow_id,),
            ).fetchall()
        ]
        return snapshot

    def _build_time_range_snapshot(self, cursor, *, started_at: str, ended_at: str) -> DatabaseLoggingSnapshot:
        snapshot = DatabaseLoggingSnapshot()
        snapshot.workflows = [
            self._deserialize_workflow_row(row)
            for row in cursor.execute(
                "SELECT * FROM workflows WHERE started_at >= ? AND started_at <= ? ORDER BY started_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        snapshot.events = [
            self._deserialize_event_row(row)
            for row in cursor.execute(
                "SELECT * FROM workflow_events WHERE recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        snapshot.steps = [
            self._deserialize_step_row(row)
            for row in cursor.execute(
                "SELECT * FROM steps WHERE recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        snapshot.errors = [
            self._deserialize_error_row(row)
            for row in cursor.execute(
                "SELECT * FROM errors WHERE recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        snapshot.checkpoints = [
            self._deserialize_checkpoint_row(row)
            for row in cursor.execute(
                "SELECT * FROM checkpoints WHERE saved_at >= ? AND saved_at <= ? ORDER BY saved_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        snapshot.extracted_data = [
            self._deserialize_extracted_data_row(row)
            for row in cursor.execute(
                "SELECT * FROM extracted_data WHERE recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        snapshot.metrics = [
            self._deserialize_metric_row(row)
            for row in cursor.execute(
                "SELECT * FROM metrics WHERE recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at ASC",
                (started_at, ended_at),
            ).fetchall()
        ]
        return snapshot

    def _replay_buffered_operation(self, operation: DatabaseBufferedOperation) -> None:
        payload = operation.payload
        if operation.operation_type is DatabaseLogOperationType.INSERT_WORKFLOW:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO workflows (workflow_id, workflow_name, started_at, ended_at, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(workflow_id) DO UPDATE SET workflow_name=excluded.workflow_name, started_at=excluded.started_at, "
                    "ended_at=excluded.ended_at, status=excluded.status, metadata_json=excluded.metadata_json",
                    (
                        payload["workflow_id"],
                        payload.get("workflow_name"),
                        payload["started_at"],
                        payload.get("ended_at"),
                        payload["status"],
                        json.dumps(payload.get("metadata", {}), sort_keys=True),
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.INSERT_EVENT:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO workflow_events (workflow_id, step_id, event_type, detail, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        payload["workflow_id"],
                        payload.get("step_id"),
                        payload["event_type"],
                        payload.get("detail"),
                        json.dumps(payload.get("payload", {}), sort_keys=True),
                        payload["recorded_at"],
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.INSERT_STEP:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO steps (workflow_id, step_id, application_name, status, succeeded, reason, payload_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(workflow_id, step_id) DO UPDATE SET application_name=excluded.application_name, status=excluded.status, "
                    "succeeded=excluded.succeeded, reason=excluded.reason, payload_json=excluded.payload_json, recorded_at=excluded.recorded_at",
                    (
                        payload["workflow_id"],
                        payload["step_id"],
                        payload["application_name"],
                        payload["status"],
                        payload.get("succeeded"),
                        payload.get("reason"),
                        json.dumps(payload.get("payload", {}), sort_keys=True),
                        payload["recorded_at"],
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.INSERT_ERROR:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO errors (workflow_id, step_id, error_type, message, detail, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        payload["workflow_id"],
                        payload.get("step_id"),
                        payload["error_type"],
                        payload["message"],
                        payload.get("detail"),
                        payload["recorded_at"],
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.INSERT_CHECKPOINT:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO checkpoints (workflow_id, step_index, saved_at, context_json, account_context_json, collected_data_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        payload["workflow_id"],
                        payload["step_index"],
                        payload["saved_at"],
                        json.dumps(payload.get("context_snapshot", {}), sort_keys=True),
                        json.dumps(payload.get("account_context", {}), sort_keys=True),
                        json.dumps(payload.get("collected_data", {}), sort_keys=True),
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.INSERT_EXTRACTED_DATA:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO extracted_data (workflow_id, step_id, data_key, data_value, source_application, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        payload["workflow_id"],
                        payload.get("step_id"),
                        payload["data_key"],
                        payload["data_value"],
                        payload.get("source_application"),
                        payload["recorded_at"],
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.INSERT_METRIC:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "INSERT INTO metrics (workflow_id, step_id, metric_name, metric_value, dimensions_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        payload["workflow_id"],
                        payload.get("step_id"),
                        payload["metric_name"],
                        payload["metric_value"],
                        json.dumps(payload.get("dimensions", {}), sort_keys=True),
                        payload["recorded_at"],
                    ),
                )
            )
            return
        if operation.operation_type is DatabaseLogOperationType.UPDATE_STEP_STATUS:
            self._execute_write(
                lambda cursor: cursor.execute(
                    "UPDATE steps SET status = ?, succeeded = ?, reason = ?, recorded_at = ? WHERE workflow_id = ? AND step_id = ?",
                    (
                        payload["status"],
                        payload.get("succeeded"),
                        payload.get("reason"),
                        payload["recorded_at"],
                        payload["workflow_id"],
                        payload["step_id"],
                    ),
                )
            )
            return
        logger.warning(f"Unsupported buffered operation type: {operation.operation_type}")

    def _load_buffered_operations(self) -> list[DatabaseBufferedOperation]:
        path = Path(self.buffer_path)
        if not path.exists():
            return []
        operations: list[DatabaseBufferedOperation] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                normalized = line.strip()
                if not normalized:
                    continue
                payload = json.loads(normalized)
                operations.append(
                    DatabaseBufferedOperation(
                        operation_type=DatabaseLogOperationType(payload["operation_type"]),
                        payload=dict(payload.get("payload", {})),
                        buffered_at=datetime.fromisoformat(payload["buffered_at"]),
                    )
                )
        return operations

    def _write_buffered_operations(self, operations: list[DatabaseBufferedOperation]) -> None:
        path = Path(self.buffer_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for operation in operations:
                handle.write(
                    json.dumps(
                        {
                            "operation_type": operation.operation_type.value,
                            "payload": operation.payload,
                            "buffered_at": operation.buffered_at.isoformat(),
                        },
                        sort_keys=True,
                    )
                )
                handle.write("\n")

    def _serialize_workflow(self, record: DatabaseWorkflowRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "workflow_name": record.workflow_name,
            "started_at": record.started_at.isoformat(),
            "ended_at": None if record.ended_at is None else record.ended_at.isoformat(),
            "status": record.status,
            "metadata": dict(record.metadata),
        }

    def _serialize_event(self, record: DatabaseWorkflowEventRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "step_id": record.step_id,
            "event_type": record.event_type,
            "detail": record.detail,
            "payload": dict(record.payload),
            "recorded_at": record.recorded_at.isoformat(),
        }

    def _serialize_step(self, record: DatabaseStepRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "step_id": record.step_id,
            "application_name": record.application_name,
            "status": record.status,
            "succeeded": record.succeeded,
            "reason": record.reason,
            "payload": dict(record.payload),
            "recorded_at": record.recorded_at.isoformat(),
        }

    def _serialize_error(self, record: DatabaseErrorRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "step_id": record.step_id,
            "error_type": record.error_type,
            "message": record.message,
            "detail": record.detail,
            "recorded_at": record.recorded_at.isoformat(),
        }

    def _serialize_checkpoint(self, record: DatabaseCheckpointRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "step_index": record.step_index,
            "saved_at": record.saved_at.isoformat(),
            "context_snapshot": dict(record.context_snapshot),
            "account_context": dict(record.account_context),
            "collected_data": dict(record.collected_data),
        }

    def _serialize_extracted_data(self, record: DatabaseExtractedDataRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "step_id": record.step_id,
            "data_key": record.data_key,
            "data_value": record.data_value,
            "source_application": record.source_application,
            "recorded_at": record.recorded_at.isoformat(),
        }

    def _serialize_metric(self, record: DatabaseMetricRecord) -> dict[str, Any]:
        return {
            "workflow_id": record.workflow_id,
            "step_id": record.step_id,
            "metric_name": record.metric_name,
            "metric_value": record.metric_value,
            "dimensions": dict(record.dimensions),
            "recorded_at": record.recorded_at.isoformat(),
        }

    def _deserialize_workflow_payload(self, payload: dict[str, Any]) -> DatabaseWorkflowRecord:
        return DatabaseWorkflowRecord(
            workflow_id=payload["workflow_id"],
            workflow_name=payload.get("workflow_name"),
            started_at=datetime.fromisoformat(payload["started_at"]),
            ended_at=None if payload.get("ended_at") is None else datetime.fromisoformat(payload["ended_at"]),
            status=payload.get("status", "running"),
            metadata=dict(payload.get("metadata", {})),
        )

    def _deserialize_event_payload(self, payload: dict[str, Any]) -> DatabaseWorkflowEventRecord:
        return DatabaseWorkflowEventRecord(
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            event_type=payload["event_type"],
            detail=payload.get("detail"),
            payload=dict(payload.get("payload", {})),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )

    def _deserialize_step_payload(self, payload: dict[str, Any]) -> DatabaseStepRecord:
        return DatabaseStepRecord(
            workflow_id=payload["workflow_id"],
            step_id=payload["step_id"],
            application_name=payload["application_name"],
            status=payload.get("status", "pending"),
            succeeded=payload.get("succeeded"),
            reason=payload.get("reason"),
            payload=dict(payload.get("payload", {})),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )

    def _deserialize_error_payload(self, payload: dict[str, Any]) -> DatabaseErrorRecord:
        return DatabaseErrorRecord(
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            error_type=payload["error_type"],
            message=payload["message"],
            detail=payload.get("detail"),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )

    def _deserialize_checkpoint_payload(self, payload: dict[str, Any]) -> DatabaseCheckpointRecord:
        return DatabaseCheckpointRecord(
            workflow_id=payload["workflow_id"],
            step_index=int(payload["step_index"]),
            saved_at=datetime.fromisoformat(payload["saved_at"]),
            context_snapshot=dict(payload.get("context_snapshot", {})),
            account_context=dict(payload.get("account_context", {})),
            collected_data=dict(payload.get("collected_data", {})),
        )

    def _deserialize_extracted_data_payload(self, payload: dict[str, Any]) -> DatabaseExtractedDataRecord:
        return DatabaseExtractedDataRecord(
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            data_key=payload["data_key"],
            data_value=payload["data_value"],
            source_application=payload.get("source_application"),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )

    def _deserialize_metric_payload(self, payload: dict[str, Any]) -> DatabaseMetricRecord:
        return DatabaseMetricRecord(
            workflow_id=payload["workflow_id"],
            step_id=payload.get("step_id"),
            metric_name=payload["metric_name"],
            metric_value=float(payload["metric_value"]),
            dimensions=dict(payload.get("dimensions", {})),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )

    def _deserialize_workflow_row(self, row) -> DatabaseWorkflowRecord:
        return DatabaseWorkflowRecord(
            workflow_id=row["workflow_id"],
            workflow_name=row["workflow_name"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=None if row["ended_at"] is None else datetime.fromisoformat(row["ended_at"]),
            status=row["status"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def _deserialize_event_row(self, row) -> DatabaseWorkflowEventRecord:
        return DatabaseWorkflowEventRecord(
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            event_type=row["event_type"],
            detail=row["detail"],
            payload=json.loads(row["payload_json"] or "{}"),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def _deserialize_step_row(self, row) -> DatabaseStepRecord:
        succeeded = row["succeeded"]
        return DatabaseStepRecord(
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            application_name=row["application_name"],
            status=row["status"],
            succeeded=None if succeeded is None else bool(succeeded),
            reason=row["reason"],
            payload=json.loads(row["payload_json"] or "{}"),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def _deserialize_error_row(self, row) -> DatabaseErrorRecord:
        return DatabaseErrorRecord(
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            error_type=row["error_type"],
            message=row["message"],
            detail=row["detail"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def _deserialize_checkpoint_row(self, row) -> DatabaseCheckpointRecord:
        return DatabaseCheckpointRecord(
            workflow_id=row["workflow_id"],
            step_index=int(row["step_index"]),
            saved_at=datetime.fromisoformat(row["saved_at"]),
            context_snapshot=json.loads(row["context_json"] or "{}"),
            account_context=json.loads(row["account_context_json"] or "{}"),
            collected_data=json.loads(row["collected_data_json"] or "{}"),
        )

    def _deserialize_extracted_data_row(self, row) -> DatabaseExtractedDataRecord:
        return DatabaseExtractedDataRecord(
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            data_key=row["data_key"],
            data_value=row["data_value"],
            source_application=row["source_application"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def _deserialize_metric_row(self, row) -> DatabaseMetricRecord:
        return DatabaseMetricRecord(
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            metric_name=row["metric_name"],
            metric_value=float(row["metric_value"]),
            dimensions=json.loads(row["dimensions_json"] or "{}"),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def _step_record_from_result(self, workflow_id: str, step_result: WorkflowStepResult | None) -> DatabaseStepRecord | None:
        if step_result is None:
            logger.warning("step_result or record is required for database logging.")
            return None
        payload: dict[str, Any] = {"dry_run": step_result.dry_run}
        if step_result.context_snapshot is not None:
            payload["context_snapshot"] = {
                "current_application": step_result.context_snapshot.current_application,
                "step_number": step_result.context_snapshot.step_number,
                "shared_data": dict(step_result.context_snapshot.shared_data),
                "active_applications": list(step_result.context_snapshot.active_applications),
                "application_signatures": dict(step_result.context_snapshot.application_signatures),
            }
        return DatabaseStepRecord(
            workflow_id=workflow_id,
            step_id=step_result.step_id,
            application_name=step_result.application_name,
            status="completed" if step_result.succeeded else "failed",
            succeeded=step_result.succeeded,
            reason=step_result.reason,
            payload=payload,
            recorded_at=self.now_fn(),
        )

    def _checkpoint_record_from_checkpoint(self, checkpoint: WorkflowCheckpoint | None) -> DatabaseCheckpointRecord | None:
        if checkpoint is None:
            logger.warning("checkpoint or record is required for database logging.")
            return None
        return DatabaseCheckpointRecord(
            workflow_id=checkpoint.workflow_id,
            step_index=checkpoint.step_index,
            saved_at=checkpoint.saved_at,
            context_snapshot={
                "current_application": checkpoint.workflow_context.current_application,
                "step_number": checkpoint.workflow_context.step_number,
                "shared_data": dict(checkpoint.workflow_context.shared_data),
                "active_applications": list(checkpoint.workflow_context.active_applications),
                "application_signatures": dict(checkpoint.workflow_context.application_signatures),
            },
            account_context=dict(checkpoint.account_context),
            collected_data=dict(checkpoint.collected_data),
        )


