import unittest.mock as mock
from desktop_automation_agent.resilience.anti_loop_detector import AntiLoopDetector
from desktop_automation_agent.resilience.self_healing_recovery import SelfHealingRecoveryModule
from desktop_automation_agent.models import (
    AntiLoopTriggerType,
    SelfHealingRecoveryRequest,
    ErrorCategory,
    RecoveryStrategy
)

def test_anti_loop_detector_execution_limit(tmp_path):
    """Verifies that AntiLoopDetector correctly identifies and triggers a failure
    after a configurable number of identical repeated steps."""
    detector = AntiLoopDetector(
        storage_path=str(tmp_path / "loops.json"),
        workflow_id="test-wf",
        max_step_execution_count=2
    )

    # Step 1: OK
    res1 = detector.before_step("click-login", metadata={})
    assert res1.triggered is False

    # Step 2: OK
    res2 = detector.before_step("click-login", metadata={})
    assert res2.triggered is False

    # Step 3: Triggered (limit exceeded)
    res3 = detector.before_step("click-login", metadata={})
    assert res3.triggered is True
    assert res3.record.trigger_type == AntiLoopTriggerType.STEP_EXECUTION_LIMIT

def test_self_healing_recovery_chain():
    """Verifies that SelfHealingRecoveryModule correctly identifies a failure category
    and executes the mapped recovery strategy before retrying the step."""
    mock_classifier = mock.MagicMock()
    mock_classifier.classify.return_value = mock.MagicMock(
        category=ErrorCategory.APPLICATION_NOT_RESPONDING,
        recovery_strategy=RecoveryStrategy.REFRESH
    )

    mock_verifier = mock.MagicMock()
    mock_verifier.verify.return_value = mock.MagicMock(failed_checks=[])

    # Track the execution order of recovery vs retry
    execution_order = []

    def mock_refresh():
        execution_order.append("refresh")
        return True

    def mock_retry():
        execution_order.append("retry")
        return mock.MagicMock(succeeded=True)

    module = SelfHealingRecoveryModule(
        classifier=mock_classifier,
        verifier=mock_verifier,
        refresh_callback=mock_refresh
    )

    request = SelfHealingRecoveryRequest(error=Exception("App stuck"))
    result = module.recover(request, retry_step=mock_retry)

    assert result.succeeded is True
    assert result.strategy == RecoveryStrategy.REFRESH
    assert execution_order == ["refresh", "retry"]

def test_self_healing_execution_logging():
    """Verifies that each step and strategy attempt in the self-healing process
    is accurately reflected in the resulting outcome object."""
    mock_classifier = mock.MagicMock()
    mock_classifier.classify.return_value = mock.MagicMock(
        category=ErrorCategory.UI_ELEMENT_NOT_FOUND,
        recovery_strategy=RecoveryStrategy.SCROLL_TO_FIND
    )

    mock_verifier = mock.MagicMock()
    # First verify fails, second succeeds after "scroll"
    mock_verifier.verify.side_effect = [
        mock.MagicMock(failed_checks=["missing"]),
        mock.MagicMock(failed_checks=[])
    ]

    mock_input = mock.MagicMock()
    mock_input.run.return_value = mock.MagicMock(succeeded=True)

    module = SelfHealingRecoveryModule(
        classifier=mock_classifier,
        verifier=mock_verifier,
        input_runner=mock_input
    )

    request = SelfHealingRecoveryRequest(
        error=Exception("No button"),
        target_checks=["check-button"],
        max_scroll_attempts=1
    )

    result = module.recover(request, retry_step=lambda: mock.MagicMock(succeeded=True))

    assert result.succeeded is True
    assert result.strategy == RecoveryStrategy.SCROLL_TO_FIND
    # Check that recovery_action_result contains metadata about the attempts
    assert result.recovery_action_result["scroll_attempts"] == 1
