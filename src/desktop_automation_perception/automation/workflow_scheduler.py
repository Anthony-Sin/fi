from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    MissedExecutionPolicy,
    WorkflowEventTrigger,
    WorkflowEventType,
    WorkflowRunRecord,
    WorkflowSchedule,
    WorkflowSchedulerEvent,
    WorkflowSchedulerResult,
    WorkflowSchedulerSnapshot,
    WorkflowTriggerType,
)


@dataclass(slots=True)
class WorkflowScheduler:
    storage_path: str
    run_callback: Callable[[WorkflowRunRecord], object] | None = None
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def upsert_schedule(self, schedule: WorkflowSchedule) -> WorkflowSchedulerResult:
        snapshot = self._load_snapshot()
        copied = self._copy_schedule(schedule)
        snapshot.schedules = [
            item for item in snapshot.schedules if item.schedule_id != copied.schedule_id
        ] + [copied]
        self._save_snapshot(snapshot)
        return WorkflowSchedulerResult(succeeded=True, schedule=copied, schedules=list(snapshot.schedules))

    def list_schedules(self) -> WorkflowSchedulerResult:
        snapshot = self._load_snapshot()
        return WorkflowSchedulerResult(succeeded=True, schedules=list(snapshot.schedules))

    def list_run_history(self) -> WorkflowSchedulerResult:
        snapshot = self._load_snapshot()
        return WorkflowSchedulerResult(succeeded=True, runs=list(snapshot.run_history))

    def tick(self, *, as_of: datetime | None = None) -> WorkflowSchedulerResult:
        snapshot = self._load_snapshot()
        current_time = self._normalize_datetime(as_of or self.now_fn())
        triggered_runs: list[WorkflowRunRecord] = []

        for schedule in snapshot.schedules:
            if not schedule.active or schedule.trigger_type is WorkflowTriggerType.EVENT:
                schedule.last_checked_at = current_time
                continue

            if schedule.trigger_type is WorkflowTriggerType.ONE_TIME:
                run = self._evaluate_one_time(schedule, current_time)
                if run is not None:
                    triggered_runs.append(run)
                    snapshot.run_history.append(run)
                    self._emit_run(run)
                continue

            if schedule.trigger_type is WorkflowTriggerType.CRON:
                runs = self._evaluate_cron(schedule, current_time)
                for run in runs:
                    triggered_runs.append(run)
                    snapshot.run_history.append(run)
                    self._emit_run(run)

        self._save_snapshot(snapshot)
        return WorkflowSchedulerResult(succeeded=True, schedules=list(snapshot.schedules), runs=triggered_runs)

    def handle_event(self, event: WorkflowSchedulerEvent) -> WorkflowSchedulerResult:
        snapshot = self._load_snapshot()
        event_time = self._normalize_datetime(event.timestamp)
        triggered_runs: list[WorkflowRunRecord] = []

        for schedule in snapshot.schedules:
            if not schedule.active or schedule.trigger_type is not WorkflowTriggerType.EVENT:
                continue
            if not self._event_matches(schedule.event_trigger, event):
                continue
            run = WorkflowRunRecord(
                schedule_id=schedule.schedule_id,
                workflow_id=schedule.workflow_id,
                workflow_version_number=schedule.payload.get("workflow_version_number"),
                trigger_type=WorkflowTriggerType.EVENT,
                trigger_detail=self._event_detail(event),
                triggered_at=event_time,
                scheduled_for=event_time,
                payload=dict(schedule.payload),
            )
            schedule.last_triggered_at = event_time
            schedule.last_checked_at = event_time
            triggered_runs.append(run)
            snapshot.run_history.append(run)
            self._emit_run(run)

        self._save_snapshot(snapshot)
        return WorkflowSchedulerResult(succeeded=True, schedules=list(snapshot.schedules), runs=triggered_runs)

    def _evaluate_one_time(
        self,
        schedule: WorkflowSchedule,
        current_time: datetime,
    ) -> WorkflowRunRecord | None:
        run_at = schedule.run_at
        if run_at is None:
            schedule.last_checked_at = current_time
            return None
        run_at = self._normalize_datetime(run_at)
        last_checked = self._normalize_datetime(schedule.last_checked_at) or run_at
        schedule.last_checked_at = current_time
        if schedule.last_triggered_at is not None:
            return None
        if run_at > current_time:
            return None

        was_missed = run_at < current_time and last_checked < run_at
        if was_missed and schedule.missed_execution_policy is MissedExecutionPolicy.SKIP:
            schedule.active = False
            return None

        run = WorkflowRunRecord(
            schedule_id=schedule.schedule_id,
            workflow_id=schedule.workflow_id,
            workflow_version_number=schedule.payload.get("workflow_version_number"),
            trigger_type=WorkflowTriggerType.ONE_TIME,
            trigger_detail="one_time_execution" if not was_missed else "missed_one_time_run_immediate",
            triggered_at=current_time,
            scheduled_for=run_at,
            payload=dict(schedule.payload),
        )
        schedule.last_triggered_at = current_time
        schedule.active = False
        return run

    def _evaluate_cron(
        self,
        schedule: WorkflowSchedule,
        current_time: datetime,
    ) -> list[WorkflowRunRecord]:
        if schedule.cron_expression is None:
            schedule.last_checked_at = current_time
            return []

        baseline = self._normalize_datetime(schedule.last_checked_at)
        if baseline is None:
            baseline = current_time - timedelta(minutes=1)

        matches = self._cron_matches_between(schedule.cron_expression, baseline, current_time)
        schedule.last_checked_at = current_time
        if not matches:
            return []

        if len(matches) > 1 and schedule.missed_execution_policy is MissedExecutionPolicy.SKIP:
            scheduled_for = matches[-1]
            if scheduled_for != current_time.replace(second=0, microsecond=0):
                return []
            run = self._build_cron_run(schedule, current_time, scheduled_for, "cron_scheduled")
            schedule.last_triggered_at = current_time
            return [run]

        if len(matches) > 1 and schedule.missed_execution_policy is MissedExecutionPolicy.RUN_IMMEDIATELY:
            scheduled_for = matches[-1]
            run = self._build_cron_run(schedule, current_time, scheduled_for, "missed_cron_run_immediate")
            schedule.last_triggered_at = current_time
            return [run]

        scheduled_for = matches[-1]
        run = self._build_cron_run(schedule, current_time, scheduled_for, "cron_scheduled")
        schedule.last_triggered_at = current_time
        return [run]

    def _build_cron_run(
        self,
        schedule: WorkflowSchedule,
        current_time: datetime,
        scheduled_for: datetime,
        detail: str,
    ) -> WorkflowRunRecord:
        return WorkflowRunRecord(
            schedule_id=schedule.schedule_id,
            workflow_id=schedule.workflow_id,
            workflow_version_number=schedule.payload.get("workflow_version_number"),
            trigger_type=WorkflowTriggerType.CRON,
            trigger_detail=detail,
            triggered_at=current_time,
            scheduled_for=scheduled_for,
            payload=dict(schedule.payload),
        )

    def _cron_matches_between(
        self,
        expression: str,
        start: datetime,
        end: datetime,
    ) -> list[datetime]:
        matches: list[datetime] = []
        probe = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        final = end.replace(second=0, microsecond=0)
        while probe <= final:
            if self._cron_matches(expression, probe):
                matches.append(probe)
            probe += timedelta(minutes=1)
        return matches

    def _cron_matches(self, expression: str, value: datetime) -> bool:
        minute, hour, day, month, weekday = expression.split()
        return (
            self._field_matches(minute, value.minute, 0, 59)
            and self._field_matches(hour, value.hour, 0, 23)
            and self._field_matches(day, value.day, 1, 31)
            and self._field_matches(month, value.month, 1, 12)
            and self._field_matches(weekday, (value.weekday() + 1) % 7, 0, 6)
        )

    def _field_matches(self, field: str, current: int, minimum: int, maximum: int) -> bool:
        for token in field.split(","):
            if self._token_matches(token.strip(), current, minimum, maximum):
                return True
        return False

    def _token_matches(self, token: str, current: int, minimum: int, maximum: int) -> bool:
        if token == "*":
            return True
        if "/" in token:
            base, step_text = token.split("/", 1)
            step = max(1, int(step_text))
            if base == "*":
                return (current - minimum) % step == 0
            if "-" in base:
                start_text, end_text = base.split("-", 1)
                start = int(start_text)
                end = int(end_text)
                return start <= current <= end and (current - start) % step == 0
            start = int(base)
            return current >= start and (current - start) % step == 0
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            return int(start_text) <= current <= int(end_text)
        return int(token) == current

    def _event_matches(
        self,
        trigger: WorkflowEventTrigger | None,
        event: WorkflowSchedulerEvent,
    ) -> bool:
        if trigger is None or trigger.event_type is not event.event_type:
            return False
        if event.event_type is WorkflowEventType.FILE_APPEARED:
            return trigger.file_path == event.file_path
        if event.event_type is WorkflowEventType.QUEUE_DEPTH_THRESHOLD:
            if trigger.queue_name != event.queue_name or trigger.depth_threshold is None or event.queue_depth is None:
                return False
            previous = event.previous_queue_depth or 0
            return previous < trigger.depth_threshold <= event.queue_depth
        return False

    def _event_detail(self, event: WorkflowSchedulerEvent) -> str:
        if event.event_type is WorkflowEventType.FILE_APPEARED:
            return f"file_appeared:{event.file_path}"
        if event.event_type is WorkflowEventType.QUEUE_DEPTH_THRESHOLD:
            return f"queue_depth_threshold:{event.queue_name}:{event.queue_depth}"
        return event.event_type.value

    def _emit_run(self, run: WorkflowRunRecord) -> None:
        if self.run_callback is not None:
            self.run_callback(run)

    def _normalize_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _load_snapshot(self) -> WorkflowSchedulerSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return WorkflowSchedulerSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return WorkflowSchedulerSnapshot(
            schedules=[self._deserialize_schedule(item) for item in payload.get("schedules", [])],
            run_history=[self._deserialize_run(item) for item in payload.get("run_history", [])],
        )

    def _save_snapshot(self, snapshot: WorkflowSchedulerSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schedules": [self._serialize_schedule(item) for item in snapshot.schedules],
            "run_history": [self._serialize_run(item) for item in snapshot.run_history],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _copy_schedule(self, schedule: WorkflowSchedule) -> WorkflowSchedule:
        return self._deserialize_schedule(self._serialize_schedule(schedule))

    def _serialize_schedule(self, schedule: WorkflowSchedule) -> dict:
        return {
            "schedule_id": schedule.schedule_id,
            "workflow_id": schedule.workflow_id,
            "trigger_type": schedule.trigger_type.value,
            "cron_expression": schedule.cron_expression,
            "run_at": None if schedule.run_at is None else schedule.run_at.isoformat(),
            "event_trigger": None if schedule.event_trigger is None else self._serialize_event_trigger(schedule.event_trigger),
            "missed_execution_policy": schedule.missed_execution_policy.value,
            "active": schedule.active,
            "payload": dict(schedule.payload),
            "last_checked_at": None if schedule.last_checked_at is None else schedule.last_checked_at.isoformat(),
            "last_triggered_at": None if schedule.last_triggered_at is None else schedule.last_triggered_at.isoformat(),
        }

    def _deserialize_schedule(self, payload: dict) -> WorkflowSchedule:
        return WorkflowSchedule(
            schedule_id=payload["schedule_id"],
            workflow_id=payload["workflow_id"],
            trigger_type=WorkflowTriggerType(payload["trigger_type"]),
            cron_expression=payload.get("cron_expression"),
            run_at=None if payload.get("run_at") is None else datetime.fromisoformat(payload["run_at"]),
            event_trigger=None
            if payload.get("event_trigger") is None
            else self._deserialize_event_trigger(payload["event_trigger"]),
            missed_execution_policy=MissedExecutionPolicy(
                payload.get("missed_execution_policy", MissedExecutionPolicy.RUN_IMMEDIATELY.value)
            ),
            active=bool(payload.get("active", True)),
            payload=dict(payload.get("payload", {})),
            last_checked_at=None
            if payload.get("last_checked_at") is None
            else datetime.fromisoformat(payload["last_checked_at"]),
            last_triggered_at=None
            if payload.get("last_triggered_at") is None
            else datetime.fromisoformat(payload["last_triggered_at"]),
        )

    def _serialize_event_trigger(self, trigger: WorkflowEventTrigger) -> dict:
        return {
            "event_type": trigger.event_type.value,
            "file_path": trigger.file_path,
            "queue_name": trigger.queue_name,
            "depth_threshold": trigger.depth_threshold,
        }

    def _deserialize_event_trigger(self, payload: dict) -> WorkflowEventTrigger:
        return WorkflowEventTrigger(
            event_type=WorkflowEventType(payload["event_type"]),
            file_path=payload.get("file_path"),
            queue_name=payload.get("queue_name"),
            depth_threshold=payload.get("depth_threshold"),
        )

    def _serialize_run(self, run: WorkflowRunRecord) -> dict:
        return {
            "schedule_id": run.schedule_id,
            "workflow_id": run.workflow_id,
            "workflow_version_number": run.workflow_version_number,
            "trigger_type": run.trigger_type.value,
            "trigger_detail": run.trigger_detail,
            "triggered_at": run.triggered_at.isoformat(),
            "scheduled_for": None if run.scheduled_for is None else run.scheduled_for.isoformat(),
            "payload": dict(run.payload),
        }

    def _deserialize_run(self, payload: dict) -> WorkflowRunRecord:
        return WorkflowRunRecord(
            schedule_id=payload["schedule_id"],
            workflow_id=payload["workflow_id"],
            workflow_version_number=payload.get("workflow_version_number"),
            trigger_type=WorkflowTriggerType(payload["trigger_type"]),
            trigger_detail=payload["trigger_detail"],
            triggered_at=datetime.fromisoformat(payload["triggered_at"]),
            scheduled_for=None
            if payload.get("scheduled_for") is None
            else datetime.fromisoformat(payload["scheduled_for"]),
            payload=dict(payload.get("payload", {})),
        )
