from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    ScreenCheckType,
    ScreenVerificationCheck,
    SessionHealthEvent,
    SessionState,
    SessionTrackerSnapshot,
    SessionValidationResult,
)


@dataclass(slots=True)
class SessionStateTracker:
    storage_path: str
    verifier: object
    reauthenticate_callback: Callable[[], bool] | None = None

    def detect_session_state(
        self,
        *,
        post_login_checks: list[ScreenVerificationCheck],
        login_page_checks: list[ScreenVerificationCheck],
    ) -> SessionValidationResult:
        logged_in_result = self.verifier.verify(post_login_checks)
        if not logged_in_result.failed_checks:
            return self._record_state(
                SessionState.LOGGED_IN,
                "Post-login UI elements are present.",
                logged_in_result.screenshot_path,
            )

        login_page_result = self.verifier.verify(login_page_checks)
        if not login_page_result.failed_checks:
            return self._record_state(
                SessionState.EXPIRED,
                "Login page detected, session appears expired or logged out.",
                login_page_result.screenshot_path,
            )

        return self._record_state(
            SessionState.UNKNOWN,
            "Unable to confirm session state from current UI.",
            logged_in_result.screenshot_path or login_page_result.screenshot_path,
        )

    def validate_session_before_high_risk_operation(
        self,
        *,
        post_login_checks: list[ScreenVerificationCheck],
        login_page_checks: list[ScreenVerificationCheck],
    ) -> SessionValidationResult:
        state = self.detect_session_state(
            post_login_checks=post_login_checks,
            login_page_checks=login_page_checks,
        )
        if state.state is SessionState.LOGGED_IN:
            return state

        if self.reauthenticate_callback is not None:
            reauthenticated = self.reauthenticate_callback()
            if reauthenticated:
                return self.detect_session_state(
                    post_login_checks=post_login_checks,
                    login_page_checks=login_page_checks,
                )
            return self._record_state(
                SessionState.LOGGED_OUT,
                "Re-authentication flow failed.",
                state.screenshot_path,
            )

        return SessionValidationResult(
            succeeded=False,
            state=state.state,
            reason=state.reason or "Session validation failed before high-risk operation.",
            screenshot_path=state.screenshot_path,
        )

    def get_health_log(self) -> list[SessionHealthEvent]:
        return self._load_snapshot().health_log

    def _record_state(
        self,
        state: SessionState,
        detail: str,
        screenshot_path: str | None,
    ) -> SessionValidationResult:
        snapshot = self._load_snapshot()
        event = SessionHealthEvent(
            state=state,
            timestamp=datetime.now(timezone.utc),
            detail=detail,
            screenshot_path=screenshot_path,
        )
        snapshot.current_state = state
        snapshot.health_log.append(event)
        self._save_snapshot(snapshot)
        return SessionValidationResult(
            succeeded=state is SessionState.LOGGED_IN,
            state=state,
            reason=None if state is SessionState.LOGGED_IN else detail,
            screenshot_path=screenshot_path,
        )

    def _load_snapshot(self) -> SessionTrackerSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return SessionTrackerSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SessionTrackerSnapshot(
            current_state=SessionState(payload.get("current_state", SessionState.UNKNOWN.value)),
            health_log=[
                SessionHealthEvent(
                    state=SessionState(item["state"]),
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    detail=item.get("detail"),
                    screenshot_path=item.get("screenshot_path"),
                )
                for item in payload.get("health_log", [])
            ],
        )

    def _save_snapshot(self, snapshot: SessionTrackerSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "current_state": snapshot.current_state.value,
            "health_log": [
                {
                    "state": event.state.value,
                    "timestamp": event.timestamp.isoformat(),
                    "detail": event.detail,
                    "screenshot_path": event.screenshot_path,
                }
                for event in snapshot.health_log
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
