from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from desktop_automation_agent.models import (
    NotificationEventType,
    SLAAlertRecord,
    SLAComplianceSnapshot,
    SLADailyPerformanceReport,
    SLAMonitorResult,
    SLARecordedStepDuration,
    SLARunRecord,
    SLASlowestStepContribution,
    SLAWorkflowConfiguration,
)


@dataclass(slots=True)
class SLAMonitor:
    storage_path: str
    notification_dispatcher: object | None = None
    default_compliance_threshold: float = 0.95
    alert_window_seconds: float = 86400.0
    now_fn: object = utc_now

    def configure_workflow_type(
        self,
        *,
        workflow_type: str,
        expected_completion_time_seconds: float,
        compliance_threshold: float | None = None,
        description: str | None = None,
    ) -> SLAMonitorResult:
        payload = self._load_payload()
        config = SLAWorkflowConfiguration(
            workflow_type=workflow_type,
            expected_completion_time_seconds=float(expected_completion_time_seconds),
            compliance_threshold=float(compliance_threshold if compliance_threshold is not None else self.default_compliance_threshold),
            description=description,
        )
        configs = [item for item in self._deserialize_configurations(payload.get("configurations", [])) if item.workflow_type != workflow_type]
        configs.append(config)
        configs.sort(key=lambda item: item.workflow_type)
        payload["configurations"] = [self._serialize_configuration(item) for item in configs]
        self._save_payload(payload)
        return SLAMonitorResult(succeeded=True, configuration=config, configurations=configs)

    def list_configurations(self) -> SLAMonitorResult:
        configs = self._deserialize_configurations(self._load_payload().get("configurations", []))
        configs.sort(key=lambda item: item.workflow_type)
        return SLAMonitorResult(succeeded=True, configurations=configs)

    def record_workflow_run(
        self,
        *,
        workflow_id: str,
        workflow_type: str,
        completion_time_seconds: float,
        step_durations: dict[str, float] | list[SLARecordedStepDuration] | None = None,
        timestamp: datetime | None = None,
    ) -> SLAMonitorResult:
        payload = self._load_payload()
        config = self._configuration_for_type(workflow_type, payload)
        if config is None:
            return SLAMonitorResult(succeeded=False, reason=f"No SLA configuration found for workflow type '{workflow_type}'.")

        run = SLARunRecord(
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            completion_time_seconds=float(completion_time_seconds),
            met_sla=float(completion_time_seconds) <= config.expected_completion_time_seconds,
            timestamp=timestamp or self.now(),
            step_durations=self._normalize_step_durations(step_durations),
        )
        runs = self._deserialize_runs(payload.get("runs", []))
        runs.append(run)
        payload["runs"] = [self._serialize_run(item) for item in runs]

        snapshots = self._build_snapshots(
            configurations=self._deserialize_configurations(payload.get("configurations", [])),
            runs=runs,
            alerts=self._deserialize_alerts(payload.get("alerts", [])),
            as_of=run.timestamp,
            window_seconds=self.alert_window_seconds,
            workflow_types=(workflow_type,),
        )
        snapshot = snapshots[0]
        alert = None
        if snapshot.alert_needed:
            existing_alerts = self._deserialize_alerts(payload.get("alerts", []))
            alert = SLAAlertRecord(
                workflow_type=workflow_type,
                compliance_rate=snapshot.compliance_rate,
                threshold=snapshot.compliance_threshold,
                run_count=snapshot.run_count,
                triggered_at=run.timestamp,
            )
            if not self._has_matching_alert(existing_alerts, alert):
                alert.notification_sent = self._dispatch_alert(alert, config=config, snapshot=snapshot)
                existing_alerts.append(alert)
                payload["alerts"] = [self._serialize_alert(item) for item in existing_alerts]
            else:
                alert = next(item for item in existing_alerts if self._has_matching_alert([item], alert))

        self._save_payload(payload)
        return SLAMonitorResult(
            succeeded=True,
            configuration=config,
            run_record=run,
            snapshot=snapshot,
            alert=alert,
        )

    def get_compliance_snapshot(
        self,
        *,
        workflow_type: str,
        window_seconds: float | None = None,
        as_of: datetime | None = None,
    ) -> SLAMonitorResult:
        payload = self._load_payload()
        config = self._configuration_for_type(workflow_type, payload)
        if config is None:
            return SLAMonitorResult(succeeded=False, reason=f"No SLA configuration found for workflow type '{workflow_type}'.")
        snapshot = self._build_snapshots(
            configurations=[config],
            runs=self._deserialize_runs(payload.get("runs", [])),
            alerts=self._deserialize_alerts(payload.get("alerts", [])),
            as_of=as_of or self.now(),
            window_seconds=float(window_seconds or self.alert_window_seconds),
            workflow_types=(workflow_type,),
        )[0]
        return SLAMonitorResult(succeeded=True, configuration=config, snapshot=snapshot)

    def generate_daily_report(
        self,
        *,
        report_date: datetime | None = None,
    ) -> SLAMonitorResult:
        payload = self._load_payload()
        configs = self._deserialize_configurations(payload.get("configurations", []))
        if not configs:
            return SLAMonitorResult(succeeded=False, reason="No SLA configurations are available.")
        report_date = report_date or self.now()
        day_start = datetime(report_date.year, report_date.month, report_date.day)
        day_end = day_start + timedelta(days=1) - timedelta(microseconds=1)
        runs = [item for item in self._deserialize_runs(payload.get("runs", [])) if day_start <= item.timestamp <= day_end]
        alerts = [item for item in self._deserialize_alerts(payload.get("alerts", [])) if day_start <= item.triggered_at <= day_end]
        summaries = self._build_snapshots(
            configurations=configs,
            runs=runs,
            alerts=alerts,
            as_of=day_end,
            window_seconds=86400.0,
            workflow_types=tuple(item.workflow_type for item in configs),
        )
        body = self._render_daily_report(report_date=day_start, summaries=summaries, alerts=alerts)
        report = SLADailyPerformanceReport(
            report_date=day_start.date().isoformat(),
            generated_at=self.now(),
            workflow_summaries=summaries,
            alerts=alerts,
            body_text=body,
        )
        return SLAMonitorResult(succeeded=True, snapshots=summaries, alerts=alerts, report=report)

    def now(self) -> datetime:
        return self.now_fn() if callable(self.now_fn) else utc_now()

    def _build_snapshots(
        self,
        *,
        configurations: list[SLAWorkflowConfiguration],
        runs: list[SLARunRecord],
        alerts: list[SLAAlertRecord],
        as_of: datetime,
        window_seconds: float,
        workflow_types: tuple[str, ...],
    ) -> list[SLAComplianceSnapshot]:
        cutoff = as_of - timedelta(seconds=window_seconds)
        latest_alert_by_type: dict[str, SLAAlertRecord] = {}
        for alert in alerts:
            prior = latest_alert_by_type.get(alert.workflow_type)
            if prior is None or alert.triggered_at > prior.triggered_at:
                latest_alert_by_type[alert.workflow_type] = alert

        snapshots: list[SLAComplianceSnapshot] = []
        for config in configurations:
            if config.workflow_type not in workflow_types:
                continue
            filtered_runs = [item for item in runs if item.workflow_type == config.workflow_type and cutoff <= item.timestamp <= as_of]
            met_sla_count = sum(1 for item in filtered_runs if item.met_sla)
            compliance_rate = (met_sla_count / len(filtered_runs)) if filtered_runs else 0.0
            miss_runs = [item for item in filtered_runs if not item.met_sla]
            snapshots.append(
                SLAComplianceSnapshot(
                    workflow_type=config.workflow_type,
                    expected_completion_time_seconds=config.expected_completion_time_seconds,
                    compliance_threshold=config.compliance_threshold,
                    run_count=len(filtered_runs),
                    met_sla_count=met_sla_count,
                    compliance_rate=compliance_rate,
                    average_completion_time_seconds=mean(item.completion_time_seconds for item in filtered_runs) if filtered_runs else 0.0,
                    sla_miss_count=len(miss_runs),
                    slowest_miss_steps=self._slowest_steps_for_misses(miss_runs),
                    latest_alert=latest_alert_by_type.get(config.workflow_type),
                    alert_needed=bool(filtered_runs) and compliance_rate < config.compliance_threshold,
                )
            )
        snapshots.sort(key=lambda item: item.workflow_type)
        return snapshots

    def _slowest_steps_for_misses(self, miss_runs: list[SLARunRecord]) -> list[SLASlowestStepContribution]:
        buckets: dict[str, list[float]] = {}
        for run in miss_runs:
            for step in run.step_durations:
                buckets.setdefault(step.step_name, []).append(step.duration_seconds)
        contributions = [
            SLASlowestStepContribution(
                step_name=step_name,
                miss_count=len(values),
                average_duration_seconds=mean(values),
                total_duration_seconds=sum(values),
            )
            for step_name, values in buckets.items()
        ]
        contributions.sort(key=lambda item: (-item.average_duration_seconds, -item.total_duration_seconds, item.step_name))
        return contributions[:5]

    def _dispatch_alert(
        self,
        alert: SLAAlertRecord,
        *,
        config: SLAWorkflowConfiguration,
        snapshot: SLAComplianceSnapshot,
    ) -> bool:
        if self.notification_dispatcher is None:
            return False
        result = self.notification_dispatcher.dispatch(
            workflow_id=alert.workflow_type,
            event_type=NotificationEventType.ANOMALY,
            description=(
                f"SLA compliance for {alert.workflow_type} dropped to {alert.compliance_rate:.2%} "
                f"against threshold {alert.threshold:.2%}."
            ),
            context_data={
                "workflow_type": alert.workflow_type,
                "expected_completion_time_seconds": config.expected_completion_time_seconds,
                "compliance_rate": alert.compliance_rate,
                "threshold": alert.threshold,
                "run_count": alert.run_count,
                "slowest_miss_steps": [
                    {
                        "step_name": item.step_name,
                        "average_duration_seconds": item.average_duration_seconds,
                        "miss_count": item.miss_count,
                    }
                    for item in snapshot.slowest_miss_steps
                ],
            },
            step_name="sla_monitor_alert",
        )
        return bool(result.succeeded)

    def _render_daily_report(
        self,
        *,
        report_date: datetime,
        summaries: list[SLAComplianceSnapshot],
        alerts: list[SLAAlertRecord],
    ) -> str:
        lines = [
            f"SLA Daily Performance Report: {report_date.date().isoformat()}",
            "",
        ]
        for summary in summaries:
            lines.extend(
                [
                    f"Workflow Type: {summary.workflow_type}",
                    f"Expected Completion Time: {summary.expected_completion_time_seconds:.2f}s",
                    f"Compliance Threshold: {summary.compliance_threshold:.2%}",
                    f"Runs Evaluated: {summary.run_count}",
                    f"Runs Meeting SLA: {summary.met_sla_count}",
                    f"SLA Compliance: {summary.compliance_rate:.2%}",
                    f"Average Completion Time: {summary.average_completion_time_seconds:.2f}s",
                    "Slowest Steps In SLA Misses:",
                ]
            )
            if summary.slowest_miss_steps:
                for step in summary.slowest_miss_steps:
                    lines.append(
                        f"- {step.step_name}: avg {step.average_duration_seconds:.2f}s across {step.miss_count} miss(es)"
                    )
            else:
                lines.append("- None")
            lines.append("")
        lines.append("Alerts:")
        if alerts:
            for alert in alerts:
                lines.append(
                    f"- {alert.workflow_type}: compliance {alert.compliance_rate:.2%} below {alert.threshold:.2%}"
                )
        else:
            lines.append("- None")
        lines.append("")
        return "\n".join(lines)

    def _configuration_for_type(self, workflow_type: str, payload: dict) -> SLAWorkflowConfiguration | None:
        for config in self._deserialize_configurations(payload.get("configurations", [])):
            if config.workflow_type == workflow_type:
                return config
        return None

    def _normalize_step_durations(
        self,
        step_durations: dict[str, float] | list[SLARecordedStepDuration] | None,
    ) -> list[SLARecordedStepDuration]:
        if step_durations is None:
            return []
        if isinstance(step_durations, dict):
            return [
                SLARecordedStepDuration(step_name=step_name, duration_seconds=float(duration))
                for step_name, duration in step_durations.items()
            ]
        return [
            item if isinstance(item, SLARecordedStepDuration) else SLARecordedStepDuration(
                step_name=str(getattr(item, "step_name", item["step_name"])),
                duration_seconds=float(getattr(item, "duration_seconds", item["duration_seconds"])),
            )
            for item in step_durations
        ]

    def _has_matching_alert(self, existing_alerts: list[SLAAlertRecord], candidate: SLAAlertRecord) -> bool:
        return any(
            item.workflow_type == candidate.workflow_type
            and item.run_count == candidate.run_count
            and abs(item.compliance_rate - candidate.compliance_rate) < 1e-9
            and abs(item.threshold - candidate.threshold) < 1e-9
            for item in existing_alerts
        )

    def _load_payload(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {"configurations": [], "runs": [], "alerts": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_payload(self, payload: dict) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _deserialize_configurations(self, payloads: list[dict]) -> list[SLAWorkflowConfiguration]:
        return [
            SLAWorkflowConfiguration(
                workflow_type=item["workflow_type"],
                expected_completion_time_seconds=float(item["expected_completion_time_seconds"]),
                compliance_threshold=float(item.get("compliance_threshold", self.default_compliance_threshold)),
                description=item.get("description"),
            )
            for item in payloads
        ]

    def _deserialize_runs(self, payloads: list[dict]) -> list[SLARunRecord]:
        return [
            SLARunRecord(
                workflow_id=item["workflow_id"],
                workflow_type=item["workflow_type"],
                completion_time_seconds=float(item["completion_time_seconds"]),
                met_sla=bool(item["met_sla"]),
                timestamp=datetime.fromisoformat(item["timestamp"]),
                step_durations=[
                    SLARecordedStepDuration(
                        step_name=step["step_name"],
                        duration_seconds=float(step["duration_seconds"]),
                    )
                    for step in item.get("step_durations", [])
                ],
            )
            for item in payloads
        ]

    def _deserialize_alerts(self, payloads: list[dict]) -> list[SLAAlertRecord]:
        return [
            SLAAlertRecord(
                workflow_type=item["workflow_type"],
                compliance_rate=float(item["compliance_rate"]),
                threshold=float(item["threshold"]),
                run_count=int(item["run_count"]),
                triggered_at=datetime.fromisoformat(item["triggered_at"]),
                notification_sent=bool(item.get("notification_sent", False)),
            )
            for item in payloads
        ]

    def _serialize_configuration(self, config: SLAWorkflowConfiguration) -> dict:
        return {
            "workflow_type": config.workflow_type,
            "expected_completion_time_seconds": config.expected_completion_time_seconds,
            "compliance_threshold": config.compliance_threshold,
            "description": config.description,
        }

    def _serialize_run(self, run: SLARunRecord) -> dict:
        return {
            "workflow_id": run.workflow_id,
            "workflow_type": run.workflow_type,
            "completion_time_seconds": run.completion_time_seconds,
            "met_sla": run.met_sla,
            "timestamp": run.timestamp.isoformat(),
            "step_durations": [
                {
                    "step_name": item.step_name,
                    "duration_seconds": item.duration_seconds,
                }
                for item in run.step_durations
            ],
        }

    def _serialize_alert(self, alert: SLAAlertRecord) -> dict:
        return {
            "workflow_type": alert.workflow_type,
            "compliance_rate": alert.compliance_rate,
            "threshold": alert.threshold,
            "run_count": alert.run_count,
            "triggered_at": alert.triggered_at.isoformat(),
            "notification_sent": alert.notification_sent,
        }


