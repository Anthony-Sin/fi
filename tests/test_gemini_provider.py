import pytest
from unittest.mock import MagicMock, patch
from desktop_automation_agent.ai.gemini_provider import GeminiProvider

def test_gemini_provider_token_count():
    with patch("google.generativeai.GenerativeModel") as mock_model_class:
        mock_model = mock_model_class.return_value
        mock_model.count_tokens.return_value = MagicMock(total_tokens=42)

        provider = GeminiProvider(api_key="mock-key")
        count = provider.get_token_count("Hello world")

        assert count == 42
        mock_model.count_tokens.assert_called_once_with("Hello world")

def test_gemini_provider_rate_limiting():
    with patch("google.generativeai.GenerativeModel") as mock_model_class:
        mock_model = mock_model_class.return_value
        mock_model.generate_content.return_value = MagicMock(text="Response")

        provider = GeminiProvider(api_key="mock-key")
        # Set a very small interval for testing
        GeminiProvider._min_interval = 0.01

        import time
        start = time.time()
        provider.generate_text("test 1")
        provider.generate_text("test 2")
        duration = time.time() - start

        assert duration >= 0.01
        assert mock_model.generate_content.call_count == 2
