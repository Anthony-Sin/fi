from desktop_automation_perception.models import (
    RetryConfiguration,
    RetryDisposition,
    RetryExceptionRule,
)
from desktop_automation_perception.retry_engine import (
    ExponentialBackoffRetryEngine,
    RetryExhaustedError,
)


def test_retry_engine_retries_until_success_with_exponential_backoff():
    sleeps = []
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    engine = ExponentialBackoffRetryEngine(sleep_fn=sleeps.append)
    result = engine.run(
        flaky,
        configuration=RetryConfiguration(
            max_retry_count=3,
            initial_delay_seconds=0.5,
            backoff_multiplier=2.0,
            max_delay_seconds=5.0,
        ),
    )

    assert result == "ok"
    assert sleeps == [0.5, 1.0]


def test_retry_engine_stops_on_non_retriable_exception_rule():
    engine = ExponentialBackoffRetryEngine(sleep_fn=lambda _: None)

    try:
        engine.run(
            lambda: (_ for _ in ()).throw(ValueError("bad input")),
            configuration=RetryConfiguration(
                max_retry_count=5,
                exception_rules=[
                    RetryExceptionRule(
                        exception_type_name="ValueError",
                        disposition=RetryDisposition.FAIL,
                    )
                ],
            ),
        )
        raise AssertionError("Expected RetryExhaustedError")
    except RetryExhaustedError as exc:
        assert exc.failure.final_exception_type == "ValueError"
        assert exc.failure.reason == "Encountered a non-retriable exception."
        assert len(exc.failure.attempts) == 1
        assert exc.failure.attempts[0].disposition is RetryDisposition.FAIL


def test_retry_engine_caps_delay_and_records_history():
    sleeps = []
    engine = ExponentialBackoffRetryEngine(sleep_fn=sleeps.append)
    attempts = {"count": 0}

    def always_fails():
        attempts["count"] += 1
        raise RuntimeError(f"fail-{attempts['count']}")

    try:
        engine.run(
            always_fails,
            configuration=RetryConfiguration(
                max_retry_count=3,
                initial_delay_seconds=2.0,
                backoff_multiplier=3.0,
                max_delay_seconds=4.0,
            ),
        )
        raise AssertionError("Expected RetryExhaustedError")
    except RetryExhaustedError as exc:
        assert sleeps == [2.0, 4.0, 4.0]
        assert len(exc.failure.attempts) == 4
        assert exc.failure.attempts[-1].delay_seconds == 0.0
        assert exc.failure.final_exception_message == "fail-4"


def test_retry_engine_uses_message_based_rule_matching():
    sleeps = []
    engine = ExponentialBackoffRetryEngine(sleep_fn=sleeps.append)
    attempts = {"count": 0}

    def action():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary network issue")
        raise RuntimeError("fatal syntax issue")

    try:
        engine.run(
            action,
            configuration=RetryConfiguration(
                max_retry_count=3,
                exception_rules=[
                    RetryExceptionRule(
                        exception_type_name="RuntimeError",
                        disposition=RetryDisposition.RETRY,
                        message_contains="temporary",
                    ),
                    RetryExceptionRule(
                        exception_type_name="RuntimeError",
                        disposition=RetryDisposition.FAIL,
                        message_contains="fatal",
                    ),
                ],
            ),
        )
        raise AssertionError("Expected RetryExhaustedError")
    except RetryExhaustedError as exc:
        assert sleeps == [0.5]
        assert len(exc.failure.attempts) == 2
        assert exc.failure.attempts[0].disposition is RetryDisposition.RETRY
        assert exc.failure.attempts[1].disposition is RetryDisposition.FAIL
