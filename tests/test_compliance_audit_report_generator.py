from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_perception.models import (
    AccountUsageEvent,
    ApprovalDecision,
    ApprovalGateAction,
    ApprovalGateResult,
    ApprovalRequest,
    ApprovalResponse,
    BrowserSessionRecord,
    CredentialAccessEvent,
    CredentialKind,
    EscalationRecord,
    EscalationResolution,
    EscalationTriggerType,
    FailSafeActivationRecord,
    FailSafeResourceReleaseResult,
    FailSafeTriggerType,
    FailureArchiveRecord,
    SensitiveAccessEvent,
    VaultCredentialAccessEvent,
    WorkflowAuditLogEntry,
    WorkflowAuditOutcome,
)
from desktop_automation_perception.observability import ComplianceAuditReportGenerator


class FakeAuditLogger:
    def __init__(self):
        self.calls = []

    def log_action(self, **kwargs):
        self.calls.append(kwargs)
        return type("AuditResult", (), {"succeeded": True})()


def test_compliance_audit_report_generator_exports_json_with_complete_evidence(tmp_path):
    generator = ComplianceAuditReportGenerator(
        generated_by="compliance-bot",
        signature_secret="secret",
    )
    now = datetime(2026, 4, 9, 11, 0, 0)
    audit_entries = [
        WorkflowAuditLogEntry(
            timestamp=now,
            workflow_id="wf-1",
            workflow_version_number=2,
            step_name="open",
            action_type="click",
            target_element="Open button",
            input_data={"actor": "worker-1"},
            output_data={"account_name": "acct-a"},
            duration_seconds=1.0,
            outcome=WorkflowAuditOutcome.SUCCESS,
            success=True,
        ),
        WorkflowAuditLogEntry(
            timestamp=now + timedelta(seconds=5),
            workflow_id="wf-1",
            workflow_version_number=2,
            step_name="submit",
            action_type="type_text",
            input_data={"actor": "worker-1"},
            duration_seconds=2.0,
            outcome=WorkflowAuditOutcome.FAILURE,
            success=False,
        ),
    ]
    approval_results = [
        ApprovalGateResult(
            succeeded=True,
            action=ApprovalGateAction(
                workflow_id="wf-1",
                step_id="submit",
                action_type="type_text",
                description="Submit regulated form",
            ),
            triggered_gate=True,
            request=ApprovalRequest(
                request_id="req-1",
                action=ApprovalGateAction(
                    workflow_id="wf-1",
                    step_id="submit",
                    action_type="type_text",
                    description="Submit regulated form",
                ),
                reviewer_channel="ops",
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            ),
            response=ApprovalResponse(
                request_id="req-1",
                decision=ApprovalDecision.APPROVE,
                reviewer_id="reviewer-1",
                responded_at=now + timedelta(minutes=1),
                reason="Looks correct",
            ),
        )
    ]
    escalation_records = [
        EscalationRecord(
            escalation_id="esc-1",
            workflow_id="wf-1",
            step_id="submit",
            trigger_type=EscalationTriggerType.REPEATED_STEP_FAILURE,
            paused=True,
            resolved=True,
            resolution=EscalationResolution.RESUME,
            operator_id="operator-1",
            created_at=now + timedelta(minutes=2),
            responded_at=now + timedelta(minutes=3),
            detail="Operator reviewed failure",
        )
    ]
    failure_records = [
        FailureArchiveRecord(
            record_id="fail-1",
            workflow_id="wf-1",
            step_name="submit",
            timestamp=now + timedelta(seconds=6),
            screenshot_path="C:\\artifacts\\submit.png",
            exception_type="RuntimeError",
            exception_message="Submission failed",
        )
    ]
    fail_safe_records = [
        FailSafeActivationRecord(
            workflow_id="wf-1",
            trigger_type=FailSafeTriggerType.MANUAL,
            checkpoint_saved=True,
            released_resources=[FailSafeResourceReleaseResult(resource_name="browser", succeeded=True)],
            timestamp=now + timedelta(seconds=7),
        )
    ]
    account_usage_events = [
        AccountUsageEvent(account_name="acct-a", action="session_start", timestamp=now, detail="Billing"),
        AccountUsageEvent(account_name="acct-a", action="logout", timestamp=now + timedelta(minutes=5), detail="Billing"),
    ]
    browser_sessions = [
        BrowserSessionRecord(
            account_name="acct-a",
            profile_directory=str(tmp_path / "profile"),
            launched_at=now,
            browser_process_id=123,
            active=False,
        )
    ]
    sensitive_events = [
        SensitiveAccessEvent(location="prompt", action="read", timestamp=now, detail="rendered prompt"),
        CredentialAccessEvent(account_identifier="acct-a", kind=CredentialKind.PASSWORD, action="decrypt", timestamp=now),
        VaultCredentialAccessEvent(secret_name="svc-token", action="fetch", timestamp=now + timedelta(seconds=1), cache_hit=True),
    ]
    output_path = Path(tmp_path) / "compliance.json"

    result = generator.export_json(
        output_path=str(output_path),
        workflow_id="wf-1",
        workflow_name="Regulated Workflow",
        audit_entries=audit_entries,
        approval_results=approval_results,
        escalation_records=escalation_records,
        failure_records=failure_records,
        fail_safe_records=fail_safe_records,
        account_usage_events=account_usage_events,
        browser_sessions=browser_sessions,
        sensitive_access_events=sensitive_events,
        generated_at=now + timedelta(minutes=10),
    )

    assert result.succeeded is True
    assert result.report is not None
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["workflow_id"] == "wf-1"
    assert payload["generated_by"] == "compliance-bot"
    assert payload["signature_algorithm"] == "hmac-sha256"
    assert len(payload["actions"]) == 2
    assert payload["actions"][0]["actor"] == "worker-1"
    assert payload["approvals"][0]["decision"] == "approve"
    assert payload["escalations"][0]["resolution"] == "resume"
    assert payload["failures"][0]["recovery_actions"] == ["checkpoint_saved", "browser"]
    assert payload["account_sessions"][0]["duration_seconds"] == 300.0
    assert len(payload["sensitive_access_events"]) == 3


def test_compliance_audit_report_generator_exports_signed_pdf_and_logs_generation(tmp_path):
    audit_logger = FakeAuditLogger()
    generator = ComplianceAuditReportGenerator(
        generated_by="auditor",
        workflow_audit_logger=audit_logger,
    )
    now = datetime(2026, 4, 9, 12, 0, 0)
    audit_entries = [
        WorkflowAuditLogEntry(
            timestamp=now,
            workflow_id="wf-2",
            step_name="verify",
            action_type="wait",
            input_data={"actor": "system"},
            duration_seconds=1.5,
            outcome=WorkflowAuditOutcome.SUCCESS,
            success=True,
        )
    ]
    pdf_path = Path(tmp_path) / "compliance.pdf"

    result = generator.export_pdf(
        output_path=str(pdf_path),
        workflow_id="wf-2",
        workflow_name="Verification Workflow",
        audit_entries=audit_entries,
        generated_at=now,
    )

    assert result.succeeded is True
    assert result.report is not None
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")
    assert audit_logger.calls
    assert audit_logger.calls[0]["action_type"] == "generate_compliance_audit_report"
    assert audit_logger.calls[0]["output_data"]["export_path"] == str(pdf_path)


def test_compliance_audit_report_generator_includes_failures_sessions_and_sensitive_access_in_body():
    generator = ComplianceAuditReportGenerator(generated_by="auditor")
    now = datetime(2026, 4, 9, 13, 0, 0)
    result = generator.generate_report(
        workflow_id="wf-3",
        workflow_name="Body Workflow",
        audit_entries=[
            WorkflowAuditLogEntry(
                timestamp=now,
                workflow_id="wf-3",
                step_name="login",
                action_type="click",
                input_data={"actor": "worker-2"},
                duration_seconds=0.5,
                outcome=WorkflowAuditOutcome.SUCCESS,
                success=True,
            )
        ],
        failure_records=[
            FailureArchiveRecord(
                record_id="f-1",
                workflow_id="wf-3",
                step_name="login",
                timestamp=now + timedelta(seconds=1),
                exception_type="TimeoutError",
                exception_message="Timed out",
            )
        ],
        browser_sessions=[
            BrowserSessionRecord(
                account_name="acct-b",
                profile_directory="C:\\profiles\\b",
                launched_at=now,
                active=True,
            )
        ],
        account_usage_events=[AccountUsageEvent(account_name="acct-b", action="session_start", timestamp=now, detail="Portal")],
        sensitive_access_events=[SensitiveAccessEvent(location="screen", action="mask", timestamp=now + timedelta(seconds=2))],
        generated_at=now + timedelta(minutes=1),
    )

    assert result.succeeded is True
    assert result.report is not None
    assert "Executed Actions:" in (result.report.body_text or "")
    assert "Failures And Recovery Actions:" in (result.report.body_text or "")
    assert "Accounts Used And Session Durations:" in (result.report.body_text or "")
    assert "Sensitive Data Access Events:" in (result.report.body_text or "")
