from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Callable

from desktop_automation_agent.models import (
    ResourceUsageSnapshot,
    WatchdogEvent,
    WatchdogEventType,
    WatchdogStatus,
)


@dataclass(slots=True)
class WatchdogTimer:
    storage_path: str
    workflow_id: str
    heartbeat_timeout_seconds: float = 30.0
    monitoring_interval_seconds: float = 5.0
    cpu_threshold_percent: float = 90.0
    memory_threshold_percent: float = 90.0
    screenshot_backend: object | None = None
    oversight_callback: Callable[[WatchdogEvent], None] | None = None
    graceful_termination_callback: Callable[[], object] | None = None
    resource_probe: Callable[[], ResourceUsageSnapshot] | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic
    thread_factory: Callable[..., Thread] = Thread
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _last_heartbeat_at: float = field(default=0.0, init=False, repr=False)
    _last_heartbeat_wallclock: datetime | None = field(default=None, init=False, repr=False)
    _last_event: WatchdogEvent | None = field(default=None, init=False, repr=False)
    _resource_alert_active: bool = field(default=False, init=False, repr=False)
    _stall_triggered: bool = field(default=False, init=False, repr=False)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._stall_triggered = False
            self._resource_alert_active = False
            self._last_heartbeat_at = self.monotonic_fn()
            self._last_heartbeat_wallclock = utc_now()
            self._thread = self.thread_factory(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self, *, wait: bool = True, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if wait and thread is not None:
            thread.join(timeout_seconds)

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat_at = self.monotonic_fn()
            self._last_heartbeat_wallclock = utc_now()
            self._stall_triggered = False

    def status(self) -> WatchdogStatus:
        with self._lock:
            thread = self._thread
            return WatchdogStatus(
                running=bool(thread is not None and thread.is_alive() and not self._stop_event.is_set()),
                workflow_id=self.workflow_id,
                last_heartbeat_at=self._last_heartbeat_wallclock,
                heartbeat_timeout_seconds=self.heartbeat_timeout_seconds,
                monitoring_interval_seconds=self.monitoring_interval_seconds,
                last_event=self._last_event,
            )

    def list_events(self) -> list[WatchdogEvent]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_event(item) for item in payload.get("events", [])]

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._check_heartbeat()
            self._check_resources()
            if self._stop_event.is_set():
                break
            self.sleep_fn(self.monitoring_interval_seconds)

    def _check_heartbeat(self) -> None:
        with self._lock:
            heartbeat_age = self.monotonic_fn() - self._last_heartbeat_at
            if self._stall_triggered or heartbeat_age <= self.heartbeat_timeout_seconds:
                return
            self._stall_triggered = True

        screenshot_path = self._capture_screenshot("stall")
        graceful_attempted = False
        if self.graceful_termination_callback is not None:
            graceful_attempted = True
            self.graceful_termination_callback()

        event = WatchdogEvent(
            event_type=WatchdogEventType.STALL,
            workflow_id=self.workflow_id,
            detail="Pipeline heartbeat timed out.",
            screenshot_path=screenshot_path,
            heartbeat_age_seconds=heartbeat_age,
            graceful_termination_attempted=graceful_attempted,
        )
        self._record_event(event)

    def _check_resources(self) -> None:
        if self.resource_probe is None:
            return
        usage = self.resource_probe()
        exceeded = (
            usage.cpu_percent >= self.cpu_threshold_percent
            or usage.memory_percent >= self.memory_threshold_percent
        )
        if not exceeded:
            self._resource_alert_active = False
            return
        if self._resource_alert_active:
            return
        self._resource_alert_active = True
        event = WatchdogEvent(
            event_type=WatchdogEventType.RESOURCE_ALERT,
            workflow_id=self.workflow_id,
            detail="System resource usage exceeded watchdog thresholds.",
            cpu_percent=usage.cpu_percent,
            memory_percent=usage.memory_percent,
        )
        self._record_event(event)

    def _record_event(self, event: WatchdogEvent) -> None:
        with self._lock:
            self._last_event = event
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("events", []).append(self._serialize_event(event))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.oversight_callback is not None:
            self.oversight_callback(event)

    def _capture_screenshot(self, reason: str) -> str | None:
        if self.screenshot_backend is None:
            return None
        timestamp = utc_now().strftime("%Y%m%dT%H%M%S")
        artifact_path = Path(self.storage_path).with_name(
            f"{self._slugify(self.workflow_id)}__{reason}__{timestamp}.png"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        captured = self.screenshot_backend.capture_screenshot_to_path(str(artifact_path))
        if isinstance(captured, str) and captured:
            return captured
        return str(artifact_path)

    def _serialize_event(self, event: WatchdogEvent) -> dict:
        return {
            "event_type": event.event_type.value,
            "workflow_id": event.workflow_id,
            "detail": event.detail,
            "screenshot_path": event.screenshot_path,
            "cpu_percent": event.cpu_percent,
            "memory_percent": event.memory_percent,
            "heartbeat_age_seconds": event.heartbeat_age_seconds,
            "graceful_termination_attempted": event.graceful_termination_attempted,
            "timestamp": event.timestamp.isoformat(),
        }

    def _deserialize_event(self, payload: dict) -> WatchdogEvent:
        return WatchdogEvent(
            event_type=WatchdogEventType(payload["event_type"]),
            workflow_id=payload["workflow_id"],
            detail=payload.get("detail"),
            screenshot_path=payload.get("screenshot_path"),
            cpu_percent=payload.get("cpu_percent"),
            memory_percent=payload.get("memory_percent"),
            heartbeat_age_seconds=payload.get("heartbeat_age_seconds"),
            graceful_termination_attempted=bool(payload.get("graceful_termination_attempted", False)),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )

    def _slugify(self, value: str) -> str:
        normalized = "".join(character.lower() if character.isalnum() else "_" for character in value.strip())
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized.strip("_") or "watchdog"


