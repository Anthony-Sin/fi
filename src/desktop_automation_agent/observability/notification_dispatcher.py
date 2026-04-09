from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

from desktop_automation_agent.models import (
    NotificationChannel,
    NotificationChannelType,
    NotificationDispatchRecord,
    NotificationDispatcherResult,
    NotificationDispatcherSnapshot,
    NotificationEventType,
    NotificationMessage,
    RateLimitRequest,
)


@dataclass(slots=True)
class NotificationDispatcher:
    storage_path: str
    channels: list[NotificationChannel]
    transport_backend: object
    rate_limiter: object | None = None
    audit_logger: object | None = None
    batch_size: int = 5
    now_fn: Callable[[], object] | None = None

    def dispatch(
        self,
        *,
        workflow_id: str,
        event_type: NotificationEventType,
        description: str,
        context_data: dict | None = None,
        step_name: str = "notification_dispatch",
    ) -> NotificationDispatcherResult:
        snapshot = self._load_snapshot()
        notification = NotificationMessage(
            notification_id=str(uuid4()),
            workflow_id=workflow_id,
            event_type=event_type,
            description=description,
            context_data={} if context_data is None else dict(context_data),
        )

        immediate_records: list[NotificationDispatchRecord] = []
        should_queue = False
        for channel in self.channels:
            if not channel.enabled:
                continue
            if channel.batch_non_urgent and event_type not in channel.urgent_event_types:
                should_queue = True
                continue
            record = self._dispatch_to_channel(channel, [notification])
            snapshot.dispatch_history.append(record)
            immediate_records.append(record)

        queued = False
        if should_queue:
            snapshot.queued_notifications.append(self._copy_notification(notification))
            queued = True

        self._save_snapshot(snapshot)
        self._log_audit(notification, immediate_records, step_name=step_name)
        return NotificationDispatcherResult(
            succeeded=bool(immediate_records) or queued,
            notification=notification,
            dispatch_records=immediate_records,
            snapshot=snapshot,
            reason=None if (immediate_records or queued) else "No enabled notification channels were available.",
        )

    def flush_batched(self, *, step_name: str = "notification_batch_flush") -> NotificationDispatcherResult:
        snapshot = self._load_snapshot()
        if not snapshot.queued_notifications:
            return NotificationDispatcherResult(succeeded=True, snapshot=snapshot)

        queued_notifications = [self._copy_notification(item) for item in snapshot.queued_notifications]
        flushed_records: list[NotificationDispatchRecord] = []
        batching_channels = [channel for channel in self.channels if channel.enabled and channel.batch_non_urgent]

        for channel in batching_channels:
            remaining = list(queued_notifications)
            while remaining:
                batch = [self._copy_notification(item) for item in remaining[: self.batch_size]]
                remaining = remaining[self.batch_size :]
                record = self._dispatch_to_channel(channel, batch, batched=True)
                snapshot.dispatch_history.append(record)
                flushed_records.append(record)

        snapshot.queued_notifications = []
        if batching_channels and any(not item.succeeded for item in flushed_records):
            snapshot.queued_notifications = queued_notifications

        self._save_snapshot(snapshot)
        return NotificationDispatcherResult(
            succeeded=bool(flushed_records) and not snapshot.queued_notifications if batching_channels else True,
            notifications=[self._copy_notification(item) for item in snapshot.queued_notifications],
            dispatch_records=flushed_records,
            snapshot=snapshot,
        )

    def inspect(self) -> NotificationDispatcherResult:
        snapshot = self._load_snapshot()
        return NotificationDispatcherResult(
            succeeded=True,
            notifications=[self._copy_notification(item) for item in snapshot.queued_notifications],
            dispatch_records=list(snapshot.dispatch_history),
            snapshot=snapshot,
        )

    def _dispatch_to_channel(
        self,
        channel: NotificationChannel,
        notifications: list[NotificationMessage],
        *,
        batched: bool = False,
    ) -> NotificationDispatchRecord:
        if self.rate_limiter is not None:
            limit_result = self.rate_limiter.submit_request(
                RateLimitRequest(
                    request_id=f"{channel.channel_id}-{uuid4().hex}",
                    application_name=channel.channel_type.value,
                    action_type="notification_dispatch",
                    payload={"channel_id": channel.channel_id},
                )
            )
            if not getattr(limit_result, "allowed", True):
                return NotificationDispatchRecord(
                    notification_id=notifications[0].notification_id,
                    channel_id=channel.channel_id,
                    channel_type=channel.channel_type,
                    status_code=None,
                    batched=batched,
                    succeeded=False,
                    detail="Notification dispatch was rate-limited.",
                )

        payload = self._build_payload(channel, notifications, batched=batched)
        try:
            response = self.transport_backend.send(
                endpoint=channel.endpoint,
                headers=dict(channel.headers),
                payload=payload,
            )
            status_code = getattr(response, "status_code", 200)
            succeeded = 200 <= int(status_code) < 300
            detail = None if succeeded else getattr(response, "detail", f"Dispatch failed with status {status_code}.")
            return NotificationDispatchRecord(
                notification_id=notifications[0].notification_id,
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                status_code=int(status_code),
                batched=batched,
                succeeded=succeeded,
                detail=detail,
            )
        except Exception as exc:
            return NotificationDispatchRecord(
                notification_id=notifications[0].notification_id,
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                status_code=None,
                batched=batched,
                succeeded=False,
                detail=str(exc),
            )

    def _build_payload(
        self,
        channel: NotificationChannel,
        notifications: list[NotificationMessage],
        *,
        batched: bool,
    ) -> dict:
        if channel.channel_type is NotificationChannelType.EMAIL:
            subject = self._email_subject(notifications, batched=batched)
            body = self._email_body(notifications, batched=batched)
            return {"subject": subject, "body": body}
        if channel.channel_type is NotificationChannelType.SLACK_WEBHOOK:
            return {"text": self._slack_text(notifications, batched=batched)}
        return {
            "batched": batched,
            "notifications": [self._serialize_notification(notification) for notification in notifications],
        }

    def _email_subject(self, notifications: list[NotificationMessage], *, batched: bool) -> str:
        first = notifications[0]
        if batched:
            return f"[Workflow {first.workflow_id}] {len(notifications)} notification updates"
        return f"[Workflow {first.workflow_id}] {first.event_type.value.replace('_', ' ').title()}"

    def _email_body(self, notifications: list[NotificationMessage], *, batched: bool) -> str:
        lines = []
        for notification in notifications:
            lines.append(f"Workflow: {notification.workflow_id}")
            lines.append(f"Event: {notification.event_type.value}")
            lines.append(f"Description: {notification.description}")
            if notification.context_data:
                lines.append(f"Context: {json.dumps(notification.context_data, sort_keys=True)}")
            lines.append("")
        if batched:
            lines.insert(0, f"Batched notifications: {len(notifications)}")
            lines.insert(1, "")
        return "\n".join(lines).strip()

    def _slack_text(self, notifications: list[NotificationMessage], *, batched: bool) -> str:
        if batched:
            header = f"{len(notifications)} workflow updates"
            details = "\n".join(
                f"- {item.workflow_id}: {item.event_type.value} - {item.description}"
                for item in notifications
            )
            return f"{header}\n{details}"
        notification = notifications[0]
        return (
            f"Workflow `{notification.workflow_id}`\n"
            f"Event: `{notification.event_type.value}`\n"
            f"{notification.description}"
        )

    def _serialize_notification(self, notification: NotificationMessage) -> dict:
        return {
            "notification_id": notification.notification_id,
            "workflow_id": notification.workflow_id,
            "event_type": notification.event_type.value,
            "description": notification.description,
            "context_data": dict(notification.context_data),
            "created_at": notification.created_at.isoformat(),
        }

    def _deserialize_notification(self, payload: dict) -> NotificationMessage:
        from datetime import datetime

        return NotificationMessage(
            notification_id=payload["notification_id"],
            workflow_id=payload["workflow_id"],
            event_type=NotificationEventType(payload["event_type"]),
            description=payload["description"],
            context_data=dict(payload.get("context_data", {})),
            created_at=datetime.fromisoformat(payload["created_at"]),
        )

    def _serialize_record(self, record: NotificationDispatchRecord) -> dict:
        return {
            "notification_id": record.notification_id,
            "channel_id": record.channel_id,
            "channel_type": record.channel_type.value,
            "status_code": record.status_code,
            "batched": record.batched,
            "succeeded": record.succeeded,
            "detail": record.detail,
            "dispatched_at": record.dispatched_at.isoformat(),
        }

    def _deserialize_record(self, payload: dict) -> NotificationDispatchRecord:
        from datetime import datetime

        return NotificationDispatchRecord(
            notification_id=payload["notification_id"],
            channel_id=payload["channel_id"],
            channel_type=NotificationChannelType(payload["channel_type"]),
            status_code=None if payload.get("status_code") is None else int(payload["status_code"]),
            batched=bool(payload.get("batched", False)),
            succeeded=bool(payload.get("succeeded", False)),
            detail=payload.get("detail"),
            dispatched_at=datetime.fromisoformat(payload["dispatched_at"]),
        )

    def _load_snapshot(self) -> NotificationDispatcherSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return NotificationDispatcherSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return NotificationDispatcherSnapshot(
            queued_notifications=[self._deserialize_notification(item) for item in payload.get("queued_notifications", [])],
            dispatch_history=[self._deserialize_record(item) for item in payload.get("dispatch_history", [])],
        )

    def _save_snapshot(self, snapshot: NotificationDispatcherSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "queued_notifications": [self._serialize_notification(item) for item in snapshot.queued_notifications],
            "dispatch_history": [self._serialize_record(item) for item in snapshot.dispatch_history],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_notification(self, notification: NotificationMessage) -> NotificationMessage:
        return self._deserialize_notification(self._serialize_notification(notification))

    def _log_audit(
        self,
        notification: NotificationMessage,
        records: list[NotificationDispatchRecord],
        *,
        step_name: str,
    ) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log_action(
            workflow_id=notification.workflow_id,
            step_name=step_name,
            action_type="notification_dispatch",
            target_element=notification.event_type.value,
            input_data={
                "description": notification.description,
                "context_data": notification.context_data,
            },
            output_data={
                "channels": [
                    {
                        "channel_id": item.channel_id,
                        "status_code": item.status_code,
                        "succeeded": item.succeeded,
                        "batched": item.batched,
                        "detail": item.detail,
                    }
                    for item in records
                ]
            },
            success=all(item.succeeded for item in records) if records else True,
            timestamp=notification.created_at,
        )
