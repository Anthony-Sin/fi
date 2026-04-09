import time
from pathlib import Path

from desktop_automation_agent.event_trigger_listener import EventDrivenTriggerListener
from desktop_automation_agent.models import (
    ClipboardContent,
    ClipboardContentType,
    EventTriggerDefinition,
    TriggerType,
    WindowContext,
)


class FakeWindowManager:
    def __init__(self, windows):
        self.windows = list(windows)

    def list_windows(self):
        return list(self.windows)


class FakeClipboardBackend:
    def __init__(self, content):
        self.content = content

    def read(self):
        return self.content


def _wait_for(predicate, timeout_seconds=1.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_event_trigger_listener_handles_multiple_trigger_types_and_logs_events(tmp_path):
    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    baseline_file = watched_dir / "baseline.txt"
    baseline_file.write_text("before", encoding="utf-8")

    callbacks = []
    window_manager = FakeWindowManager(
        [
            WindowContext(handle=1, title="Editor", process_name="editor.exe"),
        ]
    )
    clipboard_backend = FakeClipboardBackend(
        ClipboardContent(content_type=ClipboardContentType.TEXT, text="initial")
    )
    listener = EventDrivenTriggerListener(
        storage_path=str(tmp_path / "events.json"),
        callback=callbacks.append,
        window_manager=window_manager,
        clipboard_backend=clipboard_backend,
        polling_interval_seconds=0.01,
    )
    listener.register_trigger(
        EventTriggerDefinition(
            trigger_id="window-trigger",
            trigger_type=TriggerType.NEW_WINDOW,
            title_pattern="invoice",
        )
    )
    listener.register_trigger(
        EventTriggerDefinition(
            trigger_id="file-trigger",
            trigger_type=TriggerType.FILE_CHANGED,
            directory_path=str(watched_dir),
            include_subdirectories=True,
        )
    )
    listener.register_trigger(
        EventTriggerDefinition(
            trigger_id="clipboard-trigger",
            trigger_type=TriggerType.CLIPBOARD_CHANGED,
        )
    )
    listener.register_trigger(
        EventTriggerDefinition(
            trigger_id="timer-trigger",
            trigger_type=TriggerType.TIMER,
            timer_interval_seconds=0.02,
        )
    )

    listener.start()
    assert listener.status().status is not None
    assert listener.status().status.running is True

    created_file = watched_dir / "created.txt"
    created_file.write_text("hello", encoding="utf-8")
    baseline_file.write_text("after", encoding="utf-8")
    clipboard_backend.content = ClipboardContent(
        content_type=ClipboardContentType.TEXT,
        text="updated",
    )
    window_manager.windows.append(
        WindowContext(handle=2, title="Invoice Review", process_name="billing.exe")
    )

    assert _wait_for(
        lambda: any(event.trigger_type is TriggerType.TIMER for event in callbacks)
        and len(callbacks) >= 4
    )

    listener.stop()
    assert listener.status().status is not None
    assert listener.status().status.running is False

    recorded = listener.list_events()
    assert recorded.succeeded is True
    event_types = {event.trigger_type for event in recorded.events}
    assert TriggerType.NEW_WINDOW in event_types
    assert TriggerType.FILE_CHANGED in event_types
    assert TriggerType.CLIPBOARD_CHANGED in event_types
    assert TriggerType.TIMER in event_types
    assert any(event.window_title == "Invoice Review" for event in recorded.events)
    assert any(event.file_path == str(created_file) for event in recorded.events)
    assert any(event.clipboard_text == "updated" for event in recorded.events)


def test_event_trigger_listener_can_remove_triggers_and_preserve_registration_state(tmp_path):
    listener = EventDrivenTriggerListener(storage_path=str(tmp_path / "events.json"))

    first = EventTriggerDefinition(trigger_id="timer-a", trigger_type=TriggerType.TIMER, timer_interval_seconds=1.0)
    second = EventTriggerDefinition(trigger_id="timer-b", trigger_type=TriggerType.TIMER, timer_interval_seconds=2.0)

    listener.register_trigger(first)
    listener.register_trigger(second)

    listed = listener.list_triggers()
    assert listed.succeeded is True
    assert {trigger.trigger_id for trigger in listed.triggers} == {"timer-a", "timer-b"}

    removed = listener.remove_trigger("timer-a")
    assert removed.succeeded is True
    assert removed.trigger is not None
    assert removed.trigger.trigger_id == "timer-a"
    assert {trigger.trigger_id for trigger in removed.triggers} == {"timer-b"}

    missing = listener.remove_trigger("missing")
    assert missing.succeeded is False
    assert "not found" in (missing.reason or "")
