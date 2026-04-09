from pathlib import Path

from desktop_automation_agent.checkpoint_manager import CheckpointManager
from desktop_automation_agent.models import (
    CheckpointDecision,
    CheckpointResumePolicy,
    UIStateFingerprint,
    WorkflowContext,
    WorkflowStepResult,
)


def test_checkpoint_manager_saves_and_restores_checkpoint(tmp_path):
    manager = CheckpointManager(storage_path=str(Path(tmp_path) / "checkpoints.json"))
    checkpoint = manager.save_checkpoint(
        workflow_id="wf-1",
        step_index=3,
        workflow_context=WorkflowContext(
            current_application="Writer",
            step_number=3,
            shared_data={"draft": "hello"},
            active_applications=["Writer"],
            application_signatures={"Writer": "writer.exe"},
        ),
        account_context={"account": "primary"},
        collected_data={"draft": "hello"},
        step_outcomes=[
            WorkflowStepResult(
                step_id="s1",
                application_name="Writer",
                succeeded=True,
                context_snapshot=WorkflowContext(current_application="Writer", step_number=1),
            )
        ],
    )

    restored = manager.get_checkpoint("wf-1")

    assert checkpoint.workflow_id == "wf-1"
    assert restored is not None
    assert restored.step_index == 3
    assert restored.workflow_context.shared_data["draft"] == "hello"
    assert restored.account_context["account"] == "primary"
    assert restored.step_outcomes[0].step_id == "s1"


def test_checkpoint_manager_auto_resume_returns_existing_checkpoint(tmp_path):
    manager = CheckpointManager(storage_path=str(Path(tmp_path) / "checkpoints.json"))
    manager.save_checkpoint(
        workflow_id="wf-2",
        step_index=2,
        workflow_context=WorkflowContext(current_application="Sheets", step_number=2),
    )

    result = manager.restore_or_restart(
        workflow_id="wf-2",
        policy=CheckpointResumePolicy.AUTO_RESUME,
    )

    assert result.succeeded is True
    assert result.decision is CheckpointDecision.RESUME
    assert result.checkpoint is not None
    assert result.checkpoint.workflow_context.current_application == "Sheets"


def test_checkpoint_manager_auto_restart_clears_existing_checkpoint(tmp_path):
    manager = CheckpointManager(storage_path=str(Path(tmp_path) / "checkpoints.json"))
    manager.save_checkpoint(
        workflow_id="wf-3",
        step_index=4,
        workflow_context=WorkflowContext(current_application="Editor", step_number=4),
    )

    result = manager.restore_or_restart(
        workflow_id="wf-3",
        policy=CheckpointResumePolicy.AUTO_RESTART,
    )

    assert result.succeeded is True
    assert result.decision is CheckpointDecision.RESTART
    assert manager.get_checkpoint("wf-3") is None


def test_checkpoint_manager_uses_callback_decision(tmp_path):
    manager = CheckpointManager(
        storage_path=str(Path(tmp_path) / "checkpoints.json"),
        decision_callback=lambda checkpoint: CheckpointDecision.RESTART,
    )
    manager.save_checkpoint(
        workflow_id="wf-4",
        step_index=1,
        workflow_context=WorkflowContext(current_application="Mail", step_number=1),
    )

    result = manager.restore_or_restart(
        workflow_id="wf-4",
        policy=CheckpointResumePolicy.CALLBACK,
    )

    assert result.succeeded is True
    assert result.decision is CheckpointDecision.RESTART
    assert manager.get_checkpoint("wf-4") is None


def test_checkpoint_manager_reports_missing_checkpoint(tmp_path):
    manager = CheckpointManager(storage_path=str(Path(tmp_path) / "checkpoints.json"))

    result = manager.restore_or_restart(workflow_id="missing")

    assert result.succeeded is False
    assert result.decision is CheckpointDecision.RESTART
    assert "No checkpoint found" in (result.reason or "")


def test_checkpoint_manager_persists_ui_state_fingerprint(tmp_path):
    manager = CheckpointManager(storage_path=str(Path(tmp_path) / "checkpoints.json"))
    manager.save_checkpoint(
        workflow_id="wf-ui",
        step_index=5,
        workflow_context=WorkflowContext(current_application="Editor", step_number=5),
        ui_state_fingerprint=UIStateFingerprint(
            window_title_hash="abc123",
            landmark_positions={"submit": (0.8, 0.75)},
            pixel_histogram=(0.1, 0.2, 0.3, 0.4),
            screen_size=(1920, 1080),
            window_count=3,
        ),
    )

    restored = manager.get_checkpoint("wf-ui")

    assert restored is not None
    assert restored.ui_state_fingerprint is not None
    assert restored.ui_state_fingerprint.window_title_hash == "abc123"
    assert restored.ui_state_fingerprint.landmark_positions["submit"] == (0.8, 0.75)
    assert restored.ui_state_fingerprint.pixel_histogram == (0.1, 0.2, 0.3, 0.4)
    assert restored.ui_state_fingerprint.screen_size == (1920, 1080)
    assert restored.ui_state_fingerprint.window_count == 3
