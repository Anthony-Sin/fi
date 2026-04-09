from __future__ import annotations

import ctypes
import ctypes.wintypes
from dataclasses import dataclass
from time import sleep
from typing import Callable

from desktop_automation_perception.contracts import InputBackend, ScreenInspector, WindowManager
from desktop_automation_perception.models import (
    ActionLogEntry,
    AllowlistCheckRequest,
    InputAction,
    InputActionType,
    InputSimulationResult,
    ScreenBounds,
    WindowReference,
    WindowState,
)


class Win32WindowManager:
    def get_focused_window(self) -> WindowState | None:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)

        rect = ctypes.wintypes.RECT()
        has_rect = bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
        bounds = (rect.left, rect.top, rect.right, rect.bottom) if has_rect else None

        return WindowState(
            reference=WindowReference(title=buffer.value or None, handle=int(hwnd)),
            focused=True,
            bounds=bounds,
        )


@dataclass(slots=True)
class PyAutoGUIBackend:
    _module: object

    @classmethod
    def create(cls) -> "PyAutoGUIBackend":
        import pyautogui

        return cls(_module=pyautogui)

    def click(self, x: int, y: int, button: str) -> None:
        self._module.click(x=x, y=y, button=button)

    def press(self, key: str) -> None:
        self._module.press(key)

    def write(self, text: str) -> None:
        self._module.write(text)

    def scroll(self, clicks: int) -> None:
        self._module.scroll(clicks)

    def hotkey(self, *keys: str) -> None:
        self._module.hotkey(*keys)


@dataclass(slots=True)
class StaticScreenInspector:
    bounds: ScreenBounds

    def get_screen_bounds(self) -> ScreenBounds:
        return self.bounds

    def get_monitor_bounds(self, monitor_id: str | None = None) -> ScreenBounds:
        return self.bounds


class SafeInputSimulator:
    def __init__(
        self,
        backend: InputBackend,
        window_manager: WindowManager,
        screen_inspector: ScreenInspector,
        inter_action_delay_seconds: float = 0.1,
        dry_run: bool = False,
        sleep_fn: Callable[[float], None] = sleep,
        abort_checker: Callable[[], bool] | None = None,
        allowlist_enforcer: object | None = None,
        workflow_id: str | None = None,
        step_name: str = "desktop_input",
        coordinate_manager: object | None = None,
        display_handler: object | None = None,
        pacing_controller: object | None = None,
        account_name: str | None = None,
        application_name: str | None = None,
    ):
        self._backend = backend
        self._window_manager = window_manager
        self._screen_inspector = screen_inspector
        self._inter_action_delay_seconds = inter_action_delay_seconds
        self._dry_run = dry_run
        self._sleep_fn = sleep_fn
        self._abort_checker = abort_checker or (lambda: False)
        self._allowlist_enforcer = allowlist_enforcer
        self._workflow_id = workflow_id
        self._step_name = step_name
        self._coordinate_manager = coordinate_manager
        self._display_handler = display_handler
        self._pacing_controller = pacing_controller
        self._account_name = account_name
        self._application_name = application_name

    def run(self, actions: list[InputAction]) -> InputSimulationResult:
        logs: list[ActionLogEntry] = []

        for original_action in actions:
            action = self._adapt_action(original_action)
            if self._abort_checker():
                failure_reason = "Execution aborted by fail-safe controller."
                logs.append(
                    ActionLogEntry(
                        action=action,
                        executed=False,
                        delay_seconds=0.0,
                        reason=failure_reason,
                    )
                )
                return InputSimulationResult(
                    succeeded=False,
                    logs=logs,
                    failure_reason=failure_reason,
                )

            allowlist_reason = self._allow_action(action)
            if allowlist_reason is not None:
                logs.append(
                    ActionLogEntry(
                        action=action,
                        executed=False,
                        delay_seconds=0.0,
                        reason=allowlist_reason,
                    )
                )
                return InputSimulationResult(
                    succeeded=False,
                    logs=logs,
                    failure_reason=allowlist_reason,
                )

            failure_reason = self._validate_action(action)
            if failure_reason is not None:
                logs.append(
                    ActionLogEntry(
                        action=action,
                        executed=False,
                        delay_seconds=0.0,
                        reason=failure_reason,
                    )
                )
                return InputSimulationResult(
                    succeeded=False,
                    logs=logs,
                    failure_reason=failure_reason,
                )

            if self._dry_run:
                dry_run_delay = self._resolve_post_action_delay(action)
                logs.append(
                    ActionLogEntry(
                        action=action,
                        executed=False,
                        delay_seconds=dry_run_delay,
                        reason="Dry run enabled; action was logged but not executed.",
                    )
                )
                continue

            total_delay = self._execute_action(action)
            logs.append(
                ActionLogEntry(
                    action=action,
                    executed=True,
                    delay_seconds=total_delay,
                )
            )
            if self._abort_checker():
                return InputSimulationResult(
                    succeeded=False,
                    logs=logs,
                    failure_reason="Execution aborted by fail-safe controller.",
                )
        return InputSimulationResult(
            succeeded=True,
            logs=logs,
        )

    def _adapt_action(self, action: InputAction) -> InputAction:
        if self._coordinate_manager is None:
            return action

        target = action.target
        adapted_target = target
        if target is not None and target.element_bounds is not None:
            adapted_bounds = self._coordinate_manager.adapt_bounds(target.element_bounds).adapted_bounds
            adapted_target = type(target)(
                window=target.window,
                element_bounds=adapted_bounds,
                monitor_id=target.monitor_id,
            )
        adapted_position = action.position
        if action.position is not None:
            adapted_position = self._coordinate_manager.adapt_point(action.position).adapted_point
        return InputAction(
            action_type=action.action_type,
            target=adapted_target,
            position=adapted_position,
            monitor_id=action.monitor_id,
            button=action.button,
            key=action.key,
            text=action.text,
            scroll_amount=action.scroll_amount,
            hotkey=action.hotkey,
            context_tags=action.context_tags,
        )

    def _validate_action(self, action: InputAction) -> str | None:
        target = action.target
        if target is not None:
            if target.window is not None:
                window_reason = self._validate_window_focus(target.window)
                if window_reason is not None:
                    return window_reason

            if target.element_bounds is not None:
                visibility_reason = self._validate_visibility(target.element_bounds, target.monitor_id or action.monitor_id)
                if visibility_reason is not None:
                    return visibility_reason

        if action.position is not None and (action.monitor_id is not None or (target is not None and target.monitor_id is not None)):
            monitor_id = action.monitor_id or (target.monitor_id if target is not None else None)
            point_reason = self._validate_position(action.position, monitor_id)
            if point_reason is not None:
                return point_reason

        if action.action_type is InputActionType.CLICK and action.position is None:
            if target is None or target.element_bounds is None:
                return "Click action requires an explicit position or visible target element bounds."

        if action.action_type is InputActionType.KEYPRESS and not action.key:
            return "Keypress action requires a key value."

        if action.action_type is InputActionType.TYPE_TEXT and action.text is None:
            return "Type text action requires text content."

        if action.action_type is InputActionType.SCROLL and action.scroll_amount is None:
            return "Scroll action requires a scroll amount."

        if action.action_type is InputActionType.HOTKEY and not action.hotkey:
            return "Hotkey action requires at least one key."

        return None

    def _validate_window_focus(self, window: WindowReference) -> str | None:
        focused_window = self._window_manager.get_focused_window()
        if focused_window is None:
            return "No focused window is available."
        if not focused_window.focused:
            return "The current foreground window is not reported as focused."

        if window.handle is not None and focused_window.reference.handle != window.handle:
            return "Target window is not focused."

        if window.title is not None and focused_window.reference.title != window.title:
            return "Target window is not focused."

        return None

    def _validate_visibility(self, bounds: tuple[int, int, int, int], monitor_id: str | None = None) -> str | None:
        screen_bounds = self._resolve_bounds(monitor_id)
        if not screen_bounds.contains_bounds(bounds):
            return "Target element is not fully visible within screen boundaries."
        return None

    def _validate_position(self, position: tuple[int, int], monitor_id: str | None) -> str | None:
        screen_bounds = self._resolve_bounds(monitor_id)
        if not screen_bounds.contains_point(position):
            return "Click position is outside the requested monitor boundaries."
        return None

    def _execute_action(self, action: InputAction) -> float:
        total_delay = 0.0
        if action.action_type is InputActionType.CLICK:
            x, y = action.position or self._center(action.target.element_bounds)
            total_delay += self._apply_pre_action_delay(action)
            self._backend.click(x, y, action.button)
            total_delay += self._apply_post_action_delay(action)
            return total_delay

        if action.action_type is InputActionType.KEYPRESS:
            total_delay += self._apply_pre_action_delay(action)
            self._backend.press(action.key or "")
            total_delay += self._apply_post_action_delay(action)
            return total_delay

        if action.action_type is InputActionType.TYPE_TEXT:
            total_delay += self._apply_typing_action(action)
            total_delay += self._apply_post_action_delay(action)
            return total_delay

        if action.action_type is InputActionType.SCROLL:
            total_delay += self._apply_pre_action_delay(action)
            self._backend.scroll(action.scroll_amount or 0)
            total_delay += self._apply_post_action_delay(action)
            return total_delay

        if action.action_type is InputActionType.HOTKEY:
            total_delay += self._apply_pre_action_delay(action)
            self._backend.hotkey(*action.hotkey)
            total_delay += self._apply_post_action_delay(action)
            return total_delay

        raise ValueError(f"Unsupported action type: {action.action_type}")

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int]:
        if bounds is None:
            raise ValueError("Bounds are required to calculate the target center.")
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)

    def _allow_action(self, action: InputAction) -> str | None:
        if self._allowlist_enforcer is None:
            return None
        target_window = action.target.window.title if action.target is not None and action.target.window is not None else None
        decision = self._allowlist_enforcer.evaluate(
            AllowlistCheckRequest(
                workflow_id=self._workflow_id,
                step_name=self._step_name,
                action_type=action.action_type.value,
                application_name=target_window,
            )
        )
        if decision.allowed:
            return None
        return decision.reason

    def _resolve_bounds(self, monitor_id: str | None) -> ScreenBounds:
        if monitor_id is not None and self._display_handler is not None:
            return self._display_handler.get_monitor_bounds(monitor_id)
        if monitor_id is not None and hasattr(self._screen_inspector, "get_monitor_bounds"):
            return self._screen_inspector.get_monitor_bounds(monitor_id)
        return self._screen_inspector.get_screen_bounds()

    def _apply_pre_action_delay(self, action: InputAction) -> float:
        delay = self._resolve_pre_action_delay(action)
        if delay > 0:
            self._sleep_fn(delay)
        return delay

    def _apply_post_action_delay(self, action: InputAction) -> float:
        delay = self._resolve_post_action_delay(action)
        if delay > 0:
            self._sleep_fn(delay)
        return delay

    def _apply_typing_action(self, action: InputAction) -> float:
        text = action.text or ""
        if self._pacing_controller is None:
            self._backend.write(text)
            return 0.0

        total_delay = 0.0
        for index, character in enumerate(text):
            if index == 0:
                initial_delay = self._resolve_pre_action_delay(action)
                if initial_delay > 0:
                    self._sleep_fn(initial_delay)
                    total_delay += initial_delay
            self._backend.write(character)
            decisions = self._pacing_controller.typing_delays(
                character,
                account_name=self._account_name,
                application_name=self._application_name,
            )
            for decision in decisions:
                if decision.delay_seconds > 0:
                    self._sleep_fn(decision.delay_seconds)
                    total_delay += decision.delay_seconds
        if not text:
            total_delay += self._apply_pre_action_delay(action)
        return total_delay

    def _resolve_pre_action_delay(self, action: InputAction) -> float:
        if self._pacing_controller is None:
            return 0.0
        decision = self._pacing_controller.before_action(
            self._pacing_context(action)
        )
        return decision.delay_seconds

    def _resolve_post_action_delay(self, action: InputAction) -> float:
        if self._pacing_controller is None:
            return self._inter_action_delay_seconds
        decision = self._pacing_controller.after_action(
            self._pacing_context(action)
        )
        return decision.delay_seconds

    def _pacing_context(self, action: InputAction):
        from desktop_automation_perception.models import PacingContext

        return PacingContext(
            action=action,
            account_name=self._account_name,
            application_name=self._application_name,
        )
