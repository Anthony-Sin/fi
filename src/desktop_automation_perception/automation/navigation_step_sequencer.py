from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Callable

from desktop_automation_perception.contracts import ApplicationNavigator, InputRunner, StateVerifier
from desktop_automation_perception.models import (
    ApplicationLaunchRequest,
    InputAction,
    InputActionType,
    InputTarget,
    NavigationSequenceMode,
    NavigationSequenceResult,
    NavigationStep,
    NavigationStepActionType,
    NavigationStepOutcome,
    ScreenVerificationCheck,
    ScreenVerificationResult,
    WindowReference,
)


@dataclass(slots=True)
class NavigationStepSequencer:
    input_runner: InputRunner
    verifier: StateVerifier
    launcher: ApplicationNavigator | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic
    abort_checker: Callable[[], bool] = lambda: False
    anti_loop_detector: object | None = None

    def run(
        self,
        steps: list[NavigationStep],
        *,
        mode: NavigationSequenceMode = NavigationSequenceMode.STRICT,
    ) -> NavigationSequenceResult:
        outcomes: list[NavigationStepOutcome] = []

        for step in steps:
            if self.abort_checker():
                return NavigationSequenceResult(
                    succeeded=False,
                    mode=mode,
                    outcomes=outcomes,
                    failed_step_id=step.step_id,
                    reason="Execution aborted by fail-safe controller.",
                )
            if self.anti_loop_detector is not None:
                loop_result = self.anti_loop_detector.before_step(
                    step.step_id,
                    metadata={
                        "action_type": step.action_type.value,
                        "target_description": step.target_description,
                    },
                )
                if getattr(loop_result, "triggered", False):
                    return NavigationSequenceResult(
                        succeeded=False,
                        mode=mode,
                        outcomes=outcomes,
                        failed_step_id=step.step_id,
                        reason=getattr(loop_result, "reason", "Anti-loop detector halted execution."),
                    )
            outcome = self._run_step(step)
            outcomes.append(outcome)

            if outcome.succeeded:
                continue

            if mode is NavigationSequenceMode.LENIENT and step.optional:
                outcomes[-1] = NavigationStepOutcome(
                    step_id=outcome.step_id,
                    action_type=outcome.action_type,
                    succeeded=False,
                    skipped=True,
                    replayable=outcome.replayable,
                    execution_time_seconds=outcome.execution_time_seconds,
                    reason=outcome.reason,
                    action_result=outcome.action_result,
                    precondition_result=outcome.precondition_result,
                    postcondition_result=outcome.postcondition_result,
                )
                continue

            return NavigationSequenceResult(
                succeeded=False,
                mode=mode,
                outcomes=outcomes,
                failed_step_id=step.step_id,
                reason=outcome.reason or "Navigation sequence failed.",
            )

        return NavigationSequenceResult(
            succeeded=all(item.succeeded or item.skipped for item in outcomes),
            mode=mode,
            outcomes=outcomes,
        )

    def replay_step(self, step: NavigationStep) -> NavigationStepOutcome:
        if self.anti_loop_detector is not None:
            loop_result = self.anti_loop_detector.before_step(
                step.step_id,
                metadata={
                    "action_type": step.action_type.value,
                    "target_description": step.target_description,
                    "replay": True,
                },
            )
            if getattr(loop_result, "triggered", False):
                return NavigationStepOutcome(
                    step_id=step.step_id,
                    action_type=step.action_type,
                    succeeded=False,
                    replayable=False,
                    execution_time_seconds=0.0,
                    reason=getattr(loop_result, "reason", "Anti-loop detector halted execution."),
                )
        return self._run_step(step)

    def _run_step(self, step: NavigationStep) -> NavigationStepOutcome:
        started_at = self.monotonic_fn()
        precondition_result = self._verify_checks(step.preconditions)
        if precondition_result is not None and precondition_result.failed_checks:
            return NavigationStepOutcome(
                step_id=step.step_id,
                action_type=step.action_type,
                succeeded=False,
                replayable=False,
                execution_time_seconds=self.monotonic_fn() - started_at,
                reason="Step precondition state is not satisfied.",
                precondition_result=precondition_result,
            )

        action_result, action_reason = self._execute_action(step)
        if action_reason is not None:
            return NavigationStepOutcome(
                step_id=step.step_id,
                action_type=step.action_type,
                succeeded=False,
                replayable=True,
                execution_time_seconds=self.monotonic_fn() - started_at,
                reason=action_reason,
                action_result=action_result,
                precondition_result=precondition_result,
            )

        postcondition_result = self._verify_checks(step.expected_post_action_state)
        if postcondition_result is not None and postcondition_result.failed_checks:
            return NavigationStepOutcome(
                step_id=step.step_id,
                action_type=step.action_type,
                succeeded=False,
                replayable=True,
                execution_time_seconds=self.monotonic_fn() - started_at,
                reason="Step post-condition verification failed.",
                action_result=action_result,
                precondition_result=precondition_result,
                postcondition_result=postcondition_result,
            )

        return NavigationStepOutcome(
            step_id=step.step_id,
            action_type=step.action_type,
            succeeded=True,
            replayable=True,
            execution_time_seconds=self.monotonic_fn() - started_at,
            action_result=action_result,
            precondition_result=precondition_result,
            postcondition_result=postcondition_result,
        )

    def _execute_action(
        self,
        step: NavigationStep,
    ) -> tuple[object | None, str | None]:
        if step.action_type is NavigationStepActionType.WAIT:
            delay = float(step.input_data.get("seconds", step.timeout_seconds))
            self.sleep_fn(delay)
            return ({"slept_seconds": delay}, None)

        if step.action_type is NavigationStepActionType.VERIFY:
            verification = self._verify_checks(step.expected_post_action_state)
            if verification is None:
                return (None, "Verify step requires expected post-action checks.")
            if verification.failed_checks:
                return (verification, "Verification step failed.")
            return (verification, None)

        if step.action_type is NavigationStepActionType.NAVIGATE:
            if self.launcher is None:
                return (None, "Navigate step requires an application launcher.")
            launch_request = step.input_data.get("launch_request")
            if not isinstance(launch_request, ApplicationLaunchRequest):
                return (None, "Navigate step requires an ApplicationLaunchRequest in input_data['launch_request'].")
            result = self.launcher.launch(launch_request)
            if not getattr(result, "succeeded", False):
                return (result, getattr(result, "reason", "Navigation step failed to launch the target application."))
            return (result, None)

        input_action = self._build_input_action(step)
        if input_action is None:
            return (None, "Unsupported or invalid navigation step input data.")
        result = self.input_runner.run([input_action])
        if not getattr(result, "succeeded", False):
            return (result, getattr(result, "failure_reason", "Input action failed."))
        return (result, None)

    def _build_input_action(self, step: NavigationStep) -> InputAction | None:
        target = self._build_input_target(step.input_data)
        if step.action_type is NavigationStepActionType.CLICK:
            return InputAction(
                action_type=InputActionType.CLICK,
                target=target,
                position=step.input_data.get("position"),
                button=step.input_data.get("button", "left"),
            )

        if step.action_type is NavigationStepActionType.TYPE:
            text = step.input_data.get("text")
            if text is None:
                return None
            return InputAction(
                action_type=InputActionType.TYPE_TEXT,
                target=target,
                text=str(text),
            )

        if step.action_type is NavigationStepActionType.SCROLL:
            amount = step.input_data.get("scroll_amount")
            if amount is None:
                return None
            return InputAction(
                action_type=InputActionType.SCROLL,
                target=target,
                scroll_amount=int(amount),
            )

        return None

    def _build_input_target(self, input_data: dict) -> InputTarget | None:
        window_title = input_data.get("window_title")
        window_handle = input_data.get("window_handle")
        element_bounds = input_data.get("element_bounds")
        if window_title is None and window_handle is None and element_bounds is None:
            return None
        return InputTarget(
            window=WindowReference(title=window_title, handle=window_handle)
            if window_title is not None or window_handle is not None
            else None,
            element_bounds=element_bounds,
        )

    def _verify_checks(
        self,
        checks: list[ScreenVerificationCheck],
    ) -> ScreenVerificationResult | None:
        if not checks:
            return None
        return self.verifier.verify(checks)
