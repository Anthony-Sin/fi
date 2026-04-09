from datetime import datetime, timedelta, timezone
from pathlib import Path

from desktop_automation_perception.automation import WorkflowScheduler
from desktop_automation_perception.models import (
    MissedExecutionPolicy,
    WorkflowEventTrigger,
    WorkflowEventType,
    WorkflowSchedule,
    WorkflowSchedulerEvent,
    WorkflowTriggerType,
)


def test_workflow_scheduler_triggers_cron_schedule(tmp_path):
    now = datetime(2026, 4, 8, 12, 15, tzinfo=timezone.utc)
    scheduler = WorkflowScheduler(
        storage_path=str(Path(tmp_path) / "scheduler_cron.json"),
        now_fn=lambda: now,
    )
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="cron-1",
            workflow_id="wf-cron",
            trigger_type=WorkflowTriggerType.CRON,
            cron_expression="15 12 * * *",
        )
    )

    result = scheduler.tick(as_of=now)

    assert result.succeeded is True
    assert [run.workflow_id for run in result.runs] == ["wf-cron"]
    assert result.runs[0].trigger_detail == "cron_scheduled"


def test_workflow_scheduler_triggers_one_time_future_execution(tmp_path):
    run_at = datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)
    scheduler = WorkflowScheduler(
        storage_path=str(Path(tmp_path) / "scheduler_one_time.json"),
        now_fn=lambda: run_at,
    )
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="one-1",
            workflow_id="wf-once",
            trigger_type=WorkflowTriggerType.ONE_TIME,
            run_at=run_at,
        )
    )

    result = scheduler.tick(as_of=run_at)
    schedules = scheduler.list_schedules().schedules

    assert [run.workflow_id for run in result.runs] == ["wf-once"]
    assert schedules[0].active is False


def test_workflow_scheduler_handles_file_appeared_event_trigger(tmp_path):
    event_time = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    scheduler = WorkflowScheduler(storage_path=str(Path(tmp_path) / "scheduler_event_file.json"))
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="event-file-1",
            workflow_id="wf-file",
            trigger_type=WorkflowTriggerType.EVENT,
            event_trigger=WorkflowEventTrigger(
                event_type=WorkflowEventType.FILE_APPEARED,
                file_path="C:/drop/invoice.csv",
            ),
        )
    )

    result = scheduler.handle_event(
        WorkflowSchedulerEvent(
            event_type=WorkflowEventType.FILE_APPEARED,
            file_path="C:/drop/invoice.csv",
            timestamp=event_time,
        )
    )

    assert [run.workflow_id for run in result.runs] == ["wf-file"]
    assert result.runs[0].trigger_detail == "file_appeared:C:/drop/invoice.csv"


def test_workflow_scheduler_handles_queue_depth_threshold_event(tmp_path):
    scheduler = WorkflowScheduler(storage_path=str(Path(tmp_path) / "scheduler_event_queue.json"))
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="event-queue-1",
            workflow_id="wf-queue",
            trigger_type=WorkflowTriggerType.EVENT,
            event_trigger=WorkflowEventTrigger(
                event_type=WorkflowEventType.QUEUE_DEPTH_THRESHOLD,
                queue_name="automation",
                depth_threshold=5,
            ),
        )
    )

    result = scheduler.handle_event(
        WorkflowSchedulerEvent(
            event_type=WorkflowEventType.QUEUE_DEPTH_THRESHOLD,
            queue_name="automation",
            previous_queue_depth=4,
            queue_depth=5,
            timestamp=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert [run.workflow_id for run in result.runs] == ["wf-queue"]
    assert result.runs[0].trigger_detail == "queue_depth_threshold:automation:5"


def test_workflow_scheduler_runs_missed_cron_immediately_when_configured(tmp_path):
    scheduler = WorkflowScheduler(storage_path=str(Path(tmp_path) / "scheduler_missed_run.json"))
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="cron-missed-run",
            workflow_id="wf-missed-run",
            trigger_type=WorkflowTriggerType.CRON,
            cron_expression="0 * * * *",
            missed_execution_policy=MissedExecutionPolicy.RUN_IMMEDIATELY,
            last_checked_at=datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc),
        )
    )

    result = scheduler.tick(as_of=datetime(2026, 4, 8, 12, 30, tzinfo=timezone.utc))

    assert [run.workflow_id for run in result.runs] == ["wf-missed-run"]
    assert result.runs[0].trigger_detail == "missed_cron_run_immediate"


def test_workflow_scheduler_skips_missed_cron_when_configured(tmp_path):
    scheduler = WorkflowScheduler(storage_path=str(Path(tmp_path) / "scheduler_missed_skip.json"))
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="cron-missed-skip",
            workflow_id="wf-missed-skip",
            trigger_type=WorkflowTriggerType.CRON,
            cron_expression="0 * * * *",
            missed_execution_policy=MissedExecutionPolicy.SKIP,
            last_checked_at=datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc),
        )
    )

    result = scheduler.tick(as_of=datetime(2026, 4, 8, 12, 30, tzinfo=timezone.utc))

    assert result.runs == []


def test_workflow_scheduler_logs_all_triggered_runs(tmp_path):
    run_at = datetime(2026, 4, 8, 14, 0, tzinfo=timezone.utc)
    scheduler = WorkflowScheduler(storage_path=str(Path(tmp_path) / "scheduler_history.json"))
    scheduler.upsert_schedule(
        WorkflowSchedule(
            schedule_id="history-one",
            workflow_id="wf-history",
            trigger_type=WorkflowTriggerType.ONE_TIME,
            run_at=run_at,
        )
    )

    scheduler.tick(as_of=run_at)
    history = scheduler.list_run_history()

    assert history.succeeded is True
    assert len(history.runs) == 1
    assert history.runs[0].workflow_id == "wf-history"
