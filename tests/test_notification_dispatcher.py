from datetime import datetime, timezone

from desktop_automation_agent.models import (
    NotificationChannel,
    NotificationChannelType,
    NotificationEventType,
    RateLimiterResult,
)
from desktop_automation_agent.notification_dispatcher import NotificationDispatcher


class FakeTransportBackend:
    def __init__(self):
        self.calls = []

    def send(self, *, endpoint, headers, payload):
        self.calls.append({"endpoint": endpoint, "headers": dict(headers), "payload": payload})
        return type("Response", (), {"status_code": 200, "detail": None})()


class FakeRateLimiter:
    def __init__(self, blocked_applications=None):
        self.blocked_applications = set(blocked_applications or [])
        self.requests = []

    def submit_request(self, request):
        self.requests.append(request)
        blocked = request.application_name in self.blocked_applications
        return RateLimiterResult(succeeded=True, allowed=not blocked, queued=blocked, request=request)


class FakeAuditLogger:
    def __init__(self):
        self.calls = []

    def log_action(self, **kwargs):
        self.calls.append(kwargs)
        return type("Result", (), {"succeeded": True})()


def make_channels():
    return [
        NotificationChannel(
            channel_id="email-main",
            channel_type=NotificationChannelType.EMAIL,
            endpoint="mailto://ops@example.com",
        ),
        NotificationChannel(
            channel_id="slack-main",
            channel_type=NotificationChannelType.SLACK_WEBHOOK,
            endpoint="https://hooks.slack.test",
            headers={"Authorization": "Bearer secret"},
        ),
        NotificationChannel(
            channel_id="generic-main",
            channel_type=NotificationChannelType.GENERIC_WEBHOOK,
            endpoint="https://hooks.generic.test",
        ),
    ]


def test_notification_dispatcher_sends_urgent_notifications_immediately(tmp_path):
    transport = FakeTransportBackend()
    audit_logger = FakeAuditLogger()
    dispatcher = NotificationDispatcher(
        storage_path=str(tmp_path / "notifications.json"),
        channels=make_channels(),
        transport_backend=transport,
        audit_logger=audit_logger,
    )

    result = dispatcher.dispatch(
        workflow_id="wf-1",
        event_type=NotificationEventType.STEP_FAILURE,
        description="Step 3 failed while uploading a file.",
        context_data={"step_id": "step-3", "account": "acct-a"},
    )

    assert result.succeeded is True
    assert len(result.dispatch_records) == 3
    assert len(transport.calls) == 3
    assert transport.calls[0]["payload"]["subject"] == "[Workflow wf-1] Step Failure"
    assert "Step 3 failed while uploading a file." in transport.calls[1]["payload"]["text"]
    assert audit_logger.calls[0]["action_type"] == "notification_dispatch"


def test_notification_dispatcher_batches_non_urgent_notifications_and_flushes(tmp_path):
    transport = FakeTransportBackend()
    dispatcher = NotificationDispatcher(
        storage_path=str(tmp_path / "batched_notifications.json"),
        channels=make_channels(),
        transport_backend=transport,
        batch_size=2,
    )

    first = dispatcher.dispatch(
        workflow_id="wf-2",
        event_type=NotificationEventType.COMPLETION,
        description="Workflow finished successfully.",
        context_data={"duration_seconds": 14.2},
    )
    second = dispatcher.dispatch(
        workflow_id="wf-3",
        event_type=NotificationEventType.ANOMALY,
        description="Resource usage exceeded the baseline.",
        context_data={"cpu_percent": 92.0},
    )
    inspect_before = dispatcher.inspect()
    flushed = dispatcher.flush_batched()
    inspect_after = dispatcher.inspect()

    assert first.succeeded is True
    assert second.succeeded is True
    assert first.dispatch_records == []
    assert len(inspect_before.notifications) == 2
    assert len(flushed.dispatch_records) == 3
    assert inspect_after.notifications == []
    assert transport.calls[0]["payload"]["subject"] == "[Workflow wf-2] 2 notification updates"


def test_notification_dispatcher_respects_channel_specific_rate_limits(tmp_path):
    transport = FakeTransportBackend()
    rate_limiter = FakeRateLimiter(blocked_applications={NotificationChannelType.SLACK_WEBHOOK.value})
    dispatcher = NotificationDispatcher(
        storage_path=str(tmp_path / "rate_limited_notifications.json"),
        channels=make_channels(),
        transport_backend=transport,
        rate_limiter=rate_limiter,
    )

    result = dispatcher.dispatch(
        workflow_id="wf-4",
        event_type=NotificationEventType.ESCALATION,
        description="Escalation requested after repeated retries.",
        context_data={"retry_count": 5},
    )

    slack_record = next(record for record in result.dispatch_records if record.channel_id == "slack-main")
    email_record = next(record for record in result.dispatch_records if record.channel_id == "email-main")

    assert result.succeeded is True
    assert slack_record.succeeded is False
    assert slack_record.detail == "Notification dispatch was rate-limited."
    assert email_record.succeeded is True
    assert len(transport.calls) == 2


def test_notification_dispatcher_formats_generic_webhook_payload_with_context(tmp_path):
    transport = FakeTransportBackend()
    dispatcher = NotificationDispatcher(
        storage_path=str(tmp_path / "generic_notifications.json"),
        channels=[
            NotificationChannel(
                channel_id="generic-main",
                channel_type=NotificationChannelType.GENERIC_WEBHOOK,
                endpoint="https://hooks.generic.test",
                batch_non_urgent=False,
            )
        ],
        transport_backend=transport,
    )

    result = dispatcher.dispatch(
        workflow_id="wf-5",
        event_type=NotificationEventType.ANOMALY,
        description="UI structure changed unexpectedly.",
        context_data={"step_id": "step-7", "signature": "new-layout"},
    )

    payload = transport.calls[0]["payload"]

    assert result.succeeded is True
    assert payload["batched"] is False
    assert payload["notifications"][0]["workflow_id"] == "wf-5"
    assert payload["notifications"][0]["context_data"]["signature"] == "new-layout"
