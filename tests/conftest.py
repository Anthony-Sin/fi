import sys
from unittest.mock import MagicMock

# Mocking missing dependencies globally for the test suite
def pytest_configure(config):
    # Mock google-generativeai
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = MagicMock()
    sys.modules["google"] = MagicMock()
    sys.modules["google.generativeai"] = mock_genai

    # Mock opencv-python (cv2)
    sys.modules["cv2"] = MagicMock()

    # Mock numpy
    sys.modules["numpy"] = MagicMock()

    # Mock PIL (Pillow)
    sys.modules["PIL"] = MagicMock()
    sys.modules["PIL.Image"] = MagicMock()

    # Mock pytesseract
    sys.modules["pytesseract"] = MagicMock()

    # Mock pyautogui (often fails in headless environments due to DISPLAY)
    mock_pyautogui = MagicMock()
    mock_pyautogui.size.return_value = (1920, 1080)
    sys.modules["pyautogui"] = mock_pyautogui

    # Mock pywinauto (Windows only, would fail on Linux containers)
    sys.modules["pywinauto"] = MagicMock()
    sys.modules["pywinauto.application"] = MagicMock()
