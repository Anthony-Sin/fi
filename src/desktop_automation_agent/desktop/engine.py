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
        """Capture the current state of the desktop using all registered providers."""
        context = context or CaptureContext()
        results: list[PerceptionResult] = []

        import threading

        threads = []
        # Local result list to avoid race conditions on the main list
        thread_results: list[PerceptionResult] = []
        lock = threading.Lock()

        def safe_capture(p: BasePerceptionProvider, ctx: CaptureContext) -> None:
            try:
                result = p.capture(ctx)
                with lock:
                    thread_results.append(result)
            except Exception as e:
                logger.warning("Provider %s failed during capture: %s", p.__class__.__name__, e)
                with lock:
                    thread_results.append(
                        PerceptionResult(
                            source=getattr(p, "source", None),
                            confidence=0.0,
                            succeeded=False,
                            error=str(e),
                        )
                    )

        for provider in self._providers:
            # Check for stop condition BEFORE starting new threads
            with lock:
                if self._stop_on_first_success and thread_results and any(r.succeeded for r in thread_results):
                    break

            t = threading.Thread(target=safe_capture, args=(provider, context), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=15.0)  # Add a safety timeout for threads

        return DesktopState(
            captured_at=datetime.now(timezone.utc),
            results=thread_results,
        )
