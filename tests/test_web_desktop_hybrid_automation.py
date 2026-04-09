from desktop_automation_perception.automation import WebDesktopHybridAutomation
from desktop_automation_perception.models import ScreenVerificationResult, WindowContext, WindowOperationResult


class FakeBrowserLauncher:
    def __init__(self):
        self.calls = []

    def launch(self, browser_executable: str, profile_directory: str, application: str | None = None):
        self.calls.append((browser_executable, profile_directory, application))
        return 4242


class FakeWebBackend:
    def __init__(self):
        self.calls = []

    def navigate(self, url: str):
        self.calls.append(("navigate", url))
        return type("Result", (), {"succeeded": True, "url": url})()

    def click(self, selector: str, *, value=None, payload=None):
        self.calls.append(("click", selector, value, payload))
        return type("Result", (), {"succeeded": True, "selector": selector})()

    def type(self, selector: str, *, value=None, payload=None):
        self.calls.append(("type", selector, value, payload))
        return type("Result", (), {"succeeded": True, "selector": selector, "value": value})()


class FakeWindowManager:
    def __init__(self):
        self.focus_calls = []

    def focus_window(self, title=None, process_name=None):
        self.focus_calls.append((title, process_name))
        return WindowOperationResult(
            succeeded=True,
            window=WindowContext(
                handle=101,
                title=title or "Native Dialog",
                process_name=process_name,
                focused=True,
            ),
        )


class FakeInputRunner:
    def __init__(self):
        self.actions = []

    def run(self, actions):
        self.actions.append(actions)
        return type("Result", (), {"succeeded": True, "logs": list(actions), "failure_reason": None})()


class FakeStateVerifier:
    def __init__(self):
        self.calls = []

    def verify(self, checks):
        self.calls.append(list(checks))
        return ScreenVerificationResult(passed_checks=[], failed_checks=[])


def test_hybrid_automation_maintains_independent_web_and_desktop_session_state():
    module = WebDesktopHybridAutomation(
        browser_launcher=FakeBrowserLauncher(),
        web_automation_backend=FakeWebBackend(),
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
        state_verifier=FakeStateVerifier(),
        browser_executable="chrome.exe",
        profile_directory="C:/profiles/test",
        browser_application="https://example.com",
    )

    launch_result = module.launch_browser()
    nav_result = module.navigate("https://example.com/login")
    web_result = module.interact_with_web("type", "#email", value="ana@example.com")
    session = module.inspect_session()

    assert launch_result.succeeded is True
    assert nav_result.succeeded is True
    assert web_result.succeeded is True
    assert session.browser_process_id == 4242
    assert session.current_url == "https://example.com/login"
    assert session.web_state["last_action"]["selector"] == "#email"
    assert session.desktop_state == {}


def test_hybrid_automation_handles_native_dialog_via_desktop_actions():
    input_runner = FakeInputRunner()
    module = WebDesktopHybridAutomation(
        browser_launcher=FakeBrowserLauncher(),
        web_automation_backend=FakeWebBackend(),
        window_manager=FakeWindowManager(),
        input_runner=input_runner,
        state_verifier=FakeStateVerifier(),
    )

    result = module.handle_native_dialog(
        window_title="Open",
        process_name="explorer.exe",
        actions=[
            {"action_type": "type_text", "text": "C:/tmp/report.csv"},
            {"action_type": "hotkey", "hotkey": ["enter"]},
        ],
    )
    session = module.inspect_session()

    assert result.succeeded is True
    assert len(input_runner.actions) == 1
    assert session.active_window_title == "Open"
    assert session.active_process_name == "explorer.exe"
    assert session.desktop_state["last_dialog"]["window_title"] == "Open"
    assert "last_action" not in session.desktop_state


def test_hybrid_automation_can_handle_desktop_notification_with_verification_and_callback():
    verifier = FakeStateVerifier()
    callback_calls = []

    def desktop_handler(name, payload, session):
        callback_calls.append((name, payload, session.active_window_title))
        return type("Result", (), {"succeeded": True, "name": name, "payload": payload})()

    module = WebDesktopHybridAutomation(
        browser_launcher=FakeBrowserLauncher(),
        web_automation_backend=FakeWebBackend(),
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
        state_verifier=verifier,
        desktop_action_handler=desktop_handler,
    )

    result = module.handle_desktop_notification(
        window_title="Download complete",
        process_name="explorer.exe",
        verification_checks=[],
        desktop_handler_name="acknowledge_notification",
        desktop_payload={"button": "Open"},
    )
    session = module.inspect_session()

    assert result.succeeded is True
    assert callback_calls == [("acknowledge_notification", {"button": "Open"}, "Download complete")]
    assert verifier.calls == []
    assert session.desktop_state["last_notification"]["handler"] == "acknowledge_notification"


def test_hybrid_automation_verifies_desktop_notification_when_checks_are_provided():
    verifier = FakeStateVerifier()
    module = WebDesktopHybridAutomation(
        browser_launcher=FakeBrowserLauncher(),
        web_automation_backend=FakeWebBackend(),
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
        state_verifier=verifier,
    )

    result = module.handle_desktop_notification(
        window_title="Upload complete",
        process_name="notifier.exe",
        verification_checks=[object()],
    )

    assert result.succeeded is True
    assert len(verifier.calls) == 1
