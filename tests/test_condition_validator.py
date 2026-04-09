from desktop_automation_agent.condition_validator import PrePostConditionValidator
from desktop_automation_agent.models import (
    ConditionDescription,
    RetryConfiguration,
    RetryDisposition,
    RetryExceptionRule,
    ScreenCheckType,
    ScreenVerificationCheck,
    ValidationDisposition,
    ValidationPhase,
)
from desktop_automation_agent.retry_engine import ExponentialBackoffRetryEngine


class FakeVerifier:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def verify(self, checks, screenshot_path=None):
        self.calls.append(checks)
        payload = self.results.pop(0) if self.results else {"failed_checks": [], "passed_checks": checks}
        return type(
            "VerifyResult",
            (),
            {
                "failed_checks": payload.get("failed_checks", []),
                "passed_checks": payload.get("passed_checks", []),
                "screenshot_path": payload.get("screenshot_path"),
            },
        )()


def make_condition(condition_id):
    return ConditionDescription(
        condition_id=condition_id,
        description=f"Condition {condition_id}",
        checks=[
            ScreenVerificationCheck(
                check_id=condition_id,
                check_type=ScreenCheckType.TEXT_PRESENT,
                target_text=condition_id,
            )
        ],
    )


def test_condition_validator_blocks_failed_pre_condition():
    validator = PrePostConditionValidator(
        verifier=FakeVerifier(results=[{"failed_checks": ["missing"]}])
    )

    result = validator.validate_pre_condition(make_condition("ready"))

    assert result.succeeded is False
    assert result.phase is ValidationPhase.PRE_CONDITION
    assert result.disposition is ValidationDisposition.BLOCK


def test_condition_validator_allows_successful_post_condition():
    validator = PrePostConditionValidator(verifier=FakeVerifier())

    result = validator.validate_post_condition(make_condition("done"))

    assert result.succeeded is True
    assert result.disposition is ValidationDisposition.ALLOW


def test_condition_validator_retries_post_condition_and_succeeds():
    sleeps = []
    validator = PrePostConditionValidator(
        verifier=FakeVerifier(
            results=[
                {"failed_checks": [type("Failure", (), {"detail": "not ready", "check_id": "done"})()]},
                {"failed_checks": []},
            ]
        ),
        retry_engine=ExponentialBackoffRetryEngine(sleep_fn=sleeps.append),
    )

    result = validator.validate_post_condition(
        make_condition("done"),
        retry_configuration=RetryConfiguration(max_retry_count=1),
    )

    assert result.succeeded is True
    assert sleeps == [0.5]


def test_condition_validator_escalates_after_retry_exhaustion():
    validator = PrePostConditionValidator(
        verifier=FakeVerifier(
            results=[
                {"failed_checks": [type("Failure", (), {"detail": "still loading", "check_id": "loaded"})()]},
                {"failed_checks": [type("Failure", (), {"detail": "still loading", "check_id": "loaded"})()]},
            ]
        ),
        retry_engine=ExponentialBackoffRetryEngine(sleep_fn=lambda _: None),
    )

    result = validator.validate_post_condition(
        make_condition("loaded"),
        retry_configuration=RetryConfiguration(
            max_retry_count=1,
            exception_rules=[
                RetryExceptionRule("RuntimeError", RetryDisposition.RETRY)
            ],
        ),
        escalate_on_failure=True,
    )

    assert result.succeeded is False
    assert result.phase is ValidationPhase.POST_CONDITION
    assert result.disposition is ValidationDisposition.ESCALATE
    assert result.retry_failure is not None
    assert len(result.retry_failure.attempts) == 2
