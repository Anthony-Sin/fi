from __future__ import annotations

from desktop_automation_perception._time import utc_now

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from desktop_automation_perception.models import (
    BranchComparisonOperator,
    BranchConditionSpecification,
    BranchConditionType,
    BranchEvaluationContext,
    BranchEvaluationRecord,
    BranchEvaluationResult,
    BranchOption,
    BranchValueSource,
)


@dataclass(slots=True)
class ConditionalBranchEvaluator:
    custom_predicates: dict[str, Callable[[Any, BranchEvaluationContext, BranchConditionSpecification], bool]] = (
        field(default_factory=dict)
    )
    decision_log: list[BranchEvaluationRecord] = field(default_factory=list)

    def evaluate(
        self,
        branches: list[BranchOption],
        *,
        context: BranchEvaluationContext,
    ) -> BranchEvaluationResult:
        records: list[BranchEvaluationRecord] = []
        default_branch = next((branch for branch in branches if branch.default), None)

        for branch in branches:
            if branch.condition is None:
                if branch.default:
                    continue
                record = BranchEvaluationRecord(
                    condition_id=None,
                    condition_type=None,
                    source=None,
                    field_path=None,
                    matched=True,
                    selected_branch_id=branch.branch_id,
                    selected_next_step_id=branch.next_step_id,
                    detail="Unconditional branch selected.",
                    timestamp=utc_now(),
                )
                self._log_record(record)
                records.append(record)
                return BranchEvaluationResult(succeeded=True, selected_branch=branch, records=records)

            matched, actual_value, detail = self._evaluate_condition(branch.condition, context)
            record = BranchEvaluationRecord(
                condition_id=branch.condition.condition_id,
                condition_type=branch.condition.condition_type,
                source=branch.condition.source,
                field_path=branch.condition.field_path,
                actual_value=actual_value,
                expected_value=branch.condition.expected_value,
                matched=matched,
                selected_branch_id=branch.branch_id if matched else None,
                selected_next_step_id=branch.next_step_id if matched else None,
                detail=detail,
                timestamp=utc_now(),
            )
            self._log_record(record)
            records.append(record)
            if matched:
                return BranchEvaluationResult(succeeded=True, selected_branch=branch, records=records)

        if default_branch is not None:
            record = BranchEvaluationRecord(
                condition_id=default_branch.condition.condition_id if default_branch.condition is not None else None,
                condition_type=default_branch.condition.condition_type if default_branch.condition is not None else None,
                source=default_branch.condition.source if default_branch.condition is not None else None,
                field_path=default_branch.condition.field_path if default_branch.condition is not None else None,
                actual_value=None,
                expected_value=default_branch.condition.expected_value if default_branch.condition is not None else None,
                matched=True,
                selected_branch_id=default_branch.branch_id,
                selected_next_step_id=default_branch.next_step_id,
                detail="Default branch selected after no prior condition matched.",
                timestamp=utc_now(),
            )
            self._log_record(record)
            records.append(record)
            return BranchEvaluationResult(succeeded=True, selected_branch=default_branch, records=records)

        return BranchEvaluationResult(
            succeeded=False,
            records=records,
            reason="No branch condition matched and no default branch was configured.",
        )

    def register_predicate(
        self,
        name: str,
        predicate: Callable[[Any, BranchEvaluationContext, BranchConditionSpecification], bool],
    ) -> None:
        self.custom_predicates[name] = predicate

    def _evaluate_condition(
        self,
        condition: BranchConditionSpecification,
        context: BranchEvaluationContext,
    ) -> tuple[bool, Any, str]:
        actual_value = self._resolve_value(condition, context)

        if condition.condition_type is BranchConditionType.STRING_MATCH:
            matched = self._match_string(actual_value, condition.operator, condition.expected_value)
            return matched, actual_value, self._detail(condition, actual_value, matched)

        if condition.condition_type is BranchConditionType.NUMERIC_COMPARISON:
            matched = self._match_numeric(actual_value, condition.operator, condition.expected_value)
            return matched, actual_value, self._detail(condition, actual_value, matched)

        if condition.condition_type is BranchConditionType.ELEMENT_PRESENCE:
            matched = bool(actual_value) is bool(condition.expected_value if condition.expected_value is not None else True)
            return matched, actual_value, self._detail(condition, actual_value, matched)

        if condition.condition_type is BranchConditionType.CUSTOM_PREDICATE:
            predicate = self.custom_predicates.get(condition.predicate_name or "")
            if predicate is None:
                return False, actual_value, f"Custom predicate {condition.predicate_name!r} is not registered."
            matched = bool(predicate(actual_value, context, condition))
            return matched, actual_value, self._detail(condition, actual_value, matched)

        return False, actual_value, "Unsupported branch condition type."

    def _resolve_value(self, condition: BranchConditionSpecification, context: BranchEvaluationContext) -> Any:
        if condition.source is BranchValueSource.STEP_OUTPUT:
            root = context.step_output
        elif condition.source is BranchValueSource.SCREEN_OBSERVATION:
            root = context.screen_observations
        else:
            root = context.workflow_data
        return self._walk_path(root, condition.field_path)

    def _walk_path(self, payload: Any, field_path: str | None) -> Any:
        if field_path is None or field_path == "":
            return payload
        current = payload
        for part in field_path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current

    def _match_string(self, actual: Any, operator: BranchComparisonOperator, expected: Any) -> bool:
        actual_text = "" if actual is None else str(actual)
        expected_text = "" if expected is None else str(expected)
        if operator is BranchComparisonOperator.EQUALS:
            return actual_text == expected_text
        if operator is BranchComparisonOperator.NOT_EQUALS:
            return actual_text != expected_text
        if operator is BranchComparisonOperator.CONTAINS:
            return expected_text in actual_text
        if operator is BranchComparisonOperator.STARTS_WITH:
            return actual_text.startswith(expected_text)
        if operator is BranchComparisonOperator.ENDS_WITH:
            return actual_text.endswith(expected_text)
        return False

    def _match_numeric(self, actual: Any, operator: BranchComparisonOperator, expected: Any) -> bool:
        try:
            actual_number = float(actual)
            expected_number = float(expected)
        except (TypeError, ValueError):
            return False
        if operator is BranchComparisonOperator.EQUALS:
            return actual_number == expected_number
        if operator is BranchComparisonOperator.NOT_EQUALS:
            return actual_number != expected_number
        if operator is BranchComparisonOperator.GREATER_THAN:
            return actual_number > expected_number
        if operator is BranchComparisonOperator.GREATER_OR_EQUAL:
            return actual_number >= expected_number
        if operator is BranchComparisonOperator.LESS_THAN:
            return actual_number < expected_number
        if operator is BranchComparisonOperator.LESS_OR_EQUAL:
            return actual_number <= expected_number
        return False

    def _detail(
        self,
        condition: BranchConditionSpecification,
        actual_value: Any,
        matched: bool,
    ) -> str:
        return (
            f"Condition {condition.condition_id!r} evaluated against {condition.source.value}"
            f" field {condition.field_path!r}: actual={actual_value!r}, "
            f"expected={condition.expected_value!r}, matched={matched}."
        )

    def _log_record(self, record: BranchEvaluationRecord) -> None:
        self.decision_log.append(record)


