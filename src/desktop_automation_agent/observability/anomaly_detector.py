from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev
from typing import Callable

from desktop_automation_agent.models import (
    AnomalyCategory,
    AnomalyDetectionResult,
    AnomalyRecord,
    ResourceUsageSnapshot,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AnomalyDetector:
    storage_path: str
    alert_callback: Callable[[AnomalyRecord], None] | None = None
    pause_callback: Callable[[AnomalyRecord], None] | None = None
    slow_step_min_samples: int = 3
    slow_step_stddev_multiplier: float = 2.0
    slow_step_ratio_multiplier: float = 1.75
    repeated_failure_threshold: int = 3
    cpu_threshold_percent: float = 90.0
    memory_threshold_percent: float = 90.0

    def record_step_execution(
        self,
        *,
        step_id: str,
        execution_time_seconds: float,
        application_name: str | None = None,
        pause_on_detection: bool = False,
    ) -> AnomalyDetectionResult:
        snapshot = self._load_snapshot()
        timings = list(snapshot["step_timings"].get(step_id, []))
        anomalies: list[AnomalyRecord] = []

        if len(timings) >= self.slow_step_min_samples:
            baseline = mean(timings)
            deviation = pstdev(timings) if len(timings) > 1 else 0.0
            threshold = max(
                baseline * self.slow_step_ratio_multiplier,
                baseline + (deviation * self.slow_step_stddev_multiplier),
            )
            if execution_time_seconds > threshold:
                anomalies.append(
                    self._make_record(
                        category=AnomalyCategory.SLOW_STEP_EXECUTION,
                        step_id=step_id,
                        application_name=application_name,
                        detail="Step execution time exceeded the historical baseline.",
                        execution_time_seconds=execution_time_seconds,
                        baseline_seconds=baseline,
                        observed_value=execution_time_seconds,
                        threshold_value=threshold,
                        pause_requested=pause_on_detection,
                    )
                )

        timings.append(float(execution_time_seconds))
        snapshot["step_timings"][step_id] = timings
        self._persist_records(snapshot, anomalies)
        return AnomalyDetectionResult(
            succeeded=True,
            anomalies=anomalies,
            pause_requested=any(item.pause_requested for item in anomalies),
        )

    def record_step_failure(
        self,
        *,
        step_id: str,
        application_name: str | None = None,
        reason: str | None = None,
        pause_on_detection: bool = False,
    ) -> AnomalyDetectionResult:
        snapshot = self._load_snapshot()
        failure_count = int(snapshot["failure_counts"].get(step_id, 0)) + 1
        snapshot["failure_counts"][step_id] = failure_count
        anomalies: list[AnomalyRecord] = []

        if failure_count >= self.repeated_failure_threshold:
            anomalies.append(
                self._make_record(
                    category=AnomalyCategory.REPEATED_STEP_FAILURE,
                    step_id=step_id,
                    application_name=application_name,
                    detail=reason or "Step failure repeated beyond the configured threshold.",
                    observed_value=float(failure_count),
                    threshold_value=float(self.repeated_failure_threshold),
                    pause_requested=pause_on_detection,
                )
            )

        self._persist_records(snapshot, anomalies)
        return AnomalyDetectionResult(
            succeeded=True,
            anomalies=anomalies,
            pause_requested=any(item.pause_requested for item in anomalies),
        )

    def clear_step_failure(
        self,
        step_id: str,
    ) -> None:
        snapshot = self._load_snapshot()
        if step_id in snapshot["failure_counts"]:
            snapshot["failure_counts"][step_id] = 0
            self._save_snapshot(snapshot)

    def detect_application_crash(
        self,
        *,
        application_name: str,
        visible_applications: list[str],
        expected_signature: str | None = None,
        pause_on_detection: bool = False,
    ) -> AnomalyDetectionResult:
        signature = (expected_signature or application_name).casefold()
        present = any(signature in item.casefold() for item in visible_applications)
        if present:
            return AnomalyDetectionResult(succeeded=True)

        anomaly = self._make_record(
            category=AnomalyCategory.APPLICATION_CRASH,
            application_name=application_name,
            detail="Expected application is no longer visible in the active desktop state.",
            pause_requested=pause_on_detection,
        )
        self._append_record(anomaly)
        return AnomalyDetectionResult(
            succeeded=True,
            anomalies=[anomaly],
            pause_requested=anomaly.pause_requested,
        )

    def detect_ui_structure_change(
        self,
        *,
        step_id: str,
        structure_signature: str,
        application_name: str | None = None,
        pause_on_detection: bool = False,
    ) -> AnomalyDetectionResult:
        snapshot = self._load_snapshot()
        previous_signature = snapshot["ui_signatures"].get(step_id)
        snapshot["ui_signatures"][step_id] = structure_signature
        anomalies: list[AnomalyRecord] = []

        if previous_signature is not None and previous_signature != structure_signature:
            anomalies.append(
                self._make_record(
                    category=AnomalyCategory.UI_STRUCTURE_CHANGE,
                    step_id=step_id,
                    application_name=application_name,
                    detail="Observed UI structure signature changed unexpectedly.",
                    pause_requested=pause_on_detection,
                )
            )

        self._persist_records(snapshot, anomalies)
        return AnomalyDetectionResult(
            succeeded=True,
            anomalies=anomalies,
            pause_requested=any(item.pause_requested for item in anomalies),
        )

    def detect_resource_exhaustion(
        self,
        *,
        resource_usage: ResourceUsageSnapshot,
        step_id: str | None = None,
        application_name: str | None = None,
        pause_on_detection: bool = False,
    ) -> AnomalyDetectionResult:
        anomalies: list[AnomalyRecord] = []
        detail_parts: list[str] = []
        observed = 0.0
        threshold = 0.0

        if resource_usage.cpu_percent >= self.cpu_threshold_percent:
            detail_parts.append(f"CPU usage {resource_usage.cpu_percent:.1f}% exceeded threshold.")
            observed = max(observed, resource_usage.cpu_percent)
            threshold = max(threshold, self.cpu_threshold_percent)
        if resource_usage.memory_percent >= self.memory_threshold_percent:
            detail_parts.append(f"Memory usage {resource_usage.memory_percent:.1f}% exceeded threshold.")
            observed = max(observed, resource_usage.memory_percent)
            threshold = max(threshold, self.memory_threshold_percent)

        if detail_parts:
            anomalies.append(
                self._make_record(
                    category=AnomalyCategory.RESOURCE_EXHAUSTION,
                    step_id=step_id,
                    application_name=application_name or resource_usage.process_name,
                    detail=" ".join(detail_parts),
                    observed_value=observed,
                    threshold_value=threshold,
                    pause_requested=pause_on_detection,
                )
            )
            self._append_record(anomalies[0])

        return AnomalyDetectionResult(
            succeeded=True,
            anomalies=anomalies,
            pause_requested=any(item.pause_requested for item in anomalies),
        )

    def list_records(self) -> list[AnomalyRecord]:
        snapshot = self._load_snapshot()
        return [self._deserialize_record(item) for item in snapshot["records"]]

    def _persist_records(
        self,
        snapshot: dict,
        anomalies: list[AnomalyRecord],
    ) -> None:
        if anomalies:
            snapshot["records"].extend(self._serialize_record(item) for item in anomalies)
            for anomaly in anomalies:
                self._emit(anomaly)
        self._save_snapshot(snapshot)

    def _append_record(self, anomaly: AnomalyRecord) -> None:
        snapshot = self._load_snapshot()
        snapshot["records"].append(self._serialize_record(anomaly))
        self._emit(anomaly)
        self._save_snapshot(snapshot)

    def _emit(self, anomaly: AnomalyRecord) -> None:
        if self.alert_callback is not None:
            self.alert_callback(anomaly)
            anomaly.alert_sent = True
        if anomaly.pause_requested and self.pause_callback is not None:
            self.pause_callback(anomaly)

    def _make_record(
        self,
        *,
        category: AnomalyCategory,
        step_id: str | None = None,
        application_name: str | None = None,
        detail: str | None = None,
        execution_time_seconds: float | None = None,
        baseline_seconds: float | None = None,
        observed_value: float | None = None,
        threshold_value: float | None = None,
        pause_requested: bool = False,
    ) -> AnomalyRecord:
        return AnomalyRecord(
            category=category,
            step_id=step_id,
            application_name=application_name,
            detail=detail,
            execution_time_seconds=execution_time_seconds,
            baseline_seconds=baseline_seconds,
            observed_value=observed_value,
            threshold_value=threshold_value,
            pause_requested=pause_requested,
        )

    def _load_snapshot(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {
                "step_timings": {},
                "failure_counts": {},
                "ui_signatures": {},
                "records": [],
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load anomaly data from {self.storage_path}: {e}")
            return {
                "step_timings": {},
                "failure_counts": {},
                "ui_signatures": {},
                "records": [],
            }
        payload.setdefault("step_timings", {})
        payload.setdefault("failure_counts", {})
        payload.setdefault("ui_signatures", {})
        payload.setdefault("records", [])
        return payload

    def _save_snapshot(self, snapshot: dict) -> None:
        try:
            path = Path(self.storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save anomaly data to {self.storage_path}: {e}")

    def _serialize_record(self, record: AnomalyRecord) -> dict:
        return {
            "category": record.category.value,
            "step_id": record.step_id,
            "application_name": record.application_name,
            "detail": record.detail,
            "execution_time_seconds": record.execution_time_seconds,
            "baseline_seconds": record.baseline_seconds,
            "observed_value": record.observed_value,
            "threshold_value": record.threshold_value,
            "pause_requested": record.pause_requested,
            "alert_sent": record.alert_sent,
            "timestamp": record.timestamp.isoformat(),
        }

    def _deserialize_record(self, payload: dict) -> AnomalyRecord:
        from datetime import datetime

        return AnomalyRecord(
            category=AnomalyCategory(payload["category"]),
            step_id=payload.get("step_id"),
            application_name=payload.get("application_name"),
            detail=payload.get("detail"),
            execution_time_seconds=payload.get("execution_time_seconds"),
            baseline_seconds=payload.get("baseline_seconds"),
            observed_value=payload.get("observed_value"),
            threshold_value=payload.get("threshold_value"),
            pause_requested=bool(payload.get("pause_requested", False)),
            alert_sent=bool(payload.get("alert_sent", False)),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )
