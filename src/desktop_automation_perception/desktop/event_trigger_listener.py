from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep
from typing import Callable

from desktop_automation_perception.models import (
    ClipboardContentType,
    EventTriggerDefinition,
    EventTriggerListenerResult,
    EventTriggerListenerStatus,
    EventTriggerRecord,
    TriggerType,
)


@dataclass(slots=True)
class EventDrivenTriggerListener:
    storage_path: str
    callback: Callable[[EventTriggerRecord], object] | None = None
    window_manager: object | None = None
    clipboard_backend: object | None = None
    polling_interval_seconds: float = 0.25
    sleep_fn: Callable[[float], None] = sleep
    now_fn: Callable[[], datetime] = utc_now
    thread_factory: Callable[..., Thread] = Thread
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _triggers: list[EventTriggerDefinition] = field(default_factory=list, init=False, repr=False)
    _last_event: EventTriggerRecord | None = field(default=None, init=False, repr=False)
    _known_window_handles: set[int] = field(default_factory=set, init=False, repr=False)
    _known_files: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _clipboard_signature: tuple[str, str | bytes | None] | None = field(default=None, init=False, repr=False)
    _timer_due_at: dict[str, datetime] = field(default_factory=dict, init=False, repr=False)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._prime_state()
            self._stop_event.clear()
            self._thread = self.thread_factory(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self, *, wait: bool = True, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if wait and thread is not None:
            thread.join(timeout_seconds)

    def register_trigger(self, trigger: EventTriggerDefinition) -> EventTriggerListenerResult:
        with self._lock:
            self._triggers = [item for item in self._triggers if item.trigger_id != trigger.trigger_id] + [trigger]
            if trigger.trigger_type is TriggerType.TIMER and trigger.timer_interval_seconds is not None:
                self._timer_due_at[trigger.trigger_id] = self.now_fn() + timedelta(seconds=trigger.timer_interval_seconds)
        return EventTriggerListenerResult(succeeded=True, trigger=trigger, triggers=self.list_triggers().triggers)

    def remove_trigger(self, trigger_id: str) -> EventTriggerListenerResult:
        with self._lock:
            existing = next((item for item in self._triggers if item.trigger_id == trigger_id), None)
            self._triggers = [item for item in self._triggers if item.trigger_id != trigger_id]
            self._timer_due_at.pop(trigger_id, None)
        if existing is None:
            return EventTriggerListenerResult(succeeded=False, reason="Trigger was not found.")
        return EventTriggerListenerResult(succeeded=True, trigger=existing, triggers=self.list_triggers().triggers)

    def list_triggers(self) -> EventTriggerListenerResult:
        with self._lock:
            return EventTriggerListenerResult(succeeded=True, triggers=list(self._triggers))

    def list_events(self) -> EventTriggerListenerResult:
        path = Path(self.storage_path)
        if not path.exists():
            return EventTriggerListenerResult(succeeded=True, events=[])
        payload = json.loads(path.read_text(encoding="utf-8"))
        events = [self._deserialize_event(item) for item in payload.get("events", [])]
        return EventTriggerListenerResult(succeeded=True, events=events)

    def status(self) -> EventTriggerListenerResult:
        with self._lock:
            thread = self._thread
            status = EventTriggerListenerStatus(
                running=bool(thread is not None and thread.is_alive() and not self._stop_event.is_set()),
                trigger_count=len(self._triggers),
                last_event=self._last_event,
            )
        return EventTriggerListenerResult(succeeded=True, status=status)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            for event in self._poll_triggers():
                self._record_event(event)
            if self._stop_event.is_set():
                break
            self.sleep_fn(self.polling_interval_seconds)

    def _poll_triggers(self) -> list[EventTriggerRecord]:
        with self._lock:
            triggers = [item for item in self._triggers if item.active]
        events: list[EventTriggerRecord] = []
        for trigger in triggers:
            if trigger.trigger_type is TriggerType.NEW_WINDOW:
                events.extend(self._detect_new_windows(trigger))
            elif trigger.trigger_type is TriggerType.FILE_CHANGED:
                events.extend(self._detect_file_changes(trigger))
            elif trigger.trigger_type is TriggerType.CLIPBOARD_CHANGED:
                event = self._detect_clipboard_change(trigger)
                if event is not None:
                    events.append(event)
            elif trigger.trigger_type is TriggerType.TIMER:
                event = self._detect_timer_fire(trigger)
                if event is not None:
                    events.append(event)
        return events

    def _detect_new_windows(self, trigger: EventTriggerDefinition) -> list[EventTriggerRecord]:
        if self.window_manager is None:
            return []
        pattern = re.compile(trigger.title_pattern or ".*", re.IGNORECASE)
        events: list[EventTriggerRecord] = []
        windows = self.window_manager.list_windows()
        current_handles = {window.handle for window in windows}
        for window in windows:
            if window.handle in self._known_window_handles:
                continue
            if not pattern.search(window.title):
                continue
            events.append(
                EventTriggerRecord(
                    trigger_id=trigger.trigger_id,
                    trigger_type=trigger.trigger_type,
                    timestamp=self.now_fn(),
                    detail="Matching window created.",
                    window_title=window.title,
                    window_handle=window.handle,
                    metadata={"process_name": window.process_name},
                )
            )
        self._known_window_handles = current_handles
        return events

    def _detect_file_changes(self, trigger: EventTriggerDefinition) -> list[EventTriggerRecord]:
        if not trigger.directory_path:
            return []
        root = Path(trigger.directory_path)
        if not root.exists():
            return []
        iterator = root.rglob("*") if trigger.include_subdirectories else root.glob("*")
        current: dict[str, float] = {}
        events: list[EventTriggerRecord] = []
        for path in iterator:
            if not path.is_file():
                continue
            key = str(path)
            modified = path.stat().st_mtime
            current[key] = modified
            previous = self._known_files.get(key)
            if previous is None:
                events.append(
                    EventTriggerRecord(
                        trigger_id=trigger.trigger_id,
                        trigger_type=trigger.trigger_type,
                        timestamp=self.now_fn(),
                        detail="File created.",
                        file_path=key,
                    )
                )
            elif modified > previous:
                events.append(
                    EventTriggerRecord(
                        trigger_id=trigger.trigger_id,
                        trigger_type=trigger.trigger_type,
                        timestamp=self.now_fn(),
                        detail="File modified.",
                        file_path=key,
                    )
                )
        self._known_files.update(current)
        return events

    def _detect_clipboard_change(self, trigger: EventTriggerDefinition) -> EventTriggerRecord | None:
        if self.clipboard_backend is None:
            return None
        content = self.clipboard_backend.read()
        signature = self._clipboard_signature_for_content(content)
        if self._clipboard_signature is None:
            self._clipboard_signature = signature
            return None
        if signature == self._clipboard_signature:
            return None
        self._clipboard_signature = signature
        return EventTriggerRecord(
            trigger_id=trigger.trigger_id,
            trigger_type=trigger.trigger_type,
            timestamp=self.now_fn(),
            detail="Clipboard content changed.",
            clipboard_text=getattr(content, "text", None),
            clipboard_content_type=getattr(content, "content_type", ClipboardContentType.EMPTY).value,
        )

    def _detect_timer_fire(self, trigger: EventTriggerDefinition) -> EventTriggerRecord | None:
        if trigger.timer_interval_seconds is None:
            return None
        due_at = self._timer_due_at.get(trigger.trigger_id)
        current = self.now_fn()
        if due_at is None:
            self._timer_due_at[trigger.trigger_id] = current + timedelta(seconds=trigger.timer_interval_seconds)
            return None
        if current < due_at:
            return None
        self._timer_due_at[trigger.trigger_id] = current + timedelta(seconds=trigger.timer_interval_seconds)
        return EventTriggerRecord(
            trigger_id=trigger.trigger_id,
            trigger_type=trigger.trigger_type,
            timestamp=current,
            detail="Scheduled timer fired.",
            metadata={"interval_seconds": trigger.timer_interval_seconds},
        )

    def _record_event(self, event: EventTriggerRecord) -> None:
        with self._lock:
            self._last_event = event
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("events", []).append(self._serialize_event(event))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.callback is not None:
            self.callback(event)

    def _prime_state(self) -> None:
        if self.window_manager is not None:
            self._known_window_handles = {window.handle for window in self.window_manager.list_windows()}
        if self.clipboard_backend is not None:
            self._clipboard_signature = self._clipboard_signature_for_content(self.clipboard_backend.read())
        file_triggers = [item for item in self._triggers if item.trigger_type is TriggerType.FILE_CHANGED and item.directory_path]
        for trigger in file_triggers:
            root = Path(trigger.directory_path or "")
            if not root.exists():
                continue
            iterator = root.rglob("*") if trigger.include_subdirectories else root.glob("*")
            for path in iterator:
                if path.is_file():
                    self._known_files[str(path)] = path.stat().st_mtime
        for trigger in self._triggers:
            if trigger.trigger_type is TriggerType.TIMER and trigger.timer_interval_seconds is not None:
                self._timer_due_at[trigger.trigger_id] = self.now_fn() + timedelta(seconds=trigger.timer_interval_seconds)

    def _clipboard_signature_for_content(self, content) -> tuple[str, str | bytes | None]:
        content_type = getattr(content, "content_type", ClipboardContentType.EMPTY)
        if content_type is ClipboardContentType.TEXT:
            return (content_type.value, getattr(content, "text", None))
        if content_type is ClipboardContentType.IMAGE:
            return (content_type.value, getattr(content, "image_bytes", None))
        return (content_type.value, None)

    def _serialize_event(self, event: EventTriggerRecord) -> dict:
        return {
            "trigger_id": event.trigger_id,
            "trigger_type": event.trigger_type.value,
            "timestamp": event.timestamp.isoformat(),
            "detail": event.detail,
            "window_title": event.window_title,
            "window_handle": event.window_handle,
            "file_path": event.file_path,
            "clipboard_text": event.clipboard_text,
            "clipboard_content_type": event.clipboard_content_type,
            "metadata": dict(event.metadata),
        }

    def _deserialize_event(self, payload: dict) -> EventTriggerRecord:
        return EventTriggerRecord(
            trigger_id=payload["trigger_id"],
            trigger_type=TriggerType(payload["trigger_type"]),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            detail=payload.get("detail"),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
            file_path=payload.get("file_path"),
            clipboard_text=payload.get("clipboard_text"),
            clipboard_content_type=payload.get("clipboard_content_type"),
            metadata=dict(payload.get("metadata", {})),
        )


