import unittest.mock as mock
from desktop_automation_agent.ai.gemini_provider import GeminiProvider

def test_gemini_provider_request_formatting():
    """Verifies that GeminiProvider correctly formats the prompt and image
    before sending it to the underlying GenerativeModel."""
    with mock.patch("google.generativeai.GenerativeModel") as mock_model_class:
        mock_model = mock_model_class.return_value
        provider = GeminiProvider(api_key="fake-key")

        # We need to mock Image.open to avoid identifying 'fake_image_data'
        with mock.patch("PIL.Image.open") as mock_open:
            mock_img = mock.MagicMock(spec=["save"]) # Mock PIL Image
            mock_open.return_value = mock_img

            provider.analyze_image("Identify this", b"fake_image_data")

            # Check that generate_content was called with correct list structure [prompt, image]
            call_args = mock_model.generate_content.call_args[0][0]
            assert call_args[0] == "Identify this"
            assert call_args[1] == mock_img

def test_gemini_provider_rate_limiting_enforcement():
    """Verifies that GeminiProvider respects the configured minimum interval
    between successive API calls to avoid rate limit violations."""
    with mock.patch("google.generativeai.GenerativeModel"):
        provider = GeminiProvider(api_key="fake-key")
        # Ensure a small predictable interval for test
        provider._min_interval = 0.5

        with mock.patch("time.sleep") as mock_sleep:
            provider._last_call_time = 100.0 # Pretend last call was just now
            with mock.patch("time.time", return_value=100.1):
                provider.generate_text("test")
                # Expected wait = 0.5 - (100.1 - 100.0) = 0.4
                mock_sleep.assert_called_once_with(mock.ANY)
                assert mock_sleep.call_args[0][0] > 0

def test_gemini_provider_auth_failure():
    """Verifies that an authentication error from the Gemini API returns a
    descriptive error message instead of causing a process crash."""
    with mock.patch("google.generativeai.GenerativeModel") as mock_model_class:
        mock_model = mock_model_class.return_value
        # Simulate an API error (e.g. invalid key)
        mock_model.generate_content.side_effect = Exception("Invalid API Key")

        provider = GeminiProvider(api_key="invalid")

        try:
            provider.generate_text("hello")
        except Exception as e:
            assert "Invalid API Key" in str(e)
