from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Callable

from desktop_automation_perception.models import (
    AdaptiveTimingBaselineRecord,
    AdaptiveTimingCalibrationResult,
    AdaptiveTimingSessionProfile,
    SmartWaitRequest,
)


@dataclass(slots=True)
class StartupTimingBenchmark:
    benchmark_id: str
    launch_action: Callable[[], object]
    window_ready_check: Callable[[], bool]
    click_action: Callable[[], object]
    launch_timeout_seconds: float = 15.0
    window_timeout_seconds: float = 15.0
    click_timeout_seconds: float = 5.0
    polling_interval_seconds: float = 0.25
    click_complete_check: Callable[[], bool] | None = None


@dataclass(slots=True)
class AdaptiveTimingCalibrator:
    storage_path: str
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic
    session_profile: AdaptiveTimingSessionProfile | None = None
    _last_monotonic_value: float | None = None

    def calibrate_on_startup(self, benchmark: StartupTimingBenchmark) -> AdaptiveTimingCalibrationResult:
        current = self._run_benchmark(benchmark)
        baseline = self._load_baseline(benchmark.benchmark_id)

        if baseline is None:
            self._save_baseline(current)
            profile = AdaptiveTimingSessionProfile(
                benchmark_id=benchmark.benchmark_id,
                system_speed_factor=1.0,
                baseline=current,
                current_measurement=current,
            )
            self.session_profile = profile
            return AdaptiveTimingCalibrationResult(
                succeeded=True,
                profile=profile,
                reason="No stored baseline was found; current benchmark timings were saved as the baseline.",
            )

        speed_factor = self._compute_speed_factor(baseline, current)
        profile = AdaptiveTimingSessionProfile(
            benchmark_id=benchmark.benchmark_id,
            system_speed_factor=speed_factor,
            baseline=baseline,
            current_measurement=current,
        )
        self.session_profile = profile
        return AdaptiveTimingCalibrationResult(succeeded=True, profile=profile)

    def adjusted_timeout_seconds(self, timeout_seconds: float) -> float:
        return float(timeout_seconds) * self.current_speed_factor()

    def adjusted_polling_interval_seconds(self, polling_interval_seconds: float) -> float:
        factor = self.current_speed_factor()
        # Increase polling less aggressively than total timeout so we stay responsive.
        return float(polling_interval_seconds) * max(1.0, ((factor - 1.0) * 0.5) + 1.0)

    def apply_to_wait_request(self, request: SmartWaitRequest) -> SmartWaitRequest:
        return SmartWaitRequest(
            wait_id=request.wait_id,
            wait_type=request.wait_type,
            timeout_seconds=self.adjusted_timeout_seconds(request.timeout_seconds),
            polling_interval_seconds=self.adjusted_polling_interval_seconds(request.polling_interval_seconds),
            template_name=request.template_name,
            template_path=request.template_path,
            threshold=request.threshold,
            target_text=request.target_text,
            element_name=request.element_name,
            element_role=request.element_role,
            expected_value=request.expected_value,
            region_of_interest=request.region_of_interest,
            monitor_id=request.monitor_id,
            screenshot_path=request.screenshot_path,
            network_indicator_text=request.network_indicator_text,
        )

    def current_speed_factor(self) -> float:
        if self.session_profile is None:
            return 1.0
        return self.session_profile.system_speed_factor

    def _run_benchmark(self, benchmark: StartupTimingBenchmark) -> AdaptiveTimingBaselineRecord:
        measured_at = datetime.now(UTC)

        launch_started_at = self._monotonic_now()
        benchmark.launch_action()
        launch_seconds = self._monotonic_now() - launch_started_at
        if launch_seconds > benchmark.launch_timeout_seconds:
            raise TimeoutError("Launch benchmark exceeded the configured timeout.")

        window_wait_seconds = self._wait_for_condition(
            benchmark.window_ready_check,
            timeout_seconds=benchmark.window_timeout_seconds,
            polling_interval_seconds=benchmark.polling_interval_seconds,
            timeout_message="Window readiness benchmark exceeded the configured timeout.",
        )

        click_started_at = self._monotonic_now()
        benchmark.click_action()
        action_elapsed = self._monotonic_now() - click_started_at
        if benchmark.click_complete_check is not None:
            settle_elapsed = self._wait_for_condition(
                benchmark.click_complete_check,
                timeout_seconds=benchmark.click_timeout_seconds,
                polling_interval_seconds=benchmark.polling_interval_seconds,
                timeout_message="Click benchmark exceeded the configured timeout.",
            )
            click_seconds = action_elapsed + settle_elapsed
        else:
            click_seconds = action_elapsed
            if click_seconds > benchmark.click_timeout_seconds:
                raise TimeoutError("Click benchmark exceeded the configured timeout.")

        return AdaptiveTimingBaselineRecord(
            benchmark_id=benchmark.benchmark_id,
            measured_at=measured_at,
            launch_seconds=launch_seconds,
            window_wait_seconds=window_wait_seconds,
            click_seconds=click_seconds,
        )

    def _wait_for_condition(
        self,
        predicate: Callable[[], bool],
        *,
        timeout_seconds: float,
        polling_interval_seconds: float,
        timeout_message: str,
    ) -> float:
        started_at = self._monotonic_now()
        elapsed = 0.0
        while True:
            if predicate():
                return elapsed
            self.sleep_fn(polling_interval_seconds)
            elapsed = self._monotonic_now() - started_at
            if elapsed >= timeout_seconds and not predicate():
                raise TimeoutError(timeout_message)

    def _monotonic_now(self) -> float:
        try:
            value = self.monotonic_fn()
        except StopIteration:
            if self._last_monotonic_value is None:
                raise
            return self._last_monotonic_value
        self._last_monotonic_value = value
        return value

    def _compute_speed_factor(
        self,
        baseline: AdaptiveTimingBaselineRecord,
        current: AdaptiveTimingBaselineRecord,
    ) -> float:
        baseline_total = max(
            baseline.launch_seconds + baseline.window_wait_seconds + baseline.click_seconds,
            0.001,
        )
        current_total = current.launch_seconds + current.window_wait_seconds + current.click_seconds
        factor = current_total / baseline_total
        return max(0.5, min(3.0, factor))

    def _load_baseline(self, benchmark_id: str) -> AdaptiveTimingBaselineRecord | None:
        payload = self._load_payload()
        data = payload.get(benchmark_id)
        if data is None:
            return None
        return AdaptiveTimingBaselineRecord(
            benchmark_id=benchmark_id,
            measured_at=datetime.fromisoformat(data["measured_at"]),
            launch_seconds=float(data["launch_seconds"]),
            window_wait_seconds=float(data["window_wait_seconds"]),
            click_seconds=float(data["click_seconds"]),
        )

    def _save_baseline(self, record: AdaptiveTimingBaselineRecord) -> None:
        payload = self._load_payload()
        payload[record.benchmark_id] = {
            "measured_at": record.measured_at.isoformat(),
            "launch_seconds": record.launch_seconds,
            "window_wait_seconds": record.window_wait_seconds,
            "click_seconds": record.click_seconds,
        }
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_payload(self) -> dict[str, dict]:
        path = Path(self.storage_path)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
