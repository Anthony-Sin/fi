from __future__ import annotations

import logging
from time import perf_counter

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import (
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
)
from desktop_automation_agent.providers.base import BasePerceptionProvider

logger = logging.getLogger(__name__)

class AIVisionProvider(BasePerceptionProvider):
    """
    Perception provider that uses Large Multimodal Models (LMMs) like Gemini Vision to analyze the screen.
    Used as a high-reliability fallback when other providers fail.
    """
    source = PerceptionSource.AI_VISION
    priority = 3

    # Rate limit AI Vision requests to 15 per minute (due to higher latency and cost)
    _max_calls_per_minute = 15

    def capture(self, context: CaptureContext) -> PerceptionResult:
        """
        Submits a screenshot to an AI Vision model for analysis.
        """
        started = perf_counter()

        def _capture_impl():
            snapshot = context.metadata.get("ai_vision_snapshot")

            if snapshot is None:
                # In a real implementation, this would call GeminiProvider.analyze_image()
                return PerceptionResult(
                    source=self.source,
                    confidence=0.0,
                    artifacts=[],
                    raw={},
                    duration_ms=(perf_counter() - started) * 1000,
                    succeeded=False,
                    error="AI vision snapshot unavailable.",
                )

            observations = snapshot.get("observations", [])
            confidence = float(snapshot.get("confidence", 0.0))
            artifacts = [
                PerceptionArtifact(
                    kind=str(observation.get("kind", "vision_observation")),
                    confidence=float(observation.get("confidence", confidence)),
                    bounds=observation.get("bounds"),
                    payload=observation,
                )
                for observation in observations
            ]

            return PerceptionResult(
                source=self.source,
                confidence=confidence,
                artifacts=artifacts,
                raw=snapshot,
                duration_ms=(perf_counter() - started) * 1000,
            )

        return self._with_retry(_capture_impl)
