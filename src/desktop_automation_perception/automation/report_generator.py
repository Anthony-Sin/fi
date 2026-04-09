from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from desktop_automation_perception.models import (
    ActionRetryRateSummary,
    AnomalyRecord,
    DeadLetterItem,
    FailureArchiveRecord,
    WorkflowAuditLogEntry,
    WorkflowAuditOutcome,
    WorkflowReportResult,
    WorkflowReportSummary,
    WorkflowReportTimelineItem,
    WorkflowRunReport,
)


@dataclass(slots=True)
class WorkflowReportGenerator:
    sensitive_data_protector: object | None = None

    def generate_report(
        self,
        *,
        workflow_id: str,
        workflow_name: str,
        audit_entries: list[WorkflowAuditLogEntry],
        retry_summaries: list[ActionRetryRateSummary] | None = None,
        anomalies: list[AnomalyRecord] | None = None,
        failure_records: list[FailureArchiveRecord] | None = None,
        dlq_items: list[DeadLetterItem] | None = None,
    ) -> WorkflowReportResult:
        filtered_entries = sorted(
            [entry for entry in audit_entries if entry.workflow_id == workflow_id],
            key=lambda entry: entry.timestamp,
        )
        retry_summaries = sorted(list(retry_summaries or []), key=lambda item: (-item.retry_rate, item.step_type))
        anomalies = list(anomalies or [])
        failure_records = [record for record in (failure_records or []) if record.workflow_id == workflow_id]
        dlq_items = [item for item in (dlq_items or []) if item.inputs.get("workflow_id") == workflow_id]

        if not filtered_entries and not failure_records and not dlq_items:
            return WorkflowReportResult(succeeded=False, reason="No workflow run data was available for report generation.")

        started_at = filtered_entries[0].timestamp if filtered_entries else None
        ended_at = filtered_entries[-1].timestamp if filtered_entries else None
        success_count = sum(1 for entry in filtered_entries if entry.success)
        failure_count = sum(1 for entry in filtered_entries if not entry.success)
        total_duration = sum(float(entry.duration_seconds) for entry in filtered_entries)
        timeline = [
            WorkflowReportTimelineItem(
                timestamp=entry.timestamp,
                step_name=entry.step_name,
                action_type=entry.action_type,
                outcome=entry.outcome,
                duration_seconds=float(entry.duration_seconds),
                detail=self._build_timeline_detail(entry),
            )
            for entry in filtered_entries
        ]

        escalations = self._collect_escalations(filtered_entries, anomalies, dlq_items)
        unusual_patterns = self._collect_unusual_patterns(retry_summaries, anomalies, filtered_entries)
        failure_links = [record.screenshot_path for record in failure_records if record.screenshot_path]
        if self.sensitive_data_protector is not None:
            failure_links = [
                self.sensitive_data_protector.mask_text(link, location="workflow_report_link").text or link
                for link in failure_links
            ]

        summary = WorkflowReportSummary(
            workflow_name=workflow_name,
            workflow_id=workflow_id,
            started_at=started_at,
            ended_at=ended_at,
            total_steps_executed=len(filtered_entries),
            success_count=success_count,
            failure_count=failure_count,
            total_duration_seconds=total_duration,
            highest_retry_steps=retry_summaries[:5],
            escalations=escalations,
            dlq_items=[self._format_dlq_item(item) for item in dlq_items],
            unusual_patterns=unusual_patterns,
        )
        body = self._render_text_report(summary, timeline, failure_records, failure_links)
        if self.sensitive_data_protector is not None:
            body = self.sensitive_data_protector.mask_text(body, location="workflow_report").text or body
        return WorkflowReportResult(
            succeeded=True,
            report=WorkflowRunReport(
                summary=summary,
                timeline=timeline,
                failure_screenshot_links=failure_links,
                body_text=body,
            ),
        )

    def export_report(
        self,
        *,
        output_path: str,
        workflow_id: str,
        workflow_name: str,
        audit_entries: list[WorkflowAuditLogEntry],
        retry_summaries: list[ActionRetryRateSummary] | None = None,
        anomalies: list[AnomalyRecord] | None = None,
        failure_records: list[FailureArchiveRecord] | None = None,
        dlq_items: list[DeadLetterItem] | None = None,
    ) -> WorkflowReportResult:
        result = self.generate_report(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            audit_entries=audit_entries,
            retry_summaries=retry_summaries,
            anomalies=anomalies,
            failure_records=failure_records,
            dlq_items=dlq_items,
        )
        if not result.succeeded or result.report is None or result.report.body_text is None:
            return result
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.report.body_text, encoding="utf-8")
        return WorkflowReportResult(
            succeeded=True,
            report=result.report,
            export_path=str(path),
        )

    def _build_timeline_detail(self, entry: WorkflowAuditLogEntry) -> str | None:
        if entry.target_element:
            return f"{entry.action_type} on {entry.target_element}"
        return entry.action_type

    def _collect_escalations(
        self,
        audit_entries: list[WorkflowAuditLogEntry],
        anomalies: list[AnomalyRecord],
        dlq_items: list[DeadLetterItem],
    ) -> list[str]:
        escalations: list[str] = []
        for entry in audit_entries:
            if entry.action_type.casefold() == "escalate" or entry.output_data.get("escalated") is True:
                escalations.append(f"{entry.step_name} at {entry.timestamp.isoformat()}")
        for anomaly in anomalies:
            if anomaly.pause_requested or anomaly.alert_sent:
                label = anomaly.step_id or anomaly.application_name or "workflow"
                escalations.append(f"{anomaly.category.value} detected for {label}")
        if dlq_items:
            escalations.append(f"{len(dlq_items)} action(s) routed to the DLQ")
        return escalations

    def _collect_unusual_patterns(
        self,
        retry_summaries: list[ActionRetryRateSummary],
        anomalies: list[AnomalyRecord],
        audit_entries: list[WorkflowAuditLogEntry],
    ) -> list[str]:
        patterns: list[str] = []
        for retry in retry_summaries[:3]:
            patterns.append(f"High retry rate on {retry.step_type}: {retry.retry_rate:.2f}")
        for anomaly in anomalies[:5]:
            label = anomaly.step_id or anomaly.application_name or "workflow"
            patterns.append(f"{anomaly.category.value} observed for {label}")
        if audit_entries:
            slowest = max(audit_entries, key=lambda entry: float(entry.duration_seconds))
            if slowest.duration_seconds > 0:
                patterns.append(
                    f"Slowest step was {slowest.step_name} ({slowest.action_type}) at {slowest.duration_seconds:.2f}s"
                )
        deduped: list[str] = []
        seen: set[str] = set()
        for item in patterns:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _format_dlq_item(self, item: DeadLetterItem) -> str:
        return f"{item.action_type} ({item.item_id})"

    def _render_text_report(
        self,
        summary: WorkflowReportSummary,
        timeline: list[WorkflowReportTimelineItem],
        failure_records: list[FailureArchiveRecord],
        failure_links: list[str],
    ) -> str:
        lines = [
            f"Workflow Report: {summary.workflow_name}",
            f"Workflow ID: {summary.workflow_id}",
            f"Execution Time Range: {self._format_time_range(summary.started_at, summary.ended_at)}",
            f"Total Steps Executed: {summary.total_steps_executed}",
            f"Success Count: {summary.success_count}",
            f"Failure Count: {summary.failure_count}",
            f"Total Duration: {summary.total_duration_seconds:.2f}s",
            "",
            "Highest Retry Steps:",
        ]
        if summary.highest_retry_steps:
            for item in summary.highest_retry_steps:
                lines.append(f"- {item.step_type}: retry rate {item.retry_rate:.2f} ({item.retry_count}/{item.sample_count})")
        else:
            lines.append("- None")

        lines.extend(["", "Escalations and DLQ:"])
        if summary.escalations or summary.dlq_items:
            for item in summary.escalations:
                lines.append(f"- Escalation: {item}")
            for item in summary.dlq_items:
                lines.append(f"- DLQ: {item}")
        else:
            lines.append("- None")

        lines.extend(["", "Timeline:"])
        for item in timeline:
            lines.append(
                f"- {item.timestamp.isoformat()} | {item.step_name} | {item.action_type} | "
                f"{item.outcome.value} | {item.duration_seconds:.2f}s"
            )

        lines.extend(["", "Unusual Patterns:"])
        if summary.unusual_patterns:
            for item in summary.unusual_patterns:
                lines.append(f"- {item}")
        else:
            lines.append("- None observed")

        lines.extend(["", "Failure Screenshots:"])
        if failure_links:
            for record in failure_records:
                if record.screenshot_path:
                    lines.append(f"- {record.step_name}: {record.screenshot_path}")
        else:
            lines.append("- None")
        return "\n".join(lines) + "\n"

    def _format_time_range(self, started_at: datetime | None, ended_at: datetime | None) -> str:
        if started_at is None and ended_at is None:
            return "Unknown"
        if started_at is None:
            return f"Unknown -> {ended_at.isoformat()}"
        if ended_at is None:
            return f"{started_at.isoformat()} -> Unknown"
        return f"{started_at.isoformat()} -> {ended_at.isoformat()}"
