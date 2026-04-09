from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from uuid import uuid4

from desktop_automation_agent.models import (
    PromptPerformanceRecord,
    PromptPerformanceReport,
    PromptPerformanceResult,
    PromptTemplatePerformanceSummary,
)


@dataclass(slots=True)
class PromptPerformanceTracker:
    storage_path: str
    low_success_rate_threshold: float = 0.7

    def record_prompt_submission(
        self,
        *,
        template_name: str,
        variables: dict[str, str],
        response_text: str | None,
        expected_format_met: bool,
        execution_time_seconds: float,
        succeeded: bool,
        template_version: int | None = None,
    ) -> PromptPerformanceResult:
        record = PromptPerformanceRecord(
            record_id=str(uuid4()),
            template_name=template_name,
            template_version=template_version,
            variables=dict(variables),
            response_text=response_text,
            expected_format_met=bool(expected_format_met),
            execution_time_seconds=float(execution_time_seconds),
            succeeded=bool(succeeded),
        )
        snapshot = self._load_snapshot()
        snapshot.append(record)
        self._save_snapshot(snapshot)
        return PromptPerformanceResult(succeeded=True, record=record)

    def list_records(self) -> list[PromptPerformanceRecord]:
        return self._load_snapshot()

    def generate_report(self) -> PromptPerformanceResult:
        records = self._load_snapshot()
        summaries = self._build_summaries(records)
        most_reliable = sorted(
            summaries,
            key=lambda item: (-item.success_rate, -item.expected_format_rate, item.average_execution_time_seconds, item.template_name),
        )[:5]
        least_reliable = sorted(
            summaries,
            key=lambda item: (item.success_rate, item.expected_format_rate, -item.average_execution_time_seconds, item.template_name),
        )[:5]
        return PromptPerformanceResult(
            succeeded=True,
            report=PromptPerformanceReport(
                template_summaries=summaries,
                most_reliable_templates=most_reliable,
                least_reliable_templates=least_reliable,
            ),
        )

    def _build_summaries(
        self,
        records: list[PromptPerformanceRecord],
    ) -> list[PromptTemplatePerformanceSummary]:
        buckets: dict[str, list[PromptPerformanceRecord]] = {}
        for record in records:
            buckets.setdefault(record.template_name, []).append(record)

        summaries: list[PromptTemplatePerformanceSummary] = []
        for template_name, items in buckets.items():
            submission_count = len(items)
            success_count = sum(1 for item in items if item.succeeded)
            expected_format_success_count = sum(1 for item in items if item.expected_format_met)
            success_rate = (success_count / submission_count) if submission_count else 0.0
            expected_format_rate = (
                expected_format_success_count / submission_count if submission_count else 0.0
            )
            average_execution_time_seconds = mean(item.execution_time_seconds for item in items) if items else 0.0
            summaries.append(
                PromptTemplatePerformanceSummary(
                    template_name=template_name,
                    submission_count=submission_count,
                    success_count=success_count,
                    expected_format_success_count=expected_format_success_count,
                    success_rate=success_rate,
                    expected_format_rate=expected_format_rate,
                    average_execution_time_seconds=average_execution_time_seconds,
                    flagged_low_success=success_rate < self.low_success_rate_threshold,
                )
            )

        summaries.sort(key=lambda item: item.template_name)
        return summaries

    def _load_snapshot(self) -> list[PromptPerformanceRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_record(item) for item in payload.get("records", [])]

    def _save_snapshot(self, snapshot: list[PromptPerformanceRecord]) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": [self._serialize_record(item) for item in snapshot]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_record(self, record: PromptPerformanceRecord) -> dict:
        return {
            "record_id": record.record_id,
            "template_name": record.template_name,
            "template_version": record.template_version,
            "variables": record.variables,
            "response_text": record.response_text,
            "expected_format_met": record.expected_format_met,
            "execution_time_seconds": record.execution_time_seconds,
            "succeeded": record.succeeded,
            "timestamp": record.timestamp.isoformat(),
        }

    def _deserialize_record(self, payload: dict) -> PromptPerformanceRecord:
        return PromptPerformanceRecord(
            record_id=payload["record_id"],
            template_name=payload["template_name"],
            template_version=payload.get("template_version"),
            variables=dict(payload.get("variables", {})),
            response_text=payload.get("response_text"),
            expected_format_met=bool(payload.get("expected_format_met", False)),
            execution_time_seconds=float(payload.get("execution_time_seconds", 0.0)),
            succeeded=bool(payload.get("succeeded", False)),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )
