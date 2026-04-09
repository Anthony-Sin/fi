from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_agent.performance_metrics_collector import PerformanceMetricsCollector


def test_performance_metrics_collector_aggregates_latency_retry_workflow_and_session_metrics(tmp_path):
    collector = PerformanceMetricsCollector(storage_path=str(Path(tmp_path) / "metrics.json"))
    now = datetime.utcnow()

    collector.record_step_execution(workflow_id="wf-a", step_name="open", execution_time_seconds=1.0, timestamp=now)
    collector.record_step_execution(workflow_id="wf-a", step_name="open", execution_time_seconds=2.0, timestamp=now)
    collector.record_step_execution(workflow_id="wf-a", step_name="open", execution_time_seconds=4.0, timestamp=now)
    collector.record_retry(workflow_id="wf-a", step_name="open", retry_count=2, timestamp=now)
    collector.record_workflow_completion(workflow_id="wf-a", succeeded=True, step_count=3, timestamp=now)
    collector.record_workflow_completion(workflow_id="wf-a", succeeded=False, step_count=5, timestamp=now)
    collector.record_dlq_depth(depth=4, timestamp=now)
    collector.record_session(session_id="s-1", application_name="Chat", timestamp=now)
    collector.record_session(session_id="s-2", application_name="Chat", timestamp=now)

    result = collector.generate_snapshot(window_seconds=3600, as_of=now)

    assert result.succeeded is True
    assert result.snapshot is not None
    latency = result.snapshot.step_latencies[0]
    assert latency.step_name == "open"
    assert latency.mean_seconds == (1.0 + 2.0 + 4.0) / 3
    assert latency.p95_seconds == 4.0
    retry = result.snapshot.retry_rates[0]
    assert retry.retry_rate == 2 / 3
    workflow = result.snapshot.workflow_success_rates[0]
    assert workflow.success_rate == 0.5
    assert result.snapshot.dlq_depth == 4
    assert result.snapshot.session_count == 2
    assert result.snapshot.average_steps_per_workflow == 4.0


def test_performance_metrics_collector_respects_rolling_window(tmp_path):
    collector = PerformanceMetricsCollector(storage_path=str(Path(tmp_path) / "metrics.json"))
    now = datetime.utcnow()
    old = now - timedelta(hours=3)

    collector.record_step_execution(workflow_id="wf-a", step_name="open", execution_time_seconds=10.0, timestamp=old)
    collector.record_step_execution(workflow_id="wf-a", step_name="open", execution_time_seconds=2.0, timestamp=now)

    result = collector.generate_snapshot(window_seconds=3600, as_of=now)

    assert result.snapshot is not None
    assert result.snapshot.step_latencies[0].sample_count == 1
    assert result.snapshot.step_latencies[0].mean_seconds == 2.0


def test_performance_metrics_collector_renders_prometheus_compatible_endpoint(tmp_path):
    collector = PerformanceMetricsCollector(storage_path=str(Path(tmp_path) / "metrics.json"))
    now = datetime.utcnow()
    collector.record_step_execution(workflow_id="wf-a", step_name="submit", execution_time_seconds=1.5, timestamp=now)
    collector.record_workflow_completion(workflow_id="wf-a", succeeded=True, step_count=2, timestamp=now)
    collector.record_dlq_depth(depth=1, timestamp=now)

    result = collector.render_metrics_endpoint(window_seconds=3600, as_of=now)

    assert result.succeeded is True
    assert result.endpoint_payload is not None
    assert "automation_dlq_depth 1" in result.endpoint_payload
    assert 'automation_step_execution_mean_seconds{step="submit"}' in result.endpoint_payload
    assert 'automation_workflow_success_rate{workflow="wf-a"} 1.000000' in result.endpoint_payload


def test_performance_metrics_collector_detects_metric_degradation_against_baseline(tmp_path):
    collector = PerformanceMetricsCollector(
        storage_path=str(Path(tmp_path) / "metrics.json"),
        degradation_threshold_ratio=0.25,
    )
    now = datetime.utcnow()
    baseline_time = now - timedelta(hours=2)
    current_time = now - timedelta(minutes=10)

    collector.record_step_execution(workflow_id="wf-a", step_name="submit", execution_time_seconds=1.0, timestamp=baseline_time)
    collector.record_retry(workflow_id="wf-a", step_name="submit", retry_count=1, timestamp=baseline_time)
    collector.record_workflow_completion(workflow_id="wf-a", succeeded=True, step_count=2, timestamp=baseline_time)
    collector.record_dlq_depth(depth=1, timestamp=baseline_time)

    collector.record_step_execution(workflow_id="wf-a", step_name="submit", execution_time_seconds=2.0, timestamp=current_time)
    collector.record_retry(workflow_id="wf-a", step_name="submit", retry_count=2, timestamp=current_time)
    collector.record_workflow_completion(workflow_id="wf-a", succeeded=False, step_count=2, timestamp=current_time)
    collector.record_dlq_depth(depth=3, timestamp=current_time)

    result = collector.generate_snapshot(window_seconds=3600, baseline_window_seconds=3600, as_of=now)

    assert result.snapshot is not None
    metric_names = {item.metric_name for item in result.snapshot.degradations}
    assert "step_mean_seconds:submit" in metric_names
    assert "retry_rate:submit" in metric_names
    assert "workflow_success_rate:wf-a" in metric_names
    assert "dlq_depth" in metric_names
