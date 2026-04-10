from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from desktop_automation_agent.models import (
    MetricsDegradationDirection,
    MetricsDegradationRecord,
    ResourceUsageAggregate,
    ResourceUsageRunRecord,
    ResourceUsageTrackerResult,
    ResourceUsageTrendReport,
    ResourceUsageTrendSnapshot,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResourceUsageTracker:
    storage_path: str
    default_window_seconds: float = 86400.0
    degradation_threshold_ratio: float = 0.25
    now_fn: object = utc_now

    def record_run(
        self,
        *,
        workflow_id: str,
        workflow_type: str,
        account_name: str | None = None,
        cpu_time_seconds: float = 0.0,
        peak_memory_mb: float = 0.0,
        api_call_count: int = 0,
        llm_token_count: int = 0,
        run_duration_seconds: float = 0.0,
        screenshot_count: int = 0,
        timestamp: datetime | None = None,
    ) -> ResourceUsageTrackerResult:
        run = ResourceUsageRunRecord(
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            account_name=account_name,
            cpu_time_seconds=float(cpu_time_seconds),
            peak_memory_mb=float(peak_memory_mb),
            api_call_count=int(api_call_count),
            llm_token_count=int(llm_token_count),
            run_duration_seconds=float(run_duration_seconds),
            screenshot_count=int(screenshot_count),
            timestamp=timestamp or self.now(),
        )
        payload = self._load_payload()
        runs = self._deserialize_runs(payload.get("runs", []))
        runs.append(run)
        payload["runs"] = [self._serialize_run(item) for item in runs]
        self._save_payload(payload)
        return ResourceUsageTrackerResult(succeeded=True, run_record=run)

    def list_runs(self) -> ResourceUsageTrackerResult:
        runs = self._deserialize_runs(self._load_payload().get("runs", []))
        runs.sort(key=lambda item: item.timestamp)
        return ResourceUsageTrackerResult(succeeded=True, runs=runs)

    def generate_trend_snapshot(
        self,
        *,
        window_seconds: float | None = None,
        baseline_window_seconds: float | None = None,
        as_of: datetime | None = None,
    ) -> ResourceUsageTrackerResult:
        as_of = as_of or self.now()
        window_seconds = float(window_seconds or self.default_window_seconds)
        baseline_window_seconds = float(baseline_window_seconds or window_seconds)
        runs = self._deserialize_runs(self._load_payload().get("runs", []))
        current_runs = self._filter_window(runs, as_of=as_of, window_seconds=window_seconds)
        baseline_runs = self._filter_window(
            runs,
            as_of=as_of - timedelta(seconds=window_seconds),
            window_seconds=baseline_window_seconds,
        )
        workflow_aggregates = self._build_aggregates(current_runs, group_field="workflow_type")
        account_aggregates = self._build_aggregates(current_runs, group_field="account_name", unknown_label="unassigned")
        snapshot = ResourceUsageTrendSnapshot(
            generated_at=as_of,
            window_seconds=window_seconds,
            workflow_type_aggregates=workflow_aggregates,
            account_aggregates=account_aggregates,
            workflow_type_degradations=self._build_degradations(
                current=workflow_aggregates,
                baseline=self._build_aggregates(baseline_runs, group_field="workflow_type"),
            ),
        )
        return ResourceUsageTrackerResult(succeeded=True, snapshot=snapshot)

    def generate_daily_report(
        self,
        *,
        report_date: datetime | None = None,
    ) -> ResourceUsageTrackerResult:
        report_date = report_date or self.now()
        day_start = datetime(report_date.year, report_date.month, report_date.day)
        snapshot_result = self.generate_trend_snapshot(
            window_seconds=86400.0,
            baseline_window_seconds=86400.0,
            as_of=day_start + timedelta(days=1) - timedelta(microseconds=1),
        )
        if not snapshot_result.succeeded or snapshot_result.snapshot is None:
            return snapshot_result
        report = ResourceUsageTrendReport(
            report_date=day_start.date().isoformat(),
            generated_at=self.now(),
            snapshot=snapshot_result.snapshot,
            body_text=self._render_report(snapshot_result.snapshot, day_start.date().isoformat()),
        )
        return ResourceUsageTrackerResult(succeeded=True, snapshot=snapshot_result.snapshot, report=report)

    def now(self) -> datetime:
        return self.now_fn() if callable(self.now_fn) else utc_now()

    def _filter_window(
        self,
        runs: list[ResourceUsageRunRecord],
        *,
        as_of: datetime,
        window_seconds: float,
    ) -> list[ResourceUsageRunRecord]:
        start = as_of - timedelta(seconds=window_seconds)
        return [item for item in runs if start <= item.timestamp <= as_of]

    def _build_aggregates(
        self,
        runs: list[ResourceUsageRunRecord],
        *,
        group_field: str,
        unknown_label: str = "unknown",
    ) -> list[ResourceUsageAggregate]:
        buckets: dict[str, list[ResourceUsageRunRecord]] = {}
        for run in runs:
            group_key = getattr(run, group_field) or unknown_label
            buckets.setdefault(str(group_key), []).append(run)
        aggregates: list[ResourceUsageAggregate] = []
        for group_key, items in buckets.items():
            aggregates.append(
                ResourceUsageAggregate(
                    group_key=group_key,
                    run_count=len(items),
                    average_cpu_time_seconds=mean(item.cpu_time_seconds for item in items),
                    average_peak_memory_mb=mean(item.peak_memory_mb for item in items),
                    average_api_call_count=mean(item.api_call_count for item in items),
                    average_llm_token_count=mean(item.llm_token_count for item in items),
                    average_run_duration_seconds=mean(item.run_duration_seconds for item in items),
                    average_screenshot_count=mean(item.screenshot_count for item in items),
                    total_cpu_time_seconds=sum(item.cpu_time_seconds for item in items),
                    total_api_call_count=sum(item.api_call_count for item in items),
                    total_llm_token_count=sum(item.llm_token_count for item in items),
                    total_run_duration_seconds=sum(item.run_duration_seconds for item in items),
                    total_screenshot_count=sum(item.screenshot_count for item in items),
                    peak_memory_mb=max(item.peak_memory_mb for item in items) if items else 0.0,
                )
            )
        aggregates.sort(key=lambda item: item.group_key)
        return aggregates

    def _build_degradations(
        self,
        *,
        current: list[ResourceUsageAggregate],
        baseline: list[ResourceUsageAggregate],
    ) -> dict[str, list[MetricsDegradationRecord]]:
        baseline_by_key = {item.group_key: item for item in baseline}
        degradations: dict[str, list[MetricsDegradationRecord]] = {}
        for item in current:
            baseline_item = baseline_by_key.get(item.group_key)
            if baseline_item is None:
                continue
            records = [
                self._compare_metric(
                    metric_name="average_cpu_time_seconds",
                    current_value=item.average_cpu_time_seconds,
                    baseline_value=baseline_item.average_cpu_time_seconds,
                ),
                self._compare_metric(
                    metric_name="average_peak_memory_mb",
                    current_value=item.average_peak_memory_mb,
                    baseline_value=baseline_item.average_peak_memory_mb,
                ),
                self._compare_metric(
                    metric_name="average_api_call_count",
                    current_value=item.average_api_call_count,
                    baseline_value=baseline_item.average_api_call_count,
                ),
                self._compare_metric(
                    metric_name="average_llm_token_count",
                    current_value=item.average_llm_token_count,
                    baseline_value=baseline_item.average_llm_token_count,
                ),
                self._compare_metric(
                    metric_name="average_run_duration_seconds",
                    current_value=item.average_run_duration_seconds,
                    baseline_value=baseline_item.average_run_duration_seconds,
                ),
                self._compare_metric(
                    metric_name="average_screenshot_count",
                    current_value=item.average_screenshot_count,
                    baseline_value=baseline_item.average_screenshot_count,
                ),
            ]
            filtered = [record for record in records if record is not None]
            if filtered:
                degradations[item.group_key] = filtered
        return degradations

    def _compare_metric(
        self,
        *,
        metric_name: str,
        current_value: float,
        baseline_value: float,
    ) -> MetricsDegradationRecord | None:
        if baseline_value <= 0:
            return None
        delta_ratio = (current_value - baseline_value) / baseline_value
        if delta_ratio < self.degradation_threshold_ratio:
            return None
        return MetricsDegradationRecord(
            metric_name=metric_name,
            current_value=current_value,
            baseline_value=baseline_value,
            delta_ratio=delta_ratio,
            direction=MetricsDegradationDirection.HIGHER_IS_WORSE,
            threshold_ratio=self.degradation_threshold_ratio,
        )

    def _render_report(self, snapshot: ResourceUsageTrendSnapshot, report_date: str) -> str:
        lines = [
            f"Resource Usage Trend Report: {report_date}",
            "",
            "By Workflow Type:",
        ]
        if snapshot.workflow_type_aggregates:
            for item in snapshot.workflow_type_aggregates:
                lines.extend(
                    [
                        f"- {item.group_key}: runs={item.run_count}, avg_cpu={item.average_cpu_time_seconds:.2f}s, "
                        f"avg_memory={item.average_peak_memory_mb:.2f}MB, avg_api_calls={item.average_api_call_count:.2f}, "
                        f"avg_llm_tokens={item.average_llm_token_count:.2f}, avg_duration={item.average_run_duration_seconds:.2f}s, "
                        f"avg_screenshots={item.average_screenshot_count:.2f}",
                    ]
                )
                if item.group_key in snapshot.workflow_type_degradations:
                    for degradation in snapshot.workflow_type_degradations[item.group_key]:
                        lines.append(
                            f"  flagged: {degradation.metric_name} increased by {degradation.delta_ratio:.2%} "
                            f"from baseline {degradation.baseline_value:.2f} to {degradation.current_value:.2f}"
                        )
        else:
            lines.append("- None")
        lines.extend(["", "By Account:"])
        if snapshot.account_aggregates:
            for item in snapshot.account_aggregates:
                lines.append(
                    f"- {item.group_key}: runs={item.run_count}, total_cpu={item.total_cpu_time_seconds:.2f}s, "
                    f"peak_memory={item.peak_memory_mb:.2f}MB, total_api_calls={item.total_api_call_count}, "
                    f"total_llm_tokens={item.total_llm_token_count}, total_duration={item.total_run_duration_seconds:.2f}s, "
                    f"total_screenshots={item.total_screenshot_count}"
                )
        else:
            lines.append("- None")
        lines.append("")
        return "\n".join(lines)

    def _load_payload(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {"runs": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.setdefault("runs", [])
            return payload
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load resource usage from {self.storage_path}: {e}")
            return {"runs": []}

    def _save_payload(self, payload: dict) -> None:
        try:
            path = Path(self.storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save resource usage to {self.storage_path}: {e}")

    def _serialize_run(self, run: ResourceUsageRunRecord) -> dict:
        return {
            "workflow_id": run.workflow_id,
            "workflow_type": run.workflow_type,
            "account_name": run.account_name,
            "cpu_time_seconds": run.cpu_time_seconds,
            "peak_memory_mb": run.peak_memory_mb,
            "api_call_count": run.api_call_count,
            "llm_token_count": run.llm_token_count,
            "run_duration_seconds": run.run_duration_seconds,
            "screenshot_count": run.screenshot_count,
            "timestamp": run.timestamp.isoformat(),
        }

    def _deserialize_runs(self, payloads: list[dict]) -> list[ResourceUsageRunRecord]:
        return [
            ResourceUsageRunRecord(
                workflow_id=item["workflow_id"],
                workflow_type=item["workflow_type"],
                account_name=item.get("account_name"),
                cpu_time_seconds=float(item.get("cpu_time_seconds", 0.0)),
                peak_memory_mb=float(item.get("peak_memory_mb", 0.0)),
                api_call_count=int(item.get("api_call_count", 0)),
                llm_token_count=int(item.get("llm_token_count", 0)),
                run_duration_seconds=float(item.get("run_duration_seconds", 0.0)),
                screenshot_count=int(item.get("screenshot_count", 0)),
                timestamp=datetime.fromisoformat(item["timestamp"]),
            )
            for item in payloads
        ]


