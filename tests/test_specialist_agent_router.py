from pathlib import Path

from desktop_automation_agent.models import OrchestratorSubtask, OrchestratorSubtaskStatus
from desktop_automation_agent.specialist_agent_router import SpecialistAgentRouter


def make_subtask(description, responsible_module="ai_interface_navigator", subtask_id="subtask-1"):
    return OrchestratorSubtask(
        subtask_id=subtask_id,
        description=description,
        responsible_module=responsible_module,
        expected_outputs=[f"{subtask_id}_result"],
    )


def test_specialist_agent_router_selects_best_matching_agent(tmp_path):
    router = SpecialistAgentRouter(storage_path=str(Path(tmp_path) / "router.json"))
    router.register_agent(
        agent_name="chat-agent",
        capabilities=["ai_interface_navigator", "prompt"],
        module_reference="ai_interface_navigator",
    )
    router.register_agent(
        agent_name="generic-agent",
        capabilities=["automation"],
        module_reference="desktop_automation",
    )

    result = router.route_subtask(make_subtask("Submit a prompt in the AI interface"))

    assert result.decision is not None
    assert result.decision.selected_agent == "chat-agent"
    assert result.decision.matched_capability == "ai_interface_navigator"


def test_specialist_agent_router_dispatches_with_context_and_normalizes_result(tmp_path):
    router = SpecialistAgentRouter(storage_path=str(Path(tmp_path) / "router.json"))
    router.register_agent(
        agent_name="form-agent",
        capabilities=["form_automation", "form"],
        module_reference="form_automation",
    )

    captured = []
    result = router.route_subtask(
        make_subtask("Fill the form", responsible_module="form_automation"),
        context={"account": "primary"},
        dispatcher=lambda agent, subtask, context: captured.append((agent.agent_name, context["account"])) or {
            "subtask-1_result": "filled"
        },
    )

    assert captured == [("form-agent", "primary")]
    assert result.subtask_result is not None
    assert result.subtask_result.status is OrchestratorSubtaskStatus.COMPLETED
    assert result.subtask_result.produced_outputs["subtask-1_result"] == "filled"


def test_specialist_agent_router_escalates_when_no_agent_matches(tmp_path):
    router = SpecialistAgentRouter(
        storage_path=str(Path(tmp_path) / "router.json"),
        escalation_target="orchestrator",
    )
    router.register_agent(
        agent_name="menu-agent",
        capabilities=["menu_dialog_navigator"],
        module_reference="menu_dialog_navigator",
    )

    result = router.route_subtask(make_subtask("Fill a profile form", responsible_module="form_automation"))

    assert result.succeeded is False
    assert result.decision is not None
    assert result.decision.escalated is True
    assert "escalated to orchestrator" in (result.reason or "")


def test_specialist_agent_router_routes_multiple_subtasks_and_logs_decisions(tmp_path):
    router = SpecialistAgentRouter(storage_path=str(Path(tmp_path) / "router.json"))
    router.register_agent(
        agent_name="launch-agent",
        capabilities=["application_launcher"],
        module_reference="application_launcher",
    )
    router.register_agent(
        agent_name="chat-agent",
        capabilities=["ai_interface_navigator"],
        module_reference="ai_interface_navigator",
    )

    result = router.route_subtasks(
        [
            make_subtask("Launch ChatGPT", responsible_module="application_launcher", subtask_id="subtask-1"),
            make_subtask("Submit prompt", responsible_module="ai_interface_navigator", subtask_id="subtask-2"),
        ]
    )
    decisions = router.list_decisions()

    assert result.succeeded is True
    assert len(result.decisions) == 2
    assert [item.selected_agent for item in decisions] == ["launch-agent", "chat-agent"]


def test_specialist_agent_router_handles_failed_dispatch_results(tmp_path):
    router = SpecialistAgentRouter(storage_path=str(Path(tmp_path) / "router.json"))
    router.register_agent(
        agent_name="chat-agent",
        capabilities=["ai_interface_navigator"],
        module_reference="ai_interface_navigator",
    )

    result = router.route_subtask(
        make_subtask("Submit prompt"),
        dispatcher=lambda agent, subtask, context: type(
            "ExecutionResult",
            (),
            {"succeeded": False, "reason": "navigation failed"},
        )(),
    )

    assert result.succeeded is False
    assert result.subtask_result is not None
    assert result.subtask_result.status is OrchestratorSubtaskStatus.FAILED
