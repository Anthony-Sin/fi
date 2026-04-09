from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import DesktopState, PerceptionResult
from desktop_automation_agent.providers.base import BasePerceptionProvider


class DesktopPerceptionEngine:
    def __init__(self, providers: Iterable[BasePerceptionProvider], stop_on_first_success: bool = False):
        self._providers = sorted(providers, key=lambda provider: provider.priority)
        self._stop_on_first_success = stop_on_first_success

    def capture_state(self, context: CaptureContext | None = None) -> DesktopState:
        context = context or CaptureContext()
        results: list[PerceptionResult] = []

        for provider in self._providers:
            result = provider.capture(context)
            results.append(result)

            if self._stop_on_first_success and result.succeeded and result.confidence > 0:
                break

        return DesktopState(
            captured_at=datetime.now(timezone.utc),
            results=results,
        )
