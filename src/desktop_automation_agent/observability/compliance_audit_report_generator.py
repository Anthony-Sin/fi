from __future__ import annotations

from desktop_automation_agent._time import utc_now

import hashlib
import hmac
import json
import logging
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from desktop_automation_agent.models import (
    AccountUsageEvent,
    ApprovalGateResult,
    BrowserSessionRecord,
    ComplianceAccountSessionRecord,
    ComplianceActionRecord,
    ComplianceApprovalRecord,
    ComplianceAuditReport,
    ComplianceAuditReportResult,
    ComplianceEscalationRecord,
    ComplianceFailureRecord,
    ComplianceSensitiveAccessRecord,
    CredentialAccessEvent,
    EscalationRecord,
    FailSafeActivationRecord,
    FailureArchiveRecord,
    SensitiveAccessEvent,
    VaultCredentialAccessEvent,
    WorkflowAuditLogEntry,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ComplianceAuditReportGenerator:
    generated_by: str = "system"
    signature_secret: str | None = None
    workflow_audit_logger: object | None = None
    sensitive_data_protector: object | None = None

    def generate_report(
        self,
        *,
        workflow_id: str,
        workflow_name: str,
        audit_entries: list[WorkflowAuditLogEntry],
        approval_results: list[ApprovalGateResult] | None = None,
        escalation_records: list[EscalationRecord] | None = None,
        failure_records: list[FailureArchiveRecord] | None = None,
        fail_safe_records: list[FailSafeActivationRecord] | None = None,
        account_usage_events: list[AccountUsageEvent] | None = None,
        browser_sessions: list[BrowserSessionRecord] | None = None,
        sensitive_access_events: list[SensitiveAccessEvent | CredentialAccessEvent | VaultCredentialAccessEvent] | None = None,
        generated_at: datetime | None = None,
    ) -> ComplianceAuditReportResult:
        generated_at = generated_at or utc_now()
        filtered_entries = sorted(
            [entry for entry in audit_entries if entry.workflow_id == workflow_id],
            key=lambda entry: entry.timestamp,
        )
        approvals = self._build_approvals(approval_results or [], workflow_id)
        escalations = self._build_escalations(escalation_records or [], workflow_id)
        failures = self._build_failures(
            failure_records or [],
            workflow_id=workflow_id,
            fail_safe_records=fail_safe_records or [],
        )
        sessions = self._build_sessions(
            browser_sessions or [],
            account_usage_events=account_usage_events or [],
            generated_at=generated_at,
        )
        sensitive_events = self._build_sensitive_events(sensitive_access_events or [])
        if (
            not filtered_entries
            and not approvals
            and not escalations
            and not failures
            and not sessions
            and not sensitive_events
        ):
            return ComplianceAuditReportResult(
                succeeded=False,
                reason="No compliance evidence was available for report generation.",
            )

        actions = [self._build_action_record(entry) for entry in filtered_entries]
        payload = {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "generated_at": generated_at.isoformat(),
            "generated_by": self.generated_by,
            "actions": [self._serialize_value(item) for item in actions],
            "approvals": [self._serialize_value(item) for item in approvals],
            "escalations": [self._serialize_value(item) for item in escalations],
            "failures": [self._serialize_value(item) for item in failures],
            "account_sessions": [self._serialize_value(item) for item in sessions],
            "sensitive_access_events": [self._serialize_value(item) for item in sensitive_events],
        }
        payload = self._sanitize_payload(payload)
        signature, algorithm = self._sign_payload(payload)
        body_text = self._render_text_report(payload, signature=signature, signature_algorithm=algorithm)
        report = ComplianceAuditReport(
            report_id=str(uuid4()),
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            generated_at=generated_at,
            generated_by=self.generated_by,
            signature=signature,
            signature_algorithm=algorithm,
            actions=[self._deserialize_action(item) for item in payload["actions"]],
            approvals=[self._deserialize_approval(item) for item in payload["approvals"]],
            escalations=[self._deserialize_escalation(item) for item in payload["escalations"]],
            failures=[self._deserialize_failure(item) for item in payload["failures"]],
            account_sessions=[self._deserialize_session(item) for item in payload["account_sessions"]],
            sensitive_access_events=[self._deserialize_sensitive(item) for item in payload["sensitive_access_events"]],
            body_text=body_text,
        )
        return ComplianceAuditReportResult(succeeded=True, report=report)

    def export_json(
        self,
        *,
        output_path: str,
        workflow_id: str,
        workflow_name: str,
        audit_entries: list[WorkflowAuditLogEntry],
        approval_results: list[ApprovalGateResult] | None = None,
        escalation_records: list[EscalationRecord] | None = None,
        failure_records: list[FailureArchiveRecord] | None = None,
        fail_safe_records: list[FailSafeActivationRecord] | None = None,
        account_usage_events: list[AccountUsageEvent] | None = None,
        browser_sessions: list[BrowserSessionRecord] | None = None,
        sensitive_access_events: list[SensitiveAccessEvent | CredentialAccessEvent | VaultCredentialAccessEvent] | None = None,
        generated_at: datetime | None = None,
    ) -> ComplianceAuditReportResult:
        result = self.generate_report(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            audit_entries=audit_entries,
            approval_results=approval_results,
            escalation_records=escalation_records,
            failure_records=failure_records,
            fail_safe_records=fail_safe_records,
            account_usage_events=account_usage_events,
            browser_sessions=browser_sessions,
            sensitive_access_events=sensitive_access_events,
            generated_at=generated_at,
        )
        if not result.succeeded or result.report is None:
            return result
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize_report(result.report)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        result.report.json_export_path = str(path)
        self._log_generation(report=result.report, export_format="json", export_path=str(path))
        return ComplianceAuditReportResult(succeeded=True, report=result.report, export_path=str(path))

    def export_pdf(
        self,
        *,
        output_path: str,
        workflow_id: str,
        workflow_name: str,
        audit_entries: list[WorkflowAuditLogEntry],
        approval_results: list[ApprovalGateResult] | None = None,
        escalation_records: list[EscalationRecord] | None = None,
        failure_records: list[FailureArchiveRecord] | None = None,
        fail_safe_records: list[FailSafeActivationRecord] | None = None,
        account_usage_events: list[AccountUsageEvent] | None = None,
        browser_sessions: list[BrowserSessionRecord] | None = None,
        sensitive_access_events: list[SensitiveAccessEvent | CredentialAccessEvent | VaultCredentialAccessEvent] | None = None,
        generated_at: datetime | None = None,
    ) -> ComplianceAuditReportResult:
        result = self.generate_report(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            audit_entries=audit_entries,
            approval_results=approval_results,
            escalation_records=escalation_records,
            failure_records=failure_records,
            fail_safe_records=fail_safe_records,
            account_usage_events=account_usage_events,
            browser_sessions=browser_sessions,
            sensitive_access_events=sensitive_access_events,
            generated_at=generated_at,
        )
        if not result.succeeded or result.report is None or result.report.body_text is None:
            return result
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_simple_pdf(path, result.report.body_text)
        result.report.pdf_export_path = str(path)
        self._log_generation(report=result.report, export_format="pdf", export_path=str(path))
        return ComplianceAuditReportResult(succeeded=True, report=result.report, export_path=str(path))

    def _build_action_record(self, entry: WorkflowAuditLogEntry) -> ComplianceActionRecord:
        actor = (
            entry.output_data.get("actor")
            or entry.input_data.get("actor")
            or entry.output_data.get("worker_id")
            or entry.input_data.get("worker_id")
            or entry.output_data.get("account_name")
            or entry.input_data.get("account_name")
            or "system"
        )
        detail = None
        if entry.target_element:
            detail = f"{entry.action_type} on {entry.target_element}"
        return ComplianceActionRecord(
            timestamp=entry.timestamp,
            actor=str(actor),
            action_type=entry.action_type,
            step_name=entry.step_name,
            outcome="success" if entry.success else "failure",
            target_element=entry.target_element,
            workflow_version_number=entry.workflow_version_number,
            detail=detail,
        )

    def _build_approvals(
        self,
        approval_results: list[ApprovalGateResult],
        workflow_id: str,
    ) -> list[ComplianceApprovalRecord]:
        records: list[ComplianceApprovalRecord] = []
        for item in approval_results:
            action = getattr(item, "action", None)
            if action is None or getattr(action, "workflow_id", None) != workflow_id:
                continue
            request = getattr(item, "request", None)
            response = getattr(item, "response", None)
            records.append(
                ComplianceApprovalRecord(
                    request_id=getattr(request, "request_id", f"gate-{uuid4().hex}"),
                    step_id=getattr(action, "step_id", "unknown"),
                    action_type=getattr(action, "action_type", "unknown"),
                    reviewer_channel=getattr(request, "reviewer_channel", "unknown"),
                    requested_at=getattr(request, "created_at", utc_now()),
                    decision=getattr(getattr(response, "decision", None), "value", getattr(response, "decision", None)),
                    reviewer_id=getattr(response, "reviewer_id", None),
                    responded_at=getattr(response, "responded_at", None),
                    reason=getattr(response, "reason", None) or getattr(item, "reason", None),
                )
            )
        records.sort(key=lambda item: item.requested_at)
        return records

    def _build_escalations(
        self,
        escalation_records: list[EscalationRecord],
        workflow_id: str,
    ) -> list[ComplianceEscalationRecord]:
        records = [
            ComplianceEscalationRecord(
                escalation_id=item.escalation_id,
                step_id=item.step_id,
                trigger_type=item.trigger_type.value,
                created_at=item.created_at,
                resolved=item.resolved,
                resolution=item.resolution.value if item.resolution is not None else None,
                operator_id=item.operator_id,
                responded_at=item.responded_at,
                detail=item.detail,
            )
            for item in escalation_records
            if item.workflow_id == workflow_id
        ]
        records.sort(key=lambda item: item.created_at)
        return records

    def _build_failures(
        self,
        failure_records: list[FailureArchiveRecord],
        *,
        workflow_id: str,
        fail_safe_records: list[FailSafeActivationRecord],
    ) -> list[ComplianceFailureRecord]:
        recovery_actions = self._recovery_actions_for_workflow(fail_safe_records, workflow_id)
        records = [
            ComplianceFailureRecord(
                step_name=item.step_name,
                timestamp=item.timestamp,
                exception_type=item.exception_type,
                exception_message=item.exception_message,
                recovery_actions=list(recovery_actions),
                screenshot_path=item.screenshot_path,
            )
            for item in failure_records
            if item.workflow_id == workflow_id
        ]
        records.sort(key=lambda item: item.timestamp)
        return records

    def _recovery_actions_for_workflow(
        self,
        fail_safe_records: list[FailSafeActivationRecord],
        workflow_id: str,
    ) -> list[str]:
        actions: list[str] = []
        for record in fail_safe_records:
            if record.workflow_id != workflow_id:
                continue
            if record.checkpoint_saved:
                actions.append("checkpoint_saved")
            actions.extend(release.resource_name for release in record.released_resources if release.succeeded)
            if record.cancelled_task_ids:
                actions.append(f"cancelled:{','.join(record.cancelled_task_ids)}")
        deduped: list[str] = []
        seen: set[str] = set()
        for item in actions:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _build_sessions(
        self,
        browser_sessions: list[BrowserSessionRecord],
        *,
        account_usage_events: list[AccountUsageEvent],
        generated_at: datetime,
    ) -> list[ComplianceAccountSessionRecord]:
        records: list[ComplianceAccountSessionRecord] = []
        for session in browser_sessions:
            end_event = self._find_session_end_event(account_usage_events, session.account_name, session.launched_at)
            ended_at = end_event.timestamp if end_event is not None else (generated_at if not session.active else None)
            duration = (ended_at - session.launched_at).total_seconds() if ended_at is not None else None
            application_name = next(
                (
                    event.detail
                    for event in account_usage_events
                    if event.account_name == session.account_name and event.timestamp >= session.launched_at and event.detail
                ),
                None,
            )
            records.append(
                ComplianceAccountSessionRecord(
                    account_name=session.account_name,
                    application_name=application_name,
                    launched_at=session.launched_at,
                    ended_at=ended_at,
                    duration_seconds=duration,
                    active=session.active,
                )
            )
        records.sort(key=lambda item: item.launched_at or datetime.min)
        return records

    def _find_session_end_event(
        self,
        events: list[AccountUsageEvent],
        account_name: str,
        launched_at: datetime,
    ) -> AccountUsageEvent | None:
        candidates = [
            event
            for event in events
            if event.account_name == account_name
            and event.timestamp >= launched_at
            and event.action.casefold() in {"session_end", "logout", "closed", "release"}
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda item: item.timestamp)

    def _build_sensitive_events(
        self,
        events: list[SensitiveAccessEvent | CredentialAccessEvent | VaultCredentialAccessEvent],
    ) -> list[ComplianceSensitiveAccessRecord]:
        records: list[ComplianceSensitiveAccessRecord] = []
        for item in events:
            if isinstance(item, SensitiveAccessEvent):
                records.append(
                    ComplianceSensitiveAccessRecord(
                        source_type="sensitive_data_protector",
                        identifier=item.location,
                        action=item.action,
                        timestamp=item.timestamp,
                        detail=item.detail,
                        metadata=dict(item.metadata),
                    )
                )
            elif isinstance(item, CredentialAccessEvent):
                records.append(
                    ComplianceSensitiveAccessRecord(
                        source_type="credential_vault",
                        identifier=item.account_identifier,
                        action=item.action,
                        timestamp=item.timestamp,
                        detail=item.detail,
                        metadata={"kind": item.kind.value},
                    )
                )
            elif isinstance(item, VaultCredentialAccessEvent):
                records.append(
                    ComplianceSensitiveAccessRecord(
                        source_type="external_vault",
                        identifier=item.secret_name,
                        action=item.action,
                        timestamp=item.timestamp,
                        detail=item.detail,
                        metadata={"cache_hit": item.cache_hit},
                    )
                )
        records.sort(key=lambda item: item.timestamp)
        return records

    def _serialize_report(self, report: ComplianceAuditReport) -> dict[str, Any]:
        payload = self._serialize_value(report)
        return self._sanitize_payload(payload)

    def _sign_payload(self, payload: dict[str, Any]) -> tuple[str, str]:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if self.signature_secret:
            signature = hmac.new(self.signature_secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
            return signature, "hmac-sha256"
        return hashlib.sha256(canonical).hexdigest(), "sha256"

    def _render_text_report(
        self,
        payload: dict[str, Any],
        *,
        signature: str,
        signature_algorithm: str,
    ) -> str:
        lines = [
            "Compliance Audit Report",
            f"Workflow: {payload['workflow_name']} ({payload['workflow_id']})",
            f"Generated At: {payload['generated_at']}",
            f"Generated By: {payload['generated_by']}",
            f"Signature ({signature_algorithm}): {signature}",
            "",
            "Executed Actions:",
        ]
        if payload["actions"]:
            for item in payload["actions"]:
                lines.append(
                    f"- {item['timestamp']} | {item['actor']} | {item['action_type']} | "
                    f"{item['step_name']} | {item['outcome']}"
                )
        else:
            lines.append("- None")
        lines.extend(["", "Approval Gate Decisions:"])
        if payload["approvals"]:
            for item in payload["approvals"]:
                lines.append(
                    f"- {item['request_id']} | {item['step_id']} | {item['action_type']} | "
                    f"{item['decision']} | reviewer={item['reviewer_id']}"
                )
        else:
            lines.append("- None")
        lines.extend(["", "Escalations:"])
        if payload["escalations"]:
            for item in payload["escalations"]:
                lines.append(
                    f"- {item['escalation_id']} | {item['trigger_type']} | resolved={item['resolved']} | "
                    f"resolution={item['resolution']}"
                )
        else:
            lines.append("- None")
        lines.extend(["", "Failures And Recovery Actions:"])
        if payload["failures"]:
            for item in payload["failures"]:
                recovery = ", ".join(item["recovery_actions"]) if item["recovery_actions"] else "none"
                lines.append(
                    f"- {item['timestamp']} | {item['step_name']} | {item['exception_type']} | recovery={recovery}"
                )
        else:
            lines.append("- None")
        lines.extend(["", "Accounts Used And Session Durations:"])
        if payload["account_sessions"]:
            for item in payload["account_sessions"]:
                lines.append(
                    f"- {item['account_name']} | launched={item['launched_at']} | ended={item['ended_at']} | "
                    f"duration_seconds={item['duration_seconds']}"
                )
        else:
            lines.append("- None")
        lines.extend(["", "Sensitive Data Access Events:"])
        if payload["sensitive_access_events"]:
            for item in payload["sensitive_access_events"]:
                lines.append(
                    f"- {item['timestamp']} | {item['source_type']} | {item['identifier']} | {item['action']}"
                )
        else:
            lines.append("- None")
        lines.append("")
        return "\n".join(lines)

    def _write_simple_pdf(self, path: Path, text: str) -> None:
        lines = text.splitlines() or [""]
        content_lines = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
        for index, line in enumerate(lines):
            if index == 0:
                content_lines.append(f"({self._pdf_escape(line)}) Tj")
            else:
                content_lines.append("T*")
                content_lines.append(f"({self._pdf_escape(line)}) Tj")
        content_lines.append("ET")
        content = "\n".join(content_lines).encode("latin-1", errors="replace")

        objects = [
            b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
            b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n",
            f"4 0 obj<< /Length {len(content)} >>stream\n".encode("latin-1") + content + b"\nendstream\nendobj\n",
            b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for obj in objects:
            offsets.append(len(pdf))
            pdf.extend(obj)
        xref_offset = len(pdf)
        pdf.extend(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
        pdf.extend(
            (
                f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
            ).encode("latin-1")
        )
        path.write_bytes(pdf)

    def _pdf_escape(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def _log_generation(self, *, report: ComplianceAuditReport, export_format: str, export_path: str) -> None:
        if self.workflow_audit_logger is None:
            return
        self.workflow_audit_logger.log_action(
            workflow_id=report.workflow_id,
            step_name="compliance_audit_report",
            action_type="generate_compliance_audit_report",
            input_data={"format": export_format, "generated_by": report.generated_by},
            output_data={"export_path": export_path, "signature": report.signature},
            success=True,
        )

    def _sanitize_payload(self, payload: Any) -> Any:
        if self.sensitive_data_protector is None:
            return payload
        return self.sensitive_data_protector.sanitize_payload(payload, location="compliance_audit_report")

    def _serialize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if is_dataclass(value):
            return {key: self._serialize_value(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): self._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]
        if hasattr(value, "value"):
            return getattr(value, "value")
        return value

    def _deserialize_action(self, payload: dict[str, Any]) -> ComplianceActionRecord:
        return ComplianceActionRecord(
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            actor=payload["actor"],
            action_type=payload["action_type"],
            step_name=payload["step_name"],
            outcome=payload["outcome"],
            target_element=payload.get("target_element"),
            workflow_version_number=payload.get("workflow_version_number"),
            detail=payload.get("detail"),
        )

    def _deserialize_approval(self, payload: dict[str, Any]) -> ComplianceApprovalRecord:
        return ComplianceApprovalRecord(
            request_id=payload["request_id"],
            step_id=payload["step_id"],
            action_type=payload["action_type"],
            reviewer_channel=payload["reviewer_channel"],
            requested_at=datetime.fromisoformat(payload["requested_at"]),
            decision=payload.get("decision"),
            reviewer_id=payload.get("reviewer_id"),
            responded_at=datetime.fromisoformat(payload["responded_at"]) if payload.get("responded_at") else None,
            reason=payload.get("reason"),
        )

    def _deserialize_escalation(self, payload: dict[str, Any]) -> ComplianceEscalationRecord:
        return ComplianceEscalationRecord(
            escalation_id=payload["escalation_id"],
            step_id=payload.get("step_id"),
            trigger_type=payload["trigger_type"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            resolved=bool(payload.get("resolved", False)),
            resolution=payload.get("resolution"),
            operator_id=payload.get("operator_id"),
            responded_at=datetime.fromisoformat(payload["responded_at"]) if payload.get("responded_at") else None,
            detail=payload.get("detail"),
        )

    def _deserialize_failure(self, payload: dict[str, Any]) -> ComplianceFailureRecord:
        return ComplianceFailureRecord(
            step_name=payload["step_name"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            exception_type=payload.get("exception_type"),
            exception_message=payload.get("exception_message"),
            recovery_actions=list(payload.get("recovery_actions", [])),
            screenshot_path=payload.get("screenshot_path"),
        )

    def _deserialize_session(self, payload: dict[str, Any]) -> ComplianceAccountSessionRecord:
        return ComplianceAccountSessionRecord(
            account_name=payload["account_name"],
            application_name=payload.get("application_name"),
            launched_at=datetime.fromisoformat(payload["launched_at"]) if payload.get("launched_at") else None,
            ended_at=datetime.fromisoformat(payload["ended_at"]) if payload.get("ended_at") else None,
            duration_seconds=payload.get("duration_seconds"),
            active=bool(payload.get("active", False)),
        )

    def _deserialize_sensitive(self, payload: dict[str, Any]) -> ComplianceSensitiveAccessRecord:
        return ComplianceSensitiveAccessRecord(
            source_type=payload["source_type"],
            identifier=payload["identifier"],
            action=payload["action"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            detail=payload.get("detail"),
            metadata=dict(payload.get("metadata", {})),
        )


