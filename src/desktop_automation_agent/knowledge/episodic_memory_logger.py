from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path
from typing import Any
from uuid import uuid4

from .exceptions import ConfigurationError
from desktop_automation_agent.models import (
    AutomationEpisode,
    EpisodicMemoryResult,
    EpisodicMemorySearchMatch,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EpisodicMemoryLogger:
    storage_path: str

    def __post_init__(self) -> None:
        """Validate the episodic memory file is well-formed on startup."""
        try:
            self._load_snapshot()
        except ConfigurationError as e:
            logger.error(f"Failed to initialize EpisodicMemoryLogger: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during EpisodicMemoryLogger initialization: {e}")
            raise ConfigurationError(f"Unexpected error during initialization: {e}") from e

    def log_episode(
        self,
        *,
        task_description: str,
        task_type: str,
        steps_executed: list[dict[str, Any] | object],
        outcomes: list[str] | None = None,
        errors_encountered: list[str] | None = None,
        recovery_actions_taken: list[str] | None = None,
        total_duration_seconds: float = 0.0,
        succeeded: bool,
        applications: list[str] | None = None,
    ) -> EpisodicMemoryResult:
        episode = AutomationEpisode(
            episode_id=str(uuid4()),
            task_description=task_description,
            task_type=task_type,
            applications=list(applications or []),
            steps_executed=[self._normalize_step(step) for step in steps_executed],
            outcomes=list(outcomes or []),
            errors_encountered=list(errors_encountered or []),
            recovery_actions_taken=list(recovery_actions_taken or []),
            total_duration_seconds=float(total_duration_seconds),
            succeeded=bool(succeeded),
            timestamp=datetime.now(timezone.utc),
        )
        snapshot = self._load_snapshot()
        snapshot.append(episode)
        self._save_snapshot(snapshot)
        return EpisodicMemoryResult(succeeded=True, episode=episode)

    def list_episodes(self) -> list[AutomationEpisode]:
        return self._load_snapshot()

    def get_episodes_by_index(
        self,
        *,
        task_type: str | None = None,
        application: str | None = None,
        succeeded: bool | None = None,
    ) -> EpisodicMemoryResult:
        episodes = [
            episode
            for episode in self._load_snapshot()
            if self._matches_index(
                episode,
                task_type=task_type,
                application=application,
                succeeded=succeeded,
            )
        ]
        if not episodes:
            logger.warning(f"No episodes found matching task_type={task_type}, application={application}, succeeded={succeeded}")
        return EpisodicMemoryResult(succeeded=True, episodes=episodes)

    def retrieve_relevant_episodes(
        self,
        task_description: str,
        *,
        limit: int = 5,
        task_type: str | None = None,
        application: str | None = None,
    ) -> EpisodicMemoryResult:
        query_vector = self._vectorize(task_description)
        matches: list[EpisodicMemorySearchMatch] = []

        for episode in self._load_snapshot():
            if task_type is not None and episode.task_type != task_type:
                continue
            if application is not None and application not in episode.applications:
                continue
            similarity = self._similarity_score(query_vector, episode)
            if similarity <= 0.0:
                continue
            recency = self._recency_score(episode.timestamp)
            score = (similarity * 0.8) + (recency * 0.2)
            matches.append(EpisodicMemorySearchMatch(episode=episode, score=score))

        matches.sort(key=lambda item: item.score, reverse=True)
        return EpisodicMemoryResult(succeeded=True, matches=matches[:limit])

    def _matches_index(
        self,
        episode: AutomationEpisode,
        *,
        task_type: str | None,
        application: str | None,
        succeeded: bool | None,
    ) -> bool:
        if task_type is not None and episode.task_type != task_type:
            return False
        if application is not None and application not in episode.applications:
            return False
        if succeeded is not None and episode.succeeded != succeeded:
            return False
        return True

    def _normalize_step(self, step: dict[str, Any] | object) -> dict[str, Any]:
        if isinstance(step, dict):
            return dict(step)
        return {
            "step_id": getattr(step, "step_id", None),
            "step_name": getattr(step, "step_name", None),
            "application_name": getattr(step, "application_name", None),
            "succeeded": getattr(step, "succeeded", None),
            "reason": getattr(step, "reason", None),
            "execution_time_seconds": getattr(step, "execution_time_seconds", None),
        }

    def _similarity_score(
        self,
        query_vector: Counter[str],
        episode: AutomationEpisode,
    ) -> float:
        if not query_vector:
            return 0.0
        primary_text = " ".join(
            [
                episode.task_description,
                episode.task_type,
                " ".join(episode.applications),
            ]
        )
        detail_text = " ".join(
            [
                " ".join(
                    str(step.get("step_name") or step.get("step_id") or step.get("application_name") or "")
                    for step in episode.steps_executed
                ),
                " ".join(episode.outcomes),
                " ".join(episode.errors_encountered),
                " ".join(episode.recovery_actions_taken),
            ]
        )
        primary_score = self._cosine_similarity(query_vector, self._vectorize(primary_text))
        detail_score = self._cosine_similarity(query_vector, self._vectorize(detail_text))
        return (primary_score * 0.8) + (detail_score * 0.2)

    def _recency_score(self, timestamp: datetime) -> float:
        now = datetime.now(timezone.utc)
        normalized = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)
        age_seconds = max((now - normalized).total_seconds(), 0.0)
        return 1.0 / (1.0 + (age_seconds / 86400.0))

    def _vectorize(self, text: str | None) -> Counter[str]:
        if text is None:
            return Counter()
        tokens = re.findall(r"[a-z0-9]+", text.casefold())
        return Counter(tokens)

    def _cosine_similarity(
        self,
        left: Counter[str],
        right: Counter[str],
    ) -> float:
        if not left or not right:
            return 0.0
        numerator = sum(left[token] * right[token] for token in left.keys() & right.keys())
        left_norm = sqrt(sum(value * value for value in left.values()))
        right_norm = sqrt(sum(value * value for value in right.values()))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _load_snapshot(self) -> list[AutomationEpisode]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"Malformed JSON in episodic memory at {self.storage_path}: {e}")
            raise ConfigurationError(f"Malformed JSON in episodic memory: {e}") from e

        if not isinstance(payload, dict):
            raise ConfigurationError(f"Episodic memory payload must be a JSON object, got {type(payload).__name__}")

        return [self._deserialize_episode(item) for item in payload.get("episodes", [])]

    def _save_snapshot(self, snapshot: list[AutomationEpisode]) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"episodes": [self._serialize_episode(item) for item in snapshot]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_episode(self, episode: AutomationEpisode) -> dict[str, Any]:
        return {
            "episode_id": episode.episode_id,
            "task_description": episode.task_description,
            "task_type": episode.task_type,
            "applications": list(episode.applications),
            "steps_executed": list(episode.steps_executed),
            "outcomes": list(episode.outcomes),
            "errors_encountered": list(episode.errors_encountered),
            "recovery_actions_taken": list(episode.recovery_actions_taken),
            "total_duration_seconds": episode.total_duration_seconds,
            "succeeded": episode.succeeded,
            "timestamp": episode.timestamp.isoformat(),
        }

    def _deserialize_episode(self, payload: dict[str, Any]) -> AutomationEpisode:
        required_fields = ("episode_id", "task_description", "task_type", "timestamp")
        for field in required_fields:
            if field not in payload:
                raise ConfigurationError(f"Missing required field '{field}' in episode payload")

        try:
            return AutomationEpisode(
                episode_id=str(payload["episode_id"]),
                task_description=str(payload["task_description"]),
                task_type=str(payload["task_type"]),
                applications=list(payload.get("applications", [])),
                steps_executed=list(payload.get("steps_executed", [])),
                outcomes=list(payload.get("outcomes", [])),
                errors_encountered=list(payload.get("errors_encountered", [])),
                recovery_actions_taken=list(payload.get("recovery_actions_taken", [])),
                total_duration_seconds=float(payload.get("total_duration_seconds", 0.0)),
                succeeded=bool(payload.get("succeeded", False)),
                timestamp=datetime.fromisoformat(payload["timestamp"]),
            )
        except (ValueError, TypeError) as e:
            raise ConfigurationError(f"Malformed episode record for '{payload.get('episode_id', 'unknown')}': {e}") from e
