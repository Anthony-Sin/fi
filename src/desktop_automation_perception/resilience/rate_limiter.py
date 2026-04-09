from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    RateLimiterResult,
    RateLimiterSnapshot,
    RateLimitExecutionRecord,
    RateLimitRequest,
    RateLimitRule,
    RateLimitScope,
    RateLimitWindow,
    RateUsageMetric,
    ThrottlingEvent,
    ThrottlingEventType,
)


@dataclass(slots=True)
class RateLimiter:
    storage_path: str
    rules: list[RateLimitRule] = field(default_factory=list)
    slowdown_callback: Callable[[float], None] | None = None
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def submit_request(self, request: RateLimitRequest) -> RateLimiterResult:
        snapshot = self._load_snapshot()
        snapshot.rules = self._copy_rules(self.rules)
        self._prune_history(snapshot)

        metrics = self._metrics_for_request(snapshot, request)
        blocking_metrics = [metric for metric in metrics if metric.limit_reached]
        if blocking_metrics:
            queued_request = self._copy_request(request)
            snapshot.queued_requests.append(queued_request)
            events = [
                self._record_event(
                    snapshot,
                    request=queued_request,
                    event_type=ThrottlingEventType.QUEUED,
                    metric=metric,
                    detail="Rate limit reached; request queued until the window resets.",
                )
                for metric in blocking_metrics
            ]
            self._save_snapshot(snapshot)
            return RateLimiterResult(
                succeeded=True,
                allowed=False,
                queued=True,
                request=queued_request,
                metrics=metrics,
                events=events,
                reason="Rate limit reached; request queued.",
            )

        delay_seconds = self._calculate_delay(metrics)
        events: list[ThrottlingEvent] = []
        if delay_seconds > 0:
            for metric in metrics:
                if metric.approaching_limit:
                    events.append(
                        self._record_event(
                            snapshot,
                            request=request,
                            event_type=ThrottlingEventType.SLOWED,
                            metric=metric,
                            detail="Approaching rate limit; execution slowed.",
                            delay_seconds=delay_seconds,
                        )
                    )
            if self.slowdown_callback is not None:
                self.slowdown_callback(delay_seconds)

        execution = RateLimitExecutionRecord(
            request_id=request.request_id,
            account_name=request.account_name,
            application_name=request.application_name,
            action_type=request.action_type,
            timestamp=self.now_fn(),
        )
        snapshot.execution_history.append(execution)
        self._save_snapshot(snapshot)
        refreshed_metrics = self._metrics_for_request(snapshot, request)
        return RateLimiterResult(
            succeeded=True,
            allowed=True,
            queued=False,
            request=self._copy_request(request),
            metrics=refreshed_metrics,
            events=events,
            delay_seconds=delay_seconds,
        )

    def resume_queued_requests(self) -> RateLimiterResult:
        snapshot = self._load_snapshot()
        snapshot.rules = self._copy_rules(self.rules)
        self._prune_history(snapshot)

        resumed: list[RateLimitRequest] = []
        events: list[ThrottlingEvent] = []
        remaining: list[RateLimitRequest] = []

        for request in snapshot.queued_requests:
            metrics = self._metrics_for_request(snapshot, request)
            if any(metric.limit_reached for metric in metrics):
                remaining.append(request)
                continue

            snapshot.execution_history.append(
                RateLimitExecutionRecord(
                    request_id=request.request_id,
                    account_name=request.account_name,
                    application_name=request.application_name,
                    action_type=request.action_type,
                    timestamp=self.now_fn(),
                )
            )
            resumed.append(self._copy_request(request))
            for metric in metrics:
                events.append(
                    self._record_event(
                        snapshot,
                        request=request,
                        event_type=ThrottlingEventType.RESUMED,
                        metric=metric,
                        detail="Queued request resumed after rate window capacity became available.",
                    )
                )

        snapshot.queued_requests = remaining
        self._save_snapshot(snapshot)
        return RateLimiterResult(
            succeeded=True,
            allowed=bool(resumed),
            queued=bool(remaining),
            requests=resumed,
            events=events,
            metrics=self.get_usage_metrics().metrics,
            reason=None if resumed else "No queued requests were eligible to resume.",
        )

    def get_usage_metrics(
        self,
        request: RateLimitRequest | None = None,
    ) -> RateLimiterResult:
        snapshot = self._load_snapshot()
        snapshot.rules = self._copy_rules(self.rules)
        self._prune_history(snapshot)
        metrics = self._metrics_for_request(snapshot, request) if request is not None else self._all_metrics(snapshot)
        self._save_snapshot(snapshot)
        return RateLimiterResult(succeeded=True, metrics=metrics)

    def inspect_queue(self) -> RateLimiterResult:
        snapshot = self._load_snapshot()
        snapshot.rules = self._copy_rules(self.rules)
        return RateLimiterResult(
            succeeded=True,
            queued=bool(snapshot.queued_requests),
            requests=[self._copy_request(item) for item in snapshot.queued_requests],
            events=list(snapshot.throttling_events),
        )

    def _metrics_for_request(
        self,
        snapshot: RateLimiterSnapshot,
        request: RateLimitRequest,
    ) -> list[RateUsageMetric]:
        metrics: list[RateUsageMetric] = []
        for rule in snapshot.rules:
            matched_key = self._request_scope_value(request, rule.scope)
            if matched_key is None or matched_key.casefold() != rule.key.casefold():
                continue
            metrics.append(self._build_metric(snapshot.execution_history, rule, matched_key))
        return metrics

    def _all_metrics(self, snapshot: RateLimiterSnapshot) -> list[RateUsageMetric]:
        metrics: list[RateUsageMetric] = []
        for rule in snapshot.rules:
            metrics.append(self._build_metric(snapshot.execution_history, rule, rule.key))
        return metrics

    def _build_metric(
        self,
        execution_history: list[RateLimitExecutionRecord],
        rule: RateLimitRule,
        key: str,
    ) -> RateUsageMetric:
        now = self.now_fn()
        window_seconds = self._window_seconds(rule)
        window_start = now - timedelta(seconds=window_seconds)
        matching = [
            record
            for record in execution_history
            if record.timestamp >= window_start and self._record_scope_value(record, rule.scope) == key
        ]
        used_count = len(matching)
        remaining = max(rule.limit - used_count, 0)
        approaching = used_count >= max(1, int(rule.limit * rule.slowdown_threshold_ratio)) and used_count < rule.limit
        latest_timestamp = max((record.timestamp for record in matching), default=None)
        reset_at = None if latest_timestamp is None else latest_timestamp + timedelta(seconds=window_seconds)
        return RateUsageMetric(
            scope=rule.scope,
            key=key,
            window_seconds=window_seconds,
            limit=rule.limit,
            used_count=used_count,
            remaining_count=remaining,
            approaching_limit=approaching,
            limit_reached=used_count >= rule.limit,
            reset_at=reset_at,
        )

    def _calculate_delay(self, metrics: list[RateUsageMetric]) -> float:
        if not metrics:
            return 0.0
        delay = 0.0
        for metric in metrics:
            if not metric.approaching_limit:
                continue
            rule = self._matching_rule(metric.scope, metric.key)
            if rule is None:
                continue
            usage_ratio = metric.used_count / max(metric.limit, 1)
            scale = 1.0 + max(0.0, usage_ratio - rule.slowdown_threshold_ratio)
            delay = max(delay, round(rule.slowdown_delay_seconds * scale, 4))
        return delay

    def _matching_rule(self, scope: RateLimitScope, key: str) -> RateLimitRule | None:
        for rule in self.rules:
            if rule.scope is scope and rule.key.casefold() == key.casefold():
                return rule
        return None

    def _request_scope_value(self, request: RateLimitRequest, scope: RateLimitScope) -> str | None:
        if scope is RateLimitScope.ACCOUNT:
            return request.account_name
        if scope is RateLimitScope.APPLICATION:
            return request.application_name
        if scope is RateLimitScope.ACTION_TYPE:
            return request.action_type
        return None

    def _record_scope_value(self, record: RateLimitExecutionRecord, scope: RateLimitScope) -> str | None:
        if scope is RateLimitScope.ACCOUNT:
            return record.account_name
        if scope is RateLimitScope.APPLICATION:
            return record.application_name
        if scope is RateLimitScope.ACTION_TYPE:
            return record.action_type
        return None

    def _window_seconds(self, rule: RateLimitRule) -> float:
        if rule.window_seconds is not None:
            return float(rule.window_seconds)
        if rule.window is RateLimitWindow.MINUTE:
            return 60.0
        if rule.window is RateLimitWindow.HOUR:
            return 3600.0
        return 60.0

    def _record_event(
        self,
        snapshot: RateLimiterSnapshot,
        *,
        request: RateLimitRequest,
        event_type: ThrottlingEventType,
        metric: RateUsageMetric,
        detail: str,
        delay_seconds: float = 0.0,
    ) -> ThrottlingEvent:
        event = ThrottlingEvent(
            event_type=event_type,
            request_id=request.request_id,
            timestamp=self.now_fn(),
            scope=metric.scope,
            key=metric.key,
            detail=detail,
            delay_seconds=delay_seconds,
        )
        snapshot.throttling_events.append(event)
        return event

    def _prune_history(self, snapshot: RateLimiterSnapshot) -> None:
        if not snapshot.execution_history:
            return
        if snapshot.rules:
            longest_window = max(self._window_seconds(rule) for rule in snapshot.rules)
        else:
            longest_window = 3600.0
        cutoff = self.now_fn() - timedelta(seconds=longest_window)
        snapshot.execution_history = [record for record in snapshot.execution_history if record.timestamp >= cutoff]

    def _load_snapshot(self) -> RateLimiterSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return RateLimiterSnapshot(rules=self._copy_rules(self.rules))
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RateLimiterSnapshot(
            rules=[self._deserialize_rule(item) for item in payload.get("rules", [])],
            execution_history=[self._deserialize_execution(item) for item in payload.get("execution_history", [])],
            queued_requests=[self._deserialize_request(item) for item in payload.get("queued_requests", [])],
            throttling_events=[self._deserialize_event(item) for item in payload.get("throttling_events", [])],
        )

    def _save_snapshot(self, snapshot: RateLimiterSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rules": [self._serialize_rule(item) for item in snapshot.rules],
            "execution_history": [self._serialize_execution(item) for item in snapshot.execution_history],
            "queued_requests": [self._serialize_request(item) for item in snapshot.queued_requests],
            "throttling_events": [self._serialize_event(item) for item in snapshot.throttling_events],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_rules(self, rules: list[RateLimitRule]) -> list[RateLimitRule]:
        return [self._deserialize_rule(self._serialize_rule(rule)) for rule in rules]

    def _copy_request(self, request: RateLimitRequest) -> RateLimitRequest:
        return self._deserialize_request(self._serialize_request(request))

    def _serialize_rule(self, rule: RateLimitRule) -> dict:
        return {
            "scope": rule.scope.value,
            "key": rule.key,
            "limit": rule.limit,
            "window": rule.window.value,
            "window_seconds": rule.window_seconds,
            "slowdown_threshold_ratio": rule.slowdown_threshold_ratio,
            "slowdown_delay_seconds": rule.slowdown_delay_seconds,
        }

    def _deserialize_rule(self, payload: dict) -> RateLimitRule:
        return RateLimitRule(
            scope=RateLimitScope(payload["scope"]),
            key=payload["key"],
            limit=int(payload["limit"]),
            window=RateLimitWindow(payload.get("window", RateLimitWindow.MINUTE.value)),
            window_seconds=None if payload.get("window_seconds") is None else float(payload["window_seconds"]),
            slowdown_threshold_ratio=float(payload.get("slowdown_threshold_ratio", 0.8)),
            slowdown_delay_seconds=float(payload.get("slowdown_delay_seconds", 1.0)),
        )

    def _serialize_request(self, request: RateLimitRequest) -> dict:
        return {
            "request_id": request.request_id,
            "account_name": request.account_name,
            "application_name": request.application_name,
            "action_type": request.action_type,
            "payload": dict(request.payload),
            "submitted_at": request.submitted_at.isoformat(),
        }

    def _deserialize_request(self, payload: dict) -> RateLimitRequest:
        return RateLimitRequest(
            request_id=payload["request_id"],
            account_name=payload.get("account_name"),
            application_name=payload.get("application_name"),
            action_type=payload.get("action_type"),
            payload=dict(payload.get("payload", {})),
            submitted_at=datetime.fromisoformat(payload["submitted_at"]),
        )

    def _serialize_execution(self, record: RateLimitExecutionRecord) -> dict:
        return {
            "request_id": record.request_id,
            "account_name": record.account_name,
            "application_name": record.application_name,
            "action_type": record.action_type,
            "timestamp": record.timestamp.isoformat(),
        }

    def _deserialize_execution(self, payload: dict) -> RateLimitExecutionRecord:
        return RateLimitExecutionRecord(
            request_id=payload["request_id"],
            account_name=payload.get("account_name"),
            application_name=payload.get("application_name"),
            action_type=payload.get("action_type"),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )

    def _serialize_event(self, event: ThrottlingEvent) -> dict:
        return {
            "event_type": event.event_type.value,
            "request_id": event.request_id,
            "timestamp": event.timestamp.isoformat(),
            "scope": event.scope.value,
            "key": event.key,
            "detail": event.detail,
            "delay_seconds": event.delay_seconds,
        }

    def _deserialize_event(self, payload: dict) -> ThrottlingEvent:
        return ThrottlingEvent(
            event_type=ThrottlingEventType(payload["event_type"]),
            request_id=payload["request_id"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            scope=RateLimitScope(payload["scope"]),
            key=payload["key"],
            detail=payload.get("detail"),
            delay_seconds=float(payload.get("delay_seconds", 0.0)),
        )
