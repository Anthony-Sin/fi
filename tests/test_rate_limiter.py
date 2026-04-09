from datetime import datetime, timedelta, timezone
from pathlib import Path

from desktop_automation_agent.models import (
    RateLimitRequest,
    RateLimitRule,
    RateLimitScope,
    RateLimitWindow,
    ThrottlingEventType,
)
from desktop_automation_agent.rate_limiter import RateLimiter


def make_request(
    request_id: str,
    *,
    account: str | None = None,
    application: str | None = None,
    action_type: str | None = None,
) -> RateLimitRequest:
    return RateLimitRequest(
        request_id=request_id,
        account_name=account,
        application_name=application,
        action_type=action_type,
        payload={"request_id": request_id},
        submitted_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
    )


def test_rate_limiter_enforces_per_account_limit_and_queues_when_reached(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate_account.json"),
        rules=[
            RateLimitRule(scope=RateLimitScope.ACCOUNT, key="acct-a", limit=2, window=RateLimitWindow.MINUTE)
        ],
        now_fn=lambda: current_time["value"],
    )

    first = limiter.submit_request(make_request("r1", account="acct-a"))
    second = limiter.submit_request(make_request("r2", account="acct-a"))
    third = limiter.submit_request(make_request("r3", account="acct-a"))
    queued = limiter.inspect_queue()

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.queued is True
    assert [request.request_id for request in queued.requests] == ["r3"]
    assert any(event.event_type is ThrottlingEventType.QUEUED for event in third.events)


def test_rate_limiter_slows_execution_when_limit_is_approached(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}
    delays = []
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate_slowdown.json"),
        rules=[
            RateLimitRule(
                scope=RateLimitScope.APPLICATION,
                key="chatgpt",
                limit=5,
                window=RateLimitWindow.MINUTE,
                slowdown_threshold_ratio=0.6,
                slowdown_delay_seconds=2.0,
            )
        ],
        slowdown_callback=delays.append,
        now_fn=lambda: current_time["value"],
    )

    limiter.submit_request(make_request("r1", application="chatgpt"))
    limiter.submit_request(make_request("r2", application="chatgpt"))
    limiter.submit_request(make_request("r3", application="chatgpt"))
    slowed = limiter.submit_request(make_request("r4", application="chatgpt"))

    assert slowed.allowed is True
    assert slowed.delay_seconds > 0.0
    assert delays == [slowed.delay_seconds]
    assert any(event.event_type is ThrottlingEventType.SLOWED for event in slowed.events)


def test_rate_limiter_resumes_queued_requests_after_window_reset(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate_resume.json"),
        rules=[
            RateLimitRule(scope=RateLimitScope.ACTION_TYPE, key="submit", limit=1, window=RateLimitWindow.MINUTE)
        ],
        now_fn=lambda: current_time["value"],
    )

    limiter.submit_request(make_request("r1", action_type="submit"))
    blocked = limiter.submit_request(make_request("r2", action_type="submit"))
    current_time["value"] = current_time["value"] + timedelta(seconds=61)
    resumed = limiter.resume_queued_requests()

    assert blocked.queued is True
    assert [request.request_id for request in resumed.requests] == ["r2"]
    assert any(event.event_type is ThrottlingEventType.RESUMED for event in resumed.events)
    assert limiter.inspect_queue().requests == []


def test_rate_limiter_reports_current_usage_metrics_for_all_scopes(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate_metrics.json"),
        rules=[
            RateLimitRule(scope=RateLimitScope.ACCOUNT, key="acct-a", limit=3, window=RateLimitWindow.MINUTE),
            RateLimitRule(scope=RateLimitScope.APPLICATION, key="chrome", limit=10, window=RateLimitWindow.HOUR),
            RateLimitRule(scope=RateLimitScope.ACTION_TYPE, key="click", limit=4, window=RateLimitWindow.MINUTE),
        ],
        now_fn=lambda: current_time["value"],
    )
    limiter.submit_request(make_request("r1", account="acct-a", application="chrome", action_type="click"))
    limiter.submit_request(make_request("r2", account="acct-a", application="chrome", action_type="click"))

    metrics = limiter.get_usage_metrics().metrics

    account_metric = next(metric for metric in metrics if metric.scope is RateLimitScope.ACCOUNT)
    application_metric = next(metric for metric in metrics if metric.scope is RateLimitScope.APPLICATION)
    action_metric = next(metric for metric in metrics if metric.scope is RateLimitScope.ACTION_TYPE)

    assert account_metric.used_count == 2
    assert account_metric.remaining_count == 1
    assert application_metric.used_count == 2
    assert application_metric.window_seconds == 3600.0
    assert action_metric.used_count == 2


def test_rate_limiter_supports_custom_window_seconds(tmp_path):
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate_custom.json"),
        rules=[
            RateLimitRule(
                scope=RateLimitScope.ACCOUNT,
                key="acct-a",
                limit=1,
                window=RateLimitWindow.CUSTOM,
                window_seconds=10.0,
            )
        ],
        now_fn=lambda: current_time["value"],
    )

    first = limiter.submit_request(make_request("r1", account="acct-a"))
    second = limiter.submit_request(make_request("r2", account="acct-a"))
    current_time["value"] = current_time["value"] + timedelta(seconds=11)
    third = limiter.submit_request(make_request("r3", account="acct-a"))

    assert first.allowed is True
    assert second.queued is True
    assert third.allowed is True
