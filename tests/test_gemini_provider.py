import pytest
from unittest.mock import MagicMock, patch
from desktop_automation_agent.ai.gemini_provider import GeminiProvider

def test_gemini_provider_token_count():
    """Verifies that the GeminiProvider correctly wraps the underlying LLM
    SDK's token counting functionality."""
    # Patch the reference inside the gemini_provider module
    with patch("desktop_automation_agent.ai.gemini_provider.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.models.count_tokens.return_value = MagicMock(total_tokens=42)

        provider = GeminiProvider(api_key="mock-key")
        count = provider.get_token_count("Hello world")

        assert count == 42
        mock_client.models.count_tokens.assert_called_once_with(
            model=provider.model_name,
            contents="Hello world"
        )

def test_gemini_provider_rate_limiting():
    """Verifies that the GeminiProvider implements internal rate limiting
    to avoid exceeding API quotas."""
    # Patch the reference inside the gemini_provider module
    with patch("desktop_automation_agent.ai.gemini_provider.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.models.generate_content.return_value = MagicMock(text="Response")

        provider = GeminiProvider(api_key="mock-key")
        # Set a very small interval for testing
        GeminiProvider._min_interval = 0.01

        import time
        start = time.time()
        provider.generate_text("test 1")
        provider.generate_text("test 2")
        duration = time.time() - start

        assert duration >= 0.01
        assert mock_client.models.generate_content.call_count == 2

def test_gemini_provider_multiple_parts_concatenation():
    """Verifies that GeminiProvider correctly concatenates text from multiple parts,
    skipping non-text parts like thought_signature."""
    with patch("desktop_automation_agent.ai.gemini_provider.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_part1 = MagicMock()
        mock_part1.text = "Hello "

        mock_part2 = MagicMock()
        mock_part2.text = None
        mock_part2.thought_signature = "thinking..."

        mock_part3 = MagicMock()
        mock_part3.text = "World"

        mock_response = MagicMock()
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].content.parts = [mock_part1, mock_part2, mock_part3]

        mock_client.models.generate_content.return_value = mock_response

        provider = GeminiProvider(api_key="mock-key")
        result = provider.generate_text("test")

        assert result == "Hello World"
