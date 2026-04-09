from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CaptureContext:
    screenshot_path: Path | None = None
    template_paths: list[Path] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
