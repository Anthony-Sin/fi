from __future__ import annotations

from time import perf_counter

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import (
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
)
from desktop_automation_agent.providers.base import BasePerceptionProvider


class AIVisionProvider(BasePerceptionProvider):
    source = PerceptionSource.AI_VISION
    priority = 3

    def capture(self, context: CaptureContext) -> PerceptionResult:
        started = perf_counter()
        snapshot = context.metadata.get("ai_vision_snapshot")

        if snapshot is None:
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
