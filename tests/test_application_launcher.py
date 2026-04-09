from desktop_automation_agent.application_launcher import ApplicationLauncher, ApplicationRegistry
from desktop_automation_agent.allowlist_enforcer import ActionAllowlistEnforcer
from desktop_automation_agent.models import (
    AIInterfaceElementSelector,
    AccessibilityElement,
    AccessibilityElementState,
    ApplicationLaunchMode,
    ApplicationLaunchRequest,
    ApplicationLaunchStatus,
    ApplicationStartupSignature,
    KnownApplicationRecord,
)


class FakeLauncherBackend:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def launch_executable(self, executable_path, arguments):
        self.calls.append(("exe", executable_path, arguments))
        return self.results.pop(0) if self.results else True

    def launch_start_menu(self, query, arguments):
        self.calls.append(("start", query, arguments))
        return self.results.pop(0) if self.results else True

    def launch_url(self, url):
        self.calls.append(("url", url))
        return self.results.pop(0) if self.results else True


class FakeWindowManager:
    def __init__(self, windows):
        self.windows = windows

    def list_windows(self):
        return self.windows.pop(0) if self.windows else []


class FakeAccessibilityReader:
    def __init__(self, matches):
        self.matches = matches

    def find_elements(self, *, name=None, role=None, value=None):
        return type("Query", (), {"matches": self.matches.get((name, role, value), [])})()

    def get_element_text(self, element):
        return element.state.text or element.name


class FakeScreenshotBackend:
    def capture_screenshot_to_path(self, path=None):
        return "screen.png"


def make_element(name, role, bounds=(10, 10, 50, 50), text=None):
    return AccessibilityElement(
        element_id=f"{name}-{role}",
        name=name,
        role=role,
        bounds=bounds,
        state=AccessibilityElementState(text=text, enabled=True),
    )


def test_application_launcher_launches_registered_executable_and_verifies_signature(tmp_path):
    registry = ApplicationRegistry(str(tmp_path / "apps.json"))
    registry.upsert_application(
        KnownApplicationRecord(
            name="notepad",
            launch_mode=ApplicationLaunchMode.EXECUTABLE,
            executable_path="notepad.exe",
            startup_signature=ApplicationStartupSignature(window_title="Notepad"),
        )
    )
    launcher = ApplicationLauncher(
        registry=registry,
        backend=FakeLauncherBackend([True]),
        window_manager=FakeWindowManager(
            [
                [],
                [type("Window", (), {"title": "Untitled - Notepad", "process_name": "notepad.exe"})()],
            ]
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2, 0.3]).__next__,
    )

    result = launcher.launch(
        ApplicationLaunchRequest(
            application_name="notepad",
            launch_mode=ApplicationLaunchMode.EXECUTABLE,
            timeout_seconds=0.2,
            retry_attempts=1,
        )
    )

    assert result.succeeded is True
    assert result.status is ApplicationLaunchStatus.STARTED
    assert result.launched_command == ("notepad.exe",)


def test_application_launcher_supports_start_menu_launch_and_ui_element_verification(tmp_path):
    registry = ApplicationRegistry(str(tmp_path / "apps.json"))
    record = KnownApplicationRecord(
        name="calc",
        launch_mode=ApplicationLaunchMode.START_MENU,
        start_menu_query="Calculator",
        startup_signature=ApplicationStartupSignature(
            element_selector=AIInterfaceElementSelector(name="Calculator", role="window")
        ),
    )
    registry.upsert_application(record)
    launcher = ApplicationLauncher(
        registry=registry,
        backend=FakeLauncherBackend([True]),
        accessibility_reader=FakeAccessibilityReader(
            {("Calculator", "window", None): [make_element("Calculator", "window")]}
        ),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    result = launcher.launch(
        ApplicationLaunchRequest(
            application_name="calc",
            launch_mode=ApplicationLaunchMode.START_MENU,
            retry_attempts=1,
        )
    )

    assert result.succeeded is True
    assert result.status is ApplicationLaunchStatus.STARTED
    assert launcher.backend.calls[0] == ("start", "Calculator", ())


def test_application_launcher_supports_url_launch_with_parameters(tmp_path):
    registry = ApplicationRegistry(str(tmp_path / "apps.json"))
    launcher = ApplicationLauncher(
        registry=registry,
        backend=FakeLauncherBackend([True]),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    result = launcher.launch(
        ApplicationLaunchRequest(
            application_name="chat-web",
            launch_mode=ApplicationLaunchMode.URL,
            url="https://example.com/chat",
            url_parameters={"workspace": "alpha", "mode": "assist"},
            retry_attempts=1,
        )
    )

    assert result.succeeded is True
    assert result.launched_command == ("url", "https://example.com/chat?workspace=alpha&mode=assist")


def test_application_launcher_retries_then_escalates_on_failure(tmp_path):
    registry = ApplicationRegistry(str(tmp_path / "apps.json"))
    registry.upsert_application(
        KnownApplicationRecord(
            name="editor",
            launch_mode=ApplicationLaunchMode.EXECUTABLE,
            executable_path="editor.exe",
            startup_signature=ApplicationStartupSignature(window_title="Editor"),
        )
    )
    launcher = ApplicationLauncher(
        registry=registry,
        backend=FakeLauncherBackend([True, True]),
        window_manager=FakeWindowManager([[], [], [], []]),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5]).__next__,
    )

    result = launcher.launch(
        ApplicationLaunchRequest(
            application_name="editor",
            launch_mode=ApplicationLaunchMode.EXECUTABLE,
            timeout_seconds=0.1,
            retry_attempts=2,
            escalate_on_failure=True,
        )
    )

    assert result.succeeded is False
    assert result.status is ApplicationLaunchStatus.ESCALATED
    assert len(result.attempts) == 2


def test_application_registry_persists_known_applications(tmp_path):
    registry = ApplicationRegistry(str(tmp_path / "apps.json"))
    registry.upsert_application(
        KnownApplicationRecord(
            name="browser",
            launch_mode=ApplicationLaunchMode.URL,
            url="https://example.com",
            default_url_parameters={"tab": "home"},
            startup_signature=ApplicationStartupSignature(process_name="chrome.exe"),
        )
    )

    restored = ApplicationRegistry(str(tmp_path / "apps.json")).get_application("browser")

    assert restored is not None
    assert restored.url == "https://example.com"
    assert restored.default_url_parameters == {"tab": "home"}
    assert restored.startup_signature is not None
    assert restored.startup_signature.process_name == "chrome.exe"


def test_application_launcher_blocks_disallowed_launch_targets(tmp_path):
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text(
        '{"action_types":["launch_application"],"applications":["notes"],"urls":["https://safe.example/*"],"file_paths":["C:/safe/*"]}',
        encoding="utf-8",
    )
    registry = ApplicationRegistry(str(tmp_path / "apps.json"))
    launcher = ApplicationLauncher(
        registry=registry,
        backend=FakeLauncherBackend([True]),
        allowlist_enforcer=ActionAllowlistEnforcer(config_path=str(allowlist_path)),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    result = launcher.launch(
        ApplicationLaunchRequest(
            application_name="chat-web",
            launch_mode=ApplicationLaunchMode.URL,
            url="https://blocked.example/chat",
            retry_attempts=1,
        ),
        workflow_id="wf-launch",
    )

    assert result.succeeded is False
    assert result.status is ApplicationLaunchStatus.ESCALATED
    assert launcher.backend.calls == []
