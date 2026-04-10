from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable
import time

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import PerceptionResult, PerceptionSource

logger = logging.getLogger(__name__)

class BasePerceptionProvider(ABC):
    """
    Abstract base class for all perception providers.
    Defines the contract for capturing desktop state information.
    """
    source: PerceptionSource
    priority: int

    # Rate limiting defaults (can be overridden by subclasses)
    _max_calls_per_minute: int = 60
    _last_call_time: float = 0.0

    def __init__(self, sleep_fn: Callable[[float], None] = time.sleep):
        self._sleep_fn = sleep_fn

    @abstractmethod
    def capture(self, context: CaptureContext) -> PerceptionResult:
        """
        Captures information from the desktop environment based on the provided context.
        Returns a PerceptionResult containing the findings.
        """
        raise NotImplementedError

    def _apply_rate_limit(self):
        """
        Enforces a rate limit based on _max_calls_per_minute.
        """
        if self._max_calls_per_minute <= 0:
            return

        interval = 60.0 / self._max_calls_per_minute
        elapsed = time.time() - self._last_call_time
        if elapsed < interval:
            wait_time = interval - elapsed
            logger.debug(f"Rate limiting {self.source.value}: waiting {wait_time:.2f}s")
            self._sleep_fn(wait_time)

        self._last_call_time = time.time()

    def _with_retry(self, operation: Callable[[], PerceptionResult], max_retries: int = 3) -> PerceptionResult:
        """
        Executes an operation with basic retry logic.
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                self._apply_rate_limit()
                return operation()
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt + 1} for {self.source.value} failed: {e}")
                if attempt < max_retries - 1:
                    self._sleep_fn(2.0 ** attempt) # Exponential backoff

        return PerceptionResult(
            source=self.source,
            confidence=0.0,
            succeeded=False,
            error=f"Operation failed after {max_retries} attempts. Last error: {last_error}"
        )
