from pathlib import Path

from desktop_automation_perception.models import (
    ApplicationLaunchMode,
    ApplicationLaunchRequest,
    WorkflowContext,
    WorkflowDataExchangeMode,
    WorkflowExchangeRequest,
    WorkflowStep,
)
from desktop_automation_perception.multi_application_workflow_coordinator import (
    MultiApplicationWorkflowCoordinator,
)
from desktop_automation_perception.resilience import AntiLoopDetector


class FakeLauncher:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def launch(self, request):
        self.calls.append(request)
        succeeded = self.results.pop(0) if self.results else True
        return type("LaunchResult", (), {"succeeded": succeeded, "reason": None if succeeded else "launch failed"})()


class FakeWindowManager:
    def __init__(self, windows, focus_results=None):
        self.windows = windows
        self.focus_results = list(focus_results or [])
        self.focus_calls = []
        self.list_calls = 0

    def list_windows(self):
        if self.list_calls < len(self.windows):
            result = self.windows[self.list_calls]
        else:
            result = self.windows[-1]
        self.list_calls += 1
        return result

    def focus_window(self, title=None, process_name=None):
        self.focus_calls.append((title, process_name))
        succeeded = self.focus_results.pop(0) if self.focus_results else True
        return type("FocusResult", (), {"succeeded": succeeded, "reason": None if succeeded else "focus failed"})()


class FakeClipboardManager:
    def __init__(self, read_text=""):
        self.read_text = read_text
        self.writes = []

    def write_text(self, text, *, delay_seconds=0.0, encoding="utf-8"):
        self.writes.append(text)
        self.read_text = text
        return type("WriteResult", (), {"succeeded": True, "reason": None})()

    def read_clipboard(self):
        content = type("Content", (), {"text": self.read_text})()
        return type("ReadResult", (), {"succeeded": True, "content": content, "reason": None})()


def test_workflow_coordinator_switches_focus_and_propagates_clipboard_data():
    coordinator = MultiApplicationWorkflowCoordinator(
        launcher=FakeLauncher([True, True]),
        window_manager=FakeWindowManager(
            windows=[
                [type("Window", (), {"title": "Writer", "process_name": "writer.exe"})()],
                [
                    type("Window", (), {"title": "Writer", "process_name": "writer.exe"})(),
                    type("Window", (), {"title": "Sheets", "process_name": "sheets.exe"})(),
                ],
            ],
            focus_results=[True, True],
        ),
        clipboard_manager=FakeClipboardManager(),
        dry_run=False,
    )

    result = coordinator.run(
        [
            WorkflowStep(
                step_id="writer",
                application_name="Writer",
                launch_request=ApplicationLaunchRequest(
                    application_name="Writer",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="writer.exe",
                ),
                required_window_title="Writer",
                outgoing_exchange=WorkflowExchangeRequest(
                    mode=WorkflowDataExchangeMode.CLIPBOARD,
                    data_key="draft",
                    value="hello world",
                ),
            ),
            WorkflowStep(
                step_id="sheets",
                application_name="Sheets",
                launch_request=ApplicationLaunchRequest(
                    application_name="Sheets",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="sheets.exe",
                ),
                required_window_title="Sheets",
                incoming_exchange=WorkflowExchangeRequest(
                    mode=WorkflowDataExchangeMode.CLIPBOARD,
                    data_key="draft",
                ),
            ),
        ]
    )

    assert result.succeeded is True
    assert result.context.current_application == "Sheets"
    assert result.context.shared_data["draft"] == "hello world"
    assert coordinator.clipboard_manager.writes == ["hello world"]


def test_workflow_coordinator_supports_file_based_exchange(tmp_path):
    exchange_path = Path(tmp_path) / "handoff.txt"
    coordinator = MultiApplicationWorkflowCoordinator(
        launcher=FakeLauncher([True, True]),
        window_manager=FakeWindowManager(
            windows=[[type("Window", (), {"title": "App1", "process_name": "app1.exe"})()]],
            focus_results=[True, True],
        ),
        clipboard_manager=FakeClipboardManager(),
        dry_run=False,
    )

    result = coordinator.run(
        [
            WorkflowStep(
                step_id="app1",
                application_name="App1",
                launch_request=ApplicationLaunchRequest(
                    application_name="App1",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="app1.exe",
                ),
                required_window_title="App1",
                outgoing_exchange=WorkflowExchangeRequest(
                    mode=WorkflowDataExchangeMode.FILE,
                    data_key="report",
                    value="persisted",
                    file_path=str(exchange_path),
                ),
            ),
            WorkflowStep(
                step_id="app2",
                application_name="App2",
                launch_request=ApplicationLaunchRequest(
                    application_name="App2",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="app2.exe",
                ),
                required_window_title="App1",
                incoming_exchange=WorkflowExchangeRequest(
                    mode=WorkflowDataExchangeMode.FILE,
                    data_key="report",
                    file_path=str(exchange_path),
                ),
                optional=True,
            ),
        ]
    )

    assert result.succeeded is True
    assert result.context.shared_data["report"] == "persisted"
    assert exchange_path.read_text(encoding="utf-8") == "persisted"


def test_workflow_coordinator_detects_prior_application_closed_unexpectedly():
    coordinator = MultiApplicationWorkflowCoordinator(
        launcher=FakeLauncher([True]),
        window_manager=FakeWindowManager(
            windows=[
                [type("Window", (), {"title": "Writer", "process_name": "writer.exe"})()],
                [],
            ],
            focus_results=[True],
        ),
        clipboard_manager=FakeClipboardManager(),
        dry_run=False,
    )

    result = coordinator.run(
        [
            WorkflowStep(
                step_id="writer",
                application_name="Writer",
                launch_request=ApplicationLaunchRequest(
                    application_name="Writer",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="writer.exe",
                ),
                required_window_title="Writer",
            ),
            WorkflowStep(
                step_id="next",
                application_name="NextApp",
                required_window_title="NextApp",
            ),
        ]
    )

    assert result.succeeded is False
    assert "closed unexpectedly" in (result.reason or "")


def test_workflow_coordinator_supports_dry_run_trace():
    coordinator = MultiApplicationWorkflowCoordinator(
        launcher=FakeLauncher(),
        window_manager=FakeWindowManager(windows=[[type("Window", (), {"title": "App", "process_name": "app.exe"})()]]),
        clipboard_manager=FakeClipboardManager(),
        dry_run=True,
    )

    result = coordinator.run(
        [
            WorkflowStep(
                step_id="dry-1",
                application_name="AppOne",
                outgoing_exchange=WorkflowExchangeRequest(
                    mode=WorkflowDataExchangeMode.CLIPBOARD,
                    data_key="note",
                    value="draft",
                ),
            ),
            WorkflowStep(
                step_id="dry-2",
                application_name="AppTwo",
                incoming_exchange=WorkflowExchangeRequest(
                    mode=WorkflowDataExchangeMode.FILE,
                    data_key="report",
                    file_path="ignored.txt",
                ),
                optional=True,
            ),
        ],
        initial_context=WorkflowContext(shared_data={"seed": "value"}),
    )

    assert result.succeeded is True
    assert all(step_result.dry_run for step_result in result.step_results)
    assert result.context.shared_data["note"] == "draft"
    assert result.context.shared_data["seed"] == "value"


def test_workflow_coordinator_stops_when_fail_safe_is_triggered():
    abort_checks = iter([False, True]).__next__
    coordinator = MultiApplicationWorkflowCoordinator(
        launcher=FakeLauncher([True]),
        window_manager=FakeWindowManager(
            windows=[[type("Window", (), {"title": "Writer", "process_name": "writer.exe"})()]],
            focus_results=[True],
        ),
        clipboard_manager=FakeClipboardManager(),
        dry_run=False,
        abort_checker=abort_checks,
    )

    result = coordinator.run(
        [
            WorkflowStep(
                step_id="writer",
                application_name="Writer",
                launch_request=ApplicationLaunchRequest(
                    application_name="Writer",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="writer.exe",
                ),
                required_window_title="Writer",
            ),
            WorkflowStep(
                step_id="blocked",
                application_name="NextApp",
                required_window_title="NextApp",
            ),
        ]
    )

    assert result.succeeded is False
    assert result.reason == "Execution aborted by fail-safe controller."
    assert len(result.step_results) == 1


def test_workflow_coordinator_stops_when_pipeline_timeout_is_exceeded(tmp_path):
    coordinator = MultiApplicationWorkflowCoordinator(
        launcher=FakeLauncher([True]),
        window_manager=FakeWindowManager(
            windows=[[type("Window", (), {"title": "Writer", "process_name": "writer.exe"})()]],
            focus_results=[True],
        ),
        clipboard_manager=FakeClipboardManager(),
        dry_run=False,
        anti_loop_detector=AntiLoopDetector(
            storage_path=str(tmp_path / "anti_loop.json"),
            workflow_id="wf-coordinator-timeout",
            max_step_execution_count=5,
            max_pipeline_duration_seconds=0.05,
            monotonic_fn=iter([0.0, 0.2]).__next__,
        ),
    )

    result = coordinator.run(
        [
            WorkflowStep(
                step_id="writer",
                application_name="Writer",
                launch_request=ApplicationLaunchRequest(
                    application_name="Writer",
                    launch_mode=ApplicationLaunchMode.EXECUTABLE,
                    executable_path="writer.exe",
                ),
                required_window_title="Writer",
            )
        ]
    )

    assert result.succeeded is False
    assert "maximum runtime" in (result.reason or "")
    assert result.step_results == []
