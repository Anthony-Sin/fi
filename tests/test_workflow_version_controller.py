from datetime import datetime, timezone

from desktop_automation_perception.graph_workflow_planner import GraphBasedWorkflowPlanner
from desktop_automation_perception.models import (
    WorkflowAuditOutcome,
    WorkflowAuditQuery,
    WorkflowEventType,
    WorkflowEventTrigger,
    WorkflowGraphDefinition,
    WorkflowGraphEdge,
    WorkflowGraphNode,
    WorkflowRunRecord,
    WorkflowSchedule,
    WorkflowSchedulerEvent,
    WorkflowTriggerType,
    WorkflowVersionTag,
)
from desktop_automation_perception.workflow_version_controller import WorkflowVersionController
from desktop_automation_perception.workflow_audit_logger import WorkflowAuditLogger
from desktop_automation_perception.workflow_scheduler import WorkflowScheduler


def _graph(workflow_id, *, node_suffix="a", edge_suffix="1"):
    return WorkflowGraphDefinition(
        workflow_id=workflow_id,
        version=1,
        entry_node_ids=("start",),
        nodes=[
            WorkflowGraphNode(node_id="start", step_name=f"Start {node_suffix}"),
            WorkflowGraphNode(node_id=f"end-{node_suffix}", step_name="End"),
        ],
        edges=[
            WorkflowGraphEdge(
                edge_id=f"edge-{edge_suffix}",
                source_node_id="start",
                target_node_id=f"end-{node_suffix}",
            )
        ],
    )


def test_workflow_version_controller_creates_activates_rolls_back_and_tags_versions(tmp_path):
    controller = WorkflowVersionController(
        storage_path=str(tmp_path / "versions.json"),
        graph_planner=GraphBasedWorkflowPlanner(),
    )

    first = controller.create_version(
        workflow_id="wf-1",
        author="alice",
        change_description="Initial draft",
        workflow_graph=_graph("wf-1", node_suffix="a", edge_suffix="1"),
        tag=WorkflowVersionTag.EXPERIMENTAL,
        activate=True,
        timestamp=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
    )
    second = controller.create_version(
        workflow_id="wf-1",
        author="bob",
        change_description="Refine ending",
        workflow_graph=_graph("wf-1", node_suffix="b", edge_suffix="2"),
        tag=WorkflowVersionTag.STABLE,
        activate=False,
        timestamp=datetime(2026, 4, 9, 13, 0, tzinfo=timezone.utc),
    )

    assert first.version is not None and first.version.version_number == 1
    assert second.version is not None and second.version.version_number == 2
    assert controller.get_active_version("wf-1").version.version_number == 1

    activated = controller.activate_version("wf-1", 2)
    assert activated.succeeded is True
    assert activated.snapshot.active_version_number == 2

    rolled_back = controller.rollback_to_version("wf-1", 1)
    assert rolled_back.succeeded is True
    assert rolled_back.snapshot.active_version_number == 1

    tagged = controller.tag_version("wf-1", 1, WorkflowVersionTag.STABLE)
    assert tagged.version is not None
    assert tagged.version.tag is WorkflowVersionTag.STABLE


def test_workflow_version_controller_diffs_two_versions(tmp_path):
    controller = WorkflowVersionController(
        storage_path=str(tmp_path / "versions.json"),
        graph_planner=GraphBasedWorkflowPlanner(),
    )
    controller.create_version(
        workflow_id="wf-2",
        author="alice",
        change_description="v1",
        workflow_graph=_graph("wf-2", node_suffix="a", edge_suffix="1"),
    )
    controller.create_version(
        workflow_id="wf-2",
        author="alice",
        change_description="v2",
        workflow_graph=_graph("wf-2", node_suffix="b", edge_suffix="2"),
    )

    diff = controller.diff_versions("wf-2", 1, 2)

    assert diff.succeeded is True
    assert diff.diff is not None
    assert "end-b" in diff.diff.added_node_ids
    assert "end-a" in diff.diff.removed_node_ids
    assert "edge-2" in diff.diff.added_edge_ids


def test_workflow_audit_logger_records_workflow_version_for_queries(tmp_path):
    logger = WorkflowAuditLogger(storage_path=str(tmp_path / "audit.jsonl"))
    logger.log_action(
        workflow_id="wf-3",
        workflow_version_number=7,
        step_name="submit",
        action_type="click",
        success=True,
    )
    logger.log_action(
        workflow_id="wf-3",
        workflow_version_number=8,
        step_name="submit",
        action_type="click",
        success=False,
    )

    result = logger.query_logs(
        WorkflowAuditQuery(workflow_id="wf-3", workflow_version_number=7, outcome=WorkflowAuditOutcome.SUCCESS)
    )

    assert result.succeeded is True
    assert len(result.entries) == 1
    assert result.entries[0].workflow_version_number == 7


def test_workflow_scheduler_propagates_workflow_version_number_to_run_history(tmp_path):
    scheduler = WorkflowScheduler(storage_path=str(tmp_path / "scheduler.json"))
    schedule = WorkflowSchedule(
        schedule_id="schedule-1",
        workflow_id="wf-4",
        trigger_type=WorkflowTriggerType.EVENT,
        event_trigger=WorkflowEventTrigger(event_type=WorkflowEventType.FILE_APPEARED, file_path="inbox.txt"),
        payload={"workflow_version_number": 3},
    )
    scheduler.upsert_schedule(schedule)

    event_result = scheduler.handle_event(
        WorkflowSchedulerEvent(
            event_type=WorkflowEventType.FILE_APPEARED,
            file_path="inbox.txt",
        )
    )

    assert event_result.succeeded is True
    assert event_result.runs[0].workflow_version_number == 3
    history = scheduler.list_run_history()
    assert history.runs[0].workflow_version_number == 3
