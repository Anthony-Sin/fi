from __future__ import annotations

from time import perf_counter

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import (
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
)
from desktop_automation_agent.providers.base import BasePerceptionProvider


class OCRProvider(BasePerceptionProvider):
    source = PerceptionSource.OCR
    priority = 1

    def capture(self, context: CaptureContext) -> PerceptionResult:
        started = perf_counter()
        snapshot = context.metadata.get("ocr_snapshot")

        if snapshot is None:
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
