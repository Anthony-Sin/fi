from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep
from typing import Callable

from desktop_automation_agent.models import (
    FailSafeActivationRecord,
    FailSafeActivationResult,
    FailSafeResourceReleaseResult,
    FailSafeTriggerType,
    WorkflowContext,
    WorkflowStepResult,
)


class PyAutoGUIPointerBackend:
    def get_position(self) -> tuple[int, int]:
        import pyautogui

        position = pyautogui.position()
        return (int(position.x), int(position.y))

    def get_screen_size(self) -> tuple[int, int]:
        import pyautogui

        size = pyautogui.size()
        return (int(size.width), int(size.height))


class KeyboardHotkeyBackend:
    def is_hotkey_pressed(self, keys: tuple[str, ...]) -> bool:
        if not keys:
            return False
        import keyboard

        return bool(keyboard.is_pressed("+".join(keys)))


@dataclass(slots=True)
class FailSafeController:
    storage_path: str
    workflow_id: str
    checkpoint_manager: object | None = None
    audit_logger: object | None = None
    task_queue_manager: object | None = None
    screenshot_backend: object | None = None
    pointer_backend: object | None = None
    keyboard_backend: object | None = None
    workflow_context_provider: Callable[[], WorkflowContext] | None = None
    step_results_provider: Callable[[], list[WorkflowStepResult]] | None = None
    step_index_provider: Callable[[], int] | None = None
    account_context_provider: Callable[[], dict[str, str]] | None = None
    collected_data_provider: Callable[[], dict[str, str]] | None = None
    pending_action_stopper: Callable[[], object] | None = None
    polling_interval_seconds: float = 0.1
    mouse_corner: str = "top_left"
    corner_threshold_pixels: int = 8
    hotkey: tuple[str, ...] = ("ctrl", "alt", "pause")
    sleep_fn: Callable[[float], None] = sleep
    thread_factory: Callable[..., Thread] = Thread
    _resource_releasers: list[tuple[str, Callable[[], object]]] = field(default_factory=list, init=False, repr=False)
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _activation_event: Event = field(default_factory=Event, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _last_record: FailSafeActivationRecord | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = self.thread_factory(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self, *, wait: bool = True, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if wait and thread is not None:
            thread.join(timeout_seconds)

    def register_resource(self, resource_name: str, release_callback: Callable[[], object]) -> None:
        self._resource_releasers.append((resource_name, release_callback))

    def is_abort_requested(self) -> bool:
        return self._activation_event.is_set()

    def last_record(self) -> FailSafeActivationRecord | None:
        return self._last_record

    def poll(self) -> FailSafeActivationResult | None:
        if self.is_abort_requested():
            return None
        trigger = self._detect_trigger()
        if trigger is None:
            return None
        trigger_type, detail = trigger
        return self.activate(trigger_type=trigger_type, detail=detail)

    def activate(
        self,
        *,
        trigger_type: FailSafeTriggerType = FailSafeTriggerType.MANUAL,
        detail: str | None = None,
    ) -> FailSafeActivationResult:
        with self._lock:
            if self._activation_event.is_set():
                return FailSafeActivationResult(
                    succeeded=True,
                    activated=False,
                    record=self._last_record,
                    released_resources=[] if self._last_record is None else list(self._last_record.released_resources),
                    reason="Fail-safe has already been activated.",
                )
            self._activation_event.set()

        screenshot_path = self._capture_screenshot(trigger_type.value)
        checkpoint = self._save_checkpoint()
        cancelled_task_ids = self._stop_pending_actions()
        released_resources = self._release_resources()
        record = FailSafeActivationRecord(
            workflow_id=self.workflow_id,
            trigger_type=trigger_type,
            detail=detail,
            screenshot_path=screenshot_path,
            checkpoint_saved=checkpoint is not None,
            checkpoint_storage_path=getattr(self.checkpoint_manager, "storage_path", None),
            cancelled_task_ids=cancelled_task_ids,
            released_resources=released_resources,
        )
        self._append_record(record)
        self._last_record = record
        self._log_activation(record)
        self._stop_event.set()
        return FailSafeActivationResult(
            succeeded=True,
            activated=True,
            record=record,
            checkpoint=checkpoint,
            released_resources=released_resources,
        )

    def list_events(self) -> list[FailSafeActivationRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_record(item) for item in payload.get("events", [])]

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll()
            if self._stop_event.is_set():
                break
            self.sleep_fn(self.polling_interval_seconds)

    def _detect_trigger(self) -> tuple[FailSafeTriggerType, str] | None:
        keyboard_backend = self.keyboard_backend
        if keyboard_backend is not None and keyboard_backend.is_hotkey_pressed(self.hotkey):
            return (
                FailSafeTriggerType.HOTKEY,
                f"Emergency abort hotkey pressed: {'+'.join(self.hotkey)}",
            )

        pointer_backend = self.pointer_backend
        if pointer_backend is None:
            return None
        x, y = pointer_backend.get_position()
        width, height = pointer_backend.get_screen_size()
        if self._is_corner_triggered(x=x, y=y, width=width, height=height):
            return (
                FailSafeTriggerType.MOUSE_CORNER,
                f"Pointer moved to configured fail-safe corner: {self.mouse_corner}.",
            )
        return None

    def _is_corner_triggered(self, *, x: int, y: int, width: int, height: int) -> bool:
        threshold = max(0, int(self.corner_threshold_pixels))
        if self.mouse_corner == "top_left":
            return x <= threshold and y <= threshold
        if self.mouse_corner == "top_right":
            return x >= max(0, width - threshold - 1) and y <= threshold
        if self.mouse_corner == "bottom_left":
            return x <= threshold and y >= max(0, height - threshold - 1)
        if self.mouse_corner == "bottom_right":
            return x >= max(0, width - threshold - 1) and y >= max(0, height - threshold - 1)
        return False

    def _capture_screenshot(self, reason: str) -> str | None:
        if self.screenshot_backend is None:
            return None
        timestamp = utc_now().strftime("%Y%m%dT%H%M%S")
        artifact_path = Path(self.storage_path).with_name(
            f"{self._slugify(self.workflow_id)}__fail_safe__{reason}__{timestamp}.png"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        captured = self.screenshot_backend.capture_screenshot_to_path(str(artifact_path))
        if isinstance(captured, str) and captured:
            return captured
        return str(artifact_path)

    def _save_checkpoint(self):
        if self.checkpoint_manager is None:
            return None
        workflow_context = (
            self.workflow_context_provider()
            if self.workflow_context_provider is not None
            else WorkflowContext()
        )
        step_results = self.step_results_provider() if self.step_results_provider is not None else []
        step_index = self.step_index_provider() if self.step_index_provider is not None else workflow_context.step_number
        account_context = self.account_context_provider() if self.account_context_provider is not None else {}
        collected_data = self.collected_data_provider() if self.collected_data_provider is not None else {}
        return self.checkpoint_manager.save_checkpoint(
            workflow_id=self.workflow_id,
            step_index=step_index,
            workflow_context=workflow_context,
            account_context=account_context,
            collected_data=collected_data,
            step_outcomes=step_results,
        )

    def _stop_pending_actions(self) -> list[str]:
        if self.pending_action_stopper is not None:
            result = self.pending_action_stopper()
            removed = getattr(result, "removed_tasks", []) or []
            return [getattr(task, "task_id", "") for task in removed if getattr(task, "task_id", "")]
        if self.task_queue_manager is not None and hasattr(self.task_queue_manager, "clear_pending_tasks"):
            result = self.task_queue_manager.clear_pending_tasks(
                reason="Pending actions cleared by fail-safe controller."
            )
            removed = getattr(result, "removed_tasks", []) or []
            return [getattr(task, "task_id", "") for task in removed if getattr(task, "task_id", "")]
        return []

    def _release_resources(self) -> list[FailSafeResourceReleaseResult]:
        results: list[FailSafeResourceReleaseResult] = []
        for resource_name, callback in list(self._resource_releasers):
            try:
                release_result = callback()
                detail = getattr(release_result, "reason", None)
                if detail is None and getattr(release_result, "succeeded", None) is not None:
                    detail = "Released successfully." if getattr(release_result, "succeeded", False) else "Release reported failure."
                results.append(
                    FailSafeResourceReleaseResult(
                        resource_name=resource_name,
                        succeeded=False if getattr(release_result, "succeeded", True) is False else True,
                        detail=detail,
                    )
                )
            except Exception as exc:
                results.append(
                    FailSafeResourceReleaseResult(
                        resource_name=resource_name,
                        succeeded=False,
                        detail=str(exc),
                    )
                )
        return results

    def _append_record(self, record: FailSafeActivationRecord) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("events", []).append(self._serialize_record(record))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _log_activation(self, record: FailSafeActivationRecord) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log_action(
            workflow_id=self.workflow_id,
            step_name="fail_safe_controller",
            action_type="fail_safe_activated",
            target_element=record.trigger_type.value,
            input_data={"trigger_type": record.trigger_type.value, "detail": record.detail},
            output_data={
                "screenshot_path": record.screenshot_path,
                "checkpoint_saved": record.checkpoint_saved,
                "checkpoint_storage_path": record.checkpoint_storage_path,
                "cancelled_task_ids": list(record.cancelled_task_ids),
                "released_resources": [
                    {
                        "resource_name": item.resource_name,
                        "succeeded": item.succeeded,
                        "detail": item.detail,
                    }
                    for item in record.released_resources
                ],
            },
            success=False,
        )

    def _serialize_record(self, record: FailSafeActivationRecord) -> dict:
        return {
            "workflow_id": record.workflow_id,
            "trigger_type": record.trigger_type.value,
            "detail": record.detail,
            "screenshot_path": record.screenshot_path,
            "checkpoint_saved": record.checkpoint_saved,
            "checkpoint_storage_path": record.checkpoint_storage_path,
            "cancelled_task_ids": list(record.cancelled_task_ids),
            "released_resources": [
                {
                    "resource_name": item.resource_name,
                    "succeeded": item.succeeded,
                    "detail": item.detail,
                }
                for item in record.released_resources
            ],
            "timestamp": record.timestamp.isoformat(),
        }

    def _deserialize_record(self, payload: dict) -> FailSafeActivationRecord:
        return FailSafeActivationRecord(
            workflow_id=payload["workflow_id"],
            trigger_type=FailSafeTriggerType(payload["trigger_type"]),
            detail=payload.get("detail"),
            screenshot_path=payload.get("screenshot_path"),
            checkpoint_saved=bool(payload.get("checkpoint_saved", False)),
            checkpoint_storage_path=payload.get("checkpoint_storage_path"),
            cancelled_task_ids=list(payload.get("cancelled_task_ids", [])),
            released_resources=[
                FailSafeResourceReleaseResult(
                    resource_name=item["resource_name"],
                    succeeded=bool(item.get("succeeded", False)),
                    detail=item.get("detail"),
                )
                for item in payload.get("released_resources", [])
            ],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )

    def _slugify(self, value: str) -> str:
        normalized = "".join(character.lower() if character.isalnum() else "_" for character in value.strip())
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized.strip("_") or "workflow"


