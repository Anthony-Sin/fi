from desktop_automation_agent.action_history_analyzer import ActionHistoryAnalyzer
from desktop_automation_agent.models import (
    ActionLogEntry,
    InputAction,
    InputActionType,
    NavigationStepActionType,
    NavigationStepOutcome,
    PromptPipelineStepLog,
    RetryAttemptLog,
    RetryDisposition,
    RetryFailureResult,
    WorkflowStepResult,
)


def make_action_log(action_type, *, executed=True, delay_seconds=0.1, reason=None):
    return ActionLogEntry(
        action=InputAction(action_type=action_type),
        executed=executed,
        delay_seconds=delay_seconds,
        reason=reason,
    )


def test_action_history_analyzer_identifies_frequent_sequences():
    analyzer = ActionHistoryAnalyzer(sequence_window_size=2, min_sequence_frequency=2)

    result = analyzer.analyze(
        action_logs=[
            make_action_log(InputActionType.CLICK),
            make_action_log(InputActionType.TYPE_TEXT),
            make_action_log(InputActionType.CLICK),
            make_action_log(InputActionType.TYPE_TEXT),
        ]
    )

    assert result.report is not None
    assert result.report.frequent_sequences
    assert result.report.frequent_sequences[0].sequence == ["click", "type_text"]


def test_action_history_analyzer_finds_common_failure_points():
    analyzer = ActionHistoryAnalyzer()

    result = analyzer.analyze(
        action_logs=[
            make_action_log(InputActionType.CLICK, executed=False, reason="button missing"),
            make_action_log(InputActionType.TYPE_TEXT),
        ],
        navigation_outcomes=[
            NavigationStepOutcome(
                step_id="submit",
                action_type=NavigationStepActionType.CLICK,
                succeeded=False,
                reason="post-condition failed",
            )
        ],
        workflow_step_results=[
            WorkflowStepResult(
                step_id="login",
                application_name="ChatGPT",
                succeeded=False,
                reason="session expired",
            )
        ],
        prompt_step_logs=[
            PromptPipelineStepLog(
                step_id="research",
                interface_name="chatgpt",
                template_name="research",
                prompt="hello",
                succeeded=False,
                reason="bad format",
            )
        ],
    )

    step_types = {item.step_type for item in result.report.common_failure_points}
    assert {"click", "login", "research"} <= step_types


def test_action_history_analyzer_calculates_average_durations():
    analyzer = ActionHistoryAnalyzer()

    result = analyzer.analyze(
        action_logs=[
            make_action_log(InputActionType.CLICK, delay_seconds=0.2),
            make_action_log(InputActionType.CLICK, delay_seconds=0.4),
        ],
        navigation_outcomes=[
            NavigationStepOutcome(
                step_id="scroll",
                action_type=NavigationStepActionType.SCROLL,
                succeeded=True,
                execution_time_seconds=1.5,
            )
        ],
    )

    durations = {item.step_type: item.average_duration_seconds for item in result.report.average_durations}
    assert abs(durations["click"] - 0.3) < 1e-9
    assert durations["scroll"] == 1.5


def test_action_history_analyzer_identifies_high_retry_rate_steps():
    analyzer = ActionHistoryAnalyzer(high_retry_rate_threshold=0.2)

    result = analyzer.analyze(
        action_logs=[
            make_action_log(InputActionType.CLICK),
            make_action_log(InputActionType.CLICK),
            make_action_log(InputActionType.CLICK),
        ],
        retry_failures=[
            RetryFailureResult(
                attempts=[
                    RetryAttemptLog(attempt_number=1, disposition=RetryDisposition.RETRY),
                    RetryAttemptLog(attempt_number=2, disposition=RetryDisposition.RETRY),
                    RetryAttemptLog(attempt_number=3, disposition=RetryDisposition.FAIL),
                ]
            )
        ],
        retry_step_types=["click"],
    )

    assert result.report.high_retry_steps
    assert result.report.high_retry_steps[0].step_type == "click"
    assert result.report.high_retry_steps[0].retry_rate > 0.2


def test_action_history_analyzer_produces_optimization_hints():
    analyzer = ActionHistoryAnalyzer(
        high_retry_rate_threshold=0.2,
        slow_step_duration_threshold_seconds=0.5,
    )

    result = analyzer.analyze(
        action_logs=[
            make_action_log(InputActionType.CLICK, delay_seconds=0.7, executed=False, reason="missed target"),
            make_action_log(InputActionType.CLICK, delay_seconds=0.8),
            make_action_log(InputActionType.CLICK, delay_seconds=0.9),
        ],
        retry_failures=[
            RetryFailureResult(
                attempts=[
                    RetryAttemptLog(attempt_number=1, disposition=RetryDisposition.RETRY),
                    RetryAttemptLog(attempt_number=2, disposition=RetryDisposition.FAIL),
                ]
            )
        ],
        retry_step_types=["click"],
    )

    recommendations = [item.recommendation for item in result.report.optimization_hints]
    assert any("add a wait" in item for item in recommendations)
    assert any("Increase retries" in item for item in recommendations)
