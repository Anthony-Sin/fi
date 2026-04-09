from __future__ import annotations

from desktop_automation_agent._time import utc_now

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable

from desktop_automation_agent.models import (
    AllowlistCheckRequest,
    AllowlistCheckResult,
    AllowlistRuleSet,
    AllowlistScope,
    EscalationTriggerType,
)


@dataclass(slots=True)
class ActionAllowlistEnforcer:
    config_path: str
    audit_logger: object | None = None
    escalation_manager: object | None = None
    now_fn: Callable[[], datetime] = utc_now
    _rules: AllowlistRuleSet | None = field(default=None, init=False, repr=False)
    _loaded_marker: tuple[int, int, str] | None = field(default=None, init=False, repr=False)

    def evaluate(self, request: AllowlistCheckRequest) -> AllowlistCheckResult:
        try:
            rules = self.load_rules()
        except FileNotFoundError as exc:
            reason = str(exc)
            escalation_result = self._escalate(request, [AllowlistScope.ACTION_TYPE], reason)
            self._log_block(request, [AllowlistScope.ACTION_TYPE], reason)
            return AllowlistCheckResult(
                succeeded=False,
                request=request,
                allowed=False,
                violated_scopes=[AllowlistScope.ACTION_TYPE],
                escalation_result=escalation_result,
                reason=reason,
            )
        violations: list[AllowlistScope] = []

        if not self._matches(request.action_type, rules.action_types):
            violations.append(AllowlistScope.ACTION_TYPE)
        if request.application_name is not None and not self._matches(request.application_name, rules.applications):
            violations.append(AllowlistScope.APPLICATION)
        if request.url is not None and not self._matches(request.url, rules.urls):
            violations.append(AllowlistScope.URL)
        if request.file_path is not None and not self._matches(self._normalize_path(request.file_path), rules.file_paths):
            violations.append(AllowlistScope.FILE_PATH)

        if not violations:
            return AllowlistCheckResult(
                succeeded=True,
                request=request,
                allowed=True,
                rules=rules,
            )

        reason = self._violation_reason(request, violations)
        escalation_result = self._escalate(request, violations, reason)
        self._log_block(request, violations, reason)
        return AllowlistCheckResult(
            succeeded=False,
            request=request,
            allowed=False,
            rules=rules,
            violated_scopes=violations,
            escalation_result=escalation_result,
            reason=reason,
        )

    def load_rules(self, *, force: bool = False) -> AllowlistRuleSet:
        path = Path(self.config_path)
        if not path.exists():
            raise FileNotFoundError(f"Allowlist configuration file does not exist: {self.config_path}")
        stat = path.stat()
        payload_text = path.read_text(encoding="utf-8")
        marker = (
            stat.st_mtime_ns,
            stat.st_size,
            hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        )
        if not force and self._rules is not None and self._loaded_marker == marker:
            return self._rules

        payload = json.loads(payload_text)
        rules = AllowlistRuleSet(
            action_types=self._normalize_patterns(payload.get("action_types", [])),
            applications=self._normalize_patterns(payload.get("applications", [])),
            urls=self._normalize_patterns(payload.get("urls", [])),
            file_paths=self._normalize_patterns(
                [self._normalize_path(item) for item in payload.get("file_paths", [])]
            ),
            loaded_at=self.now_fn(),
        )
        self._rules = rules
        self._loaded_marker = marker
        return rules

    def _matches(self, value: str, patterns: tuple[str, ...]) -> bool:
        normalized_value = value.casefold()
        return any(fnmatchcase(normalized_value, pattern) for pattern in patterns)

    def _normalize_patterns(self, values: list[str]) -> tuple[str, ...]:
        return tuple(str(item).strip().casefold() for item in values if str(item).strip())

    def _normalize_path(self, value: str) -> str:
        return str(Path(value)).replace("\\", "/").casefold()

    def _violation_reason(
        self,
        request: AllowlistCheckRequest,
        violations: list[AllowlistScope],
    ) -> str:
        scopes = ", ".join(item.value for item in violations)
        return (
            f"Action {request.action_type!r} is not permitted by the allowlist. "
            f"Rejected scopes: {scopes}."
        )

    def _escalate(
        self,
        request: AllowlistCheckRequest,
        violations: list[AllowlistScope],
        reason: str,
    ):
        if self.escalation_manager is None or request.workflow_id is None:
            return None
        return self.escalation_manager.trigger(
            workflow_id=request.workflow_id,
            step_id=request.step_name,
            trigger_type=EscalationTriggerType.ALLOWLIST_VIOLATION,
            detail=reason,
            context_data={
                "action_type": request.action_type,
                "application_name": request.application_name,
                "url": request.url,
                "file_path": request.file_path,
                "violated_scopes": [item.value for item in violations],
                "context_data": dict(request.context_data),
            },
        )

    def _log_block(
        self,
        request: AllowlistCheckRequest,
        violations: list[AllowlistScope],
        reason: str,
    ) -> None:
        if self.audit_logger is None or request.workflow_id is None:
            return
        self.audit_logger.log_action(
            workflow_id=request.workflow_id,
            step_name=request.step_name or "allowlist_enforcer",
            action_type="allowlist_blocked",
            target_element=request.application_name or request.url or request.file_path,
            input_data={
                "action_type": request.action_type,
                "application_name": request.application_name,
                "url": request.url,
                "file_path": request.file_path,
                "context_data": dict(request.context_data),
            },
            output_data={
                "violated_scopes": [item.value for item in violations],
                "reason": reason,
            },
            success=False,
        )


