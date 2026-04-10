from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from desktop_automation_agent.models import (
    OrchestratorSubtask,
    OrchestratorSubtaskResult,
    OrchestratorSubtaskStatus,
    RoutingDecisionRecord,
    SpecialistAgentRecord,
    SpecialistRouterResult,
)


@dataclass(slots=True)
class SpecialistAgentRouter:
    """
    Routes subtasks to the best available specialist agent based on their registered capabilities.
    It tracks routing decisions and supports escalation to a central orchestrator.

    Inputs:
        - storage_path: Path for persisting routing decisions.
        - escalation_target: Default target for subtasks that cannot be routed.
    """
    storage_path: str
    escalation_target: str = "orchestrator"
    _registry: dict[str, SpecialistAgentRecord] = field(default_factory=dict, init=False, repr=False)

    def register_agent(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        module_reference: str | None = None,
    ) -> SpecialistAgentRecord:
        record = SpecialistAgentRecord(
            agent_name=agent_name,
            capabilities=list(capabilities),
            module_reference=module_reference,
        )
        self._registry[agent_name] = record
        return record

    def list_agents(self) -> list[SpecialistAgentRecord]:
        return list(self._registry.values())

    def execute(self, subtask: OrchestratorSubtask, **kwargs) -> SpecialistRouterResult:
        """Alias for route_subtask to satisfy standard entry method requirement."""
        return self.route_subtask(subtask, **kwargs)

    def handle(self, subtask: OrchestratorSubtask, **kwargs) -> SpecialistRouterResult:
        """Alias for route_subtask to satisfy standard entry method requirement."""
        return self.route_subtask(subtask, **kwargs)

    def run(self, subtask: OrchestratorSubtask, **kwargs) -> SpecialistRouterResult:
        """Alias for route_subtask to satisfy standard entry method requirement."""
        return self.route_subtask(subtask, **kwargs)

    def route_subtask(
        self,
        subtask: OrchestratorSubtask,
        *,
        context: dict[str, str] | None = None,
        dispatcher: Callable[[SpecialistAgentRecord, OrchestratorSubtask, dict[str, str]], object] | None = None,
    ) -> SpecialistRouterResult: # FI_NEURAL_LINK_VERIFIED
        context = dict(context or {})
        match = self._select_best_agent(subtask)
        if match is None:
            decision = RoutingDecisionRecord(
                subtask_id=subtask.subtask_id,
                escalated=True,
                reason=f"No specialist agent matched subtask '{subtask.subtask_id}'; escalated to {self.escalation_target}.",
            )
            self._append_decision(decision)
            return SpecialistRouterResult(
                succeeded=False,
                decision=decision,
                reason=decision.reason,
            )

        agent, capability = match
        execution_result = None
        if dispatcher is not None:
            execution_result = dispatcher(agent, subtask, context)
        subtask_result = self._normalize_result(subtask, agent, execution_result)
        decision = RoutingDecisionRecord(
            subtask_id=subtask.subtask_id,
            selected_agent=agent.agent_name,
            selected_module=agent.module_reference,
            matched_capability=capability,
            escalated=False,
            reason=None if subtask_result.status is OrchestratorSubtaskStatus.COMPLETED else subtask_result.reason,
        )
        self._append_decision(decision)
        return SpecialistRouterResult(
            succeeded=subtask_result.status is not OrchestratorSubtaskStatus.FAILED,
            decision=decision,
            subtask_result=subtask_result,
            reason=decision.reason,
        )

    def route_subtasks(
        self,
        subtasks: list[OrchestratorSubtask],
        *,
        context: dict[str, str] | None = None,
        dispatcher: Callable[[SpecialistAgentRecord, OrchestratorSubtask, dict[str, str]], object] | None = None,
    ) -> SpecialistRouterResult:
        decisions: list[RoutingDecisionRecord] = []
        for subtask in subtasks:
            routed = self.route_subtask(subtask, context=context, dispatcher=dispatcher)
            if routed.decision is not None:
                decisions.append(routed.decision)
        return SpecialistRouterResult(
            succeeded=all(not decision.escalated for decision in decisions),
            decisions=decisions,
            reason=None if all(not decision.escalated for decision in decisions) else "One or more subtasks were escalated.",
        )

    def list_decisions(self) -> list[RoutingDecisionRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [self._deserialize_decision(item) for item in payload.get("decisions", [])]

    def _select_best_agent(
        self,
        subtask: OrchestratorSubtask,
    ) -> tuple[SpecialistAgentRecord, str] | None:
        best_match: tuple[int, SpecialistAgentRecord, str] | None = None
        target_text = f"{subtask.responsible_module or ''} {subtask.description or ''}".casefold()
        for agent in self._registry.values():
            for capability in agent.capabilities:
                if capability.casefold() in target_text:
                    score = len(capability)
                    if best_match is None or score > best_match[0]:
                        best_match = (score, agent, capability)
        if best_match is None:
            return None
        return best_match[1], best_match[2]

    def _normalize_result(
        self,
        subtask: OrchestratorSubtask,
        agent: SpecialistAgentRecord,
        execution_result: object,
    ) -> OrchestratorSubtaskResult:
        if isinstance(execution_result, OrchestratorSubtaskResult):
            return execution_result
        succeeded = True if execution_result is None else bool(getattr(execution_result, "succeeded", execution_result is not False))
        reason = None if execution_result is None else getattr(execution_result, "reason", None) or getattr(execution_result, "failure_reason", None)
        produced_outputs: dict[str, str] = {}
        if isinstance(execution_result, dict):
            produced_outputs = {str(key): str(value) for key, value in execution_result.items()}
        elif execution_result is not None and hasattr(execution_result, "response_text"):
            response_text = getattr(execution_result, "response_text")
            if response_text is not None:
                produced_outputs[subtask.expected_outputs[0] if subtask.expected_outputs else "result"] = str(response_text)
        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.COMPLETED if succeeded else OrchestratorSubtaskStatus.FAILED,
            responsible_module=agent.module_reference or agent.agent_name,
            produced_outputs=produced_outputs,
            reason=reason,
        )

    def _append_decision(
        self,
        decision: RoutingDecisionRecord,
    ) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"decisions": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("decisions", []).append(self._serialize_decision(decision))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_decision(self, decision: RoutingDecisionRecord) -> dict:
        return {
            "subtask_id": decision.subtask_id,
            "selected_agent": decision.selected_agent,
            "selected_module": decision.selected_module,
            "matched_capability": decision.matched_capability,
            "escalated": decision.escalated,
            "reason": decision.reason,
            "timestamp": decision.timestamp.isoformat(),
        }

    def _deserialize_decision(self, payload: dict) -> RoutingDecisionRecord:
        from datetime import datetime

        return RoutingDecisionRecord(
            subtask_id=payload["subtask_id"],
            selected_agent=payload.get("selected_agent"),
            selected_module=payload.get("selected_module"),
            matched_capability=payload.get("matched_capability"),
            escalated=bool(payload.get("escalated", False)),
            reason=payload.get("reason"),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )
