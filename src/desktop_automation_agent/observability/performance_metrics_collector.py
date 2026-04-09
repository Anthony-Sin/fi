from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from desktop_automation_agent.models import (
    MetricsDegradationDirection,
    MetricsDegradationRecord,
    PerformanceMetricsResult,
    PerformanceMetricsSnapshot,
    StepLatencySummary,
    StepRetryRateMetric,
    WorkflowSuccessMetric,
)


@dataclass(slots=True)
class PerformanceMetricsCollector:
    storage_path: str
    default_window_seconds: float = 3600.0
    degradation_threshold_ratio: float = 0.2

    def record_step_execution(
        self,
        *,
        workflow_id: str,
        step_name: str,
        execution_time_seconds: float,
        succeeded: bool = True,
        timestamp: datetime | None = None,
    ) -> None:
        snapshot = self._load_snapshot()
        snapshot["step_executions"].append(
            {
                "workflow_id": workflow_id,
                "step_name": step_name,
                "execution_time_seconds": float(execution_time_seconds),
                "succeeded": bool(succeeded),
                "timestamp": (timestamp or utc_now()).isoformat(),
            }
        )
        self._save_snapshot(snapshot)

    def record_retry(
        self,
        *,
        workflow_id: str,
        step_name: str,
        retry_count: int = 1,
        timestamp: datetime | None = None,
    ) -> None:
        snapshot = self._load_snapshot()
        snapshot["retries"].append(
            {
                "workflow_id": workflow_id,
                "step_name": step_name,
                "retry_count": int(retry_count),
                "timestamp": (timestamp or utc_now()).isoformat(),
            }
        )
        self._save_snapshot(snapshot)

    def record_workflow_completion(
        self,
        *,
        workflow_id: str,
        succeeded: bool,
        step_count: int,
        timestamp: datetime | None = None,
    ) -> None:
        snapshot = self._load_snapshot()
        snapshot["workflow_runs"].append(
            {
                "workflow_id": workflow_id,
                "succeeded": bool(succeeded),
                "step_count": int(step_count),
                "timestamp": (timestamp or utc_now()).isoformat(),
            }
        )
        self._save_snapshot(snapshot)

    def record_dlq_depth(
        self,
        *,
        depth: int,
        timestamp: datetime | None = None,
    ) -> None:
        snapshot = self._load_snapshot()
        snapshot["dlq_depths"].append(
            {
                "depth": int(depth),
                "timestamp": (timestamp or utc_now()).isoformat(),
            }
        )
        self._save_snapshot(snapshot)

    def record_session(
        self,
        *,
        session_id: str,
        application_name: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        snapshot = self._load_snapshot()
        snapshot["sessions"].append(
            {
                "session_id": session_id,
                "application_name": application_name,
                "timestamp": (timestamp or utc_now()).isoformat(),
            }
        )
        self._save_snapshot(snapshot)

    def generate_snapshot(
        self,
        *,
        window_seconds: float | None = None,
        baseline_window_seconds: float | None = None,
        as_of: datetime | None = None,
    ) -> PerformanceMetricsResult:
        as_of = as_of or utc_now()
        window_seconds = float(window_seconds or self.default_window_seconds)
        baseline_window_seconds = float(baseline_window_seconds or window_seconds)
        snapshot = self._load_snapshot()

        current = self._filter_window(snapshot, as_of=as_of, window_seconds=window_seconds)
        baseline_end = as_of - timedelta(seconds=window_seconds)
        baseline = self._filter_window(snapshot, as_of=baseline_end, window_seconds=baseline_window_seconds)

        current_snapshot = PerformanceMetricsSnapshot(
            generated_at=as_of,
            window_seconds=window_seconds,
            step_latencies=self._build_step_latency_summaries(current["step_executions"]),
            retry_rates=self._build_retry_summaries(current["step_executions"], current["retries"]),
            workflow_success_rates=self._build_workflow_success_metrics(current["workflow_runs"]),
            dlq_depth=self._latest_dlq_depth(current["dlq_depths"]),
            session_count=len(current["sessions"]),
            average_steps_per_workflow=self._average_steps_per_workflow(current["workflow_runs"]),
        )
        current_snapshot.degradations = self._detect_degradation(current_snapshot, baseline)
        return PerformanceMetricsResult(succeeded=True, snapshot=current_snapshot)

    def render_metrics_endpoint(
        self,
        *,
        window_seconds: float | None = None,
        baseline_window_seconds: float | None = None,
        as_of: datetime | None = None,
    ) -> PerformanceMetricsResult:
        result = self.generate_snapshot(
            window_seconds=window_seconds,
            baseline_window_seconds=baseline_window_seconds,
            as_of=as_of,
        )
        snapshot = result.snapshot
        if snapshot is None:
            return PerformanceMetricsResult(succeeded=False, reason="Metrics snapshot could not be generated.")
        lines = [
            "# TYPE automation_dlq_depth gauge",
            f"automation_dlq_depth {snapshot.dlq_depth}",
            "# TYPE automation_session_count gauge",
            f"automation_session_count {snapshot.session_count}",
            "# TYPE automation_average_steps_per_workflow gauge",
            f"automation_average_steps_per_workflow {snapshot.average_steps_per_workflow:.6f}",
        ]
        for summary in snapshot.step_latencies:
            step = self._label_escape(summary.step_name)
            lines.extend(
                [
                    f'automation_step_execution_mean_seconds{{step="{step}"}} {summary.mean_seconds:.6f}',
                    f'automation_step_execution_p95_seconds{{step="{step}"}} {summary.p95_seconds:.6f}',
                    f'automation_step_execution_p99_seconds{{step="{step}"}} {summary.p99_seconds:.6f}',
                ]
            )
        for metric in snapshot.retry_rates:
            step = self._label_escape(metric.step_name)
            lines.append(f'automation_step_retry_rate{{step="{step}"}} {metric.retry_rate:.6f}')
        for metric in snapshot.workflow_success_rates:
            workflow = self._label_escape(metric.workflow_id)
            lines.append(f'automation_workflow_success_rate{{workflow="{workflow}"}} {metric.success_rate:.6f}')
        for degradation in snapshot.degradations:
            metric_name = self._metric_name(degradation.metric_name)
            lines.append(
                f'automation_metric_degradation_ratio{{metric="{metric_name}"}} {degradation.delta_ratio:.6f}'
            )
        payload = "\n".join(lines) + "\n"
        return PerformanceMetricsResult(succeeded=True, snapshot=snapshot, endpoint_payload=payload)

    def _filter_window(
        self,
        snapshot: dict,
        *,
        as_of: datetime,
        window_seconds: float,
    ) -> dict:
        start = as_of - timedelta(seconds=window_seconds)
        return {
            key: [
                item for item in snapshot[key]
                if start <= datetime.fromisoformat(item["timestamp"]) <= as_of
            ]
            for key in snapshot
        }

    def _build_step_latency_summaries(self, step_executions: list[dict]) -> list[StepLatencySummary]:
        buckets: dict[str, list[float]] = {}
        for item in step_executions:
            buckets.setdefault(item["step_name"], []).append(float(item["execution_time_seconds"]))
        summaries = [
            StepLatencySummary(
                step_name=step_name,
                sample_count=len(values),
                mean_seconds=mean(values),
                p95_seconds=self._percentile(values, 0.95),
                p99_seconds=self._percentile(values, 0.99),
            )
            for step_name, values in buckets.items()
            if values
        ]
        summaries.sort(key=lambda item: item.step_name)
        return summaries

    def _build_retry_summaries(
        self,
        step_executions: list[dict],
        retries: list[dict],
    ) -> list[StepRetryRateMetric]:
        execution_counts: dict[str, int] = {}
        retry_counts: dict[str, int] = {}
        for item in step_executions:
            execution_counts[item["step_name"]] = execution_counts.get(item["step_name"], 0) + 1
        for item in retries:
            retry_counts[item["step_name"]] = retry_counts.get(item["step_name"], 0) + int(item["retry_count"])
        metrics = []
        for step_name, retry_count in retry_counts.items():
            execution_count = max(execution_counts.get(step_name, 0), 1)
            metrics.append(
                StepRetryRateMetric(
                    step_name=step_name,
                    retry_count=retry_count,
                    execution_count=execution_count,
                    retry_rate=retry_count / execution_count,
                )
            )
        metrics.sort(key=lambda item: item.step_name)
        return metrics

    def _build_workflow_success_metrics(self, workflow_runs: list[dict]) -> list[WorkflowSuccessMetric]:
        buckets: dict[str, list[dict]] = {}
        for item in workflow_runs:
            buckets.setdefault(item["workflow_id"], []).append(item)
        metrics = []
        for workflow_id, items in buckets.items():
            run_count = len(items)
            success_count = sum(1 for item in items if item["succeeded"])
            metrics.append(
                WorkflowSuccessMetric(
                    workflow_id=workflow_id,
                    run_count=run_count,
                    success_count=success_count,
                    success_rate=success_count / run_count if run_count else 0.0,
                    average_steps_per_run=mean(float(item["step_count"]) for item in items) if items else 0.0,
                )
            )
        metrics.sort(key=lambda item: item.workflow_id)
        return metrics

    def _average_steps_per_workflow(self, workflow_runs: list[dict]) -> float:
        if not workflow_runs:
            return 0.0
        return mean(float(item["step_count"]) for item in workflow_runs)

    def _latest_dlq_depth(self, dlq_depths: list[dict]) -> int:
        if not dlq_depths:
            return 0
        latest = max(dlq_depths, key=lambda item: item["timestamp"])
        return int(latest["depth"])

    def _detect_degradation(
        self,
        current: PerformanceMetricsSnapshot,
        baseline_window: dict,
    ) -> list[MetricsDegradationRecord]:
        degradations: list[MetricsDegradationRecord] = []
        baseline_latencies = {item.step_name: item for item in self._build_step_latency_summaries(baseline_window["step_executions"])}
        for item in current.step_latencies:
            baseline = baseline_latencies.get(item.step_name)
            if baseline is not None:
                degradation = self._compare_metric(
                    metric_name=f"step_mean_seconds:{item.step_name}",
                    current_value=item.mean_seconds,
                    baseline_value=baseline.mean_seconds,
                    direction=MetricsDegradationDirection.HIGHER_IS_WORSE,
                )
                if degradation is not None:
                    degradations.append(degradation)

        baseline_retries = {item.step_name: item for item in self._build_retry_summaries(baseline_window["step_executions"], baseline_window["retries"])}
        for item in current.retry_rates:
            baseline = baseline_retries.get(item.step_name)
            if baseline is not None:
                degradation = self._compare_metric(
                    metric_name=f"retry_rate:{item.step_name}",
                    current_value=item.retry_rate,
                    baseline_value=baseline.retry_rate,
                    direction=MetricsDegradationDirection.HIGHER_IS_WORSE,
                )
                if degradation is not None:
                    degradations.append(degradation)

        baseline_workflows = {item.workflow_id: item for item in self._build_workflow_success_metrics(baseline_window["workflow_runs"])}
        for item in current.workflow_success_rates:
            baseline = baseline_workflows.get(item.workflow_id)
            if baseline is not None:
                degradation = self._compare_metric(
                    metric_name=f"workflow_success_rate:{item.workflow_id}",
                    current_value=item.success_rate,
                    baseline_value=baseline.success_rate,
                    direction=MetricsDegradationDirection.LOWER_IS_WORSE,
                )
                if degradation is not None:
                    degradations.append(degradation)

        baseline_dlq = self._latest_dlq_depth(baseline_window["dlq_depths"])
        degradation = self._compare_metric(
            metric_name="dlq_depth",
            current_value=float(current.dlq_depth),
            baseline_value=float(baseline_dlq),
            direction=MetricsDegradationDirection.HIGHER_IS_WORSE,
        )
        if degradation is not None:
            degradations.append(degradation)
        return degradations

    def _compare_metric(
        self,
        *,
        metric_name: str,
        current_value: float,
        baseline_value: float,
        direction: MetricsDegradationDirection,
    ) -> MetricsDegradationRecord | None:
        if baseline_value <= 0:
            return None
        if direction is MetricsDegradationDirection.HIGHER_IS_WORSE:
            delta_ratio = (current_value - baseline_value) / baseline_value
        else:
            delta_ratio = (baseline_value - current_value) / baseline_value
        if delta_ratio < self.degradation_threshold_ratio:
            return None
        return MetricsDegradationRecord(
            metric_name=metric_name,
            current_value=current_value,
            baseline_value=baseline_value,
            delta_ratio=delta_ratio,
            direction=direction,
            threshold_ratio=self.degradation_threshold_ratio,
        )

    def _load_snapshot(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {
                "step_executions": [],
                "retries": [],
                "workflow_runs": [],
                "dlq_depths": [],
                "sessions": [],
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("step_executions", [])
        payload.setdefault("retries", [])
        payload.setdefault("workflow_runs", [])
        payload.setdefault("dlq_depths", [])
        payload.setdefault("sessions", [])
        return payload

    def _save_snapshot(self, snapshot: dict) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    def _percentile(self, values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return ordered[0]
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio))))
        return ordered[index]

    def _label_escape(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _metric_name(self, value: str) -> str:
        return value.replace('"', "'")


