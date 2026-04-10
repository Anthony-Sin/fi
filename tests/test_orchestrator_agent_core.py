from desktop_automation_agent.models import OrchestratorSubtaskResult, OrchestratorSubtaskStatus
from desktop_automation_agent.orchestrator_agent_core import OrchestratorAgentCore


def test_orchestrator_agent_decomposes_task_into_ordered_subtasks():
    """Verifies that the orchestrator can break down a complex natural language prompt
    into a sequence of dependent subtasks with correct ordering."""
    agent = OrchestratorAgentCore()

    plan = agent.create_plan("Open ChatGPT, then submit a prompt, then verify the response.")

    assert len(plan.subtasks) == 3
    assert plan.subtasks[0].description.lower().startswith("open chatgpt")
    assert plan.subtasks[1].dependency_ids == ["subtask-1"]
    assert plan.subtasks[2].dependency_ids == ["subtask-2"]


def test_orchestrator_agent_assigns_responsible_modules_by_subtask_type():
    """Verifies that the orchestrator correctly maps different types of subtasks (launching,
    filling forms, navigating menus) to their appropriate specialist modules."""
    agent = OrchestratorAgentCore()

    plan = agent.create_plan("Launch the app, then fill the form, then open the menu dialog.")

    modules = [subtask.responsible_module for subtask in plan.subtasks]
    assert modules == ["application_launcher", "form_automation", "menu_dialog_navigator"]


def test_orchestrator_agent_tracks_completion_and_final_outputs():
    """Verifies that the orchestrator correctly executes a multi-step plan, tracking which
    subtasks have completed and aggregating their outputs into a final result set."""
    agent = OrchestratorAgentCore()
    plan = agent.create_plan("Open ChatGPT then submit a prompt")

    result = agent.execute_plan(
        plan,
        executor=lambda subtask, outputs: {"%s_result" % subtask.subtask_id: f"done:{subtask.subtask_id}"},
    )

    assert result.succeeded is True
    assert result.summary is not None
    assert result.summary.completed_subtasks == ["subtask-1", "subtask-2"]
    assert result.summary.final_outputs["subtask-2_result"] == "done:subtask-2"


def test_orchestrator_agent_reroutes_dependents_after_partial_failure():
    """Verifies that if a subtask fails, the orchestrator identifies dependent subtasks and
    automatically reroutes them to a designated fallback module (e.g., manual human review)."""
    agent = OrchestratorAgentCore(fallback_module="manual_ops")
    plan = agent.create_plan("Open ChatGPT then submit a prompt then verify response")

    def executor(subtask, outputs):
        if subtask.subtask_id == "subtask-2":
            return type("ExecutionResult", (), {"succeeded": False, "reason": "submission failed"})()
        return {"%s_result" % subtask.subtask_id: "ok"}

    result = agent.execute_plan(plan, executor=executor)

    assert result.succeeded is False
    assert result.subtask_results[1].status is OrchestratorSubtaskStatus.FAILED
    assert result.subtask_results[2].status is OrchestratorSubtaskStatus.REROUTED
    assert result.subtask_results[2].rerouted_to == "manual_ops"


def test_orchestrator_agent_accepts_prebuilt_subtask_results_from_executor():
    """Verifies that the orchestrator correctly integrates pre-packaged OrchestratorSubtaskResult
    objects returned by an execution callback, including custom outputs."""
    agent = OrchestratorAgentCore()
    plan = agent.create_plan("Navigate and verify")

    def executor(subtask, outputs):
        if subtask.subtask_id == "subtask-1":
            return OrchestratorSubtaskResult(
                subtask_id=subtask.subtask_id,
                status=OrchestratorSubtaskStatus.COMPLETED,
                responsible_module=subtask.responsible_module,
                produced_outputs={"custom_output": "ready"},
            )
        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.COMPLETED,
            responsible_module=subtask.responsible_module,
            produced_outputs={"verify_output": "passed"},
        )

    result = agent.execute_plan(plan, executor=executor)

    assert result.succeeded is True
    assert result.summary is not None
    assert result.summary.final_outputs["custom_output"] == "ready"
