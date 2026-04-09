from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from desktop_automation_perception.models import (
    ApprovalGateAction,
    ConfidenceRoutingDecision,
    ConfidenceRoutingDisposition,
    ConfidenceRoutingResult,
    ConfidenceRoutingSnapshot,
    ConfidenceThresholdRule,
)


@dataclass(slots=True)
class ConfidenceBasedAutoRouting:
    storage_path: str
    approval_gate: object
    default_threshold: float = 0.8
    error_rate_sensitivity: float = 0.2
    now_fn: Callable[[], datetime] = utc_now

    def route_action(
        self,
        *,
        action: ApprovalGateAction,
        confidence_score: float,
    ) -> ConfidenceRoutingResult:
        snapshot = self._load_snapshot()
        rule = self._rule_for_action(snapshot, action.action_type)
        threshold_used = self._effective_threshold(rule)

        if confidence_score >= threshold_used:
            decision = ConfidenceRoutingDecision(
                action_type=action.action_type,
                confidence_score=confidence_score,
                threshold_used=threshold_used,
                disposition=ConfidenceRoutingDisposition.AUTO_PROCEED,
                routed_to_approval=False,
                observed_error_rate=rule.observed_error_rate,
                timestamp=self.now_fn(),
                reason="Confidence score met or exceeded the auto-proceed threshold.",
            )
            snapshot.decisions.append(decision)
            self._save_snapshot(snapshot)
            return ConfidenceRoutingResult(succeeded=True, decision=decision, snapshot=snapshot)

        approval_result = self.approval_gate.evaluate(action)
        decision = ConfidenceRoutingDecision(
            action_type=action.action_type,
            confidence_score=confidence_score,
            threshold_used=threshold_used,
            disposition=ConfidenceRoutingDisposition.ROUTE_TO_APPROVAL,
            routed_to_approval=True,
            observed_error_rate=rule.observed_error_rate,
            timestamp=self.now_fn(),
            reason="Confidence score fell below the approval-routing threshold.",
        )
        snapshot.decisions.append(decision)
        self._save_snapshot(snapshot)
        return ConfidenceRoutingResult(
            succeeded=approval_result.succeeded,
            decision=decision,
            approval_result=approval_result,
            snapshot=snapshot,
            reason=approval_result.reason,
        )

    def update_threshold(
        self,
        *,
        action_type: str,
        minimum_confidence: float,
        observed_error_rate: float | None = None,
    ) -> ConfidenceRoutingResult:
        snapshot = self._load_snapshot()
        rule = self._rule_for_action(snapshot, action_type)
        rule.minimum_confidence = self._normalize_confidence(minimum_confidence)
        if observed_error_rate is not None:
            rule.observed_error_rate = self._normalize_confidence(observed_error_rate)
        self._save_snapshot(snapshot)
        return ConfidenceRoutingResult(succeeded=True, snapshot=snapshot)

    def adjust_thresholds_from_error_rates(self, observed_error_rates: dict[str, float]) -> ConfidenceRoutingResult:
        snapshot = self._load_snapshot()
        for action_type, error_rate in observed_error_rates.items():
            rule = self._rule_for_action(snapshot, action_type)
            rule.observed_error_rate = self._normalize_confidence(error_rate)
        self._save_snapshot(snapshot)
        return ConfidenceRoutingResult(succeeded=True, snapshot=snapshot)

    def inspect(self) -> ConfidenceRoutingResult:
        snapshot = self._load_snapshot()
        return ConfidenceRoutingResult(succeeded=True, snapshot=snapshot)

    def _effective_threshold(self, rule: ConfidenceThresholdRule) -> float:
        adjusted = rule.minimum_confidence + (rule.observed_error_rate * self.error_rate_sensitivity)
        return self._normalize_confidence(adjusted)

    def _rule_for_action(self, snapshot: ConfidenceRoutingSnapshot, action_type: str) -> ConfidenceThresholdRule:
        normalized = action_type.casefold()
        for rule in snapshot.threshold_rules:
            if rule.action_type.casefold() == normalized:
                return rule
        rule = ConfidenceThresholdRule(action_type=action_type, minimum_confidence=self.default_threshold)
        snapshot.threshold_rules.append(rule)
        return rule

    def _load_snapshot(self) -> ConfidenceRoutingSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return ConfidenceRoutingSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ConfidenceRoutingSnapshot(
            threshold_rules=[self._deserialize_rule(item) for item in payload.get("threshold_rules", [])],
            decisions=[self._deserialize_decision(item) for item in payload.get("decisions", [])],
        )

    def _save_snapshot(self, snapshot: ConfidenceRoutingSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "threshold_rules": [self._serialize_rule(item) for item in snapshot.threshold_rules],
            "decisions": [self._serialize_decision(item) for item in snapshot.decisions],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_rule(self, rule: ConfidenceThresholdRule) -> dict:
        return {
            "action_type": rule.action_type,
            "minimum_confidence": rule.minimum_confidence,
            "observed_error_rate": rule.observed_error_rate,
        }

    def _deserialize_rule(self, payload: dict) -> ConfidenceThresholdRule:
        return ConfidenceThresholdRule(
            action_type=payload["action_type"],
            minimum_confidence=float(payload.get("minimum_confidence", self.default_threshold)),
            observed_error_rate=float(payload.get("observed_error_rate", 0.0)),
        )

    def _serialize_decision(self, decision: ConfidenceRoutingDecision) -> dict:
        return {
            "action_type": decision.action_type,
            "confidence_score": decision.confidence_score,
            "threshold_used": decision.threshold_used,
            "disposition": decision.disposition.value,
            "routed_to_approval": decision.routed_to_approval,
            "observed_error_rate": decision.observed_error_rate,
            "timestamp": decision.timestamp.isoformat(),
            "reason": decision.reason,
        }

    def _deserialize_decision(self, payload: dict) -> ConfidenceRoutingDecision:
        return ConfidenceRoutingDecision(
            action_type=payload["action_type"],
            confidence_score=float(payload["confidence_score"]),
            threshold_used=float(payload["threshold_used"]),
            disposition=ConfidenceRoutingDisposition(payload["disposition"]),
            routed_to_approval=bool(payload.get("routed_to_approval", False)),
            observed_error_rate=float(payload.get("observed_error_rate", 0.0)),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            reason=payload.get("reason"),
        )

    def _normalize_confidence(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))


