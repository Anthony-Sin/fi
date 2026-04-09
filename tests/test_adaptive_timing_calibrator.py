from pathlib import Path

from desktop_automation_perception.adaptive_timing_calibrator import (
    AdaptiveTimingCalibrator,
    StartupTimingBenchmark,
)
from desktop_automation_perception.models import SmartWaitRequest, WaitType


def test_adaptive_timing_calibrator_creates_baseline_on_first_run(tmp_path):
    events = []
    clock = iter([0.0, 0.1, 0.1, 0.3, 0.3, 0.35]).__next__
    calibrator = AdaptiveTimingCalibrator(
        storage_path=str(tmp_path / "timing.json"),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = calibrator.calibrate_on_startup(
        StartupTimingBenchmark(
            benchmark_id="desktop-startup",
            launch_action=lambda: events.append("launch"),
            window_ready_check=iter([False, True]).__next__,
            click_action=lambda: events.append("click"),
            polling_interval_seconds=0.1,
        )
    )

    assert result.succeeded is True
    assert result.profile is not None
    assert result.profile.system_speed_factor == 1.0
    assert events == ["launch", "click"]
    assert Path(tmp_path / "timing.json").exists()


def test_adaptive_timing_calibrator_computes_speed_factor_against_stored_baseline(tmp_path):
    calibrator = AdaptiveTimingCalibrator(
        storage_path=str(tmp_path / "timing.json"),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.1, 0.1, 0.2, 0.2, 0.25]).__next__,
    )
    calibrator.calibrate_on_startup(
        StartupTimingBenchmark(
            benchmark_id="desktop-startup",
            launch_action=lambda: None,
            window_ready_check=lambda: True,
            click_action=lambda: None,
            polling_interval_seconds=0.1,
        )
    )

    slower = AdaptiveTimingCalibrator(
        storage_path=str(tmp_path / "timing.json"),
        sleep_fn=lambda _: None,
        monotonic_fn=iter([0.0, 0.2, 0.2, 0.6, 0.6, 0.8]).__next__,
    )
    result = slower.calibrate_on_startup(
        StartupTimingBenchmark(
            benchmark_id="desktop-startup",
            launch_action=lambda: None,
            window_ready_check=iter([False, False, False, True]).__next__,
            click_action=lambda: None,
            polling_interval_seconds=0.1,
        )
    )

    assert result.succeeded is True
    assert result.profile is not None
    assert result.profile.system_speed_factor > 1.0
    assert slower.adjusted_timeout_seconds(2.0) > 2.0


def test_adaptive_timing_calibrator_applies_speed_factor_to_wait_requests(tmp_path):
    calibrator = AdaptiveTimingCalibrator(
        storage_path=str(tmp_path / "timing.json"),
        session_profile=type(
            "Profile",
            (),
            {
                "benchmark_id": "desktop-startup",
                "system_speed_factor": 1.5,
            },
        )(),
    )

    adjusted = calibrator.apply_to_wait_request(
        SmartWaitRequest(
            wait_id="wait-1",
            wait_type=WaitType.TEXT_VISIBLE,
            timeout_seconds=4.0,
            polling_interval_seconds=0.2,
            target_text="Ready",
        )
    )

    assert adjusted.timeout_seconds == 6.0
    assert adjusted.polling_interval_seconds > 0.2
