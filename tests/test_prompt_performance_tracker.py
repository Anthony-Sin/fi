from pathlib import Path

from desktop_automation_agent.prompt_performance_tracker import PromptPerformanceTracker


def test_prompt_performance_tracker_records_prompt_submission(tmp_path):
    tracker = PromptPerformanceTracker(storage_path=str(Path(tmp_path) / "prompt_perf.json"))

    result = tracker.record_prompt_submission(
        template_name="summary-template",
        template_version=2,
        variables={"topic": "robotics"},
        response_text='{"summary":"ok"}',
        expected_format_met=True,
        execution_time_seconds=1.2,
        succeeded=True,
    )

    assert result.succeeded is True
    assert result.record is not None
    assert result.record.template_name == "summary-template"
    assert result.record.template_version == 2


def test_prompt_performance_tracker_aggregates_metrics_per_template(tmp_path):
    tracker = PromptPerformanceTracker(storage_path=str(Path(tmp_path) / "prompt_perf.json"))
    tracker.record_prompt_submission(
        template_name="summary-template",
        variables={},
        response_text="ok",
        expected_format_met=True,
        execution_time_seconds=1.0,
        succeeded=True,
    )
    tracker.record_prompt_submission(
        template_name="summary-template",
        variables={},
        response_text="bad",
        expected_format_met=False,
        execution_time_seconds=2.0,
        succeeded=False,
    )

    report = tracker.generate_report().report
    assert report is not None
    summary = next(item for item in report.template_summaries if item.template_name == "summary-template")
    assert summary.submission_count == 2
    assert summary.success_count == 1
    assert summary.expected_format_success_count == 1
    assert summary.average_execution_time_seconds == 1.5


def test_prompt_performance_tracker_flags_low_success_templates(tmp_path):
    tracker = PromptPerformanceTracker(
        storage_path=str(Path(tmp_path) / "prompt_perf.json"),
        low_success_rate_threshold=0.6,
    )
    tracker.record_prompt_submission(
        template_name="fragile-template",
        variables={},
        response_text="x",
        expected_format_met=False,
        execution_time_seconds=1.0,
        succeeded=False,
    )
    tracker.record_prompt_submission(
        template_name="fragile-template",
        variables={},
        response_text="y",
        expected_format_met=False,
        execution_time_seconds=1.3,
        succeeded=False,
    )
    tracker.record_prompt_submission(
        template_name="stable-template",
        variables={},
        response_text="ok",
        expected_format_met=True,
        execution_time_seconds=0.8,
        succeeded=True,
    )

    report = tracker.generate_report().report
    assert report is not None
    fragile = next(item for item in report.template_summaries if item.template_name == "fragile-template")
    stable = next(item for item in report.template_summaries if item.template_name == "stable-template")
    assert fragile.flagged_low_success is True
    assert stable.flagged_low_success is False


def test_prompt_performance_tracker_reports_most_and_least_reliable_templates(tmp_path):
    tracker = PromptPerformanceTracker(storage_path=str(Path(tmp_path) / "prompt_perf.json"))
    tracker.record_prompt_submission(
        template_name="best-template",
        variables={},
        response_text="ok",
        expected_format_met=True,
        execution_time_seconds=0.9,
        succeeded=True,
    )
    tracker.record_prompt_submission(
        template_name="worst-template",
        variables={},
        response_text="bad",
        expected_format_met=False,
        execution_time_seconds=2.1,
        succeeded=False,
    )
    tracker.record_prompt_submission(
        template_name="mid-template",
        variables={},
        response_text="mixed",
        expected_format_met=True,
        execution_time_seconds=1.5,
        succeeded=True,
    )
    tracker.record_prompt_submission(
        template_name="mid-template",
        variables={},
        response_text="bad",
        expected_format_met=False,
        execution_time_seconds=1.0,
        succeeded=False,
    )

    report = tracker.generate_report().report
    assert report is not None
    assert report.most_reliable_templates[0].template_name == "best-template"
    assert report.least_reliable_templates[0].template_name == "worst-template"
