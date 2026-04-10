from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

class SessionLogger:
    """
    Logs agent operations, AI interactions, and results to a file for transparency.
    Uses standard logging with rotation.
    """
    def __init__(self, log_path: str = "data/session.log", max_bytes: int = 5 * 1024 * 1024, backup_count: int = 3):
        self.log_path = log_path
        log_file = Path(self.log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("session_logger")
        self._logger.setLevel(logging.INFO)

        # Avoid duplicate handlers if re-initialized
        if not self._logger.handlers:
            handler = RotatingFileHandler(
                self.log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8"
            )
            formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    def log(self, event_type: str, data: Any):
        """Logs an event with structured data."""
        try:
            serialized_data = self._serialize(data)
            message = f"{event_type}: {json.dumps(serialized_data, sort_keys=True)}"
            self._logger.info(message)
        except Exception as e:
            # Fallback to simple string if serialization fails
            self._logger.warning(f"Logging failed for {event_type}: {e}")

    def log_start(self, action_name: str, context: dict[str, Any] | None = None):
        """Specifically log the start of an agent action."""
        self.log(f"START_{action_name}", context or {})

    def log_success(self, action_name: str, result: Any | None = None):
        """Specifically log the successful completion of an agent action."""
        self.log(f"SUCCESS_{action_name}", result or {})

    def log_failure(self, action_name: str, reason: str, context: dict[str, Any] | None = None):
        """Specifically log the failure of an agent action."""
        data = context or {}
        data["error_reason"] = reason
        self.log(f"FAILURE_{action_name}", data)

    def _serialize(self, data: Any) -> Any:
        if data is None:
            return None
        if isinstance(data, (str, int, float, bool)):
            return data
        if hasattr(data, "__dict__"):
            return {k: self._serialize(v) for k, v in data.__dict__.items() if not k.startswith("_")}
        if isinstance(data, dict):
            return {str(k): self._serialize(v) for k, v in data.items()}
        if isinstance(data, (list, tuple, set)):
            return [self._serialize(v) for v in data]
        return str(data)
