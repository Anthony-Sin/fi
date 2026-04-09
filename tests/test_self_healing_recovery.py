from desktop_automation_agent.error_classifier import ErrorClassifier
from desktop_automation_agent.models import (
    InputTarget,
    RecoveryStrategy,
    ScreenCheckType,
    ScreenVerificationCheck,
    SelfHealingRecoveryRequest,
)
from desktop_automation_agent.self_healing_recovery import SelfHealingRecoveryModule


class FakeScreenshotBackend:
    def __init__(self):
        self.count = 0

    def capture_screenshot_to_path(self, path=None):
        self.count += 1
        return f"recovery-{self.count}.png"


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


class FakeInputRunner:
    def __init__(self):
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        return type("RunResult", (), {"succeeded": True, "failure_reason": None})()


def make_classifier(tmp_path):
    return ErrorClassifier(storage_path=str(tmp_path / "error_classifier.json"))


def make_request(error, **overrides):
    payload = {
        "error": error,
        "target_checks": [
            ScreenVerificationCheck(
                check_id="ready",
                check_type=ScreenCheckType.TEXT_PRESENT,
                target_text="Ready",
            )
        ],
    }
    payload.update(overrides)
    return SelfHealingRecoveryRequest(**payload)


def test_self_healing_refreshes_and_retries_step(tmp_path):
    refresh_calls = []
    retry_calls = []
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(results=[{"failed_checks": []}]),
        screenshot_backend=FakeScreenshotBackend(),
        refresh_callback=lambda: refresh_calls.append("refresh") or {"ok": True},
    )

    result = module.recover(
        make_request(RuntimeError("Application not responding")),
        retry_step=lambda: retry_calls.append("retry") or {"succeeded": True},
    )

    assert result.succeeded is True
    assert result.strategy is RecoveryStrategy.REFRESH
    assert result.initial_screenshot_path == "recovery-1.png"
    assert refresh_calls == ["refresh"]
    assert retry_calls == ["retry"]


def test_self_healing_reauthenticates_expired_session(tmp_path):
    reauth_calls = []
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(results=[{"failed_checks": []}]),
        screenshot_backend=FakeScreenshotBackend(),
        reauthenticate_callback=lambda: reauth_calls.append("reauth") or True,
    )

    result = module.recover(
        make_request(RuntimeError("Session expired, please sign in")),
        retry_step=lambda: {"succeeded": True},
    )

    assert result.succeeded is True
    assert result.strategy is RecoveryStrategy.REAUTHENTICATE
    assert reauth_calls == ["reauth"]


def test_self_healing_dismisses_unexpected_dialog(tmp_path):
    dismiss_calls = []
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(results=[{"failed_checks": []}]),
        screenshot_backend=FakeScreenshotBackend(),
        dismiss_dialog_callback=lambda: dismiss_calls.append("dismiss") or True,
    )

    result = module.recover(
        make_request(RuntimeError("Unexpected dialog appeared")),
        retry_step=lambda: {"succeeded": True},
    )

    assert result.succeeded is True
    assert result.strategy is RecoveryStrategy.DISMISS_DIALOG
    assert dismiss_calls == ["dismiss"]


def test_self_healing_scrolls_to_find_hidden_element(tmp_path):
    input_runner = FakeInputRunner()
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(
            results=[
                {"failed_checks": ["still hidden"]},
                {"failed_checks": []},
            ]
        ),
        screenshot_backend=FakeScreenshotBackend(),
        input_runner=input_runner,
    )

    result = module.recover(
        make_request(
            RuntimeError("Unable to locate message input element"),
            input_target=InputTarget(element_bounds=(10, 10, 20, 20)),
            scroll_amount=-600,
        ),
        retry_step=lambda: {"succeeded": True},
    )

    assert result.succeeded is True
    assert result.strategy is RecoveryStrategy.SCROLL_TO_FIND
    assert len(input_runner.calls) == 1
    assert input_runner.calls[0][0].scroll_amount == -600


def test_self_healing_waits_for_loading_state_to_resolve(tmp_path):
    sleeps = []
    monotonic_values = iter([0.0, 0.0, 0.2, 0.4, 0.4]).__next__
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(
            results=[
                {"failed_checks": ["loading"]},
                {"failed_checks": []},
            ]
        ),
        screenshot_backend=FakeScreenshotBackend(),
        sleep_fn=sleeps.append,
        monotonic_fn=monotonic_values,
    )

    result = module.recover(
        make_request(
            TimeoutError("Network timed out"),
            loading_poll_interval_seconds=0.2,
            loading_timeout_seconds=2.0,
        ),
        retry_step=lambda: {"succeeded": True},
    )

    assert result.succeeded is True
    assert result.strategy is RecoveryStrategy.WAIT_FOR_LOADING
    assert sleeps == [0.2]


def test_self_healing_blocks_retry_when_recovered_state_cannot_be_verified(tmp_path):
    retry_calls = []
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(results=[{"failed_checks": ["still missing"]}] * 4),
        screenshot_backend=FakeScreenshotBackend(),
        input_runner=FakeInputRunner(),
    )

    result = module.recover(
        make_request(
            RuntimeError("Unable to locate hidden send element"),
            input_target=InputTarget(element_bounds=(1, 1, 2, 2)),
        ),
        retry_step=lambda: retry_calls.append("retry") or {"succeeded": True},
    )

    assert result.succeeded is False
    assert result.strategy is RecoveryStrategy.SCROLL_TO_FIND
    assert retry_calls == []


def test_self_healing_aborts_unrecognized_errors(tmp_path):
    module = SelfHealingRecoveryModule(
        classifier=make_classifier(tmp_path),
        verifier=FakeVerifier(),
        screenshot_backend=FakeScreenshotBackend(),
    )

    result = module.recover(
        make_request(RuntimeError("completely novel failure")),
        retry_step=lambda: {"succeeded": True},
    )

    assert result.succeeded is False
    assert result.strategy is RecoveryStrategy.ABORT
