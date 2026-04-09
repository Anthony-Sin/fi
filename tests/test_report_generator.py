from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_perception.resilience import SensitiveDataProtector
from desktop_automation_perception.models import (
    ActionRetryRateSummary,
    AnomalyCategory,
    AnomalyRecord,
    DeadLetterItem,
    FailureArchiveRecord,
    RetryFailureResult,
    WorkflowAuditLogEntry,
    WorkflowAuditOutcome,
)
from desktop_automation_perception.report_generator import WorkflowReportGenerator


def make_entry(timestamp, workflow_id, step_name, action_type, success, duration, *, target=None, output=None):
    return WorkflowAuditLogEntry(
        timestamp=timestamp,
        workflow_id=workflow_id,
        step_name=step_name,
        action_type=action_type,
        target_element=target,
        output_data=dict(output or {}),
        duration_seconds=duration,
        outcome=WorkflowAuditOutcome.SUCCESS if success else WorkflowAuditOutcome.FAILURE,
        success=success,
    )


def test_report_generator_builds_human_readable_workflow_summary():
    generator = WorkflowReportGenerator()
    start = datetime.utcnow()
    entries = [
        make_entry(start, "wf-1", "open", "click", True, 1.0, target="Open button"),
        make_entry(start + timedelta(seconds=3), "wf-1", "submit", "type_text", False, 2.5),
        make_entry(start + timedelta(seconds=6), "wf-1", "escalate", "escalate", True, 0.2, output={"escalated": True}),
    ]
    retries = [ActionRetryRateSummary(step_type="submit", retry_count=3, sample_count=4, retry_rate=0.75)]
    anomalies = [AnomalyRecord(category=AnomalyCategory.REPEATED_STEP_FAILURE, step_id="submit", pause_requested=True)]
    failures = [
        FailureArchiveRecord(
            record_id="f1",
            workflow_id="wf-1",
            step_name="submit",
            timestamp=start + timedelta(seconds=4),
            screenshot_path="C:\\artifacts\\submit.png",
        )
    ]
    dlq = [
        DeadLetterItem(
            item_id="d1",
            action_type="submit",
            inputs={"workflow_id": "wf-1"},
            retry_failure=RetryFailureResult(),
        )
    ]

    result = generator.generate_report(
        workflow_id="wf-1",
        workflow_name="Test Workflow",
        audit_entries=entries,
        retry_summaries=retries,
        anomalies=anomalies,
        failure_records=failures,
        dlq_items=dlq,
    )

    assert result.succeeded is True
    assert result.report is not None
    assert result.report.summary.total_steps_executed == 3
    assert result.report.summary.success_count == 2
    assert result.report.summary.failure_count == 1
    assert result.report.summary.highest_retry_steps[0].step_type == "submit"
    assert "C:\\artifacts\\submit.png" in result.report.failure_screenshot_links
    assert "Workflow Report: Test Workflow" in (result.report.body_text or "")


def test_report_generator_orders_timeline_and_highlights_unusual_patterns():
    generator = WorkflowReportGenerator()
    start = datetime.utcnow()
    entries = [
        make_entry(start + timedelta(seconds=5), "wf-1", "step-2", "wait", True, 4.0),
        make_entry(start, "wf-1", "step-1", "click", True, 1.0),
    ]
    retries = [ActionRetryRateSummary(step_type="wait", retry_count=2, sample_count=3, retry_rate=0.66)]
    anomalies = [AnomalyRecord(category=AnomalyCategory.SLOW_STEP_EXECUTION, step_id="step-2")]

    result = generator.generate_report(
        workflow_id="wf-1",
        workflow_name="Ordered Workflow",
        audit_entries=entries,
        retry_summaries=retries,
        anomalies=anomalies,
    )

    assert result.report is not None
    assert [item.step_name for item in result.report.timeline] == ["step-1", "step-2"]
    assert any("High retry rate on wait" in item for item in result.report.summary.unusual_patterns)
    assert any("slow_step_execution observed for step-2" in item for item in result.report.summary.unusual_patterns)


def test_report_generator_exports_text_report(tmp_path):
    generator = WorkflowReportGenerator()
    start = datetime.utcnow()
    entries = [make_entry(start, "wf-1", "step-1", "click", True, 1.0)]
    output_path = Path(tmp_path) / "report.txt"

    result = generator.export_report(
        output_path=str(output_path),
        workflow_id="wf-1",
        workflow_name="Export Workflow",
        audit_entries=entries,
    )

    assert result.succeeded is True
    assert result.export_path == str(output_path)
    text = output_path.read_text(encoding="utf-8")
    assert "Workflow Report: Export Workflow" in text
    assert "Timeline:" in text


def test_report_generator_masks_sensitive_values_in_report_body():
    generator = WorkflowReportGenerator(
        sensitive_data_protector=SensitiveDataProtector(
            sensitive_value_patterns=(r"C:\\artifacts\\submit\.png",),
        )
    )
    start = datetime.utcnow()
    entries = [make_entry(start, "wf-1", "step-1", "click", True, 1.0)]
    failures = [
        FailureArchiveRecord(
            record_id="f1",
            workflow_id="wf-1",
            step_name="submit",
            timestamp=start,
            screenshot_path="C:\\artifacts\\submit.png",
        )
    ]

    result = generator.generate_report(
        workflow_id="wf-1",
        workflow_name="Masked Workflow",
        audit_entries=entries,
        failure_records=failures,
    )

    assert result.report is not None
    assert "***SENSITIVE***" in (result.report.body_text or "")
    assert result.report.failure_screenshot_links == ["***SENSITIVE***"]
