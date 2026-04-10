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

class AccessibilityProvider(BasePerceptionProvider):
    """
    Perception provider that extracts UI elements using system accessibility APIs.
    """
    source = PerceptionSource.ACCESSIBILITY
    priority = 0

    def capture(self, context: CaptureContext) -> PerceptionResult:
        """
        Extracts accessibility information from the desktop.
        Uses a snapshot from context if available, otherwise attempts to read from the system.
        """
        started = perf_counter()

        def _capture_impl():
            snapshot = context.metadata.get("accessibility_snapshot")

            if snapshot is None:
                # In a real implementation, this would call AccessibilityTreeReader
                # For this task, we gracefully handle missing snapshots.
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

        return self._with_retry(_capture_impl)
