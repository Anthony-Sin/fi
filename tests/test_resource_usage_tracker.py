from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_agent.observability import ResourceUsageTracker


def test_resource_usage_tracker_aggregates_per_workflow_type_and_account(tmp_path):
    now = datetime(2026, 4, 9, 14, 0, 0)
    tracker = ResourceUsageTracker(
        storage_path=str(Path(tmp_path) / "resource_usage.json"),
        now_fn=lambda: now,
    )

    tracker.record_run(
        workflow_id="wf-1",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=12.0,
        peak_memory_mb=256.0,
        api_call_count=4,
        llm_token_count=1000,
        run_duration_seconds=30.0,
        screenshot_count=5,
        timestamp=now - timedelta(minutes=40),
    )
    tracker.record_run(
        workflow_id="wf-2",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=18.0,
        peak_memory_mb=320.0,
        api_call_count=6,
        llm_token_count=1200,
        run_duration_seconds=45.0,
        screenshot_count=7,
        timestamp=now - timedelta(minutes=20),
    )
    tracker.record_run(
        workflow_id="wf-3",
        workflow_type="table_extraction",
        account_name="acct-b",
        cpu_time_seconds=6.0,
        peak_memory_mb=128.0,
        api_call_count=2,
        llm_token_count=0,
        run_duration_seconds=15.0,
        screenshot_count=3,
        timestamp=now - timedelta(minutes=10),
    )

    result = tracker.generate_trend_snapshot(window_seconds=3600, baseline_window_seconds=3600, as_of=now)

    assert result.succeeded is True
    assert result.snapshot is not None
    workflow_items = {item.group_key: item for item in result.snapshot.workflow_type_aggregates}
    assert workflow_items["invoice_processing"].run_count == 2
    assert workflow_items["invoice_processing"].average_cpu_time_seconds == 15.0
    assert workflow_items["invoice_processing"].average_api_call_count == 5.0
    assert workflow_items["invoice_processing"].average_llm_token_count == 1100.0
    account_items = {item.group_key: item for item in result.snapshot.account_aggregates}
    assert account_items["acct-a"].total_run_duration_seconds == 75.0
    assert account_items["acct-a"].total_screenshot_count == 12
    assert account_items["acct-b"].peak_memory_mb == 128.0


def test_resource_usage_tracker_flags_workflow_type_regressions_against_baseline(tmp_path):
    now = datetime(2026, 4, 9, 16, 0, 0)
    tracker = ResourceUsageTracker(
        storage_path=str(Path(tmp_path) / "resource_usage.json"),
        now_fn=lambda: now,
        degradation_threshold_ratio=0.25,
    )

    tracker.record_run(
        workflow_id="wf-old-1",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=10.0,
        peak_memory_mb=200.0,
        api_call_count=3,
        llm_token_count=800,
        run_duration_seconds=20.0,
        screenshot_count=4,
        timestamp=now - timedelta(hours=30),
    )
    tracker.record_run(
        workflow_id="wf-old-2",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=12.0,
        peak_memory_mb=210.0,
        api_call_count=3,
        llm_token_count=900,
        run_duration_seconds=22.0,
        screenshot_count=4,
        timestamp=now - timedelta(hours=28),
    )
    tracker.record_run(
        workflow_id="wf-new-1",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=20.0,
        peak_memory_mb=340.0,
        api_call_count=7,
        llm_token_count=1800,
        run_duration_seconds=40.0,
        screenshot_count=9,
        timestamp=now - timedelta(hours=2),
    )
    tracker.record_run(
        workflow_id="wf-new-2",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=18.0,
        peak_memory_mb=360.0,
        api_call_count=8,
        llm_token_count=2000,
        run_duration_seconds=44.0,
        screenshot_count=10,
        timestamp=now - timedelta(hours=1),
    )

    result = tracker.generate_trend_snapshot(window_seconds=86400, baseline_window_seconds=86400, as_of=now)

    assert result.succeeded is True
    assert result.snapshot is not None
    degradations = result.snapshot.workflow_type_degradations["invoice_processing"]
    metric_names = {item.metric_name for item in degradations}
    assert "average_cpu_time_seconds" in metric_names
    assert "average_peak_memory_mb" in metric_names
    assert "average_api_call_count" in metric_names
    assert "average_llm_token_count" in metric_names
    assert "average_run_duration_seconds" in metric_names
    assert "average_screenshot_count" in metric_names


def test_resource_usage_tracker_generates_daily_report(tmp_path):
    report_time = datetime(2026, 4, 9, 20, 0, 0)
    tracker = ResourceUsageTracker(
        storage_path=str(Path(tmp_path) / "resource_usage.json"),
        now_fn=lambda: report_time,
    )

    tracker.record_run(
        workflow_id="wf-1",
        workflow_type="invoice_processing",
        account_name="acct-a",
        cpu_time_seconds=9.0,
        peak_memory_mb=220.0,
        api_call_count=4,
        llm_token_count=1500,
        run_duration_seconds=28.0,
        screenshot_count=6,
        timestamp=report_time - timedelta(hours=3),
    )
    tracker.record_run(
        workflow_id="wf-2",
        workflow_type="table_extraction",
        account_name="acct-b",
        cpu_time_seconds=4.0,
        peak_memory_mb=140.0,
        api_call_count=1,
        llm_token_count=0,
        run_duration_seconds=12.0,
        screenshot_count=2,
        timestamp=report_time - timedelta(hours=2),
    )

    result = tracker.generate_daily_report(report_date=report_time)

    assert result.succeeded is True
    assert result.report is not None
    assert result.report.report_date == "2026-04-09"
    assert result.report.snapshot is not None
    assert len(result.report.snapshot.workflow_type_aggregates) == 2
    assert "Resource Usage Trend Report: 2026-04-09" in (result.report.body_text or "")
    assert "invoice_processing" in (result.report.body_text or "")
    assert "acct-a" in (result.report.body_text or "")
