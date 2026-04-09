from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable, Generic, TypeVar

from desktop_automation_agent.models import (
    RetryAttemptLog,
    RetryConfiguration,
    RetryDisposition,
    RetryExceptionRule,
    RetryFailureResult,
)


T = TypeVar("T")


class RetryExhaustedError(Exception):
    def __init__(self, failure: RetryFailureResult):
        self.failure = failure
        super().__init__(failure.reason or "Retry attempts exhausted.")


@dataclass(slots=True)
class ExponentialBackoffRetryEngine(Generic[T]):
    sleep_fn: Callable[[float], None] = sleep

    def run(
        self,
        action: Callable[[], T],
        *,
        configuration: RetryConfiguration | None = None,
    ) -> T:
        config = configuration or RetryConfiguration()
        attempts: list[RetryAttemptLog] = []
        delay = max(0.0, config.initial_delay_seconds)
        total_attempts = max(1, config.max_retry_count + 1)

        for attempt_number in range(1, total_attempts + 1):
            try:
                return action()
            except Exception as exc:
                disposition = self._classify_exception(exc, config)
                applied_delay = 0.0
                if disposition is RetryDisposition.RETRY and attempt_number < total_attempts:
                    applied_delay = min(delay, config.max_delay_seconds)
                attempts.append(
                    RetryAttemptLog(
                        attempt_number=attempt_number,
                        delay_seconds=applied_delay,
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                        disposition=disposition,
                    )
                )

                if disposition is RetryDisposition.FAIL or attempt_number >= total_attempts:
                    failure = RetryFailureResult(
                        attempts=attempts,
                        final_exception_type=type(exc).__name__,
                        final_exception_message=str(exc),
                        reason="Retry attempts exhausted." if disposition is RetryDisposition.RETRY else "Encountered a non-retriable exception.",
                    )
                    raise RetryExhaustedError(failure) from exc

                self.sleep_fn(applied_delay)
                delay = min(max(applied_delay * config.backoff_multiplier, applied_delay), config.max_delay_seconds)

        failure = RetryFailureResult(
            attempts=attempts,
            final_exception_type=None,
            final_exception_message=None,
            reason="Retry engine terminated unexpectedly.",
        )
        raise RetryExhaustedError(failure)

    def _classify_exception(
        self,
        exc: Exception,
        configuration: RetryConfiguration,
    ) -> RetryDisposition:
        for rule in configuration.exception_rules:
            if self._matches_rule(exc, rule):
                return rule.disposition
        return configuration.default_disposition

    def _matches_rule(
        self,
        exc: Exception,
        rule: RetryExceptionRule,
    ) -> bool:
        if type(exc).__name__ != rule.exception_type_name:
            return False
        if rule.message_contains is not None and rule.message_contains not in str(exc):
            return False
        return True
