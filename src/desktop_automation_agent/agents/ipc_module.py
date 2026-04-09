from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from desktop_automation_agent.models import (
    IPCChannelType,
    IPCConnectionRecord,
    IPCMessage,
    IPCOperationResult,
    IPCSnapshot,
    RetryConfiguration,
    RetryExceptionRule,
    RetryFailureResult,
    RetryDisposition,
)
from desktop_automation_agent.resilience.retry_engine import (
    ExponentialBackoffRetryEngine,
    RetryExhaustedError,
)


class LocalSocketIPCBackend:
    def connect(self, endpoint: str) -> str:
        return endpoint

    def send(self, handle: str, message: IPCMessage) -> None:
        _ = (handle, message)

    def receive(self, handle: str) -> list[IPCMessage]:
        _ = handle
        return []

    def disconnect(self, handle: str) -> None:
        _ = handle


class NamedPipeIPCBackend(LocalSocketIPCBackend):
    pass


@dataclass(slots=True)
class IPCModule:
    storage_path: str
    process_id: str
    channel_type: IPCChannelType = IPCChannelType.LOCAL_SOCKET
    local_socket_backend: object | None = None
    named_pipe_backend: object | None = None
    retry_engine: ExponentialBackoffRetryEngine[object] | None = None
    retry_configuration: RetryConfiguration | None = None
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _handles: dict[str, object] = None

    def __post_init__(self) -> None:
        if self._handles is None:
            self._handles = {}

    def connect(self, *, remote_process_id: str, endpoint: str) -> IPCOperationResult:
        snapshot = self._load_snapshot()
        try:
            handle = self._run_with_retry(lambda: self._backend().connect(endpoint))
            self._handles[remote_process_id] = handle
            record = IPCConnectionRecord(
                process_id=remote_process_id,
                endpoint=endpoint,
                channel_type=self.channel_type,
                connected=True,
                last_connected_at=self.now_fn(),
                reconnect_attempts=0,
            )
            snapshot.connections = [
                item for item in snapshot.connections if item.process_id != remote_process_id
            ] + [record]
            self._save_snapshot(snapshot)
            return IPCOperationResult(succeeded=True, connection=record, connections=list(snapshot.connections), snapshot=snapshot)
        except RetryExhaustedError as exc:
            record = self._connection_record(snapshot, remote_process_id, endpoint)
            if record is not None:
                record.connected = False
                record.reconnect_attempts += 1
                record.last_error = exc.failure.reason
                self._upsert_connection(snapshot, record)
                self._save_snapshot(snapshot)
            return IPCOperationResult(
                succeeded=False,
                connection=record,
                connections=list(snapshot.connections),
                snapshot=snapshot,
                retry_failure=exc.failure,
                reason=exc.failure.reason,
            )

    def send_command(
        self,
        *,
        remote_process_id: str,
        message_type: str,
        payload: dict,
        correlation_id: str,
    ) -> IPCOperationResult:
        snapshot = self._load_snapshot()
        connection = self._find_connection(snapshot, remote_process_id)
        if connection is None:
            return IPCOperationResult(succeeded=False, snapshot=snapshot, reason="IPC connection not found.")
        reconnected = self._ensure_connection(snapshot, connection)
        if reconnected.succeeded is False:
            return reconnected

        message = IPCMessage(
            message_id=str(uuid4()),
            sender_id=self.process_id,
            recipient_id=remote_process_id,
            message_type=message_type,
            payload=dict(payload),
            correlation_id=correlation_id,
            timestamp=self.now_fn(),
        )
        try:
            self._send_with_reconnect(connection, message, snapshot)
            snapshot.sent_messages.append(message)
            self._save_snapshot(snapshot)
            return IPCOperationResult(
                succeeded=True,
                connection=connection,
                message=message,
                messages=[message],
                snapshot=snapshot,
            )
        except RetryExhaustedError as exc:
            connection.connected = False
            connection.reconnect_attempts += 1
            connection.last_error = exc.failure.reason
            self._upsert_connection(snapshot, connection)
            self._save_snapshot(snapshot)
            return IPCOperationResult(
                succeeded=False,
                connection=connection,
                message=message,
                snapshot=snapshot,
                retry_failure=exc.failure,
                reason=exc.failure.reason,
            )

    def receive_messages(self, *, remote_process_id: str) -> IPCOperationResult:
        snapshot = self._load_snapshot()
        connection = self._find_connection(snapshot, remote_process_id)
        if connection is None:
            return IPCOperationResult(succeeded=False, snapshot=snapshot, reason="IPC connection not found.")
        reconnected = self._ensure_connection(snapshot, connection)
        if reconnected.succeeded is False:
            return reconnected

        try:
            messages = self._receive_with_reconnect(connection, snapshot)
            normalized = [self._copy_message(message) for message in messages]
            snapshot.received_messages.extend(normalized)
            self._save_snapshot(snapshot)
            return IPCOperationResult(
                succeeded=True,
                connection=connection,
                messages=normalized,
                snapshot=snapshot,
            )
        except RetryExhaustedError as exc:
            connection.connected = False
            connection.reconnect_attempts += 1
            connection.last_error = exc.failure.reason
            self._upsert_connection(snapshot, connection)
            self._save_snapshot(snapshot)
            return IPCOperationResult(
                succeeded=False,
                connection=connection,
                snapshot=snapshot,
                retry_failure=exc.failure,
                reason=exc.failure.reason,
            )

    def broadcast_command(
        self,
        *,
        message_type: str,
        payload: dict,
        correlation_id: str,
    ) -> IPCOperationResult:
        snapshot = self._load_snapshot()
        delivered: list[IPCMessage] = []
        for connection in list(snapshot.connections):
            result = self.send_command(
                remote_process_id=connection.process_id,
                message_type=message_type,
                payload=payload,
                correlation_id=correlation_id,
            )
            if result.message is not None:
                delivered.append(result.message)
        refreshed = self._load_snapshot()
        return IPCOperationResult(
            succeeded=bool(delivered),
            connections=list(refreshed.connections),
            messages=delivered,
            snapshot=refreshed,
            reason=None if delivered else "No connected IPC workers were available for broadcast.",
        )

    def disconnect(self, *, remote_process_id: str) -> IPCOperationResult:
        snapshot = self._load_snapshot()
        connection = self._find_connection(snapshot, remote_process_id)
        if connection is None:
            return IPCOperationResult(succeeded=False, snapshot=snapshot, reason="IPC connection not found.")
        handle = self._handles.pop(remote_process_id, None)
        if handle is not None:
            try:
                self._backend().disconnect(handle)
            except Exception:
                pass
        connection.connected = False
        self._upsert_connection(snapshot, connection)
        self._save_snapshot(snapshot)
        return IPCOperationResult(succeeded=True, connection=connection, snapshot=snapshot)

    def inspect(self) -> IPCOperationResult:
        snapshot = self._load_snapshot()
        return IPCOperationResult(
            succeeded=True,
            connections=list(snapshot.connections),
            messages=list(snapshot.sent_messages) + list(snapshot.received_messages),
            snapshot=snapshot,
            )

    def _ensure_connection(self, snapshot: IPCSnapshot, connection: IPCConnectionRecord) -> IPCOperationResult:
        if connection.connected and connection.process_id in self._handles:
            return IPCOperationResult(succeeded=True, connection=connection, snapshot=snapshot)
        try:
            handle = self._run_with_retry(lambda: self._backend().connect(connection.endpoint))
            self._handles[connection.process_id] = handle
            connection.connected = True
            connection.last_connected_at = self.now_fn()
            connection.last_error = None
            self._upsert_connection(snapshot, connection)
            self._save_snapshot(snapshot)
            return IPCOperationResult(succeeded=True, connection=connection, snapshot=snapshot)
        except RetryExhaustedError as exc:
            connection.connected = False
            connection.reconnect_attempts += 1
            connection.last_error = exc.failure.reason
            self._upsert_connection(snapshot, connection)
            self._save_snapshot(snapshot)
            return IPCOperationResult(
                succeeded=False,
                connection=connection,
                snapshot=snapshot,
                retry_failure=exc.failure,
                reason=exc.failure.reason,
            )

    def _send_with_reconnect(
        self,
        connection: IPCConnectionRecord,
        message: IPCMessage,
        snapshot: IPCSnapshot,
    ) -> None:
        handle = self._handles[connection.process_id]
        try:
            self._backend().send(handle, message)
            return
        except Exception:
            connection.connected = False
            connection.reconnect_attempts += 1
            self._upsert_connection(snapshot, connection)
            reconnect_result = self._ensure_connection(snapshot, connection)
            if reconnect_result.succeeded is False:
                raise RetryExhaustedError(
                    reconnect_result.retry_failure
                    or RetryFailureResult(reason=reconnect_result.reason or "IPC reconnection failed.")
                )
        self._run_with_retry(lambda: self._backend().send(self._handles[connection.process_id], message))

    def _receive_with_reconnect(
        self,
        connection: IPCConnectionRecord,
        snapshot: IPCSnapshot,
    ) -> list[IPCMessage]:
        handle = self._handles[connection.process_id]
        try:
            return list(self._backend().receive(handle))
        except Exception:
            connection.connected = False
            connection.reconnect_attempts += 1
            self._upsert_connection(snapshot, connection)
            reconnect_result = self._ensure_connection(snapshot, connection)
            if reconnect_result.succeeded is False:
                raise RetryExhaustedError(
                    reconnect_result.retry_failure
                    or RetryFailureResult(reason=reconnect_result.reason or "IPC reconnection failed.")
                )
        return list(self._run_with_retry(lambda: self._backend().receive(self._handles[connection.process_id])))

    def _backend(self):
        if self.channel_type is IPCChannelType.NAMED_PIPE:
            return self.named_pipe_backend or NamedPipeIPCBackend()
        return self.local_socket_backend or LocalSocketIPCBackend()

    def _run_with_retry(self, action: Callable[[], object]) -> object:
        engine = self.retry_engine or ExponentialBackoffRetryEngine[object]()
        configuration = self.retry_configuration or RetryConfiguration(
            max_retry_count=2,
            initial_delay_seconds=0.25,
            backoff_multiplier=2.0,
            max_delay_seconds=2.0,
            exception_rules=[
                RetryExceptionRule(exception_type_name="ConnectionError", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="TimeoutError", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="BrokenPipeError", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="OSError", disposition=RetryDisposition.RETRY),
            ],
        )
        return engine.run(action, configuration=configuration)

    def _load_snapshot(self) -> IPCSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return IPCSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IPCSnapshot(
            connections=[self._deserialize_connection(item) for item in payload.get("connections", [])],
            sent_messages=[self._deserialize_message(item) for item in payload.get("sent_messages", [])],
            received_messages=[self._deserialize_message(item) for item in payload.get("received_messages", [])],
        )

    def _save_snapshot(self, snapshot: IPCSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "connections": [self._serialize_connection(item) for item in snapshot.connections],
            "sent_messages": [self._serialize_message(item) for item in snapshot.sent_messages],
            "received_messages": [self._serialize_message(item) for item in snapshot.received_messages],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _find_connection(self, snapshot: IPCSnapshot, remote_process_id: str) -> IPCConnectionRecord | None:
        return next((item for item in snapshot.connections if item.process_id == remote_process_id), None)

    def _connection_record(self, snapshot: IPCSnapshot, remote_process_id: str, endpoint: str) -> IPCConnectionRecord | None:
        return self._find_connection(snapshot, remote_process_id) or IPCConnectionRecord(
            process_id=remote_process_id,
            endpoint=endpoint,
            channel_type=self.channel_type,
            connected=False,
        )

    def _upsert_connection(self, snapshot: IPCSnapshot, connection: IPCConnectionRecord) -> None:
        snapshot.connections = [item for item in snapshot.connections if item.process_id != connection.process_id] + [connection]

    def _serialize_connection(self, connection: IPCConnectionRecord) -> dict:
        return {
            "process_id": connection.process_id,
            "endpoint": connection.endpoint,
            "channel_type": connection.channel_type.value,
            "connected": connection.connected,
            "last_connected_at": None if connection.last_connected_at is None else connection.last_connected_at.isoformat(),
            "reconnect_attempts": connection.reconnect_attempts,
            "last_error": connection.last_error,
        }

    def _deserialize_connection(self, payload: dict) -> IPCConnectionRecord:
        return IPCConnectionRecord(
            process_id=payload["process_id"],
            endpoint=payload["endpoint"],
            channel_type=IPCChannelType(payload["channel_type"]),
            connected=bool(payload.get("connected", True)),
            last_connected_at=None
            if payload.get("last_connected_at") is None
            else datetime.fromisoformat(payload["last_connected_at"]),
            reconnect_attempts=int(payload.get("reconnect_attempts", 0)),
            last_error=payload.get("last_error"),
        )

    def _serialize_message(self, message: IPCMessage) -> dict:
        return {
            "message_id": message.message_id,
            "sender_id": message.sender_id,
            "recipient_id": message.recipient_id,
            "message_type": message.message_type,
            "payload": dict(message.payload),
            "correlation_id": message.correlation_id,
            "timestamp": message.timestamp.isoformat(),
        }

    def _deserialize_message(self, payload: dict) -> IPCMessage:
        return IPCMessage(
            message_id=payload["message_id"],
            sender_id=payload["sender_id"],
            recipient_id=payload["recipient_id"],
            message_type=payload["message_type"],
            payload=dict(payload.get("payload", {})),
            correlation_id=payload.get("correlation_id", ""),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )

    def _copy_message(self, message: IPCMessage) -> IPCMessage:
        return self._deserialize_message(self._serialize_message(message))
