import unittest.mock as mock
from desktop_automation_agent.automation.navigation_step_sequencer import NavigationStepSequencer
from desktop_automation_agent.models import (
    NavigationStep,
    NavigationStepActionType,
    NavigationSequenceMode,
    InputActionType
)

def test_navigation_sequencer_execution_flow():
    """Verifies that NavigationStepSequencer executes a series of steps in order
    and aborts the sequence when an intermediate step fails."""

    # Mock InputRunner to fail on the second call
    mock_input_runner = mock.MagicMock()
    mock_input_runner.run.side_effect = [
        mock.MagicMock(succeeded=True), # Step 1: Click
        mock.MagicMock(succeeded=False, failure_reason="Element not found") # Step 2: Type
    ]

    mock_verifier = mock.MagicMock()
    mock_verifier.verify.return_value = mock.MagicMock(failed_checks=[])

    sequencer = NavigationStepSequencer(
        input_runner=mock_input_runner,
        verifier=mock_verifier,
        sleep_fn=lambda _: None,
        monotonic_fn=mock.MagicMock(side_effect=[0, 0.1, 0.2, 0.3, 0.4, 0.5])
    )

    steps = [
        NavigationStep(
            step_id="step-1",
            action_type=NavigationStepActionType.CLICK,
            target_description="Button",
            input_data={"element_bounds": (0, 0, 10, 10)}
        ),
        NavigationStep(
            step_id="step-2",
            action_type=NavigationStepActionType.TYPE,
            target_description="Input",
            input_data={"text": "hello", "element_bounds": (0, 0, 10, 10)}
        ),
        NavigationStep(
            step_id="step-3",
            action_type=NavigationStepActionType.CLICK,
            target_description="Submit",
            input_data={"element_bounds": (0, 0, 10, 10)}
        )
    ]

    result = sequencer.run(steps, mode=NavigationSequenceMode.STRICT)

    assert result.succeeded is False
    assert result.failed_step_id == "step-2"
    assert result.reason == "Element not found"

    # Verify outcomes
    assert len(result.outcomes) == 2
    assert result.outcomes[0].step_id == "step-1"
    assert result.outcomes[0].succeeded is True
    assert result.outcomes[1].step_id == "step-2"
    assert result.outcomes[1].succeeded is False

    # Verify InputRunner was called exactly twice (Step 3 should be skipped)
    assert mock_input_runner.run.call_count == 2

    # Verify calls
    assert mock_input_runner.run.call_args_list[0][0][0][0].action_type == InputActionType.CLICK
    assert mock_input_runner.run.call_args_list[1][0][0][0].action_type == InputActionType.TYPE_TEXT
