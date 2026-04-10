from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Callable
from urllib.parse import urlencode

from desktop_automation_agent.contracts import (
    AccessibilityReader,
    ApplicationLauncherBackend,
    OCRExtractor,
    ScreenshotBackend,
    TemplateMatcher,
    WindowManager,
)
from desktop_automation_agent.models import (
    AllowlistCheckRequest,
    AIInterfaceElementMatch,
    AIInterfaceElementSelector,
    AccessibilityElement,
    ApplicationLaunchAttempt,
    ApplicationLaunchMode,
    ApplicationLaunchRequest,
    ApplicationLaunchResult,
    ApplicationLaunchStatus,
    ApplicationRegistrySnapshot,
    ApplicationStartupSignature,
    KnownApplicationRecord,
    OCRTextBlock,
    SelectorStrategy,
    TemplateSearchRequest,
)


@dataclass(slots=True)
class SubprocessApplicationLauncherBackend:
    def launch_executable(self, executable_path: str, arguments: tuple[str, ...]) -> bool:
        import subprocess

        print(f"DEBUG: Executing subprocess.Popen([{executable_path}, {', '.join(map(repr, arguments))}])")
        subprocess.Popen([executable_path, *arguments])
        return True

    def launch_start_menu(self, query: str, arguments: tuple[str, ...]) -> bool:
        import subprocess

        cmd = ["powershell", "-Command", f"Start-Process shell:AppsFolder\\{query}"]
        print(f"DEBUG: Executing subprocess.Popen({cmd})")
        subprocess.Popen(cmd)
        return True

    def launch_url(self, url: str) -> bool:
        import webbrowser

        print(f"DEBUG: Executing webbrowser.open('{url}')")
        return bool(webbrowser.open(url))


@dataclass(slots=True)
class ApplicationRegistry:
    storage_path: str

    def list_applications(self) -> list[KnownApplicationRecord]:
        return self._load_snapshot().applications

    def get_application(self, name: str) -> KnownApplicationRecord | None:
        for application in self._load_snapshot().applications:
            if application.name.casefold() == name.casefold():
                return application
        return None

    def upsert_application(self, record: KnownApplicationRecord) -> KnownApplicationRecord:
        snapshot = self._load_snapshot()
        snapshot.applications = [
            item for item in snapshot.applications if item.name.casefold() != record.name.casefold()
        ] + [record]
        self._save_snapshot(snapshot)
        return record

    def _load_snapshot(self) -> ApplicationRegistrySnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return ApplicationRegistrySnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ApplicationRegistrySnapshot(
            applications=[self._deserialize_application(item) for item in payload.get("applications", [])]
        )

    def _save_snapshot(self, snapshot: ApplicationRegistrySnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "applications": [self._serialize_application(item) for item in snapshot.applications]
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_application(self, record: KnownApplicationRecord) -> dict:
        return {
            "name": record.name,
            "launch_mode": record.launch_mode.value,
            "executable_path": record.executable_path,
            "start_menu_query": record.start_menu_query,
            "url": record.url,
            "default_arguments": list(record.default_arguments),
            "default_url_parameters": record.default_url_parameters,
            "startup_signature": self._serialize_signature(record.startup_signature),
            "pacing_profile_id": record.pacing_profile_id,
        }

    def _deserialize_application(self, payload: dict) -> KnownApplicationRecord:
        return KnownApplicationRecord(
            name=payload["name"],
            launch_mode=ApplicationLaunchMode(payload["launch_mode"]),
            executable_path=payload.get("executable_path"),
            start_menu_query=payload.get("start_menu_query"),
            url=payload.get("url"),
            default_arguments=tuple(payload.get("default_arguments", [])),
            default_url_parameters=dict(payload.get("default_url_parameters", {})),
            startup_signature=self._deserialize_signature(payload.get("startup_signature")),
            pacing_profile_id=payload.get("pacing_profile_id"),
        )

    def _serialize_signature(self, signature: ApplicationStartupSignature | None) -> dict | None:
        if signature is None:
            return None
        selector = signature.element_selector
        return {
            "window_title": signature.window_title,
            "process_name": signature.process_name,
            "element_selector": None
            if selector is None
            else {
                "name": selector.name,
                "role": selector.role,
                "value": selector.value,
                "target_text": selector.target_text,
                "template_name": selector.template_name,
                "template_path": selector.template_path,
                "bounds": selector.bounds,
                "region_of_interest": selector.region_of_interest,
                "window_title": selector.window_title,
                "process_name": selector.process_name,
                "strategies": [strategy.value for strategy in selector.strategies],
                "threshold": selector.threshold,
                "required": selector.required,
            },
        }

    def _deserialize_signature(self, payload: dict | None) -> ApplicationStartupSignature | None:
        if payload is None:
            return None
        selector_payload = payload.get("element_selector")
        selector = None
        if selector_payload is not None:
            selector = AIInterfaceElementSelector(
                name=selector_payload.get("name"),
                role=selector_payload.get("role"),
                value=selector_payload.get("value"),
                target_text=selector_payload.get("target_text"),
                template_name=selector_payload.get("template_name"),
                template_path=selector_payload.get("template_path"),
                bounds=tuple(selector_payload["bounds"]) if selector_payload.get("bounds") is not None else None,
                region_of_interest=tuple(selector_payload["region_of_interest"])
                if selector_payload.get("region_of_interest") is not None
                else None,
                window_title=selector_payload.get("window_title"),
                process_name=selector_payload.get("process_name"),
                strategies=tuple(SelectorStrategy(item) for item in selector_payload.get("strategies", []))
                or (
                    SelectorStrategy.ACCESSIBILITY,
                    SelectorStrategy.OCR,
                    SelectorStrategy.TEMPLATE_MATCH,
                ),
                threshold=float(selector_payload.get("threshold", 0.8)),
                required=bool(selector_payload.get("required", True)),
            )
        return ApplicationStartupSignature(
            window_title=payload.get("window_title"),
            process_name=payload.get("process_name"),
            element_selector=selector,
        )


@dataclass
class ApplicationLauncher:
    registry: ApplicationRegistry
    backend: ApplicationLauncherBackend
    window_manager: WindowManager | None = None
    accessibility_reader: AccessibilityReader | None = None
    ocr_extractor: OCRExtractor | None = None
    template_matcher: TemplateMatcher | None = None
    screenshot_backend: ScreenshotBackend | None = None
    allowlist_enforcer: object | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic

    def launch(self, request: ApplicationLaunchRequest, # FI_NEURAL_LINK_VERIFIED
        *,
        workflow_id: str | None = None,
        step_name: str = "application_launch",
    ) -> ApplicationLaunchResult:
        application = self._resolve_application(request)
        if application is None:
            # GUI Fallback if app not in registry
            if self.launch_via_gui(request.application_name):
                return ApplicationLaunchResult(
                    succeeded=True,
                    status=ApplicationLaunchStatus.STARTED,
                    launched_command=("gui-fallback", request.application_name)
                )
            return ApplicationLaunchResult(
                succeeded=False,
                status=ApplicationLaunchStatus.FAILED,
                reason="Application is not registered and GUI fallback failed.",
            )
        allowlist_result = self._allow(request=request, application=application, workflow_id=workflow_id, step_name=step_name)
        if allowlist_result is not None:
            return allowlist_result

        attempts: list[ApplicationLaunchAttempt] = []
        last_command: tuple[str, ...] = ()
        for attempt_number in range(1, max(1, request.retry_attempts) + 1):
            launched, command, reason = self._launch_once(application, request)
            last_command = command
            verified = False
            if launched:
                verified = self._wait_for_startup_signature(
                    request.startup_signature or application.startup_signature,
                    timeout_seconds=request.timeout_seconds,
                )
            attempt = ApplicationLaunchAttempt(
                attempt_number=attempt_number,
                command=command,
                launched=launched,
                verified=verified,
                reason=None if launched and verified else (reason or "Application did not reach its startup signature."),
            )
            attempts.append(attempt)
            if launched and verified:
                return ApplicationLaunchResult(
                    succeeded=True,
                    status=ApplicationLaunchStatus.STARTED,
                    application=application,
                    attempts=attempts,
                    launched_command=command,
                )
            if attempt_number < max(1, request.retry_attempts):
                self.sleep_fn(request.retry_delay_seconds)

        status = ApplicationLaunchStatus.ESCALATED if request.escalate_on_failure else ApplicationLaunchStatus.TIMEOUT
        return ApplicationLaunchResult(
            succeeded=False,
            status=status,
            application=application,
            attempts=attempts,
            launched_command=last_command,
            reason="Application failed to launch successfully within the configured retry policy.",
        )

    def _allow(
        self,
        *,
        request: ApplicationLaunchRequest,
        application: KnownApplicationRecord,
        workflow_id: str | None,
        step_name: str,
    ) -> ApplicationLaunchResult | None:
        if self.allowlist_enforcer is None:
            return None
        url = None
        if application.launch_mode is ApplicationLaunchMode.URL and application.url:
            url = self._with_query_parameters(application.url, application.default_url_parameters)
        decision = self.allowlist_enforcer.evaluate(
            AllowlistCheckRequest(
                workflow_id=workflow_id,
                step_name=step_name,
                action_type="launch_application",
                application_name=application.name,
                url=url,
                context_data={"launch_mode": application.launch_mode.value},
            )
        )
        if decision.allowed:
            return None
        return ApplicationLaunchResult(
            succeeded=False,
            status=ApplicationLaunchStatus.ESCALATED,
            application=application,
            reason=decision.reason,
        )

    def register_application(self, record: KnownApplicationRecord) -> KnownApplicationRecord:
        return self.registry.upsert_application(record)

    def _resolve_application(self, request: ApplicationLaunchRequest) -> KnownApplicationRecord | None:
        registered = self.registry.get_application(request.application_name)
        if registered is None:
            if (
                request.executable_path is None
                and request.start_menu_query is None
                and request.url is None
            ):
                return None
            return KnownApplicationRecord(
                name=request.application_name,
                launch_mode=request.launch_mode,
                executable_path=request.executable_path,
                start_menu_query=request.start_menu_query,
                url=request.url,
                default_arguments=request.arguments,
                default_url_parameters=request.url_parameters,
                startup_signature=request.startup_signature,
                pacing_profile_id=None,
            )

        return KnownApplicationRecord(
            name=registered.name,
            launch_mode=request.launch_mode if request.launch_mode else registered.launch_mode,
            executable_path=request.executable_path or registered.executable_path,
            start_menu_query=request.start_menu_query or registered.start_menu_query,
            url=request.url or registered.url,
            default_arguments=registered.default_arguments + tuple(request.arguments),
            default_url_parameters={**registered.default_url_parameters, **request.url_parameters},
            startup_signature=request.startup_signature or registered.startup_signature,
            pacing_profile_id=registered.pacing_profile_id,
        )

    def _launch_once(
        self,
        application: KnownApplicationRecord,
        request: ApplicationLaunchRequest,
    ) -> tuple[bool, tuple[str, ...], str | None]:
        if application.launch_mode is ApplicationLaunchMode.EXECUTABLE:
            if not application.executable_path:
                return (False, (), "Executable path is required for executable launch mode.")
            command = (application.executable_path, *application.default_arguments)
            try:
                launched = self.backend.launch_executable(application.executable_path, application.default_arguments)
            except Exception as exc:
                return (False, command, str(exc))
            return (launched, command, None if launched else "Executable launch failed.")

        if application.launch_mode is ApplicationLaunchMode.START_MENU:
            if not application.start_menu_query:
                return (False, (), "Start menu query is required for Start menu launch mode.")
            command = ("start-menu", application.start_menu_query, *application.default_arguments)
            try:
                launched = self.backend.launch_start_menu(application.start_menu_query, application.default_arguments)
            except Exception as exc:
                return (False, command, str(exc))
            return (launched, command, None if launched else "Start menu launch failed.")

        if application.launch_mode is ApplicationLaunchMode.URL:
            if not application.url:
                return (False, (), "URL is required for URL launch mode.")
            url = self._with_query_parameters(application.url, application.default_url_parameters)
            command = ("url", url)
            try:
                launched = self.backend.launch_url(url)
            except Exception as exc:
                return (False, command, str(exc))
            return (launched, command, None if launched else "URL launch failed.")

        return (False, (), "Unsupported application launch mode.")

    def launch_via_gui(self, query: str) -> bool:
        """Fallback to GUI-based launching (Win+R) if registration or subprocess fails."""
        import pyautogui
        import time
        try:
            # Clear any existing text in Win+R dialog
            print("DEBUG: Executing GUI fallback (Win+R)")
            print("DEBUG: pyautogui.hotkey('win', 'r')")
            pyautogui.hotkey("win", "r")
            time.sleep(0.5)
            # Ensure the dialog is focused and clean
            print("DEBUG: pyautogui.hotkey('ctrl', 'a')")
            pyautogui.hotkey("ctrl", "a")
            print("DEBUG: pyautogui.press('backspace')")
            pyautogui.press("backspace")
            print(f"DEBUG: pyautogui.write('{query}')")
            pyautogui.write(query, interval=0.01)
            print("DEBUG: pyautogui.press('enter')")
            pyautogui.press("enter")
            return True
        except Exception as e:
            print(f"DEBUG: GUI fallback failed: {e}")
            return False

    def _wait_for_startup_signature(
        self,
        signature: ApplicationStartupSignature | None,
        *,
        timeout_seconds: float,
    ) -> bool:
        if signature is None:
            return True
        deadline = self.monotonic_fn() + timeout_seconds
        while self.monotonic_fn() <= deadline:
            if self._matches_startup_signature(signature):
                return True
            self.sleep_fn(0.25)
        return False

    def _matches_startup_signature(self, signature: ApplicationStartupSignature) -> bool:
        if self.window_manager is not None and (signature.window_title or signature.process_name):
            windows = self.window_manager.list_windows()
            window_match = next(
                (
                    window
                    for window in windows
                    if (signature.window_title is None or signature.window_title.casefold() in window.title.casefold())
                    and (
                        signature.process_name is None
                        or (window.process_name or "").casefold() == signature.process_name.casefold()
                    )
                ),
                None,
            )
            if window_match is None:
                return False

        if signature.element_selector is not None:
            return self._resolve_selector(signature.element_selector) is not None

        return True

    def _resolve_selector(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if selector.bounds is not None:
            return AIInterfaceElementMatch(
                selector=selector,
                strategy=SelectorStrategy.DIRECT_BOUNDS,
                bounds=selector.bounds,
                center=self._center(selector.bounds),
                confidence=1.0,
            )
        for strategy in selector.strategies:
            if strategy is SelectorStrategy.ACCESSIBILITY:
                match = self._resolve_accessibility(selector)
            elif strategy is SelectorStrategy.OCR:
                match = self._resolve_ocr(selector)
            elif strategy is SelectorStrategy.TEMPLATE_MATCH:
                match = self._resolve_template(selector)
            else:
                match = None
            if match is not None:
                return match
        return None

    def _resolve_accessibility(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.accessibility_reader is None:
            return None
        query = self.accessibility_reader.find_elements(
            name=selector.name,
            role=selector.role,
            value=selector.value,
        )
        matches = getattr(query, "matches", [])
        if not matches:
            return None
        element = matches[0]
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.ACCESSIBILITY,
            bounds=element.bounds,
            center=self._center(element.bounds),
            text=self.accessibility_reader.get_element_text(element),
            element=element,
            confidence=1.0,
        )

    def _resolve_ocr(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.ocr_extractor is None or not selector.target_text:
            return None
        result = self.ocr_extractor.find_text(
            target=selector.target_text,
            region_of_interest=selector.region_of_interest,
        )
        if not getattr(result, "succeeded", False):
            return None
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.OCR,
            bounds=getattr(result, "bounds", None),
            center=self._center(getattr(result, "bounds", None)),
            text=getattr(result, "matched_text", None),
            confidence=float(getattr(result, "confidence", 0.0)),
        )

    def _resolve_template(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.template_matcher is None or self.screenshot_backend is None or not selector.template_path:
            return None
        screenshot_path = self.screenshot_backend.capture_screenshot_to_path()
        results = self.template_matcher.search(
            screenshot_path=screenshot_path,
            requests=[
                TemplateSearchRequest(
                    template_name=selector.template_name or "startup",
                    template_path=selector.template_path,
                    threshold=selector.threshold,
                    region_of_interest=selector.region_of_interest,
                )
            ],
        )
        if not results or not results[0].matches:
            return None
        match = results[0].matches[0]
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.TEMPLATE_MATCH,
            bounds=match.bounds,
            center=match.center,
            confidence=match.confidence,
        )

    def _with_query_parameters(self, url: str, parameters: dict[str, str]) -> str:
        if not parameters:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode(parameters)}"

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
