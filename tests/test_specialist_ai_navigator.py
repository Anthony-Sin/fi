import unittest.mock as mock
from desktop_automation_agent.ai.ai_interface_navigator import AIInterfaceNavigator
from desktop_automation_agent.models import (
    AIInterfaceConfiguration,
    AIInterfaceElementSelector,
    AIInterfaceStatus,
    AIInterfaceSubmitMode,
    OCRTextBlock,
    PromptInjectionMethod,
    SelectorStrategy,
)

def test_ai_navigator_ocr_and_input_mocked():
    """Verifies that AIInterfaceNavigator correctly orchestrates prompt injection
    and response extraction by mocking the OCR extractor and input runner."""

    mock_injector = mock.MagicMock()
    mock_injector.inject_prompt.return_value = mock.MagicMock(succeeded=True)

    mock_input_runner = mock.MagicMock()
    mock_input_runner.run.return_value = mock.MagicMock(succeeded=True)

    mock_screenshot = mock.MagicMock()
    mock_screenshot.capture_screenshot_to_path.return_value = "screen.png"

    # Mock OCR to return the input field first, then the response
    mock_ocr = mock.MagicMock()

    # define return values for find_text calls
    input_match = mock.MagicMock(succeeded=True, bounds=(10, 10, 200, 50), matched_text="Ask anything", confidence=0.9)
    # The first find_text is for the input field.
    # Subsequent find_text calls are for indicators/session timeout/etc in the loop.
    # We want find_text to eventually return the response indicator or just fail if not used.
    # Actually, navigate calls _resolve_selector for the response field at the end.
    response_match = mock.MagicMock(succeeded=True, bounds=(10, 100, 500, 150), matched_text=None, confidence=0.9)

    mock_ocr.find_text.side_effect = [
        input_match, # Resolve input field
        None, # Session timeout check
        None, # Error dialog check
        None, # Loading state check
        None, # Streaming indicator check
        response_match # Resolve response field
    ]

    # extract_text for response (called when response_match.text is empty)
    response_block = OCRTextBlock(text="The weather is 72 degrees.", confidence=0.95, bounds=(10, 100, 500, 150))
    mock_ocr.extract_text.return_value = mock.MagicMock(blocks=[response_block])

    navigator = AIInterfaceNavigator(
        prompt_injector=mock_injector,
        input_runner=mock_input_runner,
        screenshot_backend=mock_screenshot,
        ocr_extractor=mock_ocr,
        sleep_fn=lambda _: None,
        monotonic_fn=mock.MagicMock(side_effect=[0, 0.1, 0.2, 0.3, 0.4, 0.5])
    )

    interface = AIInterfaceConfiguration(
        interface_name="mock-ai",
        input_selector=AIInterfaceElementSelector(target_text="Ask anything", strategies=(SelectorStrategy.OCR,)),
        response_selector=AIInterfaceElementSelector(target_text="Response", strategies=(SelectorStrategy.OCR,)),
        submit_mode=AIInterfaceSubmitMode.ENTER,
        stable_polls_required=1,
        response_timeout_seconds=10.0
    )

    result = navigator.navigate(
        prompt="What is the weather?",
        interface=interface,
        injection_method=PromptInjectionMethod.TYPE
    )

    assert result.succeeded is True
    assert result.status is AIInterfaceStatus.COMPLETED
    assert result.response_text == "The weather is 72 degrees."

    # Verify prompt was injected
    mock_injector.inject_prompt.assert_called_once()
    assert mock_injector.inject_prompt.call_args[1]["prompt"] == "What is the weather?"

    # Verify submit was called (Enter key)
    mock_input_runner.run.assert_called_once()
    assert mock_input_runner.run.call_args[0][0][0].key == "enter"
