import json
from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_perception.resilience import SensitiveDataProtector
from desktop_automation_perception.models import WorkflowAuditOutcome, WorkflowAuditQuery
from desktop_automation_perception.workflow_audit_logger import WorkflowAuditLogger


def test_workflow_audit_logger_appends_and_sanitizes_credentials(tmp_path):
    logger = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))

    result = logger.log_action(
        workflow_id="wf-1",
        step_name="login",
        action_type="type_text",
        target_element="Password field",
        input_data={"username": "user", "password": "secret"},
        output_data={"status": "ok", "session_token": "abc123"},
        duration_seconds=0.5,
        success=True,
    )

    assert result.succeeded is True
    assert result.entry is not None
    assert result.entry.input_data["password"] == "***REDACTED***"
    assert result.entry.output_data["session_token"] == "***REDACTED***"

    entries = logger.list_logs()

    assert len(entries) == 1
    assert entries[0].workflow_id == "wf-1"
    assert entries[0].outcome is WorkflowAuditOutcome.SUCCESS


def test_workflow_audit_logger_filters_by_workflow_time_action_and_outcome(tmp_path):
    logger = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))
    start = datetime.utcnow()
    logger.log_action(
        workflow_id="wf-1",
        step_name="open",
        action_type="click",
        success=True,
        timestamp=start,
    )
    logger.log_action(
        workflow_id="wf-2",
        step_name="submit",
        action_type="type_text",
        success=False,
        timestamp=start + timedelta(seconds=10),
    )

    result = logger.query_logs(
        WorkflowAuditQuery(
            workflow_id="wf-2",
            started_at=start + timedelta(seconds=5),
            ended_at=start + timedelta(seconds=20),
            action_type="type_text",
            outcome=WorkflowAuditOutcome.FAILURE,
        )
    )

    assert result.succeeded is True
    assert len(result.entries) == 1
    assert result.entries[0].step_name == "submit"


def test_workflow_audit_logger_exports_json_and_csv(tmp_path):
    logger = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))
    logger.log_action(
        workflow_id="wf-1",
        step_name="verify",
        action_type="wait",
        input_data={"attempt": 1},
        output_data={"result": "done"},
        duration_seconds=2.0,
        success=True,
    )

    json_path = Path(tmp_path) / "audit.json"
    csv_path = Path(tmp_path) / "audit.csv"

    json_result = logger.export_json(str(json_path))
    csv_result = logger.export_csv(str(csv_path))

    assert json_result.succeeded is True
    assert csv_result.succeeded is True
    assert json.loads(json_path.read_text(encoding="utf-8"))["entries"][0]["step_name"] == "verify"
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "workflow_id" in csv_text
    assert "verify" in csv_text


def test_workflow_audit_logger_uses_append_only_jsonl_store(tmp_path):
    logger = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))
    logger.log_action(workflow_id="wf-1", step_name="step-1", action_type="click", success=True)
    logger.log_action(workflow_id="wf-1", step_name="step-2", action_type="wait", success=False)

    lines = Path(tmp_path, "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["step_name"] == "step-1"
    assert json.loads(lines[1])["step_name"] == "step-2"


def test_workflow_audit_logger_masks_sensitive_values_by_pattern(tmp_path):
    logger = WorkflowAuditLogger(
        storage_path=str(Path(tmp_path) / "audit.jsonl"),
        sensitive_data_protector=SensitiveDataProtector(
            sensitive_value_patterns=(r"\b123-45-6789\b",),
        ),
    )

    result = logger.log_action(
        workflow_id="wf-2",
        step_name="review",
        action_type="note",
        input_data={"notes": "Customer SSN 123-45-6789"},
        success=True,
    )

    assert result.entry is not None
    assert result.entry.input_data["notes"] == "***SENSITIVE***"
