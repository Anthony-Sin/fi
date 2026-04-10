from desktop_automation_agent.models import (
    ApplicationLaunchMode,
    ApplicationLaunchRequest,
    NavigationSequenceMode,
    NavigationStep,
    NavigationStepActionType,
    ScreenCheckType,
    ScreenVerificationCheck,
)
from desktop_automation_agent.navigation_step_sequencer import NavigationStepSequencer
from desktop_automation_agent.resilience import AntiLoopDetector


class FakeInputRunner:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def run(self, actions):
        self.calls.append(actions)
        succeeded = self.results.pop(0) if self.results else True
        return type("RunResult", (), {"succeeded": succeeded, "failure_reason": None if succeeded else "input failed"})()


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


class FakeLauncher:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def launch(self, request):
        self.calls.append(request)
        succeeded = self.results.pop(0) if self.results else True
        return type("LaunchResult", (), {"succeeded": succeeded, "reason": None if succeeded else "launch failed"})()


def make_check(check_id):
    return ScreenVerificationCheck(
        check_id=check_id,
        check_type=ScreenCheckType.TEXT_PRESENT,
        target_text=check_id,
    )


def test_navigation_step_sequencer_executes_steps_in_order_and_verifies_post_conditions():
    """Verifies that the sequencer executes a list of navigation steps in
    their defined order and verifies the system state matches the expected
    post-conditions after each action."""
    monotonic = iter([0.0, 0.2, 0.3, 0.6]).__next__
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(),
        sleep_fn=lambda _: None,
        monotonic_fn=monotonic,
    )

    result = sequencer.run(
        [
            NavigationStep(
                step_id="click-send",
                action_type=NavigationStepActionType.CLICK,
                target_description="Send button",
                input_data={"element_bounds": (10, 10, 30, 30)},
                expected_post_action_state=[make_check("sent")],
            ),
            NavigationStep(
                step_id="type-message",
                action_type=NavigationStepActionType.TYPE,
                target_description="Composer",
                input_data={"text": "hello"},
            ),
        ]
    )

    assert result.succeeded is True
    assert [outcome.step_id for outcome in result.outcomes] == ["click-send", "type-message"]
    assert sequencer.input_runner.calls[0][0].action_type.value == "click"
    assert sequencer.input_runner.calls[1][0].text == "hello"


def test_navigation_step_sequencer_halts_in_strict_mode_on_failure():
    """Verifies that in 'STRICT' mode, the sequencer immediately stops
    execution if any step fails."""
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(results=[False]),
        verifier=FakeVerifier(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    result = sequencer.run(
        [
            NavigationStep(
                step_id="click-send",
                action_type=NavigationStepActionType.CLICK,
                target_description="Send button",
                input_data={"element_bounds": (10, 10, 30, 30)},
            ),
            NavigationStep(
                step_id="never-runs",
                action_type=NavigationStepActionType.WAIT,
                target_description="Wait",
                input_data={"seconds": 1.0},
            ),
        ],
        mode=NavigationSequenceMode.STRICT,
    )

    assert result.succeeded is False
    assert result.failed_step_id == "click-send"
    assert len(result.outcomes) == 1


def test_navigation_step_sequencer_skips_failed_optional_steps_in_lenient_mode():
    """Verifies that in 'LENIENT' mode, failed steps that are marked as
    'optional' are skipped without halting the overall sequence."""
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(results=[False, True]),
        verifier=FakeVerifier(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2, 0.3]).__next__,
    )

    result = sequencer.run(
        [
            NavigationStep(
                step_id="optional-scroll",
                action_type=NavigationStepActionType.SCROLL,
                target_description="Timeline",
                input_data={"scroll_amount": -10},
                optional=True,
            ),
            NavigationStep(
                step_id="wait-next",
                action_type=NavigationStepActionType.WAIT,
                target_description="Wait",
                input_data={"seconds": 0.5},
            ),
        ],
        mode=NavigationSequenceMode.LENIENT,
    )

    assert result.succeeded is True
    assert result.outcomes[0].skipped is True
    assert result.outcomes[1].succeeded is True


def test_navigation_step_sequencer_replays_only_when_preconditions_are_met():
    """Verifies that a step is only replayed if its defined pre-conditions
    are correctly satisfied by the current system state."""
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(
            results=[
                {"failed_checks": []},
                {"failed_checks": []},
            ]
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.2]).__next__,
    )

    outcome = sequencer.replay_step(
        NavigationStep(
            step_id="verify-ready",
            action_type=NavigationStepActionType.VERIFY,
            target_description="Ready state",
            preconditions=[make_check("ready")],
            expected_post_action_state=[make_check("ready")],
        )
    )

    assert outcome.succeeded is True
    assert outcome.precondition_result is not None
    assert outcome.postcondition_result is not None


def test_navigation_step_sequencer_marks_step_non_replayable_when_preconditions_fail():
    """Verifies that the sequencer identifies a step as 'non-replayable'
    if the current environment state does not allow for its execution."""
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(results=[{"failed_checks": ["missing"]}]),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    outcome = sequencer.replay_step(
        NavigationStep(
            step_id="click-send",
            action_type=NavigationStepActionType.CLICK,
            target_description="Send",
            input_data={"element_bounds": (10, 10, 30, 30)},
            preconditions=[make_check("ready")],
        )
    )

    assert outcome.succeeded is False
    assert outcome.replayable is False
    assert outcome.reason == "Step precondition state is not satisfied."


def test_navigation_step_sequencer_supports_navigate_action():
    """Verifies that the sequencer can handle 'NAVIGATE' actions by
    delegating to the application launcher."""
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(),
        launcher=FakeLauncher(results=[True]),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    result = sequencer.run(
        [
            NavigationStep(
                step_id="open-chat",
                action_type=NavigationStepActionType.NAVIGATE,
                target_description="Open chat app",
                input_data={
                    "launch_request": ApplicationLaunchRequest(
                        application_name="chat",
                        launch_mode=ApplicationLaunchMode.URL,
                        url="https://example.com",
                    )
                },
            )
        ]
    )

    assert result.succeeded is True
    assert len(sequencer.launcher.calls) == 1


def test_navigation_step_sequencer_stops_when_fail_safe_is_triggered():
    """Verifies that the sequencer respects external abort signals from
    the fail-safe controller."""
    abort_checks = iter([False, True]).__next__
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
        abort_checker=abort_checks,
    )

    result = sequencer.run(
        [
            NavigationStep(
                step_id="wait-first",
                action_type=NavigationStepActionType.WAIT,
                target_description="Wait",
                input_data={"seconds": 0.1},
            ),
            NavigationStep(
                step_id="never-runs",
                action_type=NavigationStepActionType.TYPE,
                target_description="Editor",
                input_data={"text": "blocked"},
            ),
        ]
    )

    assert result.succeeded is False
    assert result.failed_step_id == "never-runs"
    assert result.reason == "Execution aborted by fail-safe controller."
    assert len(result.outcomes) == 1


def test_navigation_step_sequencer_stops_when_step_execution_limit_is_exceeded(tmp_path):
    """Verifies that the sequencer integrates with the anti-loop detector
    to prevent repeated execution of the same step."""
    sequencer = NavigationStepSequencer(
        input_runner=FakeInputRunner(),
        verifier=FakeVerifier(),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
        anti_loop_detector=AntiLoopDetector(
            storage_path=str(tmp_path / "anti_loop.json"),
            workflow_id="wf-navigation-loop",
            max_step_execution_count=1,
            max_pipeline_duration_seconds=10.0,
            monotonic_fn=iter([0.0, 0.1, 0.2]).__next__,
        ),
    )

    result = sequencer.run(
        [
            NavigationStep(
                step_id="repeat",
                action_type=NavigationStepActionType.WAIT,
                target_description="Wait",
                input_data={"seconds": 0.1},
            ),
            NavigationStep(
                step_id="repeat",
                action_type=NavigationStepActionType.WAIT,
                target_description="Wait again",
                input_data={"seconds": 0.1},
            ),
        ]
    )

    assert result.succeeded is False
    assert result.failed_step_id == "repeat"
    assert "maximum execution count" in (result.reason or "")
