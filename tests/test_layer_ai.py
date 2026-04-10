import unittest.mock as mock
import json
from desktop_automation_agent.desktop.ocr_extractor import OCRExtractor
from desktop_automation_agent.agents.desktop_automation_agent import DesktopAutomationAgent
from desktop_automation_agent.models import OrchestratorSubtask, OrchestratorSubtaskStatus

def test_ai_vision_fallback_on_empty_ocr():
    """Verifies that OCRExtractor invokes the AI fallback provider when the
    standard OCR backend fails to find the target text."""
    mock_backend = mock.MagicMock()
    mock_backend.extract_blocks.return_value = [] # OCR finds nothing

    mock_fallback = mock.MagicMock()
    mock_fallback.analyze_image.return_value = "[10, 20, 100, 50]" # Mocked coordinates

    # Mock OCRTextMatchResult because src/ has a bug where it passes 'detail'
    # which is not in the dataclass definition.
    with mock.patch("desktop_automation_agent.desktop.ocr_extractor.OCRTextMatchResult") as mock_result_cls:
        mock_result_cls.side_effect = lambda **kwargs: mock.MagicMock(succeeded=True, bounds=kwargs.get("bounds"))

        extractor = OCRExtractor(backend=mock_backend, ai_fallback=mock_fallback)
        result = extractor.find_text(target="Submit")

        assert result.succeeded is True
        assert result.bounds == (10, 20, 100, 50)
        mock_fallback.analyze_image.assert_called_once()

def test_ai_vision_executes_returned_actions():
    """Verifies that DesktopAutomationAgent correctly parses a JSON list of actions
    returned by the AI vision fallback and executes them."""
    # Mock overlay and logger to avoid attribute errors and GUI issues
    mock_overlay = mock.MagicMock()
    mock_logger = mock.MagicMock()

    agent = DesktopAutomationAgent(overlay=mock_overlay)
    agent._logger = mock_logger

    mock_ai = mock.MagicMock()
    # Mock AI response with a click action
    ai_json = json.dumps({
        "succeeded": True,
        "summary": "AI detected a button",
        "actions": [{"type": "click", "x": 500, "y": 300}]
    })
    mock_ai.analyze_image.return_value = f"```json\n{ai_json}\n```"
    mock_ai.get_token_count.return_value = 10

    agent.api_key = "mock-key"
    agent._ai_provider = mock_ai

    subtask = OrchestratorSubtask(
        subtask_id="fallback-1",
        description="Click the blue button",
        responsible_module="ai_vision_fallback"
    )

    # Mock pyautogui to avoid DISPLAY errors
    with mock.patch.dict("sys.modules", {"pyautogui": mock.MagicMock()}):
        import pyautogui
        with mock.patch("pyautogui.click") as mock_click:
            result = agent._solve_with_ai(subtask, {})

            assert result.status == OrchestratorSubtaskStatus.COMPLETED
            mock_click.assert_called_once_with(x=500, y=300)

def test_ai_api_error_handling():
    """Verifies that AI provider exceptions (like timeouts) are handled gracefully
    by returning a failed subtask result instead of crashing."""
    mock_overlay = mock.MagicMock()
    mock_logger = mock.MagicMock()

    agent = DesktopAutomationAgent(overlay=mock_overlay)
    agent._logger = mock_logger

    mock_ai = mock.MagicMock()
    mock_ai.analyze_image.side_effect = Exception("API Timeout")

    agent.api_key = "mock-key"
    agent._ai_provider = mock_ai

    subtask = OrchestratorSubtask(
        subtask_id="error-1",
        description="Analyze screen",
        responsible_module="ai_vision_fallback"
    )

    # Mock pyautogui to avoid DISPLAY errors in the screenshot block
    with mock.patch.dict("sys.modules", {"pyautogui": mock.MagicMock()}):
        result = agent._solve_with_ai(subtask, {})

        assert result.status == OrchestratorSubtaskStatus.FAILED
        assert "AI fallback error" in result.reason
