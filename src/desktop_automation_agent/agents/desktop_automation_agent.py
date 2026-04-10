from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from desktop_automation_agent._time import utc_now
from desktop_automation_agent.agents.hierarchical_task_decomposer import HierarchicalTaskDecomposer
from desktop_automation_agent.agents.desktop_automation_overlay import DesktopAutomationOverlay
from desktop_automation_agent.agents.orchestrator_agent_core import OrchestratorAgentCore
from desktop_automation_agent.agents.specialist_agent_router import SpecialistAgentRouter
from desktop_automation_agent.models import (
    OrchestratorAgentResult,
    OrchestratorSubtask,
    OrchestratorSubtaskResult,
    OrchestratorSubtaskStatus,
    SpecialistAgentRecord,
)


@dataclass(slots=True)
class DesktopAutomationAgent:
    """
    Unified agent that orchestrates complex desktop automation tasks.
    It decomposes high-level requests into subtasks and routes them to specialized modules.

    This agent acts as the central brain, coordinating perception, action, and account management.
    """
    orchestrator: OrchestratorAgentCore = field(default_factory=OrchestratorAgentCore)
    router: Optional[SpecialistAgentRouter] = None
    specialists: Dict[str, Any] = field(default_factory=dict)
    overlay: Optional[DesktopAutomationOverlay] = None
    command_history: List[Dict[str, Any]] = field(default_factory=list)
    api_key: str = ""
    selected_model: str = "gemini-3.1-flash-lite-preview"
    total_tokens: int = 0
    total_cost: float = 0.0

    def __post_init__(self):
        if self.router is None:
            # Internal router state
            self.router = SpecialistAgentRouter(storage_path="data/agent_routing.json")

        if self.orchestrator.task_decomposer is None:
            self.orchestrator.task_decomposer = HierarchicalTaskDecomposer()

        if self.overlay is None:
            self.overlay = DesktopAutomationOverlay(
                on_command_received=self.execute_from_overlay,
                on_settings_changed=self.update_settings
            )

    def register_specialist(
        self,
        module_name: str,
        instance: Any,
        capabilities: Optional[List[str]] = None
    ):
        """
        Registers a specialist module instance and its capabilities.

        Common modules include:
        - application_launcher
        - account_rotation_orchestrator
        - ai_interface_navigator
        - multi_application_workflow_coordinator
        - navigation_step_sequencer
        - form_automation
        """
        self.specialists[module_name] = instance
        if self.router:
            self.router.register_agent(
                agent_name=module_name,
                capabilities=capabilities or [module_name],
                module_reference=module_name
            )

    def execute(self, task_description: str) -> OrchestratorAgentResult:
        """
        Decomposes and executes a high-level task description.

        Example: "Switch to my work account, open ChatGPT, and ask 'What is the weather in Tokyo?'"
        """
        self.overlay.update_status("Decomposing task...")
        plan = self.orchestrator.create_plan(task_description)

        # Notify overlay about the plan
        self.overlay.set_active_plan(plan)

        def monitored_executor(subtask: OrchestratorSubtask, context: Dict[str, str]) -> Any:
            self.overlay.update_subtask_status(subtask.subtask_id, "RUNNING")
            res = self._dispatch_with_router(subtask, context)

            # Simulate token usage and cost for the subtask (real integration would use the AI provider response)
            # In a real scenario, specialists would return token counts.
            tokens_used = 150  # Placeholder
            cost_per_token = 0.0000001 if "lite" in self.selected_model.lower() else 0.0000005
            subtask_cost = tokens_used * cost_per_token

            self.total_tokens += tokens_used
            self.total_cost += subtask_cost

            self.overlay.update_resource_usage(self.total_tokens, self.total_cost)

            status = "COMPLETED" if getattr(res, 'succeeded', True) else "FAILED"
            self.overlay.update_subtask_status(subtask.subtask_id, status)
            return res

        result = self.orchestrator.execute_plan(plan, executor=monitored_executor)

        history_entry = {
            "timestamp": str(utc_now()),
            "command": task_description,
            "succeeded": result.succeeded,
            "reason": result.reason,
            "tokens": 150 * len(plan.subtasks), # rough estimate for history
            "cost": 150 * len(plan.subtasks) * (0.0000001 if "lite" in self.selected_model.lower() else 0.0000005)
        }
        self.command_history.append(history_entry)
        self.overlay.add_history_entry(history_entry)

        if result.succeeded:
            self.overlay.update_status("Task completed successfully")
        else:
            self.overlay.update_status(f"Failed: {result.reason[:30]}")

        return result

    def update_settings(self, api_key: str, model_name: str):
        """Updates agent settings from the overlay."""
        self.api_key = api_key
        self.selected_model = model_name
        print(f"DEBUG: Settings updated - Model: {self.selected_model}")

    def execute_from_overlay(self, command: str):
        """Callback for overlay interaction."""
        return self.execute(command)

    def run_interactive(self):
        """Launches the agent with its UI overlay."""
        self.overlay.launch()

    def _dispatch_with_router(self, subtask: OrchestratorSubtask, context: Dict[str, str]) -> Any:
        """Internal executor that uses the router to find and invoke the correct specialist."""
        if not self.router:
            return self._invoke_specialist(subtask.responsible_module, subtask, context)

        routing_result = self.router.route_subtask(
            subtask,
            context=context,
            dispatcher=self._router_dispatcher
        )

        if routing_result.subtask_result:
            return routing_result.subtask_result

        # If router failed to find a match, try invoking directly (which has fallback logic)
        if not routing_result.succeeded and routing_result.decision and routing_result.decision.escalated:
             return self._invoke_specialist(subtask.responsible_module, subtask, context)

        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.FAILED,
            responsible_module=subtask.responsible_module,
            reason=routing_result.reason or f"Routing failed for {subtask.responsible_module}"
        )

    def _router_dispatcher(
        self,
        agent_record: SpecialistAgentRecord,
        subtask: OrchestratorSubtask,
        context: Dict[str, str]
    ) -> Any:
        """Callback for the router to invoke the specialist instance."""
        module_name = agent_record.module_reference or agent_record.agent_name
        print(f"DEBUG: Router dispatching to {module_name} for subtask {subtask.subtask_id}")
        return self._invoke_specialist(module_name, subtask, context)

    def _invoke_specialist(self, module_name: str, subtask: OrchestratorSubtask, context: Dict[str, str]) -> Any:
        """Invokes the specialist instance based on its type and standard method names."""
        # Normalize module name as it might come from router registry
        specialist = self.specialists.get(module_name)
        if not specialist:
            # Fallback to a mock/noop if not registered, for demonstration/testing
            return OrchestratorSubtaskResult(
                subtask_id=subtask.subtask_id,
                status=OrchestratorSubtaskStatus.COMPLETED,
                responsible_module=module_name,
                produced_outputs={f"{subtask.subtask_id}_result": f"Simulated execution of: {subtask.description}"},
                reason=f"Note: Specialist module '{module_name}' is not registered; using simulation."
            )

        try:
            # The executor in OrchestratorAgentCore expects an object with 'succeeded' attribute or a dict.
            # Most specialists in this library return a Result object with 'succeeded' and 'reason'.

            # We try to pass subtask.description as the primary input.
            # If the specialist is a complex object like MultiApplicationWorkflowCoordinator,
            # it might expect list of WorkflowStep, which we don't have here from just a string.
            # In a real integration, there would be an LLM-based step to convert description to structured objects.

            if module_name == "ai_interface_navigator" and hasattr(specialist, "navigate"):
                 # AIInterfaceNavigator.navigate(prompt, interface, ...)
                 # Here we'd ideally have the interface config.
                 return specialist.navigate(prompt=subtask.description)

            if module_name == "application_launcher" and hasattr(specialist, "launch"):
                 return specialist.launch(subtask.description)

            if hasattr(specialist, "execute"):
                return specialist.execute(subtask.description)

            if hasattr(specialist, "run"):
                return specialist.run(subtask.description)

            if callable(specialist):
                return specialist(subtask, context)

        except Exception as e:
            return OrchestratorSubtaskResult(
                subtask_id=subtask.subtask_id,
                status=OrchestratorSubtaskStatus.FAILED,
                responsible_module=module_name,
                reason=f"Error invoking specialist '{module_name}': {str(e)}"
            )

        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.FAILED,
            responsible_module=module_name,
            reason=f"Specialist module '{module_name}' does not have a recognized execution method."
        )
