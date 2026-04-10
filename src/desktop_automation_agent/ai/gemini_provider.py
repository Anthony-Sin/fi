from __future__ import annotations

import os
from typing import Any, Optional, Union, List
import google.generativeai as genai
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

    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model_name)

    def _wait_for_rate_limit(self):
        with self._lock:
            elapsed = time.time() - GeminiProvider._last_call_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            GeminiProvider._last_call_time = time.time()

    def generate_text(self, prompt: str) -> str:
        """Generates text based on a prompt."""
        self._wait_for_rate_limit()
        response = self.model.generate_content(prompt)
        return response.text

    def analyze_image(self, prompt: str, image: Union[Image.Image, bytes]) -> str:
        """Analyzes an image with a text prompt."""
        self._wait_for_rate_limit()
        if isinstance(image, bytes):
            image = Image.open(io.BytesIO(image))

        response = self.model.generate_content([prompt, image])
        return response.text

    def get_token_count(self, contents: Union[str, List[Any]]) -> int:
        """Estimates token count for given contents."""
        response = self.model.count_tokens(contents)
        return response.total_tokens

    def update_config(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        """Updates the provider configuration."""
        if api_key:
            self.api_key = api_key
            genai.configure(api_key=self.api_key)
        if model_name:
            self.model_name = model_name
            self.model = genai.GenerativeModel(self.model_name)
