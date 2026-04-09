from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Callable

from desktop_automation_agent.contracts import FailureClassifier, InputRunner, StateVerifier
from desktop_automation_agent.models import (
    ErrorCategory,
    ErrorClassificationResult,
    InputAction,
    InputActionType,
    RecoveryStrategy,
    ScreenVerificationCheck,
    SelfHealingRecoveryRequest,
    SelfHealingRecoveryResult,
)


@dataclass(slots=True)
class SelfHealingRecoveryModule:
    classifier: FailureClassifier
    verifier: StateVerifier
    screenshot_backend: object | None = None
    input_runner: InputRunner | None = None
    refresh_callback: Callable[[], object] | None = None
    reauthenticate_callback: Callable[[], object] | None = None
    dismiss_dialog_callback: Callable[[], object] | None = None
    sleep_fn: Callable[[float], None] = sleep
    monotonic_fn: Callable[[], float] = monotonic

    def recover(
        self,
        request: SelfHealingRecoveryRequest,
        *,
        retry_step: Callable[[], object],
    ) -> SelfHealingRecoveryResult:
        initial_screenshot_path = self._capture_screenshot()
        classification = self._classify(request.error, initial_screenshot_path)
        strategy = self._resolve_strategy(classification, request)

        if strategy in {RecoveryStrategy.ABORT, RecoveryStrategy.ESCALATE}:
            return SelfHealingRecoveryResult(
                succeeded=False,
                classification=classification,
                strategy=strategy,
                initial_screenshot_path=initial_screenshot_path,
                reason="Recovery strategy does not permit automated healing.",
            )

        recovery_action_result, verification = self._apply_strategy(
            strategy=strategy,
            request=request,
        )

        if request.target_checks and getattr(verification, "failed_checks", []):
            return SelfHealingRecoveryResult(
                succeeded=False,
                classification=classification,
                strategy=strategy,
                initial_screenshot_path=initial_screenshot_path,
                recovery_action_result=recovery_action_result,
                verification=verification,
                reason="Recovered state could not be verified.",
            )

        step_result = retry_step()
        if not self._result_succeeded(step_result):
            return SelfHealingRecoveryResult(
                succeeded=False,
                classification=classification,
                strategy=strategy,
                initial_screenshot_path=initial_screenshot_path,
                recovery_action_result=recovery_action_result,
                verification=verification,
                step_result=step_result,
                reason=getattr(step_result, "reason", None)
                or getattr(step_result, "failure_reason", None)
                or "Retried step failed after recovery.",
            )

        return SelfHealingRecoveryResult(
            succeeded=True,
            classification=classification,
            strategy=strategy,
            initial_screenshot_path=initial_screenshot_path,
            recovery_action_result=recovery_action_result,
            verification=verification,
            step_result=step_result,
        )

    def _classify(
        self,
        error: Exception | object,
        screenshot_path: str | None,
    ) -> ErrorClassificationResult:
        if isinstance(error, ErrorClassificationResult):
            return error
        if isinstance(error, dict):
            payload = dict(error)
            payload.setdefault("screenshot_path", screenshot_path)
            return self.classifier.classify(payload)
        if screenshot_path is not None:
            try:
                setattr(error, "screenshot_path", getattr(error, "screenshot_path", screenshot_path))
            except Exception:
                pass
        return self.classifier.classify(error)

    def _resolve_strategy(
        self,
        classification: ErrorClassificationResult,
        request: SelfHealingRecoveryRequest,
    ) -> RecoveryStrategy:
        if request.strategy_override is not None:
            return request.strategy_override
        mapping = {
            ErrorCategory.UI_ELEMENT_NOT_FOUND: RecoveryStrategy.SCROLL_TO_FIND,
            ErrorCategory.APPLICATION_NOT_RESPONDING: RecoveryStrategy.REFRESH,
            ErrorCategory.SESSION_EXPIRED: RecoveryStrategy.REAUTHENTICATE,
            ErrorCategory.NETWORK_TIMEOUT: RecoveryStrategy.WAIT_FOR_LOADING,
            ErrorCategory.UNEXPECTED_DIALOG_APPEARED: RecoveryStrategy.DISMISS_DIALOG,
            ErrorCategory.SCREEN_STATE_MISMATCH: RecoveryStrategy.WAIT_FOR_LOADING,
            ErrorCategory.UNRECOGNIZED_ERROR: RecoveryStrategy.ABORT,
        }
        return mapping.get(classification.category, classification.recovery_strategy)

    def _apply_strategy(
        self,
        *,
        strategy: RecoveryStrategy,
        request: SelfHealingRecoveryRequest,
    ) -> tuple[object | None, object | None]:
        if strategy is RecoveryStrategy.REFRESH:
            action_result = self._refresh()
            return action_result, self._verify_target_state(request.target_checks)
        if strategy is RecoveryStrategy.REAUTHENTICATE:
            action_result = self._run_callback(
                self.reauthenticate_callback,
                "Re-authentication strategy requires a callback.",
            )
            return action_result, self._verify_target_state(request.target_checks)
        if strategy is RecoveryStrategy.DISMISS_DIALOG:
            action_result = self._run_callback(
                self.dismiss_dialog_callback,
                "Dismiss-dialog strategy requires a callback.",
            )
            return action_result, self._verify_target_state(request.target_checks)
        if strategy is RecoveryStrategy.SCROLL_TO_FIND:
            return self._scroll_to_find(request)
        if strategy is RecoveryStrategy.WAIT_FOR_LOADING:
            return self._wait_for_loading(request)
        return None, self._verify_target_state(request.target_checks)

    def _refresh(self) -> object:
        if self.refresh_callback is not None:
            return self.refresh_callback()
        if self.input_runner is None:
            raise RuntimeError("Refresh strategy requires a callback or input runner.")
        return self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.KEYPRESS,
                    key="f5",
                )
            ]
        )

    def _scroll_to_find(
        self,
        request: SelfHealingRecoveryRequest,
    ) -> tuple[object | None, object | None]:
        if self.input_runner is None:
            raise RuntimeError("Scroll recovery requires an input runner.")

        last_action_result = None
        verification = self._verify_target_state(request.target_checks)
        if not getattr(verification, "failed_checks", []):
            return {"scroll_attempts": 0}, verification

        for attempt in range(1, request.max_scroll_attempts + 1):
            last_action_result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.SCROLL,
                        target=request.input_target,
                        scroll_amount=request.scroll_amount,
                    )
                ]
            )
            verification = self._verify_target_state(request.target_checks)
            if not getattr(verification, "failed_checks", []):
                return (
                    {
                        "scroll_attempts": attempt,
                        "last_action_result": last_action_result,
                    },
                    verification,
                )

        return (
            {
                "scroll_attempts": request.max_scroll_attempts,
                "last_action_result": last_action_result,
            },
            verification,
        )

    def _wait_for_loading(
        self,
        request: SelfHealingRecoveryRequest,
    ) -> tuple[object | None, object | None]:
        started_at = self.monotonic_fn()
        verification = self._verify_target_state(request.target_checks)
        while getattr(verification, "failed_checks", []) and (
            self.monotonic_fn() - started_at < request.loading_timeout_seconds
        ):
            self.sleep_fn(request.loading_poll_interval_seconds)
            verification = self._verify_target_state(request.target_checks)
        waited_seconds = self.monotonic_fn() - started_at
        return ({"waited_seconds": waited_seconds}, verification)

    def _verify_target_state(
        self,
        checks: list[ScreenVerificationCheck],
    ):
        if not checks:
            return None
        return self.verifier.verify(checks)

    def _run_callback(
        self,
        callback: Callable[[], object] | None,
        missing_message: str,
    ) -> object:
        if callback is None:
            raise RuntimeError(missing_message)
        return callback()

    def _capture_screenshot(self) -> str | None:
        if self.screenshot_backend is None:
            return None
        return self.screenshot_backend.capture_screenshot_to_path()

    def _result_succeeded(self, result: object) -> bool:
        if isinstance(result, bool):
            return result
        if result is None:
            return True
        return bool(getattr(result, "succeeded", True))
