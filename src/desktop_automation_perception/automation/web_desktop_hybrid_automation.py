from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from desktop_automation_perception.models import (
    HybridAutomationResult,
    HybridAutomationSession,
    InputAction,
    InputActionType,
    InputTarget,
    ScreenVerificationCheck,
    WindowReference,
)


@dataclass(slots=True)
class WebDesktopHybridAutomation:
    browser_launcher: object
    web_automation_backend: object
    window_manager: object
    input_runner: object
    state_verifier: object | None = None
    browser_executable: str | None = None
    profile_directory: str | None = None
    browser_application: str | None = None
    desktop_action_handler: Callable[[str, dict[str, Any], HybridAutomationSession], object] | None = None
    _session: HybridAutomationSession = field(default_factory=HybridAutomationSession, init=False, repr=False)

    def launch_browser(self) -> HybridAutomationResult:
        process_id = None
        if self.browser_executable is not None and self.profile_directory is not None:
            process_id = self.browser_launcher.launch(
                browser_executable=self.browser_executable,
                profile_directory=self.profile_directory,
                application=self.browser_application,
            )
        self._session.browser_process_id = process_id
        self._session.web_session_id = self._session.web_session_id or f"web-session-{uuid4().hex}"
        self._session.web_state["launched"] = True
        return HybridAutomationResult(succeeded=True, session=self._snapshot_session(), web_result=process_id)

    def navigate(self, url: str) -> HybridAutomationResult:
        result = self.web_automation_backend.navigate(url)
        if getattr(result, "succeeded", True) is False:
            return HybridAutomationResult(
                succeeded=False,
                session=self._snapshot_session(),
                web_result=result,
                reason=getattr(result, "reason", "Failed to navigate browser to the requested URL."),
            )
        self._session.current_url = url
        self._session.web_state["current_url"] = url
        return HybridAutomationResult(succeeded=True, session=self._snapshot_session(), web_result=result)

    def interact_with_web(
        self,
        action: str,
        selector: str,
        *,
        value: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> HybridAutomationResult:
        interaction_payload = {} if payload is None else dict(payload)
        backend_action = getattr(self.web_automation_backend, action, None)
        if not callable(backend_action):
            return HybridAutomationResult(
                succeeded=False,
                session=self._snapshot_session(),
                reason=f"Web automation backend does not support action {action!r}.",
            )

        result = backend_action(selector, value=value, payload=interaction_payload)
        if getattr(result, "succeeded", True) is False:
            return HybridAutomationResult(
                succeeded=False,
                session=self._snapshot_session(),
                web_result=result,
                reason=getattr(result, "reason", "Web interaction failed."),
            )
        self._session.web_state["last_action"] = {
            "action": action,
            "selector": selector,
            "value": value,
            "payload": interaction_payload,
        }
        return HybridAutomationResult(succeeded=True, session=self._snapshot_session(), web_result=result)

    def handle_native_dialog(
        self,
        *,
        window_title: str | None = None,
        process_name: str | None = None,
        actions: list[dict[str, Any]] | None = None,
        desktop_handler_name: str | None = None,
        desktop_payload: dict[str, Any] | None = None,
    ) -> HybridAutomationResult:
        focus_result = self.window_manager.focus_window(title=window_title, process_name=process_name)
        if getattr(focus_result, "succeeded", False) is False:
            return HybridAutomationResult(
                succeeded=False,
                session=self._snapshot_session(),
                desktop_result=focus_result,
                reason=getattr(focus_result, "reason", "Failed to focus native dialog."),
            )
        self._update_desktop_focus(focus_result)

        desktop_result = None
        if desktop_handler_name is not None:
            desktop_result = self._run_desktop_handler(desktop_handler_name, desktop_payload or {})
            if getattr(desktop_result, "succeeded", True) is False:
                return HybridAutomationResult(
                    succeeded=False,
                    session=self._snapshot_session(),
                    desktop_result=desktop_result,
                    reason=getattr(desktop_result, "reason", "Desktop dialog handler failed."),
                )
        elif actions:
            runner_actions = [self._build_input_action(item) for item in actions]
            desktop_result = self.input_runner.run(runner_actions)
            if getattr(desktop_result, "succeeded", False) is False:
                return HybridAutomationResult(
                    succeeded=False,
                    session=self._snapshot_session(),
                    desktop_result=desktop_result,
                    reason=getattr(desktop_result, "failure_reason", "Desktop input sequence failed."),
                )

        self._session.desktop_state["last_dialog"] = {
            "window_title": window_title,
            "process_name": process_name,
            "actions": [] if actions is None else list(actions),
            "handler": desktop_handler_name,
        }
        return HybridAutomationResult(
            succeeded=True,
            session=self._snapshot_session(),
            desktop_result=desktop_result or focus_result,
        )

    def handle_desktop_notification(
        self,
        *,
        window_title: str | None = None,
        process_name: str | None = None,
        verification_checks: list[ScreenVerificationCheck] | None = None,
        desktop_handler_name: str | None = None,
        desktop_payload: dict[str, Any] | None = None,
    ) -> HybridAutomationResult:
        desktop_result = None
        if window_title is not None or process_name is not None:
            focus_result = self.window_manager.focus_window(title=window_title, process_name=process_name)
            if getattr(focus_result, "succeeded", False) is False:
                return HybridAutomationResult(
                    succeeded=False,
                    session=self._snapshot_session(),
                    desktop_result=focus_result,
                    reason=getattr(focus_result, "reason", "Failed to focus desktop notification."),
                )
            self._update_desktop_focus(focus_result)
            desktop_result = focus_result

        if desktop_handler_name is not None:
            desktop_result = self._run_desktop_handler(desktop_handler_name, desktop_payload or {})
            if getattr(desktop_result, "succeeded", True) is False:
                return HybridAutomationResult(
                    succeeded=False,
                    session=self._snapshot_session(),
                    desktop_result=desktop_result,
                    reason=getattr(desktop_result, "reason", "Desktop notification handler failed."),
                )

        verification = None
        if verification_checks and self.state_verifier is not None:
            verification = self.state_verifier.verify(verification_checks)
            if getattr(verification, "failed_checks", []):
                return HybridAutomationResult(
                    succeeded=False,
                    session=self._snapshot_session(),
                    desktop_result=desktop_result,
                    verification=verification,
                    reason="Desktop notification verification failed.",
                )

        self._session.desktop_state["last_notification"] = {
            "window_title": window_title,
            "process_name": process_name,
            "handler": desktop_handler_name,
        }
        return HybridAutomationResult(
            succeeded=True,
            session=self._snapshot_session(),
            desktop_result=desktop_result,
            verification=verification,
        )

    def inspect_session(self) -> HybridAutomationSession:
        return self._snapshot_session()

    def _run_desktop_handler(self, name: str, payload: dict[str, Any]) -> object:
        if self.desktop_action_handler is None:
            raise RuntimeError("No desktop action handler is configured.")
        return self.desktop_action_handler(name, payload, self._snapshot_session())

    def _build_input_action(self, payload: dict[str, Any]) -> InputAction:
        action_type = InputActionType(payload["action_type"])
        window_title = payload.get("window_title")
        window_handle = payload.get("window_handle")
        element_bounds = payload.get("element_bounds")
        target = None
        if window_title is not None or window_handle is not None or element_bounds is not None:
            target = InputTarget(
                window=WindowReference(title=window_title, handle=window_handle)
                if window_title is not None or window_handle is not None
                else None,
                element_bounds=element_bounds,
            )
        return InputAction(
            action_type=action_type,
            target=target,
            position=payload.get("position"),
            button=payload.get("button", "left"),
            key=payload.get("key"),
            text=payload.get("text"),
            scroll_amount=payload.get("scroll_amount"),
            hotkey=tuple(payload.get("hotkey", ())),
        )

    def _update_desktop_focus(self, focus_result: object) -> None:
        window = getattr(focus_result, "window", None)
        if window is not None:
            self._session.active_window_title = getattr(window, "title", None)
            self._session.active_process_name = getattr(window, "process_name", None)
            self._session.desktop_state["active_window_title"] = self._session.active_window_title
            self._session.desktop_state["active_process_name"] = self._session.active_process_name

    def _snapshot_session(self) -> HybridAutomationSession:
        return HybridAutomationSession(
            browser_process_id=self._session.browser_process_id,
            web_session_id=self._session.web_session_id,
            current_url=self._session.current_url,
            active_window_title=self._session.active_window_title,
            active_process_name=self._session.active_process_name,
            web_state=dict(self._session.web_state),
            desktop_state=dict(self._session.desktop_state),
        )
