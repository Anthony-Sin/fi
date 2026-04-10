import unittest.mock as mock
from desktop_automation_agent.desktop.screen_state_verifier import PyAutoGUIScreenshotBackend
from desktop_automation_agent.desktop.ocr_extractor import OCRExtractor
from desktop_automation_agent.desktop.input_simulator import SafeInputSimulator, PyAutoGUIBackend
from desktop_automation_agent.models import (
    OCRTextBlock,
    InputAction,
    InputActionType,
    ScreenBounds
)

def test_screenshot_capture_returns_image(tmp_path):
    """Verifies that PyAutoGUIScreenshotBackend captures a screenshot using pyautogui
    and saves it to the specified path."""
    backend = PyAutoGUIScreenshotBackend()
    path = str(tmp_path / "test.png")

    # We mock sys.modules to avoid pyautogui import errors in environments without a display
    with mock.patch.dict("sys.modules", {"pyautogui": mock.MagicMock()}):
        import pyautogui
        from pathlib import Path
        mock_ss = pyautogui.screenshot
        mock_image = mock.MagicMock()
        mock_ss.return_value = mock_image

        result_path = backend.capture_screenshot_to_path(path=path)

        assert result_path == path
        mock_ss.assert_called_once()
        # The implementation uses Path(path) which might pass a PosixPath object to save()
        mock_image.save.assert_called_once_with(Path(path))

def test_ocr_extractor_returns_text():
    """Verifies that OCRExtractor correctly returns extracted text blocks from the provided backend."""
    mock_backend = mock.MagicMock()
    mock_backend.capture_screenshot.return_value = "fake_image"
    mock_backend.extract_blocks.return_value = [
        OCRTextBlock(text="Hello", confidence=0.9, bounds=(0, 0, 50, 20))
    ]

    extractor = OCRExtractor(backend=mock_backend)
    result = extractor.extract_text()

    assert len(result.blocks) == 1
    assert result.blocks[0].text == "Hello"

def test_ocr_extractor_graceful_empty():
    """Verifies that OCRExtractor handles scenarios where no text is detected without crashing,
    returning an empty result set."""
    mock_backend = mock.MagicMock()
    mock_backend.capture_screenshot.return_value = "fake_image"
    mock_backend.extract_blocks.return_value = []

    extractor = OCRExtractor(backend=mock_backend)
    result = extractor.extract_text()

    assert len(result.blocks) == 0

def test_mouse_click_coordinates():
    """Verifies that PyAutoGUIBackend passes the correct pixel coordinates and button
    type to the underlying pyautogui library."""
    mock_pyautogui = mock.MagicMock()
    backend = PyAutoGUIBackend(_module=mock_pyautogui)

    backend.click(x=100, y=200, button="right")

    mock_pyautogui.click.assert_called_once_with(x=100, y=200, button="right")

def test_scroll_action_logging():
    """Verifies that the SafeInputSimulator correctly executes a scroll action via the backend
    and records the execution in its action logs."""
    mock_pyautogui = mock.MagicMock()
    backend = PyAutoGUIBackend(_module=mock_pyautogui)

    mock_window_manager = mock.MagicMock()
    mock_window_manager.get_focused_window.return_value = mock.MagicMock(focused=True)

    mock_screen_inspector = mock.MagicMock()
    mock_screen_inspector.get_screen_bounds.return_value = ScreenBounds(width=1920, height=1080)

    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=mock_window_manager,
        screen_inspector=mock_screen_inspector
    )

    action = InputAction(
        action_type=InputActionType.SCROLL,
        scroll_amount=-500
    )

    result = simulator.run([action])

    assert result.succeeded is True
    assert len(result.logs) == 1
    assert result.logs[0].action.action_type == InputActionType.SCROLL
    mock_pyautogui.scroll.assert_called_once_with(-500)
