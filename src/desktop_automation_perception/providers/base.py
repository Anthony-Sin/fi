from __future__ import annotations

from abc import ABC, abstractmethod

from desktop_automation_perception.context import CaptureContext
from desktop_automation_perception.models import PerceptionResult, PerceptionSource


class BasePerceptionProvider(ABC):
    source: PerceptionSource
    priority: int

    @abstractmethod
    def capture(self, context: CaptureContext) -> PerceptionResult:
        raise NotImplementedError
