from __future__ import annotations

from time import perf_counter

from desktop_automation_perception.context import CaptureContext
from desktop_automation_perception.models import (
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
)
from desktop_automation_perception.providers.base import BasePerceptionProvider


class AccessibilityProvider(BasePerceptionProvider):
    source = PerceptionSource.ACCESSIBILITY
    priority = 0

    def capture(self, context: CaptureContext) -> PerceptionResult:
        started = perf_counter()
        snapshot = context.metadata.get("accessibility_snapshot")

        if snapshot is None:
            return PerceptionResult(
                source=self.source,
                confidence=0.0,
                artifacts=[],
                raw={},
                duration_ms=(perf_counter() - started) * 1000,
                succeeded=False,
                error="Accessibility snapshot unavailable.",
            )

        elements = snapshot.get("elements", [])
        confidence = float(snapshot.get("confidence", 0.95 if elements else 0.0))
        artifacts = [
            PerceptionArtifact(
                kind="ui_element",
                confidence=float(element.get("confidence", confidence)),
                bounds=element.get("bounds"),
                payload=element,
            )
            for element in elements
        ]

        return PerceptionResult(
            source=self.source,
            confidence=confidence,
            artifacts=artifacts,
            raw=snapshot,
            duration_ms=(perf_counter() - started) * 1000,
        )
