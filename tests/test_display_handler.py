from pathlib import Path

from desktop_automation_agent.display_handler import MultiMonitorDisplayHandler
from desktop_automation_agent.input_simulator import SafeInputSimulator, StaticScreenInspector
from desktop_automation_agent.locator import MultiStrategyElementLocator
from desktop_automation_agent.models import (
    DesktopState,
    InputAction,
    InputActionType,
    InputTarget,
    LocatorTarget,
    MonitorDescriptor,
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
    ScreenBounds,
    WindowContext,
    WindowReference,
    WindowState,
)


class FakeMonitorBackend:
    def __init__(self, monitor_sets):
        self.monitor_sets = list(monitor_sets)
        self.index = 0

    def enumerate_monitors(self):
        current = self.monitor_sets[min(self.index, len(self.monitor_sets) - 1)]
        self.index += 1
        return list(current)


class FakeCaptureBackend:
    def __init__(self):
        self.captured_monitors = []
        self.saved_paths = []

    def capture(self, monitor=None):
        self.captured_monitors.append(None if monitor is None else monitor.monitor_id)
        return f"image:{None if monitor is None else monitor.monitor_id}"

    def save(self, image, path):
        self.saved_paths.append((image, path))
        Path(path).write_text(str(image), encoding="utf-8")
        return path


class FakeWindowManager:
    def __init__(self, windows):
        self.windows = {window.handle: window for window in windows}
        self.moves = []

    def list_windows(self):
        return list(self.windows.values())

    def move_resize_window(self, handle, x, y, width, height):
        self.moves.append((handle, x, y, width, height))
        window = self.windows[handle]
        window.position = (x, y)
        window.size = (width, height)
        return type("Result", (), {"succeeded": True, "window": window, "reason": None})()


class FakeFocusedWindowManager:
    def __init__(self, focused_window):
        self.focused_window = focused_window

    def get_focused_window(self):
        return self.focused_window


class FakeInputBackend:
    def __init__(self):
        self.calls = []

    def click(self, x, y, button):
        self.calls.append(("click", x, y, button))

    def press(self, key):
        self.calls.append(("press", key))

    def write(self, text):
        self.calls.append(("write", text))

    def scroll(self, clicks):
        self.calls.append(("scroll", clicks))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))


def _monitors():
    return [
        MonitorDescriptor(
            monitor_id="primary",
            bounds=(0, 0, 1920, 1080),
            work_area=(0, 0, 1920, 1040),
            resolution=(1920, 1080),
            primary=True,
        ),
        MonitorDescriptor(
            monitor_id="secondary",
            bounds=(1920, 0, 3200, 1024),
            work_area=(1920, 0, 3200, 984),
            resolution=(1280, 1024),
            primary=False,
        ),
    ]


def test_display_handler_enumerates_monitors_and_detects_configuration_changes():
    changed_monitors = _monitors() + [
        MonitorDescriptor(
            monitor_id="tertiary",
            bounds=(-1280, 0, 0, 1024),
            work_area=(-1280, 0, 0, 984),
            resolution=(1280, 1024),
            primary=False,
        )
    ]
    handler = MultiMonitorDisplayHandler(
        window_manager=FakeWindowManager([]),
        monitor_backend=FakeMonitorBackend([_monitors(), _monitors(), changed_monitors]),
    )

    snapshot = handler.start_session()
    unchanged = handler.detect_configuration_change()
    changed = handler.detect_configuration_change()

    assert [monitor.monitor_id for monitor in snapshot.monitors] == ["primary", "secondary"]
    assert unchanged.changed is False
    assert changed.changed is True
    assert changed.current is not None
    assert len(changed.current.monitors) == 3


def test_display_handler_moves_windows_between_monitors_and_captures_specific_monitor(tmp_path):
    capture_backend = FakeCaptureBackend()
    handler = MultiMonitorDisplayHandler(
        window_manager=FakeWindowManager(
            [WindowContext(handle=9, title="Editor", position=(50, 60), size=(800, 600), monitor_id="primary")]
        ),
        monitor_backend=FakeMonitorBackend([_monitors(), _monitors(), _monitors()]),
        capture_backend=capture_backend,
    )

    move = handler.move_window_to_monitor(9, "secondary")
    screenshot_path = handler.capture_screenshot_to_path(str(tmp_path / "secondary.txt"), monitor_id="secondary")
    virtual = handler.capture_image()

    assert move.succeeded is True
    assert move.window is not None
    assert move.window.position == (2160, 192)
    assert move.window.monitor_id == "secondary"
    assert screenshot_path.endswith("secondary.txt")
    assert capture_backend.captured_monitors == ["secondary", None]
    assert virtual == "image:None"


def test_locator_can_scope_search_to_specific_monitor():
    display_handler = MultiMonitorDisplayHandler(
        window_manager=FakeWindowManager([]),
        monitor_backend=FakeMonitorBackend([_monitors(), _monitors(), _monitors()]),
    )
    locator = MultiStrategyElementLocator(confidence_threshold=0.8, display_handler=display_handler)
    state = DesktopState.empty()
    state.results = [
        PerceptionResult(
            source=PerceptionSource.ACCESSIBILITY,
            confidence=0.95,
            artifacts=[
                PerceptionArtifact(
                    kind="button",
                    confidence=0.9,
                    bounds=(100, 100, 200, 150),
                    payload={"name": "Send", "role": "button"},
                ),
                PerceptionArtifact(
                    kind="button",
                    confidence=0.92,
                    bounds=(2200, 200, 2300, 250),
                    payload={"name": "Send", "role": "button"},
                ),
            ],
        )
    ]

    result = locator.locate(state, LocatorTarget(text="Send", element_type="button"), monitor_id="secondary")

    assert result.succeeded is True
    assert result.monitor_id == "secondary"
    assert result.center == (2250, 225)


def test_safe_input_simulator_validates_clicks_against_requested_monitor():
    display_handler = MultiMonitorDisplayHandler(
        window_manager=FakeWindowManager([]),
        monitor_backend=FakeMonitorBackend([_monitors(), _monitors(), _monitors()]),
    )
    simulator = SafeInputSimulator(
        backend=FakeInputBackend(),
        window_manager=FakeFocusedWindowManager(WindowState(reference=WindowReference(title="Editor"), focused=True)),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=3200, height=1080)),
        display_handler=display_handler,
        dry_run=False,
        sleep_fn=lambda _: None,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.CLICK,
                monitor_id="secondary",
                target=InputTarget(
                    window=WindowReference(title="Editor"),
                    element_bounds=(2000, 100, 2100, 180),
                    monitor_id="secondary",
                ),
                position=(2050, 140),
            ),
            InputAction(
                action_type=InputActionType.CLICK,
                monitor_id="secondary",
                target=InputTarget(
                    window=WindowReference(title="Editor"),
                    element_bounds=(100, 100, 200, 180),
                    monitor_id="secondary",
                ),
                position=(150, 140),
            ),
        ]
    )

    assert result.succeeded is False
    assert result.logs[0].executed is True
    assert result.failure_reason == "Target element is not fully visible within screen boundaries."
