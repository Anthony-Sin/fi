from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic
from typing import Callable

from desktop_automation_agent.contracts import AIInterfaceNavigationExecutor, PromptTemplateRenderer
from desktop_automation_agent.models import (
    PipelinePauseDecision,
    PipelineReviewRequest,
    PipelineReviewResult,
    PipelineResponseAction,
    PipelineStatus,
    PromptPipelineResult,
    PromptPipelineStep,
    PromptPipelineStepLog,
)


@dataclass(slots=True)
class PromptPipelineRunner:
    template_manager: PromptTemplateRenderer
    navigator: AIInterfaceNavigationExecutor
    review_callback: Callable[[PipelineReviewRequest], PipelineReviewResult] | None = None
    monotonic_fn: Callable[[], float] = monotonic

    def run(
        self,
        steps: list[PromptPipelineStep],
        *,
        initial_variables: dict[str, str] | None = None,
    ) -> PromptPipelineResult:
        variables = dict(initial_variables or {})
        logs: list[PromptPipelineStepLog] = []

        for step in steps:
            step_start = self.monotonic_fn()
            merged_variables = {**variables, **step.template_variables}
            rendered = self.template_manager.render_template(step.template_name, merged_variables)
            if not rendered.succeeded or rendered.rendered_prompt is None:
                logs.append(
                    PromptPipelineStepLog(
                        step_id=step.step_id,
                        interface_name=step.interface.interface_name,
                        template_name=step.template_name,
                        prompt="",
                        execution_time_seconds=self.monotonic_fn() - step_start,
                        succeeded=False,
                        reason=rendered.reason or "Prompt template rendering failed.",
                    )
                )
                return PromptPipelineResult(
                    succeeded=False,
                    status=PipelineStatus.FAILED,
                    logs=logs,
                    final_variables=variables,
                    failed_step_id=step.step_id,
                    reason=rendered.reason or "Prompt template rendering failed.",
                )

            prompt = rendered.rendered_prompt
            navigation = self.navigator.navigate(
                prompt=prompt,
                interface=step.interface,
                injection_method=step.injection_method,
            )
            execution_time_seconds = self.monotonic_fn() - step_start

            log = PromptPipelineStepLog(
                step_id=step.step_id,
                interface_name=step.interface.interface_name,
                template_name=step.template_name,
                prompt=prompt,
                response=navigation.response_text,
                execution_time_seconds=execution_time_seconds,
                succeeded=navigation.succeeded,
                reason=navigation.reason,
            )

            if not navigation.succeeded or navigation.response_text is None:
                logs.append(log)
                return PromptPipelineResult(
                    succeeded=False,
                    status=PipelineStatus.FAILED,
                    logs=logs,
                    final_variables=variables,
                    failed_step_id=step.step_id,
                    reason=navigation.reason or "Prompt pipeline step failed.",
                )

            if step.expected_response_pattern is not None:
                matched = re.search(step.expected_response_pattern, navigation.response_text, re.MULTILINE) is not None
                log.matched_expected_pattern = matched
                if not matched:
                    log.succeeded = False
                    log.reason = "Response did not match the expected pattern."
                    logs.append(log)
                    return PromptPipelineResult(
                        succeeded=False,
                        status=PipelineStatus.FAILED,
                        logs=logs,
                        final_variables=variables,
                        failed_step_id=step.step_id,
                        reason=log.reason,
                    )

            self._apply_response_action(step, navigation.response_text, variables)

            if step.allow_human_review:
                log.review_requested = True
                review_result = self._review_step(
                    step=step,
                    prompt=prompt,
                    response_text=navigation.response_text,
                    rendered_variables=merged_variables,
                )
                log.review_decision = review_result.decision
                if review_result.decision is PipelinePauseDecision.REJECTED:
                    log.succeeded = False
                    log.reason = review_result.reason or "Pipeline paused for human review."
                    logs.append(log)
                    return PromptPipelineResult(
                        succeeded=False,
                        status=PipelineStatus.PAUSED,
                        logs=logs,
                        final_variables=variables,
                        failed_step_id=step.step_id,
                        reason=log.reason,
                    )

            logs.append(log)

        return PromptPipelineResult(
            succeeded=True,
            status=PipelineStatus.COMPLETED,
            logs=logs,
            final_variables=variables,
        )

    def _apply_response_action(
        self,
        step: PromptPipelineStep,
        response_text: str,
        variables: dict[str, str],
    ) -> None:
        if step.response_action is PipelineResponseAction.NOOP:
            return

        if step.response_action is PipelineResponseAction.STORE_AS:
            variables[step.output_variable_name] = response_text
            return

        if step.response_action is PipelineResponseAction.APPEND_TO_VARIABLE:
            target = step.action_target_variable or step.output_variable_name
            previous = variables.get(target, "")
            variables[target] = f"{previous}\n{response_text}".strip() if previous else response_text
            return

        if step.response_action is PipelineResponseAction.REPLACE_VARIABLES:
            for key in list(variables):
                variables[key] = response_text
            variables[step.output_variable_name] = response_text
            return

        raise ValueError(f"Unsupported pipeline response action: {step.response_action}")

    def _review_step(
        self,
        *,
        step: PromptPipelineStep,
        prompt: str,
        response_text: str,
        rendered_variables: dict[str, str],
    ) -> PipelineReviewResult:
        if self.review_callback is None:
            return PipelineReviewResult(
                decision=PipelinePauseDecision.REJECTED,
                reason="Human review was requested but no review callback is configured.",
            )
        return self.review_callback(
            PipelineReviewRequest(
                step_id=step.step_id,
                prompt=prompt,
                response_text=response_text,
                rendered_variables=rendered_variables,
            )
        )
