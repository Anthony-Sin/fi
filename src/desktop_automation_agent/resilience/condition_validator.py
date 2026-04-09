from __future__ import annotations

from dataclasses import dataclass

from desktop_automation_agent.contracts import StateVerifier
from desktop_automation_agent.models import (
    ConditionDescription,
    ConditionValidationResult,
    RetryConfiguration,
    RetryFailureResult,
    ValidationDisposition,
    ValidationPhase,
)
from desktop_automation_agent.resilience.retry_engine import (
    ExponentialBackoffRetryEngine,
    RetryExhaustedError,
)


@dataclass(slots=True)
class PrePostConditionValidator:
    verifier: StateVerifier
    retry_engine: ExponentialBackoffRetryEngine | None = None

    def validate_pre_condition(
        self,
        condition: ConditionDescription | None,
    ) -> ConditionValidationResult:
        if condition is None:
            return ConditionValidationResult(
                succeeded=True,
                phase=ValidationPhase.PRE_CONDITION,
                disposition=ValidationDisposition.ALLOW,
                condition=ConditionDescription(condition_id="none", description="No pre-condition."),
            )
        verification = self.verifier.verify(condition.checks)
        failed = bool(getattr(verification, "failed_checks", []))
        return ConditionValidationResult(
            succeeded=not failed,
            phase=ValidationPhase.PRE_CONDITION,
            disposition=ValidationDisposition.ALLOW if not failed else ValidationDisposition.BLOCK,
            condition=condition,
            verification=verification,
            reason=None if not failed else "Pre-condition failed; action execution is blocked.",
        )

    def validate_post_condition(
        self,
        condition: ConditionDescription | None,
        *,
        retry_configuration: RetryConfiguration | None = None,
        escalate_on_failure: bool = True,
    ) -> ConditionValidationResult:
        if condition is None:
            return ConditionValidationResult(
                succeeded=True,
                phase=ValidationPhase.POST_CONDITION,
                disposition=ValidationDisposition.ALLOW,
                condition=ConditionDescription(condition_id="none", description="No post-condition."),
            )

        if retry_configuration is None or self.retry_engine is None:
            verification = self.verifier.verify(condition.checks)
            failed = bool(getattr(verification, "failed_checks", []))
            return ConditionValidationResult(
                succeeded=not failed,
                phase=ValidationPhase.POST_CONDITION,
                disposition=self._post_failure_disposition(failed, escalate_on_failure),
                condition=condition,
                verification=verification,
                reason=None if not failed else self._post_failure_reason(escalate_on_failure),
            )

        try:
            verification = self.retry_engine.run(
                lambda: self._verify_or_raise(condition),
                configuration=retry_configuration,
            )
            return ConditionValidationResult(
                succeeded=True,
                phase=ValidationPhase.POST_CONDITION,
                disposition=ValidationDisposition.ALLOW,
                condition=condition,
                verification=verification,
            )
        except RetryExhaustedError as exc:
            return ConditionValidationResult(
                succeeded=False,
                phase=ValidationPhase.POST_CONDITION,
                disposition=ValidationDisposition.ESCALATE if escalate_on_failure else ValidationDisposition.RETRY,
                condition=condition,
                retry_failure=exc.failure,
                reason=self._post_failure_reason(escalate_on_failure),
            )

    def _verify_or_raise(self, condition: ConditionDescription):
        verification = self.verifier.verify(condition.checks)
        if getattr(verification, "failed_checks", []):
            reason = ", ".join(item.detail or item.check_id for item in verification.failed_checks) or "Post-condition failed."
            error = RuntimeError(reason)
            setattr(error, "verification", verification)
            raise error
        return verification

    def _post_failure_disposition(
        self,
        failed: bool,
        escalate_on_failure: bool,
    ) -> ValidationDisposition:
        if not failed:
            return ValidationDisposition.ALLOW
        return ValidationDisposition.ESCALATE if escalate_on_failure else ValidationDisposition.RETRY

    def _post_failure_reason(self, escalate_on_failure: bool) -> str:
        return (
            "Post-condition failed after validation; escalation required."
            if escalate_on_failure
            else "Post-condition failed after validation; retry recommended."
        )
