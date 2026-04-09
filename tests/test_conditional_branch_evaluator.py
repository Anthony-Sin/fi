from desktop_automation_perception.conditional_branch_evaluator import ConditionalBranchEvaluator
from desktop_automation_perception.models import (
    BranchComparisonOperator,
    BranchConditionSpecification,
    BranchConditionType,
    BranchEvaluationContext,
    BranchOption,
    BranchValueSource,
)


def test_conditional_branch_evaluator_selects_string_match_branch_and_logs_values():
    evaluator = ConditionalBranchEvaluator()
    context = BranchEvaluationContext(step_output={"status": "approved"})
    branches = [
        BranchOption(
            branch_id="approved-branch",
            next_step_id="continue",
            condition=BranchConditionSpecification(
                condition_id="status-check",
                condition_type=BranchConditionType.STRING_MATCH,
                source=BranchValueSource.STEP_OUTPUT,
                field_path="status",
                operator=BranchComparisonOperator.EQUALS,
                expected_value="approved",
            ),
        ),
        BranchOption(branch_id="fallback", next_step_id="review", default=True),
    ]

    result = evaluator.evaluate(branches, context=context)

    assert result.succeeded is True
    assert result.selected_branch is not None
    assert result.selected_branch.branch_id == "approved-branch"
    assert result.records[0].actual_value == "approved"
    assert result.records[0].selected_next_step_id == "continue"
    assert evaluator.decision_log[-1].condition_id == "status-check"


def test_conditional_branch_evaluator_supports_numeric_comparison_from_workflow_data():
    evaluator = ConditionalBranchEvaluator()
    context = BranchEvaluationContext(workflow_data={"retry_count": 3})
    branches = [
        BranchOption(
            branch_id="retry-limit",
            next_step_id="escalate",
            condition=BranchConditionSpecification(
                condition_id="retry-threshold",
                condition_type=BranchConditionType.NUMERIC_COMPARISON,
                source=BranchValueSource.WORKFLOW_DATA,
                field_path="retry_count",
                operator=BranchComparisonOperator.GREATER_OR_EQUAL,
                expected_value=3,
            ),
        ),
    ]

    result = evaluator.evaluate(branches, context=context)

    assert result.succeeded is True
    assert result.selected_branch is not None
    assert result.selected_branch.next_step_id == "escalate"


def test_conditional_branch_evaluator_supports_element_presence_from_screen_observations():
    evaluator = ConditionalBranchEvaluator()
    context = BranchEvaluationContext(screen_observations={"spinner_visible": False})
    branches = [
        BranchOption(
            branch_id="loaded",
            next_step_id="next-screen",
            condition=BranchConditionSpecification(
                condition_id="spinner-gone",
                condition_type=BranchConditionType.ELEMENT_PRESENCE,
                source=BranchValueSource.SCREEN_OBSERVATION,
                field_path="spinner_visible",
                expected_value=False,
            ),
        ),
    ]

    result = evaluator.evaluate(branches, context=context)

    assert result.succeeded is True
    assert result.selected_branch is not None
    assert result.selected_branch.branch_id == "loaded"


def test_conditional_branch_evaluator_supports_custom_predicates_and_default_branch():
    evaluator = ConditionalBranchEvaluator()
    evaluator.register_predicate(
        "has-urgent-flag",
        lambda actual, context, condition: bool(actual) and context.workflow_data.get("priority") == "high",
    )
    context = BranchEvaluationContext(
        step_output={"flags": {"urgent": True}},
        workflow_data={"priority": "high"},
    )
    branches = [
        BranchOption(
            branch_id="urgent",
            next_step_id="fast-lane",
            condition=BranchConditionSpecification(
                condition_id="urgent-predicate",
                condition_type=BranchConditionType.CUSTOM_PREDICATE,
                source=BranchValueSource.STEP_OUTPUT,
                field_path="flags.urgent",
                predicate_name="has-urgent-flag",
            ),
        ),
        BranchOption(branch_id="standard", next_step_id="normal-lane", default=True),
    ]

    result = evaluator.evaluate(branches, context=context)

    assert result.succeeded is True
    assert result.selected_branch is not None
    assert result.selected_branch.next_step_id == "fast-lane"
    assert "matched=True" in (result.records[0].detail or "")
