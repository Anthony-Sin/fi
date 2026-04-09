from pathlib import Path

from desktop_automation_agent.models import ResourceUsageSnapshot, WatchdogEventType
from desktop_automation_agent.watchdog_timer import WatchdogTimer


class FakeScreenshotBackend:
    def __init__(self):
        self.paths = []

    def capture_screenshot_to_path(self, path=None):
        self.paths.append(path)
        if path is not None:
            Path(path).write_text("image", encoding="utf-8")
        return path


def test_watchdog_status_updates_on_heartbeat(tmp_path):
    monotonic_values = iter([0.0, 0.0]).__next__
    watchdog = WatchdogTimer(
        storage_path=str(Path(tmp_path) / "watchdog.json"),
        workflow_id="wf",
        monitoring_interval_seconds=0.1,
        heartbeat_timeout_seconds=5.0,
        monotonic_fn=monotonic_values,
    )

    watchdog.heartbeat()
    status = watchdog.status()

    assert status.workflow_id == "wf"
    assert status.last_heartbeat_at is not None


def test_watchdog_records_stall_captures_screenshot_and_terminates(tmp_path):
    oversight = []
    terminations = []
    screenshot_backend = FakeScreenshotBackend()
    monotonic_values = iter([10.0]).__next__
    watchdog = WatchdogTimer(
        storage_path=str(Path(tmp_path) / "watchdog.json"),
        workflow_id="ops-workflow",
        heartbeat_timeout_seconds=5.0,
        screenshot_backend=screenshot_backend,
        oversight_callback=oversight.append,
        graceful_termination_callback=lambda: terminations.append("terminate"),
        monotonic_fn=monotonic_values,
    )

    watchdog._last_heartbeat_at = 0.0
    watchdog._check_heartbeat()
    events = watchdog.list_events()

    assert len(events) == 1
    assert events[0].event_type is WatchdogEventType.STALL
    assert events[0].graceful_termination_attempted is True
    assert Path(events[0].screenshot_path).exists()
    assert terminations == ["terminate"]
    assert len(oversight) == 1


def test_watchdog_does_not_duplicate_stall_until_heartbeat_resets(tmp_path):
    monotonic_values = iter([8.0, 9.0, 9.1, 9.2]).__next__
    watchdog = WatchdogTimer(
        storage_path=str(Path(tmp_path) / "watchdog.json"),
        workflow_id="wf",
        heartbeat_timeout_seconds=5.0,
        monotonic_fn=monotonic_values,
    )

    watchdog._last_heartbeat_at = 0.0
    watchdog._check_heartbeat()
    watchdog._check_heartbeat()
    assert len(watchdog.list_events()) == 1

    watchdog.heartbeat()
    watchdog._check_heartbeat()
    assert len(watchdog.list_events()) == 1


def test_watchdog_emits_resource_alert_when_threshold_exceeded(tmp_path):
    oversight = []
    watchdog = WatchdogTimer(
        storage_path=str(Path(tmp_path) / "watchdog.json"),
        workflow_id="wf",
        cpu_threshold_percent=80.0,
        memory_threshold_percent=75.0,
        oversight_callback=oversight.append,
        resource_probe=lambda: ResourceUsageSnapshot(cpu_percent=85.0, memory_percent=78.0),
    )

    watchdog._check_resources()
    events = watchdog.list_events()

    assert len(events) == 1
    assert events[0].event_type is WatchdogEventType.RESOURCE_ALERT
    assert events[0].cpu_percent == 85.0
    assert events[0].memory_percent == 78.0
    assert len(oversight) == 1


def test_watchdog_suppresses_duplicate_resource_alerts_until_usage_recovers(tmp_path):
    probe_values = iter(
        [
            ResourceUsageSnapshot(cpu_percent=85.0, memory_percent=50.0),
            ResourceUsageSnapshot(cpu_percent=88.0, memory_percent=50.0),
            ResourceUsageSnapshot(cpu_percent=40.0, memory_percent=40.0),
            ResourceUsageSnapshot(cpu_percent=86.0, memory_percent=50.0),
        ]
    )
    watchdog = WatchdogTimer(
        storage_path=str(Path(tmp_path) / "watchdog.json"),
        workflow_id="wf",
        cpu_threshold_percent=80.0,
        resource_probe=lambda: next(probe_values),
    )

    watchdog._check_resources()
    watchdog._check_resources()
    watchdog._check_resources()
    watchdog._check_resources()

    events = watchdog.list_events()
    assert len(events) == 2
    assert all(event.event_type is WatchdogEventType.RESOURCE_ALERT for event in events)
