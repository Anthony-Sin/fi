from pathlib import Path

from desktop_automation_agent.anomaly_detector import AnomalyDetector
from desktop_automation_agent.models import AnomalyCategory, ResourceUsageSnapshot


def test_anomaly_detector_flags_slow_step_against_baseline(tmp_path):
    detector = AnomalyDetector(storage_path=str(Path(tmp_path) / "anomalies.json"))

    detector.record_step_execution(step_id="submit", execution_time_seconds=1.0)
    detector.record_step_execution(step_id="submit", execution_time_seconds=1.1)
    detector.record_step_execution(step_id="submit", execution_time_seconds=0.9)
    result = detector.record_step_execution(step_id="submit", execution_time_seconds=3.5)

    assert len(result.anomalies) == 1
    assert result.anomalies[0].category is AnomalyCategory.SLOW_STEP_EXECUTION


def test_anomaly_detector_flags_repeated_failures_and_can_pause(tmp_path):
    pauses = []
    alerts = []
    detector = AnomalyDetector(
        storage_path=str(Path(tmp_path) / "failures.json"),
        repeated_failure_threshold=2,
        pause_callback=pauses.append,
        alert_callback=alerts.append,
    )

    detector.record_step_failure(step_id="login", reason="bad state")
    result = detector.record_step_failure(
        step_id="login",
        reason="bad state",
        pause_on_detection=True,
    )

    assert len(result.anomalies) == 1
    assert result.anomalies[0].category is AnomalyCategory.REPEATED_STEP_FAILURE
    assert result.pause_requested is True
    assert len(pauses) == 1
    assert len(alerts) == 1


def test_anomaly_detector_detects_application_crash(tmp_path):
    detector = AnomalyDetector(storage_path=str(Path(tmp_path) / "crash.json"))

    result = detector.detect_application_crash(
        application_name="ChatGPT",
        expected_signature="chatgpt.exe",
        visible_applications=["writer.exe", "notepad"],
    )

    assert len(result.anomalies) == 1
    assert result.anomalies[0].category is AnomalyCategory.APPLICATION_CRASH


def test_anomaly_detector_detects_ui_structure_change(tmp_path):
    detector = AnomalyDetector(storage_path=str(Path(tmp_path) / "ui.json"))

    initial = detector.detect_ui_structure_change(
        step_id="compose",
        application_name="Chat App",
        structure_signature="window:1|buttons:3|inputs:1",
    )
    changed = detector.detect_ui_structure_change(
        step_id="compose",
        application_name="Chat App",
        structure_signature="window:1|buttons:1|inputs:0",
    )

    assert initial.anomalies == []
    assert len(changed.anomalies) == 1
    assert changed.anomalies[0].category is AnomalyCategory.UI_STRUCTURE_CHANGE


def test_anomaly_detector_detects_resource_exhaustion(tmp_path):
    detector = AnomalyDetector(
        storage_path=str(Path(tmp_path) / "resources.json"),
        cpu_threshold_percent=85.0,
        memory_threshold_percent=80.0,
    )

    result = detector.detect_resource_exhaustion(
        step_id="generate",
        resource_usage=ResourceUsageSnapshot(
            cpu_percent=91.0,
            memory_percent=82.0,
            process_name="chatgpt.exe",
        ),
    )

    assert len(result.anomalies) == 1
    assert result.anomalies[0].category is AnomalyCategory.RESOURCE_EXHAUSTION
    assert "CPU usage" in (result.anomalies[0].detail or "")
    assert "Memory usage" in (result.anomalies[0].detail or "")


def test_anomaly_detector_persists_records(tmp_path):
    detector = AnomalyDetector(storage_path=str(Path(tmp_path) / "persist.json"))

    detector.detect_application_crash(
        application_name="Sheets",
        visible_applications=[],
    )
    records = detector.list_records()

    assert len(records) == 1
    assert records[0].application_name == "Sheets"
