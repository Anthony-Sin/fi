from pathlib import Path

from desktop_automation_agent.fail_safe_controller import FailSafeController
from desktop_automation_agent.models import (
    AutomationTask,
    FailSafeTriggerType,
    TaskPriority,
    WorkflowContext,
    WorkflowStepResult,
)
from desktop_automation_agent.task_queue_manager import TaskQueueManager
from desktop_automation_agent.checkpoint_manager import CheckpointManager
from desktop_automation_agent.workflow_audit_logger import WorkflowAuditLogger


class FakeScreenshotBackend:
    def __init__(self):
        self.paths = []

    def capture_screenshot_to_path(self, path=None):
        self.paths.append(path)
        if path is not None:
            Path(path).write_text("image", encoding="utf-8")
        return path


class FakePointerBackend:
    def __init__(self, position=(0, 0), size=(1920, 1080)):
        self.position = position
        self.size = size

    def get_position(self):
        return self.position

    def get_screen_size(self):
        return self.size


class FakeKeyboardBackend:
    def __init__(self, pressed=False):
        self.pressed = pressed

    def is_hotkey_pressed(self, keys):
        return self.pressed


def make_task(task_id: str) -> AutomationTask:
    return AutomationTask(
        task_id=task_id,
        priority=TaskPriority.HIGH,
        required_module="automation",
        required_account="acct",
    )


def test_fail_safe_controller_activates_and_persists_artifacts(tmp_path):
    checkpoint_manager = CheckpointManager(storage_path=str(Path(tmp_path) / "checkpoints.json"))
    audit_logger = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))
    queue = TaskQueueManager(storage_path=str(Path(tmp_path) / "queue.json"))
    queue.enqueue(make_task("pending-1"))
    queue.enqueue(make_task("pending-2"))
    screenshot_backend = FakeScreenshotBackend()

    released = []
    context = WorkflowContext(
        current_application="Writer",
        step_number=2,
        shared_data={"draft": "hello"},
        active_applications=["Writer"],
        application_signatures={"Writer": "writer.exe"},
    )
    step_results = [
        WorkflowStepResult(
            step_id="step-1",
            application_name="Writer",
            succeeded=True,
            context_snapshot=context,
        )
    ]

    controller = FailSafeController(
        storage_path=str(Path(tmp_path) / "fail_safe.json"),
        workflow_id="wf-1",
        checkpoint_manager=checkpoint_manager,
        audit_logger=audit_logger,
        task_queue_manager=queue,
        screenshot_backend=screenshot_backend,
        workflow_context_provider=lambda: context,
        step_results_provider=lambda: step_results,
        step_index_provider=lambda: 2,
        account_context_provider=lambda: {"account": "acct"},
        collected_data_provider=lambda: {"draft": "hello"},
    )
    controller.register_resource("window:writer", lambda: released.append("window") or type("Result", (), {"succeeded": True, "reason": None})())
    controller.register_resource("session:file-handle", lambda: released.append("session") or type("Result", (), {"succeeded": True, "reason": None})())

    result = controller.activate(trigger_type=FailSafeTriggerType.MANUAL, detail="Operator abort.")

    checkpoint = checkpoint_manager.get_checkpoint("wf-1")
    queue_snapshot = queue.get_snapshot()
    audit_entries = audit_logger.list_logs()
    events = controller.list_events()

    assert result.succeeded is True
    assert result.activated is True
    assert controller.is_abort_requested() is True
    assert checkpoint is not None
    assert checkpoint.step_index == 2
    assert checkpoint.workflow_context.current_application == "Writer"
    assert queue_snapshot.tasks == []
    assert [entry.action_type for entry in audit_entries] == ["fail_safe_activated"]
    assert audit_entries[0].output_data["cancelled_task_ids"] == ["pending-1", "pending-2"]
    assert Path(events[0].screenshot_path).exists()
    assert [item.resource_name for item in events[0].released_resources] == [
        "window:writer",
        "session:file-handle",
    ]
    assert released == ["window", "session"]


def test_fail_safe_controller_detects_mouse_corner_and_hotkey(tmp_path):
    screenshot_backend = FakeScreenshotBackend()

    hotkey_controller = FailSafeController(
        storage_path=str(Path(tmp_path) / "hotkey.json"),
        workflow_id="wf-hotkey",
        screenshot_backend=screenshot_backend,
        keyboard_backend=FakeKeyboardBackend(pressed=True),
    )
    hotkey_result = hotkey_controller.poll()

    mouse_controller = FailSafeController(
        storage_path=str(Path(tmp_path) / "mouse.json"),
        workflow_id="wf-mouse",
        screenshot_backend=screenshot_backend,
        pointer_backend=FakePointerBackend(position=(3, 4), size=(1920, 1080)),
        mouse_corner="top_left",
        corner_threshold_pixels=5,
    )
    mouse_result = mouse_controller.poll()

    assert hotkey_result is not None
    assert hotkey_result.record is not None
    assert hotkey_result.record.trigger_type is FailSafeTriggerType.HOTKEY
    assert mouse_result is not None
    assert mouse_result.record is not None
    assert mouse_result.record.trigger_type is FailSafeTriggerType.MOUSE_CORNER
