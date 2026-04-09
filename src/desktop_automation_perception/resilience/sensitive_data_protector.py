from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from desktop_automation_perception.models import SensitiveAccessEvent, SensitiveProtectionResult


@dataclass(slots=True)
class SensitiveDataProtector:
    sensitive_field_names: tuple[str, ...] = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credential",
        "auth",
        "cookie",
        "ssn",
        "email",
        "phone",
    )
    sensitive_value_patterns: tuple[str, ...] = ()
    placeholder_token: str = "***SENSITIVE***"
    access_audit_path: str | None = None
    _compiled_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in self.sensitive_value_patterns)

    def sanitize_payload(self, payload: Any, *, location: str | None = None) -> Any:
        if isinstance(payload, dict):
            return {
                key: (
                    self.placeholder_token
                    if self._is_sensitive_field(key) or self._matches_sensitive_value(value)
                    else self.sanitize_payload(value, location=location)
                )
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self.sanitize_payload(item, location=location) for item in payload]
        if isinstance(payload, tuple):
            return [self.sanitize_payload(item, location=location) for item in payload]
        if isinstance(payload, str) and self._matches_sensitive_value(payload):
            return self.placeholder_token
        return payload

    def mask_text(self, text: str, *, location: str | None = None) -> SensitiveProtectionResult:
        masked = text
        violations: list[str] = []
        for pattern in self._compiled_patterns:
            if pattern.search(masked):
                violations.append(pattern.pattern)
                masked = pattern.sub(self.placeholder_token, masked)
        return SensitiveProtectionResult(
            succeeded=True,
            text=masked,
            violations=violations,
        )

    def protect_screenshot_file(self, screenshot_path: str) -> SensitiveProtectionResult:
        path = Path(screenshot_path)
        if not path.exists():
            return SensitiveProtectionResult(succeeded=False, file_path=screenshot_path, reason="Screenshot file does not exist.")

        raw = path.read_bytes()
        masked_bytes = raw
        violations: list[str] = []

        for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
            try:
                text = masked_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
            result = self.mask_text(text, location="screenshot")
            if result.text is not None and result.text != text:
                masked_bytes = result.text.encode(encoding)
                violations.extend(result.violations)
                break

        path.write_bytes(masked_bytes)
        return SensitiveProtectionResult(
            succeeded=True,
            file_path=str(path),
            violations=violations,
        )

    def validate_prompt(self, prompt: str, *, location: str) -> SensitiveProtectionResult:
        matches = [pattern.pattern for pattern in self._compiled_patterns if pattern.search(prompt)]
        if matches:
            self.audit_access(
                location=location,
                action="prompt_validation_blocked",
                detail="Prompt contained sensitive value patterns.",
                metadata={"patterns": matches},
            )
            return SensitiveProtectionResult(
                succeeded=False,
                text=prompt,
                violations=matches,
                reason="Prompt contains sensitive values and cannot be submitted.",
            )
        return SensitiveProtectionResult(succeeded=True, text=prompt)

    def audit_access(
        self,
        *,
        location: str,
        action: str,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SensitiveAccessEvent:
        event = SensitiveAccessEvent(
            location=location,
            action=action,
            timestamp=utc_now(),
            detail=detail,
            metadata={} if metadata is None else self.sanitize_payload(metadata),
        )
        if self.access_audit_path is not None:
            path = Path(self.access_audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "location": event.location,
                "action": event.action,
                "timestamp": event.timestamp.isoformat(),
                "detail": event.detail,
                "metadata": event.metadata,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True))
                handle.write("\n")
        return event

    def _is_sensitive_field(self, key: str) -> bool:
        lowered = key.casefold()
        return any(token in lowered for token in self.sensitive_field_names)

    def _matches_sensitive_value(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return any(pattern.search(value) for pattern in self._compiled_patterns)


