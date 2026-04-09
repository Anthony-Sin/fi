from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from desktop_automation_agent.models import (
    OrchestratorAgentResult,
    OrchestratorExecutionSummary,
    OrchestratorSubtask,
    OrchestratorSubtaskResult,
    OrchestratorSubtaskStatus,
    OrchestratorTaskPlan,
)


@dataclass(slots=True)
class OrchestratorAgentCore:
    fallback_module: str = "human_review"
    task_decomposer: object | None = None
    max_decomposition_depth: int = 3
    execution_expansion_depth: int | None = None

    def create_plan(
        self,
        task_description: str,
    ) -> OrchestratorTaskPlan:
        if self.task_decomposer is not None:
            decomposition = self.task_decomposer.decompose(
                task_description,
                max_depth=self.max_decomposition_depth,
                execution_expansion_depth=self.execution_expansion_depth,
            )
            if getattr(decomposition, "succeeded", False) and getattr(decomposition, "tree", None) is not None:
                return self.task_decomposer.to_orchestrator_plan(decomposition.tree)
        return OrchestratorTaskPlan(
            task_description=task_description,
            subtasks=self._decompose_task(task_description),
        )

    def execute_plan(
        self,
        plan: OrchestratorTaskPlan,
        *,
        executor: Callable[[OrchestratorSubtask, dict[str, str]], object],
    ) -> OrchestratorAgentResult:
        results: list[OrchestratorSubtaskResult] = []
        outputs: dict[str, str] = {}
        result_by_id: dict[str, OrchestratorSubtaskResult] = {}

        for subtask in plan.subtasks:
            blocked_dependency = self._failed_dependency(subtask, result_by_id)
            if blocked_dependency is not None:
                rerouted = self._reroute_subtask(subtask, blocked_dependency)
                results.append(rerouted)
                result_by_id[subtask.subtask_id] = rerouted
                continue

            execution_result = executor(subtask, dict(outputs))
            normalized = self._normalize_execution_result(subtask, execution_result)
            results.append(normalized)
            result_by_id[subtask.subtask_id] = normalized
            if normalized.status is OrchestratorSubtaskStatus.COMPLETED:
                outputs.update(normalized.produced_outputs)

        summary = self._build_summary(results, outputs)
        return OrchestratorAgentResult(
            succeeded=summary.succeeded,
            plan=plan,
            subtask_results=results,
            summary=summary,
            reason=summary.reason,
        )

    def _decompose_task(
        self,
        task_description: str,
    ) -> list[OrchestratorSubtask]:
        segments = self._split_task_description(task_description)
        subtasks: list[OrchestratorSubtask] = []
        prior_subtask_id: str | None = None

        for index, segment in enumerate(segments, start=1):
            subtask_id = f"subtask-{index}"
            responsible_module = self._infer_module(segment)
            expected_outputs = [f"{subtask_id}_result"]
            subtasks.append(
                OrchestratorSubtask(
                    subtask_id=subtask_id,
                    description=segment,
                    responsible_module=responsible_module,
                    required_inputs=[] if prior_subtask_id is None else [f"{prior_subtask_id}_result"],
                    expected_outputs=expected_outputs,
                    dependency_ids=[] if prior_subtask_id is None else [prior_subtask_id],
                )
            )
            prior_subtask_id = subtask_id

        if not subtasks:
            subtasks.append(
                OrchestratorSubtask(
                    subtask_id="subtask-1",
                    description=task_description.strip() or "Execute requested task",
                    responsible_module=self._infer_module(task_description),
                    expected_outputs=["subtask-1_result"],
                )
            )
        return subtasks

    def _split_task_description(
        self,
        task_description: str,
    ) -> list[str]:
        normalized = re.sub(r"\s+", " ", task_description.strip())
        if not normalized:
            return []
        segments = re.split(r"\b(?:then|and then|after that|next|finally)\b", normalized, flags=re.IGNORECASE)
        cleaned = [segment.strip(" ,.;") for segment in segments if segment.strip(" ,.;")]
        return cleaned or [normalized]

    def _infer_module(self, segment: str) -> str:
        normalized = segment.casefold()
        if any(token in normalized for token in ("launch", "open application", "start app", "open browser")):
            return "application_launcher"
        if any(token in normalized for token in ("account", "login", "profile", "credential", "session")):
            return "account_rotation_orchestrator"
        if any(token in normalized for token in ("chat", "prompt", "ai", "llm", "assistant")):
            return "ai_interface_navigator"
        if any(token in normalized for token in ("form", "field", "dropdown", "checkbox")):
            return "form_automation"
        if any(token in normalized for token in ("menu", "dialog", "modal", "popup")):
            return "menu_dialog_navigator"
        if any(token in normalized for token in ("workflow", "switch application", "clipboard", "handoff")):
            return "multi_application_workflow_coordinator"
        if any(token in normalized for token in ("navigate", "click", "scroll", "verify", "wait")):
            return "navigation_step_sequencer"
        return "desktop_automation"

    def _failed_dependency(
        self,
        subtask: OrchestratorSubtask,
        result_by_id: dict[str, OrchestratorSubtaskResult],
    ) -> OrchestratorSubtaskResult | None:
        for dependency_id in subtask.dependency_ids:
            dependency_result = result_by_id.get(dependency_id)
            if dependency_result is None:
                continue
            if dependency_result.status in {
                OrchestratorSubtaskStatus.FAILED,
                OrchestratorSubtaskStatus.SKIPPED,
                OrchestratorSubtaskStatus.REROUTED,
            }:
                return dependency_result
        return None

    def _reroute_subtask(
        self,
        subtask: OrchestratorSubtask,
        dependency_result: OrchestratorSubtaskResult,
    ) -> OrchestratorSubtaskResult:
        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.REROUTED,
            responsible_module=subtask.responsible_module,
            rerouted_to=self.fallback_module,
            reason=(
                f"Dependency {dependency_result.subtask_id} did not complete successfully; "
                f"rerouted to {self.fallback_module}."
            ),
        )

    def _normalize_execution_result(
        self,
        subtask: OrchestratorSubtask,
        execution_result: object,
    ) -> OrchestratorSubtaskResult:
        if isinstance(execution_result, OrchestratorSubtaskResult):
            return execution_result

        succeeded = bool(getattr(execution_result, "succeeded", execution_result is not False))
        produced_outputs = self._extract_outputs(subtask, execution_result)
        reason = getattr(execution_result, "reason", None) or getattr(execution_result, "failure_reason", None)
        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.COMPLETED if succeeded else OrchestratorSubtaskStatus.FAILED,
            responsible_module=subtask.responsible_module,
            produced_outputs=produced_outputs,
            reason=reason,
        )

    def _extract_outputs(
        self,
        subtask: OrchestratorSubtask,
        execution_result: object,
    ) -> dict[str, str]:
        if execution_result is None:
            return {}
        if isinstance(execution_result, dict):
            return {str(key): str(value) for key, value in execution_result.items()}

        outputs: dict[str, str] = {}
        for output_name in subtask.expected_outputs:
            if hasattr(execution_result, output_name):
                value = getattr(execution_result, output_name)
                if value is not None:
                    outputs[output_name] = str(value)
        if not outputs and hasattr(execution_result, "response_text"):
            value = getattr(execution_result, "response_text")
            if value is not None:
                outputs[subtask.expected_outputs[0]] = str(value)
        if not outputs and hasattr(execution_result, "reason") and getattr(execution_result, "reason") is not None:
            outputs[subtask.expected_outputs[0]] = str(getattr(execution_result, "reason"))
        return outputs

    def _build_summary(
        self,
        results: list[OrchestratorSubtaskResult],
        outputs: dict[str, str],
    ) -> OrchestratorExecutionSummary:
        completed = [item.subtask_id for item in results if item.status is OrchestratorSubtaskStatus.COMPLETED]
        failed = [item.subtask_id for item in results if item.status is OrchestratorSubtaskStatus.FAILED]
        skipped = [item.subtask_id for item in results if item.status is OrchestratorSubtaskStatus.SKIPPED]
        rerouted = [item.subtask_id for item in results if item.status is OrchestratorSubtaskStatus.REROUTED]
        succeeded = not failed and not rerouted
        reason = None
        if failed:
            reason = f"Failed subtasks: {', '.join(failed)}"
        elif rerouted:
            reason = f"Rerouted subtasks: {', '.join(rerouted)}"
        return OrchestratorExecutionSummary(
            succeeded=succeeded,
            completed_subtasks=completed,
            failed_subtasks=failed,
            skipped_subtasks=skipped,
            rerouted_subtasks=rerouted,
            final_outputs=dict(outputs),
            reason=reason,
        )
