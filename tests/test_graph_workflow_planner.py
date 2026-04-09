from desktop_automation_perception.graph_workflow_planner import GraphBasedWorkflowPlanner
from desktop_automation_perception.models import (
    WorkflowGraphCondition,
    WorkflowGraphConditionOperator,
    WorkflowGraphDefinition,
    WorkflowGraphEdge,
    WorkflowGraphEdgeType,
    WorkflowGraphNode,
    WorkflowGraphNodeType,
)


def test_graph_workflow_planner_supports_sequential_and_conditional_transitions():
    planner = GraphBasedWorkflowPlanner()
    graph = WorkflowGraphDefinition(
        workflow_id="wf-1",
        entry_node_ids=("start",),
        nodes=[
            WorkflowGraphNode(node_id="start", step_name="Start"),
            WorkflowGraphNode(node_id="success", step_name="Success"),
            WorkflowGraphNode(node_id="failure", step_name="Failure"),
        ],
        edges=[
            WorkflowGraphEdge(
                edge_id="edge-success",
                source_node_id="start",
                target_node_id="success",
                edge_type=WorkflowGraphEdgeType.CONDITIONAL,
                condition=WorkflowGraphCondition(
                    output_key="status",
                    operator=WorkflowGraphConditionOperator.EQUALS,
                    expected_value="ok",
                ),
            ),
            WorkflowGraphEdge(
                edge_id="edge-failure",
                source_node_id="start",
                target_node_id="failure",
                edge_type=WorkflowGraphEdgeType.CONDITIONAL,
                condition=WorkflowGraphCondition(
                    output_key="status",
                    operator=WorkflowGraphConditionOperator.EQUALS,
                    expected_value="error",
                ),
            ),
        ],
    )

    initialized = planner.initialize_state(graph)
    assert initialized.succeeded is True
    assert [node.node_id for node in initialized.ready_nodes] == ["start"]

    updated = planner.complete_step(graph, initialized.state, "start", {"status": "ok"})
    assert updated.succeeded is True
    assert [node.node_id for node in updated.ready_nodes] == ["success"]


def test_graph_workflow_planner_supports_parallel_branches_and_merge_points():
    planner = GraphBasedWorkflowPlanner()
    graph = WorkflowGraphDefinition(
        workflow_id="wf-2",
        entry_node_ids=("start",),
        nodes=[
            WorkflowGraphNode(node_id="start", step_name="Start"),
            WorkflowGraphNode(node_id="branch-a", step_name="Branch A"),
            WorkflowGraphNode(node_id="branch-b", step_name="Branch B"),
            WorkflowGraphNode(
                node_id="merge",
                step_name="Merge",
                node_type=WorkflowGraphNodeType.MERGE,
                wait_for_all_predecessors=True,
            ),
        ],
        edges=[
            WorkflowGraphEdge("start-a", "start", "branch-a", edge_type=WorkflowGraphEdgeType.PARALLEL),
            WorkflowGraphEdge("start-b", "start", "branch-b", edge_type=WorkflowGraphEdgeType.PARALLEL),
            WorkflowGraphEdge("a-merge", "branch-a", "merge"),
            WorkflowGraphEdge("b-merge", "branch-b", "merge"),
        ],
    )

    state = planner.initialize_state(graph).state
    after_start = planner.complete_step(graph, state, "start", {})
    assert {node.node_id for node in after_start.ready_nodes} == {"branch-a", "branch-b"}

    after_a = planner.complete_step(graph, after_start.state, "branch-a", {})
    assert {node.node_id for node in after_a.ready_nodes} == {"branch-b"}

    after_b = planner.complete_step(graph, after_a.state, "branch-b", {})
    assert {node.node_id for node in after_b.ready_nodes} == {"merge"}


def test_graph_workflow_planner_supports_retry_cycles_with_max_iteration_count():
    planner = GraphBasedWorkflowPlanner()
    graph = WorkflowGraphDefinition(
        workflow_id="wf-3",
        entry_node_ids=("retryable",),
        nodes=[
            WorkflowGraphNode(node_id="retryable", step_name="Retryable Step"),
            WorkflowGraphNode(node_id="done", step_name="Done"),
        ],
        edges=[
            WorkflowGraphEdge(
                edge_id="retry-edge",
                source_node_id="retryable",
                target_node_id="retryable",
                edge_type=WorkflowGraphEdgeType.RETRY,
                condition=WorkflowGraphCondition(
                    output_key="retry",
                    operator=WorkflowGraphConditionOperator.TRUTHY,
                ),
                loop_id="retry-loop",
                max_iterations=2,
            ),
            WorkflowGraphEdge(
                edge_id="done-edge",
                source_node_id="retryable",
                target_node_id="done",
                edge_type=WorkflowGraphEdgeType.CONDITIONAL,
                condition=WorkflowGraphCondition(
                    output_key="retry",
                    operator=WorkflowGraphConditionOperator.FALSY,
                ),
            ),
        ],
    )

    state = planner.initialize_state(graph).state
    retry_one = planner.complete_step(graph, state, "retryable", {"retry": True})
    assert [node.node_id for node in retry_one.ready_nodes] == ["retryable"]

    retry_two = planner.complete_step(graph, retry_one.state, "retryable", {"retry": True})
    assert [node.node_id for node in retry_two.ready_nodes] == ["retryable"]

    retry_three = planner.complete_step(graph, retry_two.state, "retryable", {"retry": True})
    assert retry_three.ready_nodes == []
    assert retry_three.state.loop_iterations["retry-loop"] == 2


def test_graph_workflow_planner_round_trips_json_for_storage_and_versioning():
    planner = GraphBasedWorkflowPlanner()
    graph = WorkflowGraphDefinition(
        workflow_id="wf-4",
        version=3,
        entry_node_ids=("a",),
        nodes=[
            WorkflowGraphNode(node_id="a", step_name="A", metadata={"kind": "entry"}),
            WorkflowGraphNode(node_id="b", step_name="B", application_name="CRM"),
        ],
        edges=[
            WorkflowGraphEdge(
                edge_id="a-b",
                source_node_id="a",
                target_node_id="b",
                metadata={"label": "next"},
            )
        ],
        metadata={"owner": "planner"},
    )

    serialized = planner.to_json(graph)
    restored = planner.from_json(serialized)

    assert restored.workflow_id == "wf-4"
    assert restored.version == 3
    assert restored.entry_node_ids == ("a",)
    assert restored.nodes[0].metadata["kind"] == "entry"
    assert restored.edges[0].metadata["label"] == "next"
