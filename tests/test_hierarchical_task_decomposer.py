from desktop_automation_agent.hierarchical_task_decomposer import HierarchicalTaskDecomposer
from desktop_automation_agent.models import TaskDecompositionLevel
from desktop_automation_agent.orchestrator_agent_core import OrchestratorAgentCore


def test_hierarchical_task_decomposer_builds_nested_phase_task_step_tree():
    decomposer = HierarchicalTaskDecomposer(max_depth=3)

    result = decomposer.decompose(
        "Open the CRM dashboard, collect account details and export the report, then verify the results"
    )

    assert result.succeeded is True
    assert result.tree is not None
    assert len(result.tree.root_nodes) == 2
    first_phase = result.tree.root_nodes[0]
    assert first_phase.level is TaskDecompositionLevel.PHASE
    assert first_phase.children
    assert all(child.level is TaskDecompositionLevel.TASK for child in first_phase.children)
    assert any(grandchild.level is TaskDecompositionLevel.STEP for child in first_phase.children for grandchild in child.children)
    assert result.tree.fully_decomposed is True


def test_hierarchical_task_decomposer_supports_partial_decomposition_frontier():
    decomposer = HierarchicalTaskDecomposer(max_depth=3, execution_expansion_depth=2)

    result = decomposer.decompose(
        "Launch the portal and review the inbox, then submit the response",
    )

    assert result.succeeded is True
    assert result.tree is not None
    assert result.tree.fully_decomposed is False
    frontier = decomposer.to_orchestrator_plan(result.tree)
    assert frontier.decomposition_tree is not None
    assert frontier.subtasks
    assert all(subtask.description for subtask in frontier.subtasks)
    assert any(node.abstract for phase in result.tree.root_nodes for node in phase.children)


def test_hierarchical_task_decomposer_round_trips_structured_tree_json():
    decomposer = HierarchicalTaskDecomposer(max_depth=3)
    tree = decomposer.decompose("Open app then fill form and submit").tree

    serialized = decomposer.to_json(tree)
    restored = decomposer.from_json(serialized)

    assert restored.task_description == tree.task_description
    assert restored.max_depth == tree.max_depth
    assert restored.root_nodes[0].level is tree.root_nodes[0].level
    assert restored.root_nodes[0].children[0].title == tree.root_nodes[0].children[0].title


def test_orchestrator_agent_core_can_use_hierarchical_task_decomposer():
    decomposer = HierarchicalTaskDecomposer(max_depth=3, execution_expansion_depth=2)
    orchestrator = OrchestratorAgentCore(
        task_decomposer=decomposer,
        max_decomposition_depth=3,
        execution_expansion_depth=2,
    )

    plan = orchestrator.create_plan("Open browser and sign in, then capture the confirmation")

    assert plan.decomposition_tree is not None
    assert plan.subtasks
    assert all(subtask.expected_outputs for subtask in plan.subtasks)
