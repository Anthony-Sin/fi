from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop_automation_perception.models import (
    ErrorCategory,
    ErrorClassificationRecord,
    ErrorClassificationResult,
    RecoveryStrategy,
)


@dataclass(slots=True)
class ErrorClassifier:
    storage_path: str
    screenshot_backend: object | None = None

    def classify(
        self,
        error: Exception | object,
    ) -> ErrorClassificationResult:
        exception_type, message, screenshot_path = self._normalize_error(error)
        category = self._categorize(exception_type, message)
        strategy = self._recovery_strategy_for(category)
        if screenshot_path is None:
            screenshot_path = self._capture_screenshot()
        record = ErrorClassificationRecord(
            category=category,
            recovery_strategy=strategy,
            exception_type=exception_type,
            message=message,
            screenshot_path=screenshot_path,
        )
        self._append_record(record)
        return ErrorClassificationResult(
            succeeded=True,
            category=category,
            recovery_strategy=strategy,
            record=record,
        )

    def list_records(self) -> list[ErrorClassificationRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            ErrorClassificationRecord(
                category=ErrorCategory(item["category"]),
                recovery_strategy=RecoveryStrategy(item["recovery_strategy"]),
                exception_type=item.get("exception_type"),
                message=item.get("message"),
                screenshot_path=item.get("screenshot_path"),
                timestamp=self._parse_timestamp(item["timestamp"]),
            )
            for item in payload.get("records", [])
        ]

    def _categorize(self, exception_type: str, message: str) -> ErrorCategory:
        normalized = f"{exception_type} {message}".casefold()
        if any(token in normalized for token in ("not found", "no matching", "unable to locate", "element")):
            return ErrorCategory.UI_ELEMENT_NOT_FOUND
        if any(token in normalized for token in ("not responding", "hung", "unresponsive")):
            return ErrorCategory.APPLICATION_NOT_RESPONDING
        if any(token in normalized for token in ("session expired", "logged out", "sign in", "reauth")):
            return ErrorCategory.SESSION_EXPIRED
        if any(token in normalized for token in ("timeout", "timed out", "connection reset", "network")):
            return ErrorCategory.NETWORK_TIMEOUT
        if any(token in normalized for token in ("dialog", "modal", "popup", "unexpected dialog")):
            return ErrorCategory.UNEXPECTED_DIALOG_APPEARED
        if any(token in normalized for token in ("state mismatch", "post-condition", "pre-condition", "verification failed")):
            return ErrorCategory.SCREEN_STATE_MISMATCH
        return ErrorCategory.UNRECOGNIZED_ERROR

    def _recovery_strategy_for(self, category: ErrorCategory) -> RecoveryStrategy:
        mapping = {
            ErrorCategory.UI_ELEMENT_NOT_FOUND: RecoveryStrategy.RETRY,
            ErrorCategory.APPLICATION_NOT_RESPONDING: RecoveryStrategy.REFRESH,
            ErrorCategory.SESSION_EXPIRED: RecoveryStrategy.REAUTHENTICATE,
            ErrorCategory.NETWORK_TIMEOUT: RecoveryStrategy.RETRY,
            ErrorCategory.UNEXPECTED_DIALOG_APPEARED: RecoveryStrategy.DISMISS_DIALOG,
            ErrorCategory.SCREEN_STATE_MISMATCH: RecoveryStrategy.ESCALATE,
            ErrorCategory.UNRECOGNIZED_ERROR: RecoveryStrategy.ABORT,
        }
        return mapping[category]

    def _normalize_error(self, error: Exception | object) -> tuple[str, str, str | None]:
        if isinstance(error, BaseException):
            return type(error).__name__, str(error), self._extract_screenshot_path(error)
        if isinstance(error, dict):
            exception_type = self._coalesce_text(
                error.get("exception_type"),
                error.get("error_type"),
                error.get("event_type"),
            )
            message = self._coalesce_text(
                error.get("message"),
                error.get("error_message"),
                error.get("reason"),
                error.get("description"),
            )
            screenshot_path = self._coalesce_text(
                error.get("screenshot_path"),
                error.get("context_screenshot"),
            )
            return exception_type or "AutomationFailureEvent", message or str(error), screenshot_path
        exception_type = self._coalesce_text(
            getattr(error, "exception_type", None),
            getattr(error, "error_type", None),
            getattr(error, "event_type", None),
        )
        message = self._coalesce_text(
            getattr(error, "message", None),
            getattr(error, "error_message", None),
            getattr(error, "reason", None),
            getattr(error, "description", None),
        )
        screenshot_path = self._coalesce_text(
            getattr(error, "screenshot_path", None),
            getattr(error, "context_screenshot", None),
        )
        return exception_type or type(error).__name__, message or str(error), screenshot_path

    def _capture_screenshot(self) -> str | None:
        if self.screenshot_backend is None:
            return None
        return self.screenshot_backend.capture_screenshot_to_path()

    def _append_record(self, record: ErrorClassificationRecord) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("records", []).append(
            {
                "category": record.category.value,
                "recovery_strategy": record.recovery_strategy.value,
                "exception_type": record.exception_type,
                "message": record.message,
                "screenshot_path": record.screenshot_path,
                "timestamp": record.timestamp.isoformat(),
            }
        )
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _parse_timestamp(self, value: str):
        from datetime import datetime

        return datetime.fromisoformat(value)

    def _extract_screenshot_path(self, error: BaseException) -> str | None:
        return self._coalesce_text(
            getattr(error, "screenshot_path", None),
            getattr(error, "context_screenshot", None),
        )

    def _coalesce_text(self, *values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value
        return None
