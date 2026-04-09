from __future__ import annotations

from abc import ABC, abstractmethod

from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.models import PerceptionResult, PerceptionSource


class BasePerceptionProvider(ABC):
    source: PerceptionSource
    priority: int

    @abstractmethod
    def capture(self, context: CaptureContext) -> PerceptionResult:
        raise NotImplementedError
