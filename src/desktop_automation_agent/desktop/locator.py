from __future__ import annotations

from dataclasses import dataclass

from desktop_automation_agent.models import (
    DesktopState,
    LocatorCandidate,
    LocatorResult,
    LocatorStrategy,
    LocatorTarget,
    PerceptionArtifact,
    PerceptionResult,
    PerceptionSource,
)


@dataclass(slots=True)
class _StrategyMatch:
    strategy: LocatorStrategy
    candidate: LocatorCandidate | None


class MultiStrategyElementLocator:
    def __init__(self, confidence_threshold: float = 0.75, display_handler: object | None = None):
        self._confidence_threshold = confidence_threshold
        self._display_handler = display_handler

    def locate(
        self,
        desktop_state: DesktopState,
        target: LocatorTarget,
        confidence_threshold: float | None = None,
        monitor_id: str | None = None,
    ) -> LocatorResult:
        threshold = self._confidence_threshold if confidence_threshold is None else confidence_threshold
        requested_monitor_id = monitor_id or target.monitor_id
        matches = [
            self._locate_from_result(
                LocatorStrategy.ACCESSIBILITY,
                self._find_result(desktop_state, PerceptionSource.ACCESSIBILITY),
                target,
                requested_monitor_id,
            ),
            self._locate_from_result(
                LocatorStrategy.OCR,
                self._find_result(desktop_state, PerceptionSource.OCR),
                target,
                requested_monitor_id,
            ),
            self._locate_from_result(
                LocatorStrategy.TEMPLATE_MATCH,
                self._find_result(desktop_state, PerceptionSource.TEMPLATE_MATCH),
                target,
                requested_monitor_id,
            ),
        ]

        candidates = [match.candidate for match in matches if match.candidate is not None]
        if not candidates:
            return LocatorResult(
                succeeded=False,
                confidence=0.0,
                threshold=threshold,
                reason="No matching candidate found in accessibility, OCR, or template matching results.",
            )

        best = max(candidates, key=lambda candidate: candidate.confidence)
        for match in matches:
            candidate = match.candidate
            if candidate is None:
                continue
            if candidate.confidence >= threshold:
                return LocatorResult(
                    succeeded=True,
                    confidence=candidate.confidence,
                    threshold=threshold,
                    strategy=candidate.strategy,
                    bounds=candidate.bounds,
                    center=candidate.center,
                    best_candidate=candidate,
                    monitor_id=candidate.monitor_id,
                )

        if best.confidence < threshold:
            return LocatorResult(
                succeeded=False,
                confidence=best.confidence,
                threshold=threshold,
                strategy=best.strategy,
                bounds=best.bounds,
                center=best.center,
                best_candidate=best,
                monitor_id=best.monitor_id,
                reason=best.reason or f"Best candidate confidence {best.confidence:.2f} is below threshold {threshold:.2f}.",
            )
        return LocatorResult(
            succeeded=False,
            confidence=best.confidence,
            threshold=threshold,
            strategy=best.strategy,
            bounds=best.bounds,
            center=best.center,
            best_candidate=best,
            monitor_id=best.monitor_id,
            reason="A matching candidate was found, but the cascade could not resolve it within the configured threshold.",
        )

    def _find_result(
        self,
        desktop_state: DesktopState,
        source: PerceptionSource,
    ) -> PerceptionResult | None:
        for result in desktop_state.results:
            if result.source == source and result.succeeded:
                return result
        return None

    def _locate_from_result(
        self,
        strategy: LocatorStrategy,
        result: PerceptionResult | None,
        target: LocatorTarget,
        monitor_id: str | None,
    ) -> _StrategyMatch:
        if result is None:
            return _StrategyMatch(strategy=strategy, candidate=None)

        if strategy is LocatorStrategy.ACCESSIBILITY:
            return _StrategyMatch(strategy=strategy, candidate=self._match_accessibility(result, target, monitor_id))
        if strategy is LocatorStrategy.OCR:
            return _StrategyMatch(strategy=strategy, candidate=self._match_ocr(result, target, monitor_id))
        return _StrategyMatch(strategy=strategy, candidate=self._match_template(result, target, monitor_id))

    def _match_accessibility(
        self,
        result: PerceptionResult,
        target: LocatorTarget,
        monitor_id: str | None,
    ) -> LocatorCandidate | None:
        best: LocatorCandidate | None = None
        for artifact in result.artifacts:
            score = self._score_accessibility_artifact(artifact, target)
            candidate = self._build_candidate(
                LocatorStrategy.ACCESSIBILITY,
                artifact,
                score,
                "Accessibility match confidence is below threshold.",
                monitor_id,
            )
            best = self._pick_better(best, candidate)
        return best

    def _match_ocr(
        self,
        result: PerceptionResult,
        target: LocatorTarget,
        monitor_id: str | None,
    ) -> LocatorCandidate | None:
        if not target.text:
            return None

        best: LocatorCandidate | None = None
        normalized_target = target.text.casefold()
        for artifact in result.artifacts:
            text = str(artifact.payload.get("text", ""))
            if not text:
                continue

            normalized_text = text.casefold()
            if normalized_target == normalized_text:
                score = artifact.confidence
            elif normalized_target in normalized_text:
                score = artifact.confidence * 0.92
            else:
                continue

            candidate = self._build_candidate(
                LocatorStrategy.OCR,
                artifact,
                score,
                "OCR text match confidence is below threshold.",
                monitor_id,
            )
            best = self._pick_better(best, candidate)
        return best

    def _match_template(
        self,
        result: PerceptionResult,
        target: LocatorTarget,
        monitor_id: str | None,
    ) -> LocatorCandidate | None:
        if not target.template_name:
            return None

        best: LocatorCandidate | None = None
        normalized_template = target.template_name.casefold()
        for artifact in result.artifacts:
            template_name = str(artifact.payload.get("template", ""))
            if template_name.casefold() != normalized_template:
                continue

            candidate = self._build_candidate(
                LocatorStrategy.TEMPLATE_MATCH,
                artifact,
                artifact.confidence,
                "Template match confidence is below threshold.",
                monitor_id,
            )
            best = self._pick_better(best, candidate)
        return best

    def _score_accessibility_artifact(
        self,
        artifact: PerceptionArtifact,
        target: LocatorTarget,
    ) -> float | None:
        payload = artifact.payload
        score = artifact.confidence
        matched = False

        if target.text:
            name = str(payload.get("name", ""))
            if not name:
                return None
            normalized_name = name.casefold()
            normalized_target = target.text.casefold()
            if normalized_name == normalized_target:
                matched = True
            elif normalized_target in normalized_name:
                score *= 0.94
                matched = True
            else:
                return None

        if target.element_type:
            role = str(payload.get("role", payload.get("type", "")))
            if not role:
                return None
            normalized_role = role.casefold()
            normalized_type = target.element_type.casefold()
            if normalized_role == normalized_type:
                matched = True
            elif normalized_type in normalized_role:
                score *= 0.96
                matched = True
            else:
                return None

        if target.template_name:
            template_name = str(payload.get("template", ""))
            if template_name.casefold() == target.template_name.casefold():
                matched = True
            else:
                return None

        return score if matched else None

    def _build_candidate(
        self,
        strategy: LocatorStrategy,
        artifact: PerceptionArtifact,
        score: float | None,
        reason: str,
        monitor_id: str | None,
    ) -> LocatorCandidate | None:
        if score is None or artifact.bounds is None:
            return None
        candidate_monitor_id = self._resolve_monitor_id(artifact.bounds)
        if monitor_id is not None and candidate_monitor_id != monitor_id:
            return None
        left, top, right, bottom = artifact.bounds
        center = ((left + right) // 2, (top + bottom) // 2)
        return LocatorCandidate(
            strategy=strategy,
            confidence=score,
            bounds=artifact.bounds,
            center=center,
            artifact=artifact,
            monitor_id=candidate_monitor_id,
            reason=reason,
        )

    def _pick_better(
        self,
        current: LocatorCandidate | None,
        candidate: LocatorCandidate | None,
    ) -> LocatorCandidate | None:
        if candidate is None:
            return current
        if current is None or candidate.confidence > current.confidence:
            return candidate
        return current

    def _resolve_monitor_id(self, bounds: tuple[int, int, int, int]) -> str | None:
        if self._display_handler is None:
            return None
        left, top, right, bottom = bounds
        center = ((left + right) // 2, (top + bottom) // 2)
        monitor = self._display_handler.get_monitor_for_point(center)
        return None if monitor is None else monitor.monitor_id
