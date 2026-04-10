import os
import json
from datetime import datetime
from typing import Any, Dict

class SessionLogger:
    """
    Logs agent operations, AI interactions, and results to a file for transparency.
    """
    def __init__(self, log_path: str = "data/session.log"):
        self.log_path = log_path
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def log(self, event_type: str, data: Any):
        """Logs an event with a timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        serialized_data = self._serialize(data)

        # User-friendly log line
        log_line = f"[{timestamp}] {event_type}: {json.dumps(serialized_data)}"

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")

    def _serialize(self, data: Any) -> Any:
        if hasattr(data, "__dict__"):
            return {k: self._serialize(v) for k, v in data.__dict__.items()}
        if isinstance(data, dict):
            return {k: self._serialize(v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return [self._serialize(v) for v in data]
        if isinstance(data, (str, int, float, bool)) or data is None:
            return data
        return str(data)
