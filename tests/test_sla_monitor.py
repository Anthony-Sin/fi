from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_perception.models import NotificationEventType
from desktop_automation_perception.observability import SLAMonitor


class FakeNotificationDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return type("DispatchResult", (), {"succeeded": True})()


def test_sla_monitor_tracks_compliance_per_workflow_type_and_alerts_on_drop(tmp_path):
    dispatcher = FakeNotificationDispatcher()
    now = datetime(2026, 4, 9, 9, 0, 0)
    monitor = SLAMonitor(
        storage_path=str(Path(tmp_path) / "sla.json"),
        notification_dispatcher=dispatcher,
        alert_window_seconds=3600,
        now_fn=lambda: now,
    )

    configure = monitor.configure_workflow_type(
        workflow_type="invoice_processing",
        expected_completion_time_seconds=30.0,
        compliance_threshold=0.75,
    )

    assert configure.succeeded is True
    assert configure.configuration is not None
    assert configure.configuration.workflow_type == "invoice_processing"

    first = monitor.record_workflow_run(
        workflow_id="wf-1",
        workflow_type="invoice_processing",
        completion_time_seconds=24.0,
        step_durations={"open": 5.0, "submit": 10.0},
        timestamp=now - timedelta(minutes=30),
    )
    second = monitor.record_workflow_run(
        workflow_id="wf-2",
        workflow_type="invoice_processing",
        completion_time_seconds=32.0,
        step_durations={"open": 6.0, "submit": 14.0, "confirm": 8.0},
        timestamp=now - timedelta(minutes=20),
    )
    third = monitor.record_workflow_run(
        workflow_id="wf-3",
        workflow_type="invoice_processing",
        completion_time_seconds=35.0,
        step_durations={"open": 7.0, "submit": 16.0, "confirm": 9.0},
        timestamp=now - timedelta(minutes=10),
    )

    assert first.run_record is not None
    assert first.run_record.met_sla is True
    assert second.run_record is not None
    assert second.run_record.met_sla is False
    assert third.snapshot is not None
    assert third.snapshot.run_count == 3
    assert third.snapshot.met_sla_count == 1
    assert round(third.snapshot.compliance_rate, 4) == round(1 / 3, 4)
    assert third.snapshot.alert_needed is True
    assert third.alert is not None
    assert dispatcher.calls
    assert dispatcher.calls[0]["event_type"] is NotificationEventType.ANOMALY
    assert dispatcher.calls[0]["context_data"]["workflow_type"] == "invoice_processing"
    assert third.snapshot.slowest_miss_steps[0].step_name == "submit"


def test_sla_monitor_generates_daily_report_with_slowest_steps(tmp_path):
    report_time = datetime(2026, 4, 9, 18, 0, 0)
    monitor = SLAMonitor(
        storage_path=str(Path(tmp_path) / "sla.json"),
        now_fn=lambda: report_time,
    )
    monitor.configure_workflow_type(
        workflow_type="invoice_processing",
        expected_completion_time_seconds=30.0,
        compliance_threshold=0.8,
    )
    monitor.configure_workflow_type(
        workflow_type="table_extraction",
        expected_completion_time_seconds=20.0,
        compliance_threshold=0.9,
    )
    monitor.record_workflow_run(
        workflow_id="wf-a",
        workflow_type="invoice_processing",
        completion_time_seconds=34.0,
        step_durations={"open": 6.0, "submit": 18.0},
        timestamp=report_time - timedelta(hours=2),
    )
    monitor.record_workflow_run(
        workflow_id="wf-b",
        workflow_type="invoice_processing",
        completion_time_seconds=26.0,
        step_durations={"open": 4.0, "submit": 9.0},
        timestamp=report_time - timedelta(hours=1),
    )
    monitor.record_workflow_run(
        workflow_id="wf-c",
        workflow_type="table_extraction",
        completion_time_seconds=19.0,
        step_durations={"navigate": 5.0, "extract": 10.0},
        timestamp=report_time - timedelta(hours=3),
    )

    result = monitor.generate_daily_report(report_date=report_time)

    assert result.succeeded is True
    assert result.report is not None
    assert result.report.report_date == "2026-04-09"
    summaries = {item.workflow_type: item for item in result.report.workflow_summaries}
    assert summaries["invoice_processing"].sla_miss_count == 1
    assert summaries["invoice_processing"].slowest_miss_steps[0].step_name == "submit"
    assert summaries["table_extraction"].compliance_rate == 1.0
    assert "SLA Daily Performance Report: 2026-04-09" in (result.report.body_text or "")
    assert "Workflow Type: invoice_processing" in (result.report.body_text or "")


def test_sla_monitor_supports_independent_configuration_per_workflow_type(tmp_path):
    now = datetime(2026, 4, 9, 12, 0, 0)
    monitor = SLAMonitor(
        storage_path=str(Path(tmp_path) / "sla.json"),
        now_fn=lambda: now,
    )
    monitor.configure_workflow_type(workflow_type="login_flow", expected_completion_time_seconds=10.0, compliance_threshold=1.0)
    monitor.configure_workflow_type(workflow_type="data_export", expected_completion_time_seconds=120.0, compliance_threshold=0.5)

    monitor.record_workflow_run(
        workflow_id="wf-login",
        workflow_type="login_flow",
        completion_time_seconds=12.0,
        step_durations={"launch": 3.0, "signin": 6.0},
        timestamp=now - timedelta(minutes=5),
    )
    monitor.record_workflow_run(
        workflow_id="wf-export",
        workflow_type="data_export",
        completion_time_seconds=90.0,
        step_durations={"prepare": 20.0, "download": 50.0},
        timestamp=now - timedelta(minutes=4),
    )

    login_snapshot = monitor.get_compliance_snapshot(workflow_type="login_flow", window_seconds=3600)
    export_snapshot = monitor.get_compliance_snapshot(workflow_type="data_export", window_seconds=3600)

    assert login_snapshot.snapshot is not None
    assert login_snapshot.snapshot.compliance_rate == 0.0
    assert login_snapshot.snapshot.expected_completion_time_seconds == 10.0
    assert export_snapshot.snapshot is not None
    assert export_snapshot.snapshot.compliance_rate == 1.0
    assert export_snapshot.snapshot.expected_completion_time_seconds == 120.0
