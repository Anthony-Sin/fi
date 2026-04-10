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

class TemplateMatchingProvider(BasePerceptionProvider):
    """
    Perception provider that finds UI elements by matching them against predefined image templates.
    """
    source = PerceptionSource.TEMPLATE_MATCH
    priority = 2

    def capture(self, context: CaptureContext) -> PerceptionResult:
        """
        Searches for template matches on the screen.
        """
        started = perf_counter()

        def _capture_impl():
            snapshot = context.metadata.get("template_matches")

            if snapshot is None:
                # In a real implementation, this would call TemplateImageMatcher
                return PerceptionResult(
                    source=self.source,
                    confidence=0.0,
                    artifacts=[],
                    raw={},
                    duration_ms=(perf_counter() - started) * 1000,
                    succeeded=False,
                    error="Template matching snapshot unavailable.",
                )

            matches = snapshot.get("matches", [])
            best_confidence = max((float(match.get("confidence", 0.0)) for match in matches), default=0.0)
            artifacts = [
                PerceptionArtifact(
                    kind="template_match",
                    confidence=float(match.get("confidence", best_confidence)),
                    bounds=match.get("bounds"),
                    payload=match,
                )
                for match in matches
            ]

            return PerceptionResult(
                source=self.source,
                confidence=float(snapshot.get("confidence", best_confidence)),
                artifacts=artifacts,
                raw=snapshot,
                duration_ms=(perf_counter() - started) * 1000,
            )

        return self._with_retry(_capture_impl)
