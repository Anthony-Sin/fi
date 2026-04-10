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

class OCRProvider(BasePerceptionProvider):
    """
    Perception provider that extracts text from the screen using Optical Character Recognition (OCR).
    """
    source = PerceptionSource.OCR
    priority = 1

    # Rate limit OCR requests to 30 per minute
    _max_calls_per_minute = 30

    def capture(self, context: CaptureContext) -> PerceptionResult:
        """
        Performs OCR on the current screen or a provided snapshot.
        """
        started = perf_counter()

        def _capture_impl():
            snapshot = context.metadata.get("ocr_snapshot")

            if snapshot is None:
                # In a real implementation, this would call OCRExtractor.extract_text()
                return PerceptionResult(
                    source=self.source,
                    confidence=0.0,
                    artifacts=[],
                    raw={},
                    duration_ms=(perf_counter() - started) * 1000,
                    succeeded=False,
                    error="OCR snapshot unavailable.",
                )

            text_blocks = snapshot.get("text_blocks", [])
            average_confidence = (
                sum(float(block.get("confidence", 0.0)) for block in text_blocks) / len(text_blocks)
                if text_blocks
                else 0.0
            )
            artifacts = [
                PerceptionArtifact(
                    kind="text",
                    confidence=float(block.get("confidence", average_confidence)),
                    bounds=block.get("bounds"),
                    payload={"text": block.get("text", ""), **block},
                )
                for block in text_blocks
            ]

            return PerceptionResult(
                source=self.source,
                confidence=float(snapshot.get("confidence", average_confidence)),
                artifacts=artifacts,
                raw=snapshot,
                duration_ms=(perf_counter() - started) * 1000,
            )

        return self._with_retry(_capture_impl)
