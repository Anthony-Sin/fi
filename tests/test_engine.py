import json
from datetime import datetime, timedelta, timezone

from desktop_automation_agent.account_rotation_orchestrator import AccountRotationOrchestrator
from desktop_automation_agent.account_registry import AccountRegistry
from desktop_automation_agent.accessibility_tree_reader import AccessibilityTreeReader
from desktop_automation_agent.allowlist_enforcer import ActionAllowlistEnforcer
from desktop_automation_agent.browser_profile_switcher import BrowserProfileSwitcher
from desktop_automation_agent.change_detection_monitor import ScreenChangeDetectionMonitor
from desktop_automation_agent.clipboard import ClipboardManager
from desktop_automation_agent.context import CaptureContext
from desktop_automation_agent.credential_vault import CredentialVault
from desktop_automation_agent.engine import DesktopPerceptionEngine
from desktop_automation_agent.input_simulator import SafeInputSimulator, StaticScreenInspector
from desktop_automation_agent.locator import MultiStrategyElementLocator
from desktop_automation_agent.models import (
    AccessibilityElement,
    AccessibilityElementState,
    AccessibilityTree,
    AccountExecutionMode,
    AccountRecord,
    ClipboardContent,
    ClipboardContentType,
    ClipboardPasteMode,
    BrowserProfileRecord,
    CredentialKind,
    InputAction,
    InputActionType,
    InputTarget,
    LineEndingStyle,
    LocatorStrategy,
    LocatorTarget,
    OCRTextBlock,
    PerceptionSource,
    PromptInjectionMethod,
    PromptInjectionTarget,
    PromptReadbackMethod,
    PromptTemplateVersion,
    ResolutionCalibrationProfile,
    ResolutionReferenceElement,
    ScreenCheckType,
    ScreenVerificationCheck,
    ScreenBounds,
    SessionState,
    RotationTask,
    TemplateMatch,
    TemplateSearchRequest,
    WindowContext,
    WindowReference,
    WindowZoneType,
    WindowState,
)
from desktop_automation_agent.ocr_extractor import OCRExtractor
from desktop_automation_agent.prompt_template_manager import PromptTemplateManager
from desktop_automation_agent.resilience import SensitiveDataProtector
from desktop_automation_agent.screen_state_verifier import ScreenStateVerifier
from desktop_automation_agent.session_state_tracker import SessionStateTracker
from desktop_automation_agent.target_application_prompt_injector import (
    TargetApplicationPromptInjector,
)
from desktop_automation_agent.providers import (
    AIVisionProvider,
    AccessibilityProvider,
    OCRProvider,
    TemplateMatchingProvider,
)
from desktop_automation_agent.template_image_matcher import TemplateImageMatcher
from desktop_automation_agent.resolution_adaptive_coordinate_manager import (
    ResolutionAdaptiveCoordinateManager,
)
from desktop_automation_agent.dynamic_region_of_interest_calculator import (
    DynamicRegionOfInterestCalculator,
)
from desktop_automation_agent.window_manager import (
    DesktopWindowManager,
    SW_MAXIMIZE,
    SW_MINIMIZE,
)


def test_engine_respects_provider_priority():
    engine = DesktopPerceptionEngine(
        providers=[
            AIVisionProvider(),
            TemplateMatchingProvider(),
            OCRProvider(),
            AccessibilityProvider(),
        ]
    )

    state = engine.capture_state(CaptureContext())

    assert [result.source for result in state.results] == [
        PerceptionSource.ACCESSIBILITY,
        PerceptionSource.OCR,
        PerceptionSource.TEMPLATE_MATCH,
        PerceptionSource.AI_VISION,
    ]


def test_best_result_uses_highest_confidence_success():
    context = CaptureContext(
        metadata={
            "accessibility_snapshot": {
                "confidence": 0.91,
                "elements": [{"role": "button", "name": "Send", "bounds": (1, 2, 3, 4)}],
            },
            "ocr_snapshot": {
                "text_blocks": [{"text": "Send", "confidence": 0.84, "bounds": (1, 2, 3, 4)}]
            },
            "template_matches": {
                "matches": [{"template": "send_button", "confidence": 0.7, "bounds": (1, 2, 3, 4)}]
            },
            "ai_vision_snapshot": {
                "confidence": 0.62,
                "observations": [{"kind": "button", "label": "Send", "confidence": 0.62}],
            },
        }
    )

    engine = DesktopPerceptionEngine(
        providers=[
            AccessibilityProvider(),
            OCRProvider(),
            TemplateMatchingProvider(),
            AIVisionProvider(),
        ]
    )

    state = engine.capture_state(context)
    best = state.best_result()

    assert best is not None
    assert best.source == PerceptionSource.ACCESSIBILITY
    assert best.confidence == 0.91


def test_stop_on_first_success_short_circuits():
    context = CaptureContext(
        metadata={
            "accessibility_snapshot": {
                "confidence": 0.9,
                "elements": [{"role": "window", "name": "Editor"}],
            }
        }
    )

    engine = DesktopPerceptionEngine(
        providers=[
            AccessibilityProvider(),
            OCRProvider(),
            TemplateMatchingProvider(),
            AIVisionProvider(),
        ],
        stop_on_first_success=True,
    )

    state = engine.capture_state(context)

    assert len(state.results) == 1
    assert state.results[0].source == PerceptionSource.ACCESSIBILITY


def test_locator_prefers_accessibility_before_fallbacks():
    context = CaptureContext(
        metadata={
            "accessibility_snapshot": {
                "confidence": 0.83,
                "elements": [
                    {
                        "role": "button",
                        "name": "Send",
                        "bounds": (10, 20, 110, 70),
                        "confidence": 0.83,
                    }
                ],
            },
            "ocr_snapshot": {
                "text_blocks": [
                    {"text": "Send", "confidence": 0.95, "bounds": (12, 22, 112, 72)}
                ]
            },
            "template_matches": {
                "matches": [
                    {"template": "send_button", "confidence": 0.9, "bounds": (14, 24, 114, 74)}
                ]
            },
        }
    )
    engine = DesktopPerceptionEngine(
        providers=[AccessibilityProvider(), OCRProvider(), TemplateMatchingProvider()]
    )
    state = engine.capture_state(context)

    locator = MultiStrategyElementLocator(confidence_threshold=0.8)
    result = locator.locate(state, LocatorTarget(text="Send", element_type="button"))

    assert result.succeeded is True
    assert result.strategy == LocatorStrategy.ACCESSIBILITY
    assert result.center == (60, 45)
    assert result.bounds == (10, 20, 110, 70)


def test_locator_falls_back_to_ocr_when_accessibility_misses():
    context = CaptureContext(
        metadata={
            "ocr_snapshot": {
                "text_blocks": [
                    {"text": "Continue", "confidence": 0.88, "bounds": (30, 40, 130, 80)}
                ]
            }
        }
    )
    engine = DesktopPerceptionEngine(
        providers=[AccessibilityProvider(), OCRProvider(), TemplateMatchingProvider()]
    )
    state = engine.capture_state(context)

    locator = MultiStrategyElementLocator(confidence_threshold=0.8)
    result = locator.locate(state, LocatorTarget(text="Continue"))

    assert result.succeeded is True
    assert result.strategy == LocatorStrategy.OCR
    assert result.center == (80, 60)


def test_locator_returns_failure_with_best_candidate_when_below_threshold():
    context = CaptureContext(
        metadata={
            "template_matches": {
                "matches": [
                    {"template": "save_button", "confidence": 0.61, "bounds": (50, 60, 150, 100)}
                ]
            }
        }
    )
    engine = DesktopPerceptionEngine(
        providers=[AccessibilityProvider(), OCRProvider(), TemplateMatchingProvider()]
    )
    state = engine.capture_state(context)

    locator = MultiStrategyElementLocator(confidence_threshold=0.8)
    result = locator.locate(state, LocatorTarget(template_name="save_button"))

    assert result.succeeded is False
    assert result.strategy == LocatorStrategy.TEMPLATE_MATCH
    assert result.best_candidate is not None
    assert result.best_candidate.confidence == 0.61
    assert "below threshold" in (result.reason or "")


class FakeWindowManager:
    def __init__(self, focused_window: WindowState | None):
        self.focused_window = focused_window

    def get_focused_window(self) -> WindowState | None:
        return self.focused_window


class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def click(self, x: int, y: int, button: str) -> None:
        self.calls.append(("click", (x, y, button)))

    def press(self, key: str) -> None:
        self.calls.append(("press", (key,)))

    def write(self, text: str) -> None:
        self.calls.append(("write", (text,)))

    def scroll(self, clicks: int) -> None:
        self.calls.append(("scroll", (clicks,)))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", keys))


def test_safe_input_simulator_dry_run_logs_without_executing():
    backend = FakeBackend()
    delays: list[float] = []
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Editor"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=1920, height=1080)),
        inter_action_delay_seconds=0.25,
        dry_run=True,
        sleep_fn=delays.append,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.CLICK,
                target=InputTarget(
                    window=WindowReference(title="Editor"),
                    element_bounds=(100, 100, 200, 160),
                ),
            ),
            InputAction(
                action_type=InputActionType.TYPE_TEXT,
                target=InputTarget(window=WindowReference(title="Editor")),
                text="hello",
            ),
        ]
    )

    assert result.succeeded is True
    assert backend.calls == []
    assert len(result.logs) == 2
    assert all(log.executed is False for log in result.logs)
    assert delays == []


def test_safe_input_simulator_executes_and_applies_delay():
    backend = FakeBackend()
    delays: list[float] = []
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Editor"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=1920, height=1080)),
        inter_action_delay_seconds=0.2,
        dry_run=False,
        sleep_fn=delays.append,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.KEYPRESS,
                target=InputTarget(window=WindowReference(title="Editor")),
                key="enter",
            ),
            InputAction(
                action_type=InputActionType.HOTKEY,
                target=InputTarget(window=WindowReference(title="Editor")),
                hotkey=("ctrl", "s"),
            ),
        ]
    )

    assert result.succeeded is True
    assert backend.calls == [("press", ("enter",)), ("hotkey", ("ctrl", "s"))]
    assert delays == [0.2, 0.2]


def test_safe_input_simulator_fails_when_window_is_not_focused():
    backend = FakeBackend()
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Different"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=1920, height=1080)),
        dry_run=False,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.TYPE_TEXT,
                target=InputTarget(window=WindowReference(title="Editor")),
                text="blocked",
            )
        ]
    )

    assert result.succeeded is False
    assert result.failure_reason == "Target window is not focused."
    assert backend.calls == []


def test_safe_input_simulator_fails_when_element_is_outside_screen():
    backend = FakeBackend()
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Editor"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=400, height=300)),
        dry_run=False,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.CLICK,
                target=InputTarget(
                    window=WindowReference(title="Editor"),
                    element_bounds=(350, 250, 450, 320),
                ),
            )
        ]
    )

    assert result.succeeded is False
    assert result.failure_reason == "Target element is not fully visible within screen boundaries."
    assert backend.calls == []


def test_safe_input_simulator_stops_when_fail_safe_is_triggered():
    backend = FakeBackend()
    abort_checks = iter([False, True]).__next__
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Editor"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=1920, height=1080)),
        dry_run=False,
        sleep_fn=lambda _: None,
        abort_checker=abort_checks,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.KEYPRESS,
                target=InputTarget(window=WindowReference(title="Editor")),
                key="enter",
            ),
            InputAction(
                action_type=InputActionType.TYPE_TEXT,
                target=InputTarget(window=WindowReference(title="Editor")),
                text="blocked",
            ),
        ]
    )

    assert result.succeeded is False
    assert result.failure_reason == "Execution aborted by fail-safe controller."
    assert backend.calls == [("press", ("enter",))]


def test_safe_input_simulator_blocks_disallowed_action_type(tmp_path):
    backend = FakeBackend()
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text(
        '{"action_types":["keypress"],"applications":["editor"],"urls":["https://safe.example/*"],"file_paths":["C:/safe/*"]}',
        encoding="utf-8",
    )
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Editor"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=1920, height=1080)),
        dry_run=False,
        allowlist_enforcer=ActionAllowlistEnforcer(config_path=str(allowlist_path)),
        workflow_id="wf-input",
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.TYPE_TEXT,
                target=InputTarget(window=WindowReference(title="Editor")),
                text="blocked",
            )
        ]
    )

    assert result.succeeded is False
    assert "allowlist" in (result.failure_reason or "")
    assert backend.calls == []


def test_safe_input_simulator_adapts_coordinates_for_current_resolution():
    backend = FakeBackend()
    manager = ResolutionAdaptiveCoordinateManager(
        calibration_profile=ResolutionCalibrationProfile(
            baseline_resolution=(1000, 500),
            baseline_dpi=96,
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=2000, height=1000, dpi=96)),
    )
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(
            WindowState(reference=WindowReference(title="Editor"), focused=True)
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=2000, height=1000, dpi=96)),
        dry_run=False,
        sleep_fn=lambda _: None,
        coordinate_manager=manager,
    )

    result = simulator.run(
        [
            InputAction(
                action_type=InputActionType.CLICK,
                target=InputTarget(
                    window=WindowReference(title="Editor"),
                    element_bounds=(50, 60, 150, 160),
                ),
                position=(100, 120),
            )
        ]
    )

    assert result.succeeded is True
    assert backend.calls == [("click", (200, 240, "left"))]


class FakeWindowBackend:
    def __init__(self, windows: list[WindowContext]):
        self.windows = {window.handle: window for window in windows}
        self.focus_calls: list[int] = []
        self.move_resize_calls: list[tuple[int, int, int, int, int]] = []
        self.show_calls: list[tuple[int, int]] = []
        self.enumerations: list[list[WindowContext]] | None = None

    def enumerate_windows(self) -> list[WindowContext]:
        if self.enumerations:
            current = self.enumerations.pop(0)
            self.windows.update({window.handle: window for window in current})
            return current
        return list(self.windows.values())

    def get_foreground_window_handle(self) -> int | None:
        for window in self.windows.values():
            if window.focused:
                return window.handle
        return None

    def focus_window(self, handle: int) -> bool:
        self.focus_calls.append(handle)
        for window in self.windows.values():
            window.focused = window.handle == handle
        return handle in self.windows

    def move_resize_window(self, handle: int, x: int, y: int, width: int, height: int) -> bool:
        self.move_resize_calls.append((handle, x, y, width, height))
        window = self.windows.get(handle)
        if window is None:
            return False
        window.position = (x, y)
        window.size = (width, height)
        return True

    def show_window(self, handle: int, command: int) -> bool:
        self.show_calls.append((handle, command))
        window = self.windows.get(handle)
        if window is None:
            return False
        if command == SW_MINIMIZE:
            window.minimized = True
            window.maximized = False
        if command == SW_MAXIMIZE:
            window.maximized = True
            window.minimized = False
        return True

    def get_window_by_handle(self, handle: int) -> WindowContext | None:
        return self.windows.get(handle)


def test_window_manager_can_focus_window_by_title():
    backend = FakeWindowBackend(
        [
            WindowContext(handle=1, title="Notes", process_name="notepad.exe"),
            WindowContext(handle=2, title="Browser", process_name="chrome.exe"),
        ]
    )
    manager = DesktopWindowManager(backend=backend, timeout_seconds=1.0, retry_count=3, sleep_fn=lambda _: None)

    result = manager.focus_window(title="Notes")

    assert result.succeeded is True
    assert result.window is not None
    assert result.window.handle == 1
    assert backend.focus_calls == [1]


def test_window_manager_can_focus_window_by_process_name():
    backend = FakeWindowBackend(
        [
            WindowContext(handle=3, title="Project", process_name="code.exe"),
        ]
    )
    manager = DesktopWindowManager(backend=backend, timeout_seconds=1.0, retry_count=3, sleep_fn=lambda _: None)

    result = manager.focus_window(process_name="code.exe")

    assert result.succeeded is True
    assert result.window is not None
    assert result.window.process_name == "code.exe"


def test_window_manager_can_move_and_resize_window():
    backend = FakeWindowBackend(
        [
            WindowContext(handle=4, title="Console", process_name="terminal.exe", position=(0, 0), size=(400, 300)),
        ]
    )
    manager = DesktopWindowManager(backend=backend, timeout_seconds=1.0, retry_count=3, sleep_fn=lambda _: None)

    result = manager.move_resize_window(handle=4, x=50, y=60, width=900, height=700)

    assert result.succeeded is True
    assert result.window is not None
    assert result.window.position == (50, 60)
    assert result.window.size == (900, 700)


def test_window_manager_can_minimize_and_maximize_window():
    backend = FakeWindowBackend(
        [
            WindowContext(handle=5, title="Viewer", process_name="viewer.exe"),
        ]
    )
    manager = DesktopWindowManager(backend=backend, timeout_seconds=1.0, retry_count=3, sleep_fn=lambda _: None)

    minimized = manager.minimize_window(5)
    maximized = manager.maximize_window(5)

    assert minimized.succeeded is True
    assert minimized.window is not None and minimized.window.minimized is True
    assert maximized.succeeded is True
    assert maximized.window is not None and maximized.window.maximized is True


def test_window_manager_detects_new_window():
    backend = FakeWindowBackend(
        [
            WindowContext(handle=10, title="Existing", process_name="one.exe"),
        ]
    )
    backend.enumerations = [
        [WindowContext(handle=10, title="Existing", process_name="one.exe")],
        [
            WindowContext(handle=10, title="Existing", process_name="one.exe"),
            WindowContext(handle=11, title="Popup", process_name="two.exe"),
        ],
    ]
    manager = DesktopWindowManager(backend=backend, timeout_seconds=1.0, retry_count=3, sleep_fn=lambda _: None)

    result = manager.wait_for_new_window()

    assert result.succeeded is True
    assert result.window is not None
    assert result.window.handle == 11


def test_window_manager_returns_failure_after_poll_timeout():
    backend = FakeWindowBackend(
        [
            WindowContext(handle=12, title="Missing Focus", process_name="app.exe"),
        ]
    )

    class NoFocusBackend(FakeWindowBackend):
        def focus_window(self, handle: int) -> bool:
            self.focus_calls.append(handle)
            return True

    stubborn_backend = NoFocusBackend(list(backend.windows.values()))
    sleeps: list[float] = []
    manager = DesktopWindowManager(
        backend=stubborn_backend,
        timeout_seconds=2.0,
        retry_count=4,
        sleep_fn=sleeps.append,
    )

    result = manager.focus_window(title="Missing Focus")

    assert result.succeeded is False
    assert result.reason == "Window did not become focused within the configured timeout."
    assert sleeps == [0.5, 0.5, 0.5]


class FakeClipboardBackend:
    def __init__(self, content: ClipboardContent | None = None):
        self.content = content or ClipboardContent(content_type=ClipboardContentType.EMPTY)
        self.read_sequence: list[ClipboardContent] | None = None

    def read(self) -> ClipboardContent:
        if self.read_sequence:
            self.content = self.read_sequence.pop(0)
        return self.content

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self.content = ClipboardContent(
            content_type=ClipboardContentType.TEXT,
            text=normalized,
            encoding=encoding,
        )

    def write_image(self, image_bytes: bytes) -> None:
        self.content = ClipboardContent(
            content_type=ClipboardContentType.IMAGE,
            image_bytes=image_bytes,
        )


class FakeInputRunner:
    def __init__(self):
        self.actions: list[list[InputAction]] = []

    def run(self, actions: list[InputAction]):
        self.actions.append(actions)
        return None


def test_clipboard_manager_reads_and_writes_text_with_logging():
    backend = FakeClipboardBackend()
    waits: list[float] = []
    manager = ClipboardManager(
        backend=backend,
        stabilization_wait_seconds=0.2,
        sleep_fn=waits.append,
    )

    write_result = manager.write_text("hello\r\nworld", encoding="utf-8")
    read_result = manager.read_clipboard()

    assert write_result.succeeded is True
    assert read_result.content is not None
    assert read_result.content.text == "hello\nworld"
    assert waits == [0.2, 0.2]
    assert [event.operation for event in manager.get_event_log()] == ["write_text", "read"]


def test_clipboard_manager_detects_clipboard_changes():
    backend = FakeClipboardBackend(
        ClipboardContent(content_type=ClipboardContentType.TEXT, text="before")
    )
    backend.read_sequence = [
        ClipboardContent(content_type=ClipboardContentType.TEXT, text="before"),
        ClipboardContent(content_type=ClipboardContentType.TEXT, text="after"),
    ]
    waits: list[float] = []
    manager = ClipboardManager(
        backend=backend,
        stabilization_wait_seconds=0.1,
        sleep_fn=waits.append,
    )

    result = manager.monitor_changes(timeout_seconds=1.0, retry_count=4)

    assert result.succeeded is True
    assert result.content is not None
    assert result.content.text == "after"
    assert waits == [0.1]


def test_clipboard_manager_can_write_image():
    backend = FakeClipboardBackend()
    manager = ClipboardManager(backend=backend, stabilization_wait_seconds=0.0, sleep_fn=lambda _: None)

    result = manager.write_image(b"image-bytes")

    assert result.succeeded is True
    assert result.content is not None
    assert result.content.content_type == ClipboardContentType.IMAGE


def test_paste_and_verify_uses_clipboard_and_hotkey_mode():
    backend = FakeClipboardBackend()
    runner = FakeInputRunner()
    manager = ClipboardManager(backend=backend, stabilization_wait_seconds=0.0, sleep_fn=lambda _: None)

    result = manager.paste_and_verify(
        text="hello",
        input_runner=runner,
        target=InputTarget(window=WindowReference(title="Editor")),
        readback=lambda: "hello",
        mode=ClipboardPasteMode.PASTE,
    )

    assert result.succeeded is True
    assert len(runner.actions) == 1
    assert runner.actions[0][0].action_type == InputActionType.HOTKEY
    assert runner.actions[0][0].hotkey == ("ctrl", "v")


def test_paste_and_verify_supports_type_mode_and_mismatch_reason():
    backend = FakeClipboardBackend()
    runner = FakeInputRunner()
    manager = ClipboardManager(backend=backend, stabilization_wait_seconds=0.0, sleep_fn=lambda _: None)

    result = manager.paste_and_verify(
        text="expected",
        input_runner=runner,
        target=InputTarget(window=WindowReference(title="Editor")),
        readback=lambda: "actual",
        mode=ClipboardPasteMode.TYPE,
    )

    assert result.succeeded is False
    assert result.actual == "actual"
    assert result.reason == "Read-back text does not match the expected clipboard input."
    assert runner.actions[0][0].action_type == InputActionType.TYPE_TEXT


class FakeTemplateBackend:
    def __init__(self):
        self.saved_images: list[tuple[object, str]] = []
        self.last_roi: tuple[int, int, int, int] | None = None
        self.scales_used: list[float] = []

    def load_image(self, path: str):
        return {"kind": "template", "path": path}

    def load_screenshot(self, screenshot_path: str | None = None):
        return {
            "kind": "screenshot",
            "path": screenshot_path,
            "size": (1920, 1080),
            "payload": "full-screen-image",
        }

    def crop_image(self, image, bounds: tuple[int, int, int, int]):
        return {"cropped_from": image, "bounds": bounds}

    def save_image(self, image, path: str) -> None:
        self.saved_images.append((image, path))

    def resize_image(self, image, scale_factor: float):
        self.scales_used.append(scale_factor)
        resized = dict(image)
        resized["scale_factor"] = scale_factor
        return resized

    def get_image_size(self, image) -> tuple[int, int]:
        return image["size"]

    def find_matches(
        self,
        screenshot,
        template,
        threshold: float,
        region_of_interest: tuple[int, int, int, int] | None = None,
    ) -> list[TemplateMatch]:
        self.last_roi = region_of_interest
        scale_factor = template.get("scale_factor", 1.0)
        if template["path"].endswith("send.png"):
            matches = [
                TemplateMatch("ignored", 0.93, (100, 200, 150, 240), (125, 220)),
                TemplateMatch("ignored", 0.85, (300, 400, 350, 440), (325, 420)),
            ]
            if scale_factor > 1.0:
                matches.insert(0, TemplateMatch("ignored", 0.97, (102, 202, 152, 242), (127, 222)))
            return matches
        if template["path"].endswith("avatar.png"):
            return [TemplateMatch("ignored", 0.88, (50, 60, 90, 100), (70, 80))]
        return []


def test_template_image_matcher_returns_sorted_matches_for_multiple_templates():
    backend = FakeTemplateBackend()
    matcher = TemplateImageMatcher(backend=backend)

    results = matcher.search(
        screenshot_path="screen.png",
        requests=[
            TemplateSearchRequest(
                template_name="send_button",
                template_path="templates/send.png",
                threshold=0.8,
                region_of_interest=(0, 0, 500, 500),
            ),
            TemplateSearchRequest(
                template_name="profile_avatar",
                template_path="templates/avatar.png",
                threshold=0.82,
            ),
        ],
    )

    assert len(results) == 2
    assert results[0].template_name == "send_button"
    assert [match.confidence for match in results[0].matches] == [0.93, 0.85]
    assert results[0].matches[0].template_name == "send_button"
    assert results[1].template_name == "profile_avatar"


def test_template_image_matcher_passes_region_of_interest():
    backend = FakeTemplateBackend()
    matcher = TemplateImageMatcher(backend=backend)

    matcher.search(
        screenshot_path="screen.png",
        requests=[
            TemplateSearchRequest(
                template_name="send_button",
                template_path="templates/send.png",
                threshold=0.8,
                region_of_interest=(10, 20, 110, 120),
            )
        ],
    )

    assert backend.last_roi == (10, 20, 110, 120)


def test_template_image_matcher_captures_reference_template_with_metadata(tmp_path):
    backend = FakeTemplateBackend()
    matcher = TemplateImageMatcher(backend=backend)

    result = matcher.capture_reference_template(
        name="send button",
        output_directory=str(tmp_path),
        bounds=(100, 200, 180, 240),
        application_name="Chat App",
        screenshot_path="screen.png",
    )

    assert result.succeeded is True
    assert result.reference is not None
    assert result.reference.application_name == "Chat App"
    assert result.reference.screen_resolution == (1920, 1080)
    assert backend.saved_images
    metadata_path = tmp_path / "send_button.json"
    assert metadata_path.exists()


def test_template_image_matcher_uses_multi_scale_matching_with_coordinate_manager():
    backend = FakeTemplateBackend()
    manager = ResolutionAdaptiveCoordinateManager(
        calibration_profile=ResolutionCalibrationProfile(
            baseline_resolution=(1920, 1080),
            baseline_dpi=96,
            multi_scale_steps=(0.9, 1.0, 1.1),
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=1920, height=1080, dpi=96)),
    )
    matcher = TemplateImageMatcher(backend=backend, coordinate_manager=manager)

    results = matcher.search(
        screenshot_path="screen.png",
        requests=[
            TemplateSearchRequest(
                template_name="send_button",
                template_path="templates/send.png",
                threshold=0.8,
            )
        ],
    )

    assert backend.scales_used == [0.9, 1.0, 1.1]
    assert results[0].matches[0].confidence == 0.97


def test_resolution_adaptive_coordinate_manager_verifies_scale_from_reference_elements():
    backend = FakeTemplateBackend()
    manager = ResolutionAdaptiveCoordinateManager(
        calibration_profile=ResolutionCalibrationProfile(
            baseline_resolution=(1000, 500),
            baseline_dpi=96,
            scale_tolerance=0.2,
            reference_elements=[
                ResolutionReferenceElement(
                    name="send_button",
                    expected_bounds=(24, 60, 100, 160),
                    template_path="templates/send.png",
                    threshold=0.8,
                )
            ],
        ),
        screen_inspector=StaticScreenInspector(ScreenBounds(width=2000, height=1000, dpi=96)),
        template_matcher=TemplateImageMatcher(backend=backend),
    )

    result = manager.verify_scale(screenshot_path="screen.png")
    adapted = manager.adapt_point((100, 110))

    assert result.succeeded is True
    assert result.actual_scale_x > 1.9
    assert result.actual_scale_y > 1.9
    assert result.references[0].name == "send_button"
    assert adapted.adapted_point is not None
    assert adapted.adapted_point[0] >= 190


def test_dynamic_roi_calculator_routes_toolbar_and_content_checks_to_window_zones():
    calculator = DynamicRegionOfInterestCalculator(
        window_manager=FakeWindowManagerForVerifier(
            [
                WindowContext(
                    handle=99,
                    title="Editor",
                    process_name="editor.exe",
                    position=(100, 50),
                    size=(1000, 800),
                    focused=True,
                )
            ]
        )
    )

    toolbar_result = calculator.calculate_for_check(
        ScreenVerificationCheck(
            check_id="toolbar",
            check_type=ScreenCheckType.IMAGE_PRESENT,
            template_name="toolbar_search",
            template_path="toolbar.png",
            element_role="toolbar",
        )
    )
    content_result = calculator.calculate_for_check(
        ScreenVerificationCheck(
            check_id="content",
            check_type=ScreenCheckType.TEXT_PRESENT,
            target_text="Document body",
        )
    )

    assert toolbar_result.succeeded is True
    assert toolbar_result.roi is not None
    assert toolbar_result.roi.zone_type is WindowZoneType.TOOLBAR
    assert toolbar_result.roi.bounds[1] == 50
    assert content_result.succeeded is True
    assert content_result.roi is not None
    assert content_result.roi.zone_type is WindowZoneType.CONTENT
    assert content_result.roi.bounds[1] > toolbar_result.roi.bounds[3] - 5


def test_dynamic_roi_calculator_updates_zone_bounds_after_window_resize():
    window_manager = FakeWindowManagerForVerifier(
        [
            WindowContext(
                handle=100,
                title="App",
                process_name="app.exe",
                position=(10, 20),
                size=(800, 600),
                focused=True,
            )
        ]
    )
    calculator = DynamicRegionOfInterestCalculator(window_manager=window_manager)

    first = calculator.calculate_for_check(
        ScreenVerificationCheck(
            check_id="sidebar",
            check_type=ScreenCheckType.ELEMENT_VALUE,
            element_name="Folder Tree",
            element_role="sidebar",
        )
    )
    window_manager._windows = [
        WindowContext(
            handle=100,
            title="App",
            process_name="app.exe",
            position=(20, 40),
            size=(1200, 900),
            focused=True,
        )
    ]
    second = calculator.calculate_for_check(
        ScreenVerificationCheck(
            check_id="sidebar",
            check_type=ScreenCheckType.ELEMENT_VALUE,
            element_name="Folder Tree",
            element_role="sidebar",
        )
    )

    assert first.roi is not None and second.roi is not None
    assert first.roi.zone_type is WindowZoneType.SIDEBAR
    assert second.roi.zone_type is WindowZoneType.SIDEBAR
    assert second.roi.bounds != first.roi.bounds
    assert second.roi.window_bounds == (20, 40, 1220, 940)


class FakeOCRBackend:
    def capture_screenshot(self, region: tuple[int, int, int, int] | None = None):
        return {"region": region}

    def load_image(self, screenshot_path: str):
        return {"path": screenshot_path}

    def extract_blocks(self, image, language: str) -> list[OCRTextBlock]:
        return [
            OCRTextBlock(text="Continue", confidence=0.91, bounds=(10, 20, 100, 50)),
            OCRTextBlock(text="Settngs", confidence=0.74, bounds=(120, 20, 220, 50)),
            OCRTextBlock(text="Low", confidence=0.4, bounds=(240, 20, 280, 50)),
        ]


def test_ocr_extractor_filters_blocks_by_minimum_confidence():
    extractor = OCRExtractor(backend=FakeOCRBackend())

    result = extractor.extract_text(
        region_of_interest=(100, 200, 400, 500),
        minimum_confidence=0.6,
        language="eng",
    )

    assert len(result.blocks) == 2
    assert result.blocks[0].bounds == (110, 220, 200, 250)
    assert result.blocks[1].bounds == (220, 220, 320, 250)


def test_ocr_extractor_find_text_supports_exact_and_partial_matches():
    extractor = OCRExtractor(backend=FakeOCRBackend())

    exact = extractor.find_text(target="Continue", minimum_confidence=0.6)
    partial = extractor.find_text(target="Cont", minimum_confidence=0.6)

    assert exact.succeeded is True
    assert exact.bounds == (10, 20, 100, 50)
    assert partial.succeeded is True
    assert partial.matched_text == "Continue"


def test_ocr_extractor_find_text_supports_fuzzy_matching():
    extractor = OCRExtractor(backend=FakeOCRBackend())

    result = extractor.find_text(
        target="Settings",
        minimum_confidence=0.6,
        fuzzy_threshold=0.7,
    )

    assert result.succeeded is True
    assert result.matched_text == "Settngs"


def test_ocr_extractor_returns_failure_when_no_match_clears_threshold():
    extractor = OCRExtractor(backend=FakeOCRBackend())

    result = extractor.find_text(
        target="Unrelated",
        minimum_confidence=0.6,
        fuzzy_threshold=0.9,
    )

    assert result.succeeded is False
    assert result.reason == "No OCR text matched the target strongly enough."


class FakeAccessibilityBackend:
    def __init__(self, tree: AccessibilityTree | None):
        self.tree = tree

    def get_active_application_tree(self) -> AccessibilityTree | None:
        return self.tree


class FakeRawWindowBackend:
    def __init__(self, root: AccessibilityElement | None, children: list[AccessibilityElement] | None = None):
        self.root = root
        self.children = children or []

    def get_active_window_handle(self) -> int | None:
        return self.root.handle if self.root is not None else None

    def inspect_window(self, handle: int) -> AccessibilityElement | None:
        return self.root

    def inspect_children(self, handle: int) -> list[AccessibilityElement]:
        return list(self.children)

    def get_application_name(self, handle: int) -> str | None:
        return "fallback.exe"


def test_accessibility_tree_reader_finds_elements_by_name_role_and_value():
    child = AccessibilityElement(
        element_id="child-1",
        name="Send",
        role="Button",
        value="primary",
        state=AccessibilityElementState(text="Send", enabled=True, selected=False),
    )
    root = AccessibilityElement(
        element_id="root-1",
        name="Window",
        role="Window",
        children=[child],
    )
    reader = AccessibilityTreeReader(
        accessibility_backend=FakeAccessibilityBackend(
            AccessibilityTree(application_name="chat.exe", root=root)
        ),
        raw_window_backend=FakeRawWindowBackend(None),
    )

    result = reader.find_elements(name="Send", role="Button", value="primary")

    assert len(result.matches) == 1
    assert result.matches[0].element_id == "child-1"
    assert result.used_fallback is False


def test_accessibility_tree_reader_enumerates_children_and_state_helpers():
    child = AccessibilityElement(
        element_id="child-2",
        name="Message",
        role="Edit",
        value="hello",
        state=AccessibilityElementState(text="hello", enabled=True, selected=True),
    )
    root = AccessibilityElement(
        element_id="root-2",
        name="Composer",
        role="Pane",
        children=[child],
    )
    reader = AccessibilityTreeReader(
        accessibility_backend=FakeAccessibilityBackend(
            AccessibilityTree(application_name="chat.exe", root=root)
        ),
        raw_window_backend=FakeRawWindowBackend(None),
    )

    children = reader.enumerate_children(root)

    assert children[0].element_id == "child-2"
    assert reader.get_element_text(child) == "hello"
    assert reader.is_element_enabled(child) is True
    assert reader.is_element_selected(child) is True


def test_accessibility_tree_reader_falls_back_to_raw_window_inspection():
    fallback_root = AccessibilityElement(
        element_id="win32:10",
        name="Legacy App",
        role="Window",
        value="Legacy App",
        state=AccessibilityElementState(text="Legacy App", enabled=True, selected=False),
        source="raw_window",
        handle=10,
    )
    fallback_child = AccessibilityElement(
        element_id="win32:11",
        name="OK",
        role="Button",
        value="OK",
        state=AccessibilityElementState(text="OK", enabled=True, selected=False),
        source="raw_window",
        handle=11,
    )
    reader = AccessibilityTreeReader(
        accessibility_backend=FakeAccessibilityBackend(None),
        raw_window_backend=FakeRawWindowBackend(fallback_root, [fallback_child]),
    )

    tree = reader.read_active_application_tree()
    result = reader.find_elements(name="OK", role="Button")

    assert tree.root is not None
    assert tree.application_name == "fallback.exe"
    assert len(tree.root.children) == 1
    assert result.used_fallback is True
    assert result.matches[0].element_id == "win32:11"


def test_accessibility_tree_reader_uses_raw_children_when_element_has_no_children():
    root = AccessibilityElement(
        element_id="win32:20",
        name="Legacy Parent",
        role="Window",
        state=AccessibilityElementState(enabled=True),
        source="raw_window",
        handle=20,
    )
    raw_child = AccessibilityElement(
        element_id="win32:21",
        name="Child",
        role="Static",
        source="raw_window",
        handle=21,
    )
    reader = AccessibilityTreeReader(
        accessibility_backend=FakeAccessibilityBackend(
            AccessibilityTree(application_name="legacy.exe", root=root)
        ),
        raw_window_backend=FakeRawWindowBackend(root, [raw_child]),
    )

    children = reader.enumerate_children(root)

    assert len(children) == 1
    assert children[0].element_id == "win32:21"


class FakeScreenshotBackend:
    def capture_screenshot_to_path(self, path: str | None = None) -> str:
        return path or "verification.png"


class FakeOCRExtractorForVerifier:
    def __init__(self, matches: dict[str, bool]):
        self.matches = matches

    def find_text(self, *, target: str, screenshot_path: str | None = None, region_of_interest=None):
        matched = self.matches.get(target, False)
        return type(
            "OCRMatch",
            (),
            {
                "succeeded": matched,
                "matched_text": target if matched else None,
                "reason": None if matched else "not found",
            },
        )()


class FakeTemplateMatcherForVerifier:
    def __init__(self, present_templates: set[str]):
        self.present_templates = present_templates
        self.last_requests: list[TemplateSearchRequest] = []

    def search(self, screenshot_path: str | None, requests: list[TemplateSearchRequest]):
        self.last_requests = list(requests)
        results = []
        for request in requests:
            matches = []
            if request.template_name in self.present_templates:
                matches = [TemplateMatch(request.template_name, 0.95, (10, 10, 30, 30), (20, 20))]
            results.append(type("TemplateResult", (), {"template_name": request.template_name, "matches": matches})())
        return results


class FakeWindowManagerForVerifier:
    def __init__(self, windows: list[WindowContext]):
        self._windows = windows

    def list_windows(self):
        return self._windows


def test_screen_state_verifier_returns_passed_and_failed_checks():
    accessibility_root = AccessibilityElement(
        element_id="1",
        name="Status",
        role="Text",
        value="Ready",
        state=AccessibilityElementState(text="Ready", enabled=True, selected=False),
    )
    verifier = ScreenStateVerifier(
        ocr_extractor=FakeOCRExtractorForVerifier({"Continue": True, "loading": False}),
        template_matcher=FakeTemplateMatcherForVerifier({"send_button"}),
        window_manager=FakeWindowManagerForVerifier(
            [WindowContext(handle=1, title="Notepad", process_name="notepad.exe", focused=True)]
        ),
        accessibility_reader=AccessibilityTreeReader(
            accessibility_backend=FakeAccessibilityBackend(
                AccessibilityTree(application_name="app.exe", root=accessibility_root)
            ),
            raw_window_backend=FakeRawWindowBackend(None),
        ),
        screenshot_backend=FakeScreenshotBackend(),
        sleep_fn=lambda _: None,
    )

    result = verifier.verify(
        [
            ScreenVerificationCheck(check_id="text", check_type=ScreenCheckType.TEXT_PRESENT, target_text="Continue"),
            ScreenVerificationCheck(
                check_id="image",
                check_type=ScreenCheckType.IMAGE_PRESENT,
                template_name="send_button",
                template_path="send.png",
            ),
            ScreenVerificationCheck(
                check_id="window",
                check_type=ScreenCheckType.ACTIVE_WINDOW,
                window_title="Notepad",
            ),
            ScreenVerificationCheck(
                check_id="value",
                check_type=ScreenCheckType.ELEMENT_VALUE,
                element_name="Status",
                element_role="Text",
                expected_value="Ready",
            ),
            ScreenVerificationCheck(
                check_id="loading",
                check_type=ScreenCheckType.LOADING_ABSENT,
                target_text="loading",
            ),
        ]
    )

    assert result.screenshot_path == "verification.png"
    assert [check.check_id for check in result.passed_checks] == ["text", "image", "window", "value", "loading"]
    assert result.failed_checks == []


def test_screen_state_verifier_reports_failures_after_timeout():
    modal_root = AccessibilityElement(
        element_id="modal",
        name="Loading",
        role="Dialog",
        source="accessibility",
    )
    sleeps: list[float] = []
    verifier = ScreenStateVerifier(
        ocr_extractor=FakeOCRExtractorForVerifier({"Missing": False, "loading": True}),
        template_matcher=FakeTemplateMatcherForVerifier(set()),
        window_manager=FakeWindowManagerForVerifier(
            [WindowContext(handle=1, title="Browser", process_name="chrome.exe", focused=True)]
        ),
        accessibility_reader=AccessibilityTreeReader(
            accessibility_backend=FakeAccessibilityBackend(
                AccessibilityTree(application_name="browser.exe", root=modal_root)
            ),
            raw_window_backend=FakeRawWindowBackend(None),
        ),
        screenshot_backend=FakeScreenshotBackend(),
        sleep_fn=sleeps.append,
    )

    result = verifier.verify(
        [
            ScreenVerificationCheck(
                check_id="missing-text",
                check_type=ScreenCheckType.TEXT_PRESENT,
                target_text="Missing",
                timeout_seconds=1.0,
                polling_interval_seconds=0.5,
            ),
            ScreenVerificationCheck(
                check_id="modal",
                check_type=ScreenCheckType.MODAL_ABSENT,
                element_name="Loading",
                timeout_seconds=1.0,
                polling_interval_seconds=0.5,
            ),
        ]
    )

    assert [check.check_id for check in result.failed_checks] == ["missing-text", "modal"]
    assert len(sleeps) == 2


def test_screen_state_verifier_uses_dynamic_roi_when_check_region_is_not_provided():
    template_matcher = FakeTemplateMatcherForVerifier({"toolbar_search"})
    roi_window_manager = FakeWindowManagerForVerifier(
        [
            WindowContext(
                handle=77,
                title="Workspace",
                process_name="workspace.exe",
                position=(50, 40),
                size=(1000, 700),
                focused=True,
            )
        ]
    )
    verifier = ScreenStateVerifier(
        ocr_extractor=FakeOCRExtractorForVerifier({"Document": True}),
        template_matcher=template_matcher,
        window_manager=roi_window_manager,
        accessibility_reader=AccessibilityTreeReader(
            accessibility_backend=FakeAccessibilityBackend(
                AccessibilityTree(application_name="workspace.exe", root=None)
            ),
            raw_window_backend=FakeRawWindowBackend(None),
        ),
        screenshot_backend=FakeScreenshotBackend(),
        sleep_fn=lambda _: None,
        roi_calculator=DynamicRegionOfInterestCalculator(window_manager=roi_window_manager),
    )

    result = verifier.verify(
        [
            ScreenVerificationCheck(
                check_id="toolbar-image",
                check_type=ScreenCheckType.IMAGE_PRESENT,
                template_name="toolbar_search",
                template_path="toolbar.png",
                element_role="toolbar",
            )
        ]
    )

    assert result.failed_checks == []
    assert template_matcher.last_requests[0].region_of_interest is not None
    assert template_matcher.last_requests[0].region_of_interest[1] == 40


class FakeCaptureBackend:
    def __init__(self, images: list[object]):
        self.images = images
        self.saved: list[tuple[object, str]] = []
        self.regions: list[tuple[int, int, int, int] | None] = []

    def capture(
        self,
        region_of_interest: tuple[int, int, int, int] | None = None,
        monitor_id: str | None = None,
    ):
        self.regions.append(region_of_interest)
        return self.images.pop(0)

    def save(self, image, path: str) -> str:
        self.saved.append((image, path))
        return path


class FakeDifferenceBackend:
    def __init__(self, differences: list[float]):
        self.differences = differences

    def compute_difference(self, previous_image, current_image) -> float:
        return self.differences.pop(0)


def test_change_detection_monitor_emits_change_event_when_threshold_exceeded():
    capture = FakeCaptureBackend(images=["before", "after"])
    diff = FakeDifferenceBackend(differences=[0.24])
    monitor = ScreenChangeDetectionMonitor(
        capture_backend=capture,
        difference_backend=diff,
        sleep_fn=lambda _: None,
    )

    result = monitor.wait_for_change(
        region_of_interest=(10, 20, 110, 120),
        change_threshold=0.2,
        timeout_seconds=1.0,
        polling_interval_seconds=0.5,
        screenshot_path="changed.png",
    )

    assert result.changed is True
    assert result.event is not None
    assert result.event.difference_metric == 0.24
    assert result.event.screenshot_path == "changed.png"
    assert capture.regions == [(10, 20, 110, 120), (10, 20, 110, 120)]


def test_change_detection_monitor_times_out_when_change_is_below_threshold():
    capture = FakeCaptureBackend(images=["first", "second", "third"])
    diff = FakeDifferenceBackend(differences=[0.03, 0.04])
    sleeps: list[float] = []
    monitor = ScreenChangeDetectionMonitor(
        capture_backend=capture,
        difference_backend=diff,
        sleep_fn=sleeps.append,
    )

    result = monitor.wait_for_change(
        change_threshold=0.2,
        timeout_seconds=1.0,
        polling_interval_seconds=0.5,
    )

    assert result.changed is False
    assert "Timeout expired" in (result.reason or "")
    assert sleeps == [0.5]


def test_change_detection_monitor_can_sample_single_change():
    capture = FakeCaptureBackend(images=["next"])
    diff = FakeDifferenceBackend(differences=[0.11])
    monitor = ScreenChangeDetectionMonitor(
        capture_backend=capture,
        difference_backend=diff,
        sleep_fn=lambda _: None,
    )

    result = monitor.sample_change(
        "previous",
        change_threshold=0.1,
        screenshot_path="sample.png",
    )

    assert result.changed is True
    assert result.event is not None
    assert result.event.threshold == 0.1


def test_account_registry_persists_and_lists_accounts(tmp_path):
    storage = tmp_path / "accounts.json"
    registry = AccountRegistry(storage_path=str(storage))

    result = registry.upsert_account(
        AccountRecord(
            name="work",
            credential_reference="vault://work",
            account_type="workspace",
            application="https://example.com",
            health_score=0.95,
        )
    )

    reopened = AccountRegistry(storage_path=str(storage))

    assert result.succeeded is True
    assert [account.name for account in reopened.list_accounts()] == ["work"]


def test_account_registry_can_lookup_by_name_and_type(tmp_path):
    storage = tmp_path / "accounts.json"
    registry = AccountRegistry(storage_path=str(storage))
    registry.upsert_account(
        AccountRecord(
            name="alpha",
            credential_reference="vault://alpha",
            account_type="app",
            application="notepad.exe",
        )
    )
    registry.upsert_account(
        AccountRecord(
            name="beta",
            credential_reference="vault://beta",
            account_type="app",
            application="calc.exe",
            active=False,
        )
    )

    by_name = registry.get_account_by_name("alpha")
    by_type = registry.get_accounts_by_type("app", active_only=True)

    assert by_name.succeeded is True
    assert by_name.account is not None and by_name.account.credential_reference == "vault://alpha"
    assert [account.name for account in by_type] == ["alpha"]


def test_account_registry_updates_active_status_last_used_and_history(tmp_path):
    storage = tmp_path / "accounts.json"
    registry = AccountRegistry(storage_path=str(storage))
    registry.upsert_account(
        AccountRecord(
            name="gamma",
            credential_reference="vault://gamma",
            account_type="service",
            application="https://service.example",
        )
    )

    timestamp = datetime(2026, 4, 8, 15, 30, tzinfo=timezone.utc)
    inactive = registry.set_account_active("gamma", False)
    updated = registry.update_last_used("gamma", timestamp=timestamp)
    logged = registry.log_account_usage("gamma", "health_check", "score refreshed")
    history = registry.get_usage_history("gamma")

    assert inactive.succeeded is True
    assert inactive.account is not None and inactive.account.active is False
    assert updated.succeeded is True
    assert updated.account is not None and updated.account.last_used_at == timestamp
    assert logged.succeeded is True
    assert [event.action for event in history] == ["set_inactive", "update_last_used", "health_check"]


class FakeCipher:
    def encrypt(self, value: str) -> str:
        return value.encode("utf-8").hex()

    def decrypt(self, encrypted_value: str) -> str:
        return bytes.fromhex(encrypted_value).decode("utf-8")


def test_credential_vault_stores_without_plaintext_and_retrieves_value(tmp_path):
    storage = tmp_path / "vault.json"
    vault = CredentialVault(storage_path=str(storage), cipher=FakeCipher())

    stored = vault.store_credential(
        account_identifier="acct-1",
        kind=CredentialKind.PASSWORD,
        value="super-secret",
    )
    retrieved = vault.retrieve_credential("acct-1", CredentialKind.PASSWORD)
    raw_payload = storage.read_text(encoding="utf-8")

    assert stored.succeeded is True
    assert retrieved.succeeded is True
    assert retrieved.value == "super-secret"
    assert "super-secret" not in raw_payload
    assert "73757065722d736563726574" in raw_payload


def test_credential_vault_logs_access_events(tmp_path):
    storage = tmp_path / "vault.json"
    vault = CredentialVault(storage_path=str(storage), cipher=FakeCipher())
    vault.store_credential(
        account_identifier="acct-2",
        kind=CredentialKind.USERNAME,
        value="worker",
    )

    vault.retrieve_credential("acct-2", CredentialKind.USERNAME)
    events = vault.get_access_log("acct-2")

    assert [event.action for event in events] == ["store", "retrieve"]


def test_credential_vault_can_alert_when_near_expiry(tmp_path):
    storage = tmp_path / "vault.json"
    alerts: list[tuple[str, CredentialKind]] = []
    vault = CredentialVault(
        storage_path=str(storage),
        cipher=FakeCipher(),
        alert_callback=lambda account_identifier, kind, expires_at: alerts.append((account_identifier, kind)),
    )
    vault.store_credential(
        account_identifier="acct-3",
        kind=CredentialKind.TOKEN,
        value="soon-expiring",
        expires_at=datetime.now(timezone.utc),
    )

    result = vault.retrieve_credential("acct-3", CredentialKind.TOKEN)

    assert result.succeeded is True
    assert alerts == [("acct-3", CredentialKind.TOKEN)]


def test_credential_vault_can_refresh_near_expiry_credentials(tmp_path):
    storage = tmp_path / "vault.json"
    vault = CredentialVault(
        storage_path=str(storage),
        cipher=FakeCipher(),
        refresh_callback=lambda account_identifier, kind: "refreshed-token",
    )
    vault.store_credential(
        account_identifier="acct-4",
        kind=CredentialKind.TOKEN,
        value="old-token",
        expires_at=datetime.now(timezone.utc),
    )

    result = vault.retrieve_credential("acct-4", CredentialKind.TOKEN)

    assert result.succeeded is True
    assert result.value == "refreshed-token"


def test_credential_vault_audits_sensitive_access_locations(tmp_path):
    storage = tmp_path / "vault.json"
    audit_path = tmp_path / "sensitive_access.jsonl"
    vault = CredentialVault(
        storage_path=str(storage),
        cipher=FakeCipher(),
        sensitive_data_protector=SensitiveDataProtector(
            access_audit_path=str(audit_path),
        ),
    )
    vault.store_credential(
        account_identifier="acct-5",
        kind=CredentialKind.PASSWORD,
        value="very-secret",
    )

    vault.retrieve_credential("acct-5", CredentialKind.PASSWORD)
    lines = audit_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "store"
    assert json.loads(lines[1])["action"] == "retrieve"


class FakeBrowserLauncher:
    def __init__(self):
        self.launches: list[tuple[str, str, str | None]] = []
        self.closes: list[tuple[int | None, str]] = []
        self.next_pid = 1000

    def launch(self, browser_executable: str, profile_directory: str, application: str | None = None) -> int | None:
        self.launches.append((browser_executable, profile_directory, application))
        self.next_pid += 1
        return self.next_pid

    def close(self, process_id: int | None, profile_directory: str) -> bool:
        self.closes.append((process_id, profile_directory))
        return True


def test_browser_profile_switcher_creates_and_lists_profiles(tmp_path):
    storage = tmp_path / "profiles.json"
    switcher = BrowserProfileSwitcher(
        storage_path=str(storage),
        launcher=FakeBrowserLauncher(),
        account_verifier=lambda account_name, profile_directory: True,
    )

    created = switcher.create_profile(
        account_name="alpha",
        profile_directory=str(tmp_path / "alpha"),
        browser_executable="chrome.exe",
        application="https://example.com",
    )

    assert created.succeeded is True
    assert [profile.account_name for profile in switcher.list_profiles()] == ["alpha"]


def test_browser_profile_switcher_launches_and_tracks_session_persistence(tmp_path):
    storage = tmp_path / "profiles.json"
    launcher = FakeBrowserLauncher()
    switcher = BrowserProfileSwitcher(
        storage_path=str(storage),
        launcher=launcher,
        account_verifier=lambda account_name, profile_directory: account_name == "alpha",
    )
    switcher.create_profile(
        account_name="alpha",
        profile_directory=str(tmp_path / "alpha"),
        browser_executable="chrome.exe",
        application="https://example.com",
    )

    result = switcher.launch_profile("alpha")
    profile_result = switcher.get_profile("alpha")

    assert result.succeeded is True
    assert result.session is not None and result.session.active is True
    assert launcher.launches == [("chrome.exe", str(tmp_path / "alpha"), "https://example.com")]
    assert profile_result.profile is not None and profile_result.profile.persistent_session is True


def test_browser_profile_switcher_switches_by_closing_current_and_launching_target(tmp_path):
    storage = tmp_path / "profiles.json"
    launcher = FakeBrowserLauncher()
    switcher = BrowserProfileSwitcher(
        storage_path=str(storage),
        launcher=launcher,
        account_verifier=lambda account_name, profile_directory: True,
    )
    switcher.create_profile(
        account_name="alpha",
        profile_directory=str(tmp_path / "alpha"),
        browser_executable="chrome.exe",
    )
    switcher.create_profile(
        account_name="beta",
        profile_directory=str(tmp_path / "beta"),
        browser_executable="chrome.exe",
    )
    first = switcher.launch_profile("alpha")
    switched = switcher.switch_profile("beta")

    assert first.succeeded is True
    assert switched.succeeded is True
    assert launcher.closes == [(first.session.browser_process_id if first.session else None, str(tmp_path / "alpha"))]
    assert launcher.launches[-1] == ("chrome.exe", str(tmp_path / "beta"), None)


class FakeVerifier:
    def __init__(self, results: list[object]):
        self.results = results

    def verify(self, checks, screenshot_path=None):
        return self.results.pop(0)


class FakeVerificationResult:
    def __init__(self, failed_checks=None, screenshot_path="screen.png"):
        self.failed_checks = failed_checks or []
        self.passed_checks = []
        self.screenshot_path = screenshot_path


def test_session_state_tracker_detects_logged_in_state(tmp_path):
    tracker = SessionStateTracker(
        storage_path=str(tmp_path / "session.json"),
        verifier=FakeVerifier([FakeVerificationResult(failed_checks=[])]),
        reauthenticate_callback=None,
    )

    result = tracker.detect_session_state(
        post_login_checks=[
            ScreenVerificationCheck("post", ScreenCheckType.TEXT_PRESENT, target_text="Dashboard")
        ],
        login_page_checks=[
            ScreenVerificationCheck("login", ScreenCheckType.TEXT_PRESENT, target_text="Sign in")
        ],
    )

    assert result.succeeded is True
    assert result.state is SessionState.LOGGED_IN


def test_session_state_tracker_detects_expired_session_and_reauthenticates(tmp_path):
    tracker = SessionStateTracker(
        storage_path=str(tmp_path / "session.json"),
        verifier=FakeVerifier(
            [
                FakeVerificationResult(failed_checks=["missing-post"]),
                FakeVerificationResult(failed_checks=[]),
                FakeVerificationResult(failed_checks=[]),
            ]
        ),
        reauthenticate_callback=lambda: True,
    )

    result = tracker.validate_session_before_high_risk_operation(
        post_login_checks=[
            ScreenVerificationCheck("post", ScreenCheckType.TEXT_PRESENT, target_text="Dashboard")
        ],
        login_page_checks=[
            ScreenVerificationCheck("login", ScreenCheckType.TEXT_PRESENT, target_text="Sign in")
        ],
    )

    assert result.succeeded is True
    assert result.state is SessionState.LOGGED_IN


def test_session_state_tracker_logs_failed_reauthentication(tmp_path):
    tracker = SessionStateTracker(
        storage_path=str(tmp_path / "session.json"),
        verifier=FakeVerifier(
            [
                FakeVerificationResult(failed_checks=["missing-post"], screenshot_path="expired.png"),
                FakeVerificationResult(failed_checks=[]),
            ]
        ),
        reauthenticate_callback=lambda: False,
    )

    result = tracker.validate_session_before_high_risk_operation(
        post_login_checks=[
            ScreenVerificationCheck("post", ScreenCheckType.TEXT_PRESENT, target_text="Dashboard")
        ],
        login_page_checks=[
            ScreenVerificationCheck("login", ScreenCheckType.TEXT_PRESENT, target_text="Sign in")
        ],
    )

    history = tracker.get_health_log()

    assert result.succeeded is False
    assert result.state is SessionState.LOGGED_OUT
    assert history[-1].state is SessionState.LOGGED_OUT


def test_account_rotation_orchestrator_groups_tasks_sequentially(tmp_path):
    registry = AccountRegistry(storage_path=str(tmp_path / "accounts.json"))
    registry.upsert_account(AccountRecord(name="alpha", credential_reference="vault://a", account_type="app", application="app", health_score=0.9))
    registry.upsert_account(AccountRecord(name="beta", credential_reference="vault://b", account_type="app", application="app", health_score=0.9))

    executed: list[tuple[str, str]] = []
    orchestrator = AccountRotationOrchestrator(
        storage_path=str(tmp_path / "rotation.json"),
        account_registry=registry,
        task_executor=lambda task, account_name: executed.append((task.task_id, account_name)) or True,
    )

    result = orchestrator.execute(
        [
            RotationTask(task_id="1", required_account="alpha"),
            RotationTask(task_id="2", required_account="beta"),
            RotationTask(task_id="3", required_account="alpha"),
        ],
        mode=AccountExecutionMode.SEQUENTIAL,
    )

    assert result.succeeded is True
    assert [[task.task_id for task in batch] for batch in result.scheduled_batches] == [["1", "3"], ["2"]]
    assert executed == [("1", "alpha"), ("3", "alpha"), ("2", "beta")]


def test_account_rotation_orchestrator_skips_unhealthy_accounts(tmp_path):
    registry = AccountRegistry(storage_path=str(tmp_path / "accounts.json"))
    registry.upsert_account(AccountRecord(name="healthy", credential_reference="vault://h", account_type="app", application="app", health_score=0.9))
    registry.upsert_account(AccountRecord(name="unhealthy", credential_reference="vault://u", account_type="app", application="app", health_score=0.2))

    orchestrator = AccountRotationOrchestrator(
        storage_path=str(tmp_path / "rotation.json"),
        account_registry=registry,
        task_executor=lambda task, account_name: True,
    )

    result = orchestrator.execute(
        [
            RotationTask(task_id="1", required_account="healthy"),
            RotationTask(task_id="2", required_account="unhealthy"),
        ],
        mode=AccountExecutionMode.SEQUENTIAL,
        unhealthy_threshold=0.5,
    )

    assert [event.task_id for event in result.executed_events] == ["1"]
    assert [event.task_id for event in result.skipped_tasks] == ["2"]


def test_account_rotation_orchestrator_enforces_reuse_interval_and_supports_parallel_mode(tmp_path):
    registry = AccountRegistry(storage_path=str(tmp_path / "accounts.json"))
    registry.upsert_account(AccountRecord(name="alpha", credential_reference="vault://a", account_type="app", application="app", health_score=0.9))
    registry.upsert_account(AccountRecord(name="beta", credential_reference="vault://b", account_type="app", application="app", health_score=0.9))
    registry.update_last_used("alpha", timestamp=datetime.now(timezone.utc))

    executed: list[tuple[str, str]] = []
    orchestrator = AccountRotationOrchestrator(
        storage_path=str(tmp_path / "rotation.json"),
        account_registry=registry,
        task_executor=lambda task, account_name: executed.append((task.task_id, account_name)) or True,
    )

    result = orchestrator.execute(
        [
            RotationTask(task_id="1", required_account="alpha"),
            RotationTask(task_id="2", required_account="beta"),
        ],
        mode=AccountExecutionMode.PARALLEL,
        minimum_reuse_interval=timedelta(minutes=5),
    )

    assert executed == [("2", "beta")]
    assert [event.task_id for event in result.skipped_tasks] == ["1"]
    assert result.mode is AccountExecutionMode.PARALLEL


def test_prompt_template_manager_renders_template(tmp_path):
    storage = tmp_path / "templates.json"
    manager = PromptTemplateManager(storage_path=str(storage))
    manager.upsert_template(
        name="status",
        description="Status prompt",
        body="Project ${name} is ${state}.",
        target_context="dashboard",
    )

    rendered = manager.render_template("status", {"name": "OmniReach", "state": "green"})

    assert rendered.succeeded is True
    assert rendered.rendered_prompt == "Project OmniReach is green."


def test_prompt_template_manager_versions_templates(tmp_path):
    storage = tmp_path / "templates.json"
    manager = PromptTemplateManager(storage_path=str(storage))
    manager.upsert_template(
        name="summary",
        description="Summary prompt",
        body="Version one for ${topic}.",
        target_context="reports",
    )
    updated = manager.upsert_template(
        name="summary",
        description="Summary prompt",
        body="Version two for ${topic}.",
        target_context="reports",
    )
    version_one = manager.get_template_version("summary", 1)

    assert updated.succeeded is True
    assert updated.template is not None and updated.template.current_version == 2
    assert version_one.succeeded is True
    assert version_one.template is not None and version_one.template.body == "Version one for ${topic}."


def test_prompt_template_manager_reports_missing_variables(tmp_path):
    storage = tmp_path / "templates.json"
    manager = PromptTemplateManager(storage_path=str(storage))
    manager.upsert_template(
        name="missing-vars",
        description="Template with vars",
        body="Hello ${name}",
        target_context="general",
    )

    result = manager.render_template("missing-vars", {})

    assert result.succeeded is False
    assert result.reason == "Missing template variable: name"


def test_prompt_template_manager_blocks_sensitive_rendered_prompts(tmp_path):
    storage = tmp_path / "templates.json"
    manager = PromptTemplateManager(
        storage_path=str(storage),
        sensitive_data_protector=SensitiveDataProtector(
            sensitive_value_patterns=(r"super-secret",),
        ),
    )
    manager.upsert_template(
        name="sensitive",
        description="Sensitive template",
        body="Token: ${token}",
        target_context="general",
    )

    result = manager.render_template("sensitive", {"token": "super-secret"})

    assert result.succeeded is False
    assert result.reason == "Prompt contains sensitive values and cannot be submitted."


class FakePromptInputRunner:
    def __init__(self):
        self.runs: list[list[InputAction]] = []

    def run(self, actions: list[InputAction]):
        self.runs.append(actions)
        return type("Result", (), {"succeeded": True, "failure_reason": None})()


class FakeClipboardWriter:
    def __init__(self):
        self.writes: list[str] = []

    def write_text(self, text: str, *, delay_seconds: float = 0.0, encoding: str = "utf-8"):
        self.writes.append(text)
        return type("Result", (), {"succeeded": True, "reason": None})()


class FakeOCRExtractor:
    def __init__(self, blocks: list[OCRTextBlock]):
        self.blocks = blocks
        self.last_region = None

    def extract_text(
        self,
        *,
        screenshot_path: str | None = None,
        region_of_interest: tuple[int, int, int, int] | None = None,
        language: str = "eng",
        minimum_confidence: float = 0.0,
    ):
        self.last_region = region_of_interest
        return type("Extraction", (), {"blocks": self.blocks})()


class FakeAccessibilityReader:
    def __init__(self, matches: list[AccessibilityElement], *, refreshed_matches: list[AccessibilityElement] | None = None):
        self.matches = matches
        self.refreshed_matches = refreshed_matches if refreshed_matches is not None else matches
        self.find_calls = 0

    def read_active_application_tree(self):
        root = self.matches[0] if self.matches else None
        return AccessibilityTree(application_name="App", root=root)

    def find_elements(self, *, name=None, role=None, value=None):
        self.find_calls += 1
        matches = self.matches if self.find_calls == 1 else self.refreshed_matches
        return type("Query", (), {"matches": matches, "used_fallback": False})()

    def get_element_text(self, element: AccessibilityElement) -> str | None:
        return element.state.text or element.value or element.name

    def is_element_enabled(self, element: AccessibilityElement) -> bool | None:
        return element.state.enabled


class FakePromptWindowManager:
    def list_windows(self):
        return [
            WindowContext(
                handle=42,
                title="Prompt App",
                process_name="prompt.exe",
                position=(0, 0),
                size=(800, 600),
                focused=True,
            )
        ]


class FakePlatformInputBackend:
    def __init__(self):
        self.injected: list[str] = []

    def inject_text(self, text: str) -> None:
        self.injected.append(text)


def test_prompt_injector_types_multiline_prompt_and_verifies_with_accessibility():
    field = AccessibilityElement(
        element_id="field-1",
        name="Prompt",
        role="edit",
        value=None,
        state=AccessibilityElementState(text=None, enabled=True, selected=True),
        bounds=(10, 20, 210, 120),
    )
    refreshed = AccessibilityElement(
        element_id="field-1",
        name="Prompt",
        role="edit",
        value=None,
        state=AccessibilityElementState(text="Line one\nLine two", enabled=True, selected=True),
        bounds=(10, 20, 210, 120),
    )
    runner = FakePromptInputRunner()
    injector = TargetApplicationPromptInjector(
        input_runner=runner,
        accessibility_reader=FakeAccessibilityReader([field], refreshed_matches=[refreshed]),
        window_manager=FakePromptWindowManager(),
    )

    result = injector.inject_prompt(
        prompt="Line one\r\nLine two",
        target=PromptInjectionTarget(window_title="Prompt App", element_name="Prompt", element_role="edit"),
        method=PromptInjectionMethod.TYPE,
        line_endings=LineEndingStyle.LF,
    )

    assert result.succeeded is True
    assert result.normalized_prompt == "Line one\nLine two"
    assert result.verification_method is PromptReadbackMethod.ACCESSIBILITY
    assert runner.runs[1][0].hotkey == ("ctrl", "a")
    assert runner.runs[2][0].text == "Line one\nLine two"


def test_prompt_injector_uses_clipboard_and_ocr_fallback_for_verification():
    field = AccessibilityElement(
        element_id="field-2",
        name="Composer",
        role="edit",
        value=None,
        state=AccessibilityElementState(text=None, enabled=True, selected=True),
        bounds=(30, 40, 330, 200),
    )
    runner = FakePromptInputRunner()
    clipboard = FakeClipboardWriter()
    ocr = FakeOCRExtractor(
        [
            OCRTextBlock(text="Hello", confidence=0.9, bounds=(1, 1, 50, 20)),
            OCRTextBlock(text="world", confidence=0.91, bounds=(60, 1, 120, 20)),
            OCRTextBlock(text="Again", confidence=0.92, bounds=(1, 30, 60, 50)),
        ]
    )
    injector = TargetApplicationPromptInjector(
        input_runner=runner,
        clipboard_manager=clipboard,
        accessibility_reader=FakeAccessibilityReader([field]),
        ocr_extractor=ocr,
    )

    result = injector.inject_prompt(
        prompt="Hello world\r\nAgain",
        target=PromptInjectionTarget(element_name="Composer", element_role="edit"),
        method=PromptInjectionMethod.CLIPBOARD,
        line_endings=LineEndingStyle.LF,
    )

    assert result.succeeded is True
    assert clipboard.writes == ["Hello world\nAgain"]
    assert result.verification_method is PromptReadbackMethod.OCR
    assert ocr.last_region == (30, 40, 330, 200)
    assert runner.runs[2][0].hotkey == ("ctrl", "v")


def test_prompt_injector_uses_platform_api_and_reports_verification_mismatch():
    field = AccessibilityElement(
        element_id="field-3",
        name="Notes",
        role="edit",
        value=None,
        state=AccessibilityElementState(text=None, enabled=True, selected=True),
        bounds=(5, 5, 205, 105),
    )
    platform_backend = FakePlatformInputBackend()
    injector = TargetApplicationPromptInjector(
        input_runner=FakePromptInputRunner(),
        accessibility_reader=FakeAccessibilityReader(
            [field],
            refreshed_matches=[
                AccessibilityElement(
                    element_id="field-3",
                    name="Notes",
                    role="edit",
                    value=None,
                    state=AccessibilityElementState(text="Wrong text", enabled=True, selected=True),
                    bounds=(5, 5, 205, 105),
                )
            ],
        ),
        platform_input_backend=platform_backend,
        window_manager=FakePromptWindowManager(),
    )

    result = injector.inject_prompt(
        prompt="Expected",
        target=PromptInjectionTarget(window_title="Prompt App", process_name="prompt.exe", element_name="Notes", element_role="edit"),
        method=PromptInjectionMethod.PLATFORM_API,
    )

    assert platform_backend.injected == ["Expected"]
    assert result.succeeded is False
    assert result.reason == "Read-back text does not match the expected injected prompt."


def test_prompt_injector_blocks_sensitive_prompt_submission():
    field = AccessibilityElement(
        element_id="field-4",
        name="Notes",
        role="edit",
        value=None,
        state=AccessibilityElementState(text=None, enabled=True, selected=True),
        bounds=(5, 5, 205, 105),
    )
    runner = FakePromptInputRunner()
    injector = TargetApplicationPromptInjector(
        input_runner=runner,
        accessibility_reader=FakeAccessibilityReader([field]),
        sensitive_data_protector=SensitiveDataProtector(
            sensitive_value_patterns=(r"private-secret",),
        ),
    )

    result = injector.inject_prompt(
        prompt="Contains private-secret",
        target=PromptInjectionTarget(element_name="Notes", element_role="edit"),
        method=PromptInjectionMethod.TYPE,
    )

    assert result.succeeded is False
    assert result.reason == "Prompt contains sensitive values and cannot be submitted."
    assert runner.runs == []
