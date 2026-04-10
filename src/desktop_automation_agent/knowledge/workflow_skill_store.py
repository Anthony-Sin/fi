from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from pathlib import Path
from typing import Any

from .exceptions import ConfigurationError
from desktop_automation_agent.models import (
    WorkflowSkillDocument,
    WorkflowSkillSearchMatch,
    WorkflowSkillStep,
    WorkflowSkillStoreResult,
    WorkflowSkillVersion,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkflowSkillStore:
    storage_path: str

    def __post_init__(self) -> None:
        """Validate the skill store file exists and is well-formed on startup."""
        try:
            self._load_snapshot()
        except ConfigurationError as e:
            logger.error(f"Failed to initialize WorkflowSkillStore: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during WorkflowSkillStore initialization: {e}")
            raise ConfigurationError(f"Unexpected error during initialization: {e}") from e

    def record_successful_workflow(
        self,
        *,
        workflow_name: str,
        description: str,
        steps: list[WorkflowSkillStep | dict[str, Any]],
        execution_time_seconds: float,
        contextual_notes: str | None = None,
        task_description: str | None = None,
    ) -> WorkflowSkillStoreResult:
        snapshot = self._load_snapshot()
        existing = self._find_skill(snapshot, workflow_name)
        normalized_steps = [self._normalize_step(step) for step in steps]

        if existing is None:
            version = WorkflowSkillVersion(
                version=1,
                description=description,
                steps=normalized_steps,
                execution_time_seconds=float(execution_time_seconds),
                contextual_notes=contextual_notes,
                task_description=task_description,
            )
            skill = WorkflowSkillDocument(
                workflow_name=workflow_name,
                description=description,
                current_version=1,
                versions=[version],
                deprecated=False,
                deprecated_reason=None,
                updated_at=version.timestamp,
            )
            snapshot.append(skill)
        else:
            next_version = existing.current_version + 1
            version = WorkflowSkillVersion(
                version=next_version,
                description=description,
                steps=normalized_steps,
                execution_time_seconds=float(execution_time_seconds),
                contextual_notes=contextual_notes,
                task_description=task_description,
            )
            skill = WorkflowSkillDocument(
                workflow_name=existing.workflow_name,
                description=description,
                current_version=next_version,
                versions=existing.versions + [version],
                deprecated=existing.deprecated,
                deprecated_reason=existing.deprecated_reason,
                updated_at=version.timestamp,
            )
            snapshot = [
                skill if item.workflow_name.casefold() == workflow_name.casefold() else item
                for item in snapshot
            ]

        self._save_snapshot(snapshot)
        return WorkflowSkillStoreResult(succeeded=True, skill=skill)

    def get_skill(self, workflow_name: str) -> WorkflowSkillStoreResult:
        skill = self._find_skill(self._load_snapshot(), workflow_name)
        if skill is None:
            logger.warning(f"Workflow skill not found: {workflow_name}")
            return WorkflowSkillStoreResult(succeeded=False, reason=f"Workflow skill not found: {workflow_name}")
        return WorkflowSkillStoreResult(succeeded=True, skill=skill)

    def search_skills(
        self,
        task_description: str,
        *,
        include_deprecated: bool = False,
        limit: int = 5,
    ) -> WorkflowSkillStoreResult:
        query_vector = self._vectorize(task_description)
        matches: list[WorkflowSkillSearchMatch] = []

        for skill in self._load_snapshot():
            if skill.deprecated and not include_deprecated:
                continue
            score = self._semantic_score(query_vector, skill)
            if score <= 0.0:
                continue
            matches.append(
                WorkflowSkillSearchMatch(
                    workflow_name=skill.workflow_name,
                    score=score,
                    skill=skill,
                )
            )

        matches.sort(key=lambda item: item.score, reverse=True)
        return WorkflowSkillStoreResult(succeeded=True, matches=matches[:limit])

    def deprecate_skill(
        self,
        workflow_name: str,
        *,
        reason: str | None = None,
    ) -> WorkflowSkillStoreResult:
        snapshot = self._load_snapshot()
        skill = self._find_skill(snapshot, workflow_name)
        if skill is None:
            return WorkflowSkillStoreResult(succeeded=False, reason="Workflow skill not found.")

        updated = WorkflowSkillDocument(
            workflow_name=skill.workflow_name,
            description=skill.description,
            current_version=skill.current_version,
            versions=[
                WorkflowSkillVersion(
                    version=item.version,
                    description=item.description,
                    steps=list(item.steps),
                    execution_time_seconds=item.execution_time_seconds,
                    contextual_notes=item.contextual_notes,
                    task_description=item.task_description,
                    timestamp=item.timestamp,
                    deprecated=True,
                )
                for item in skill.versions
            ],
            deprecated=True,
            deprecated_reason=reason,
            updated_at=utc_now(),
        )
        snapshot = [
            updated if item.workflow_name.casefold() == workflow_name.casefold() else item
            for item in snapshot
        ]
        self._save_snapshot(snapshot)
        return WorkflowSkillStoreResult(succeeded=True, skill=updated)

    def get_skill_version(
        self,
        workflow_name: str,
        version: int,
    ) -> WorkflowSkillStoreResult:
        result = self.get_skill(workflow_name)
        if not result.succeeded or result.skill is None:
            return result
        match = next((item for item in result.skill.versions if item.version == version), None)
        if match is None:
            logger.warning(f"Workflow skill version not found: {workflow_name} v{version}")
            return WorkflowSkillStoreResult(succeeded=False, reason=f"Workflow skill version not found: {workflow_name} v{version}")
        historical = WorkflowSkillDocument(
            workflow_name=result.skill.workflow_name,
            description=match.description,
            current_version=match.version,
            versions=[match],
            deprecated=result.skill.deprecated,
            deprecated_reason=result.skill.deprecated_reason,
            updated_at=match.timestamp,
        )
        return WorkflowSkillStoreResult(succeeded=True, skill=historical)

    def list_skills(
        self,
        *,
        include_deprecated: bool = True,
    ) -> list[WorkflowSkillDocument]:
        skills = self._load_snapshot()
        if include_deprecated:
            return skills
        return [item for item in skills if not item.deprecated]

    def _semantic_score(
        self,
        query_vector: Counter[str],
        skill: WorkflowSkillDocument,
    ) -> float:
        if not query_vector:
            return 0.0
        current = next((item for item in skill.versions if item.version == skill.current_version), None)
        search_text = " ".join(
            [
                skill.workflow_name,
                skill.description,
                "" if current is None else current.description,
                "" if current is None else (current.contextual_notes or ""),
                "" if current is None else (current.task_description or ""),
                " ".join(step.step_name for step in ([] if current is None else current.steps)),
            ]
        )
        skill_vector = self._vectorize(search_text)
        if not skill_vector:
            return 0.0
        numerator = sum(query_vector[token] * skill_vector[token] for token in query_vector.keys() & skill_vector.keys())
        query_norm = sqrt(sum(value * value for value in query_vector.values()))
        skill_norm = sqrt(sum(value * value for value in skill_vector.values()))
        if query_norm == 0.0 or skill_norm == 0.0:
            return 0.0
        return numerator / (query_norm * skill_norm)

    def _vectorize(self, text: str | None) -> Counter[str]:
        if text is None:
            return Counter()
        tokens = re.findall(r"[a-z0-9]+", text.casefold())
        return Counter(tokens)

    def _normalize_step(self, step: WorkflowSkillStep | dict[str, Any]) -> WorkflowSkillStep:
        if isinstance(step, WorkflowSkillStep):
            return WorkflowSkillStep(
                step_name=step.step_name,
                parameters=dict(step.parameters),
            )
        return WorkflowSkillStep(
            step_name=str(step.get("step_name") or step.get("name") or step.get("step_id") or "step"),
            parameters=dict(step.get("parameters") or step.get("input_data") or {}),
        )

    def _find_skill(
        self,
        snapshot: list[WorkflowSkillDocument],
        workflow_name: str,
    ) -> WorkflowSkillDocument | None:
        for skill in snapshot:
            if skill.workflow_name.casefold() == workflow_name.casefold():
                return skill
        return None

    def _load_snapshot(self) -> list[WorkflowSkillDocument]:
        path = Path(self.storage_path)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"Malformed JSON in skill store at {self.storage_path}: {e}")
            raise ConfigurationError(f"Malformed JSON in skill store: {e}") from e

        if not isinstance(payload, dict):
            raise ConfigurationError(f"Skill store payload must be a JSON object, got {type(payload).__name__}")

        return [self._deserialize_skill(item) for item in payload.get("skills", [])]

    def _save_snapshot(self, snapshot: list[WorkflowSkillDocument]) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"skills": [self._serialize_skill(item) for item in snapshot]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_skill(self, skill: WorkflowSkillDocument) -> dict[str, Any]:
        return {
            "workflow_name": skill.workflow_name,
            "description": skill.description,
            "current_version": skill.current_version,
            "deprecated": skill.deprecated,
            "deprecated_reason": skill.deprecated_reason,
            "updated_at": skill.updated_at.isoformat(),
            "versions": [
                {
                    "version": version.version,
                    "description": version.description,
                    "steps": [
                        {
                            "step_name": step.step_name,
                            "parameters": step.parameters,
                        }
                        for step in version.steps
                    ],
                    "execution_time_seconds": version.execution_time_seconds,
                    "contextual_notes": version.contextual_notes,
                    "task_description": version.task_description,
                    "timestamp": version.timestamp.isoformat(),
                    "deprecated": version.deprecated,
                }
                for version in skill.versions
            ],
        }

    def _deserialize_skill(self, payload: dict[str, Any]) -> WorkflowSkillDocument:
        for field in ("workflow_name", "description"):
            if field not in payload:
                raise ConfigurationError(f"Missing required field '{field}' in skill payload")

        try:
            return WorkflowSkillDocument(
                workflow_name=str(payload["workflow_name"]),
                description=str(payload["description"]),
                current_version=int(payload.get("current_version", 1)),
                versions=[
                    WorkflowSkillVersion(
                        version=int(version["version"]),
                        description=str(version.get("description", "")),
                        steps=[
                            WorkflowSkillStep(
                                step_name=str(step["step_name"]),
                                parameters=dict(step.get("parameters", {})),
                            )
                            for step in version.get("steps", [])
                            if "step_name" in step
                        ],
                        execution_time_seconds=float(version.get("execution_time_seconds", 0.0)),
                        contextual_notes=version.get("contextual_notes"),
                        task_description=version.get("task_description"),
                        timestamp=datetime.fromisoformat(version["timestamp"]),
                        deprecated=bool(version.get("deprecated", False)),
                    )
                    for version in payload.get("versions", [])
                    if "version" in version and "timestamp" in version
                ],
                deprecated=bool(payload.get("deprecated", False)),
                deprecated_reason=payload.get("deprecated_reason"),
                updated_at=datetime.fromisoformat(payload["updated_at"])
                if payload.get("updated_at")
                else utc_now(),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise ConfigurationError(f"Malformed skill record for '{payload.get('workflow_name', 'unknown')}': {e}") from e


