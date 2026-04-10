from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import DesktopState, PerceptionResult
from desktop_automation_agent.providers.base import BasePerceptionProvider


logger = logging.getLogger(__name__)


class DesktopPerceptionEngine:
    def __init__(self, providers: Iterable[BasePerceptionProvider], stop_on_first_success: bool = False):
        self._providers = sorted(providers, key=lambda provider: provider.priority)
        self._stop_on_first_success = stop_on_first_success

    def capture_state(self, context: CaptureContext | None = None) -> DesktopState:
        context = context or CaptureContext()
        results: list[PerceptionResult] = []

        import threading

        threads = []
        def safe_capture(p, ctx, res):
            try:
                res.append(p.capture(ctx))
            except Exception as e:
                logger.warning(f"Provider {p.__class__.__name__} failed during capture: {e}")
                from desktop_automation_agent.models import PerceptionResult
                res.append(PerceptionResult(source=getattr(p, 'source', None), confidence=0.0, succeeded=False, error=str(e)))

        for provider in self._providers:
            if self._stop_on_first_success and results and any(r.succeeded for r in results):
                break

            t = threading.Thread(target=safe_capture, args=(provider, context, results))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        return DesktopState(
            captured_at=datetime.now(timezone.utc),
            results=results,
        )
