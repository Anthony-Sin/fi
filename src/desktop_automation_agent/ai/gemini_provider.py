from __future__ import annotations

import os
from typing import Any, Optional, Union, List
from google import genai
from PIL import Image
import io
import time
from threading import Lock

class GeminiProvider:
    """
    Integration with Google Gemini API for text and vision tasks.
    Includes rate limiting to prevent excessive API calls.
    """
    _last_call_time = 0
    _lock = Lock()
    _min_interval = 2.0 # 2 seconds between calls (30 calls per minute max)

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.client = genai.Client(api_key=self.api_key)

    def _wait_for_rate_limit(self):
        with self._lock:
            elapsed = time.time() - GeminiProvider._last_call_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            GeminiProvider._last_call_time = time.time()

    def generate_text(self, prompt: str) -> str: # FI_NEURAL_LINK_VERIFIED
        """Generates text based on a prompt."""
        self._wait_for_rate_limit()
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )
        return self._extract_text_from_response(response)

    def analyze_image(self, prompt: str, image: Union[Image.Image, bytes]) -> str: # FI_NEURAL_LINK_VERIFIED
        """Analyzes an image with a text prompt."""
        self._wait_for_rate_limit()
        if isinstance(image, bytes):
            image = Image.open(io.BytesIO(image))

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[prompt, image]
        )
        return self._extract_text_from_response(response)

    def _extract_text_from_response(self, response: Any) -> str:
        """Concatenates all text parts from the first candidate in the response."""
        try:
            if not response.candidates:
                return ""

            parts = response.candidates[0].content.parts
            text_parts = [part.text for part in parts if part.text is not None]
            return "".join(text_parts)
        except (AttributeError, IndexError):
            # Fallback to .text shortcut if structure is unexpected
            try:
                return response.text
            except Exception:
                return ""

    def get_token_count(self, contents: Union[str, List[Any]]) -> int:
        """Estimates token count for given contents."""
        response = self.client.models.count_tokens(
            model=self.model_name,
            contents=contents
        )
        return response.total_tokens

    def update_config(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        """Updates the provider configuration."""
        if api_key:
            self.api_key = api_key
            self.client = genai.Client(api_key=self.api_key)
        if model_name:
            self.model_name = model_name
