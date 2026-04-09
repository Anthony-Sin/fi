from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Iterable

from desktop_automation_agent.models import (
    ActionFailurePoint,
    ActionHistoryAnalysisReport,
    ActionHistoryAnalysisResult,
    ActionHistorySequencePattern,
    ActionLogEntry,
    ActionOptimizationHint,
    ActionRetryRateSummary,
    ActionStepDurationSummary,
    NavigationStepOutcome,
    PromptPipelineStepLog,
    RetryFailureResult,
    WorkflowStepResult,
)


@dataclass(slots=True)
class ActionHistoryAnalyzer:
    sequence_window_size: int = 3
    min_sequence_frequency: int = 2
    high_retry_rate_threshold: float = 0.3
    slow_step_duration_threshold_seconds: float = 1.0

    def analyze(
        self,
        *,
        action_logs: list[ActionLogEntry] | None = None,
        navigation_outcomes: list[NavigationStepOutcome] | None = None,
        workflow_step_results: list[WorkflowStepResult] | None = None,
        prompt_step_logs: list[PromptPipelineStepLog] | None = None,
        retry_failures: list[RetryFailureResult] | None = None,
        retry_step_types: list[str] | None = None,
    ) -> ActionHistoryAnalysisResult:
        action_logs = list(action_logs or [])
        navigation_outcomes = list(navigation_outcomes or [])
        workflow_step_results = list(workflow_step_results or [])
        prompt_step_logs = list(prompt_step_logs or [])
        retry_failures = list(retry_failures or [])
        retry_step_types = list(retry_step_types or [])

        frequent_sequences = self._find_frequent_sequences(action_logs)
        common_failure_points = self._find_failure_points(action_logs, navigation_outcomes, workflow_step_results, prompt_step_logs)
        average_durations = self._calculate_average_durations(action_logs, navigation_outcomes, prompt_step_logs)
        high_retry_steps = self._calculate_retry_rates(action_logs, retry_failures, retry_step_types)
        optimization_hints = self._build_hints(average_durations, high_retry_steps, common_failure_points)

        return ActionHistoryAnalysisResult(
            succeeded=True,
            report=ActionHistoryAnalysisReport(
                frequent_sequences=frequent_sequences,
                common_failure_points=common_failure_points,
                average_durations=average_durations,
                high_retry_steps=high_retry_steps,
                optimization_hints=optimization_hints,
            ),
        )

    def _find_frequent_sequences(
        self,
        action_logs: list[ActionLogEntry],
    ) -> list[ActionHistorySequencePattern]:
        action_types = [self._action_type_name(item) for item in action_logs]
        if len(action_types) < self.sequence_window_size:
            return []
        counts: Counter[tuple[str, ...]] = Counter()
        for index in range(0, len(action_types) - self.sequence_window_size + 1):
            window = tuple(action_types[index : index + self.sequence_window_size])
            counts[window] += 1
        patterns = [
            ActionHistorySequencePattern(sequence=list(sequence), count=count)
            for sequence, count in counts.items()
            if count >= self.min_sequence_frequency
        ]
        patterns.sort(key=lambda item: (-item.count, item.sequence))
        return patterns

    def _find_failure_points(
        self,
        action_logs: list[ActionLogEntry],
        navigation_outcomes: list[NavigationStepOutcome],
        workflow_step_results: list[WorkflowStepResult],
        prompt_step_logs: list[PromptPipelineStepLog],
    ) -> list[ActionFailurePoint]:
        buckets: dict[str, list[str]] = defaultdict(list)

        for log in action_logs:
            if not log.executed or log.reason is not None:
                buckets[self._action_type_name(log)].append(log.reason or "Action failed.")
        for outcome in navigation_outcomes:
            if not outcome.succeeded and not outcome.skipped:
                buckets[outcome.action_type.value].append(outcome.reason or "Navigation step failed.")
        for result in workflow_step_results:
            if not result.succeeded:
                buckets[result.step_id].append(result.reason or "Workflow step failed.")
        for log in prompt_step_logs:
            if not log.succeeded:
                buckets[log.step_id].append(log.reason or "Prompt pipeline step failed.")

        failure_points = [
            ActionFailurePoint(
                step_type=step_type,
                failure_count=len(reasons),
                reasons=sorted(set(reason for reason in reasons if reason)),
            )
            for step_type, reasons in buckets.items()
        ]
        failure_points.sort(key=lambda item: (-item.failure_count, item.step_type))
        return failure_points

    def _calculate_average_durations(
        self,
        action_logs: list[ActionLogEntry],
        navigation_outcomes: list[NavigationStepOutcome],
        prompt_step_logs: list[PromptPipelineStepLog],
    ) -> list[ActionStepDurationSummary]:
        durations: dict[str, list[float]] = defaultdict(list)
        for log in action_logs:
            durations[self._action_type_name(log)].append(float(log.delay_seconds))
        for outcome in navigation_outcomes:
            durations[outcome.action_type.value].append(float(outcome.execution_time_seconds))
        for log in prompt_step_logs:
            durations[log.step_id].append(float(log.execution_time_seconds))

        summaries = [
            ActionStepDurationSummary(
                step_type=step_type,
                average_duration_seconds=mean(values),
                sample_count=len(values),
            )
            for step_type, values in durations.items()
            if values
        ]
        summaries.sort(key=lambda item: (-item.average_duration_seconds, item.step_type))
        return summaries

    def _calculate_retry_rates(
        self,
        action_logs: list[ActionLogEntry],
        retry_failures: list[RetryFailureResult],
        retry_step_types: list[str],
    ) -> list[ActionRetryRateSummary]:
        sample_counts: Counter[str] = Counter(self._action_type_name(log) for log in action_logs)
        retry_counts: Counter[str] = Counter()

        for index, failure in enumerate(retry_failures):
            if not failure.attempts:
                continue
            step_type = retry_step_types[index] if index < len(retry_step_types) else self._infer_retry_step_type(failure)
            retry_counts[step_type] += max(len(failure.attempts) - 1, 0)
            if step_type not in sample_counts:
                sample_counts[step_type] = 1

        summaries = []
        for step_type, retry_count in retry_counts.items():
            sample_count = max(sample_counts.get(step_type, 1), 1)
            retry_rate = retry_count / sample_count
            if retry_rate >= self.high_retry_rate_threshold:
                summaries.append(
                    ActionRetryRateSummary(
                        step_type=step_type,
                        retry_count=retry_count,
                        sample_count=sample_count,
                        retry_rate=retry_rate,
                    )
                )
        summaries.sort(key=lambda item: (-item.retry_rate, item.step_type))
        return summaries

    def _build_hints(
        self,
        average_durations: list[ActionStepDurationSummary],
        high_retry_steps: list[ActionRetryRateSummary],
        common_failure_points: list[ActionFailurePoint],
    ) -> list[ActionOptimizationHint]:
        hints: list[ActionOptimizationHint] = []

        for summary in average_durations:
            if summary.average_duration_seconds >= self.slow_step_duration_threshold_seconds:
                hints.append(
                    ActionOptimizationHint(
                        step_type=summary.step_type,
                        recommendation=(
                            f"Pre-emptively add a wait before '{summary.step_type}' because its average duration "
                            f"is {summary.average_duration_seconds:.2f}s."
                        ),
                    )
                )

        for summary in high_retry_steps:
            hints.append(
                ActionOptimizationHint(
                    step_type=summary.step_type,
                    recommendation=(
                        f"Increase retries for '{summary.step_type}' because its retry rate is "
                        f"{summary.retry_rate:.2f}."
                    ),
                )
            )

        for failure in common_failure_points[:3]:
            if failure.failure_count > 1:
                hints.append(
                    ActionOptimizationHint(
                        step_type=failure.step_type,
                        recommendation=(
                            f"Add defensive verification around '{failure.step_type}' because it fails "
                            f"frequently ({failure.failure_count} occurrences)."
                        ),
                    )
                )

        deduped: dict[str, ActionOptimizationHint] = {}
        for hint in hints:
            deduped.setdefault(hint.step_type + hint.recommendation, hint)
        return list(deduped.values())

    def _action_type_name(self, log: ActionLogEntry) -> str:
        return log.action.action_type.value

    def _infer_retry_step_type(self, failure: RetryFailureResult) -> str:
        first_attempt = next(iter(failure.attempts), None)
        if first_attempt is None:
            return "unknown"
        if first_attempt.exception_type is not None:
            return first_attempt.exception_type
        return "unknown"
