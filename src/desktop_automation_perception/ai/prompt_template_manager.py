from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from desktop_automation_perception.models import (
    PromptTemplateRecord,
    PromptTemplateResult,
    PromptTemplateSnapshot,
    PromptTemplateVersion,
)


@dataclass(slots=True)
class PromptTemplateManager:
    storage_path: str
    sensitive_data_protector: object | None = None

    def list_templates(self) -> list[PromptTemplateRecord]:
        return self._load_snapshot().templates

    def get_template(self, name: str) -> PromptTemplateResult:
        template = self._find_template(self._load_snapshot(), name)
        if template is None:
            return PromptTemplateResult(succeeded=False, reason="Prompt template not found.")
        return PromptTemplateResult(succeeded=True, template=template)

    def upsert_template(
        self,
        *,
        name: str,
        description: str,
        body: str,
        target_context: str,
    ) -> PromptTemplateResult:
        snapshot = self._load_snapshot()
        existing = self._find_template(snapshot, name)

        if existing is None:
            record = PromptTemplateRecord(
                name=name,
                description=description,
                body=body,
                target_context=target_context,
                current_version=1,
                version_history=[
                    PromptTemplateVersion(
                        version=1,
                        body=body,
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            )
            snapshot.templates.append(record)
        else:
            next_version = existing.current_version + 1
            record = PromptTemplateRecord(
                name=name,
                description=description,
                body=body,
                target_context=target_context,
                current_version=next_version,
                version_history=existing.version_history
                + [
                    PromptTemplateVersion(
                        version=next_version,
                        body=body,
                        timestamp=datetime.now(timezone.utc),
                    )
                ],
            )
            snapshot.templates = [
                record if template.name.casefold() == name.casefold() else template
                for template in snapshot.templates
            ]

        self._save_snapshot(snapshot)
        return PromptTemplateResult(succeeded=True, template=record)

    def render_template(self, name: str, variables: dict[str, str]) -> PromptTemplateResult:
        template_result = self.get_template(name)
        if not template_result.succeeded or template_result.template is None:
            return template_result

        try:
            rendered = Template(template_result.template.body).substitute(variables)
        except KeyError as exc:
            return PromptTemplateResult(
                succeeded=False,
                template=template_result.template,
                reason=f"Missing template variable: {exc.args[0]}",
            )

        if self.sensitive_data_protector is not None:
            validation = self.sensitive_data_protector.validate_prompt(
                rendered,
                location=f"prompt_template:{name}",
            )
            if not validation.succeeded:
                return PromptTemplateResult(
                    succeeded=False,
                    template=template_result.template,
                    rendered_prompt=rendered,
                    reason=validation.reason,
                )

        return PromptTemplateResult(
            succeeded=True,
            template=template_result.template,
            rendered_prompt=rendered,
        )

    def get_template_version(self, name: str, version: int) -> PromptTemplateResult:
        template_result = self.get_template(name)
        if not template_result.succeeded or template_result.template is None:
            return template_result

        match = next(
            (item for item in template_result.template.version_history if item.version == version),
            None,
        )
        if match is None:
            return PromptTemplateResult(succeeded=False, reason="Requested template version not found.")

        historical = PromptTemplateRecord(
            name=template_result.template.name,
            description=template_result.template.description,
            body=match.body,
            target_context=template_result.template.target_context,
            current_version=match.version,
            version_history=template_result.template.version_history,
        )
        return PromptTemplateResult(succeeded=True, template=historical)

    def _find_template(self, snapshot: PromptTemplateSnapshot, name: str) -> PromptTemplateRecord | None:
        for template in snapshot.templates:
            if template.name.casefold() == name.casefold():
                return template
        return None

    def _load_snapshot(self) -> PromptTemplateSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return PromptTemplateSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return PromptTemplateSnapshot(
            templates=[self._deserialize_template(item) for item in payload.get("templates", [])]
        )

    def _save_snapshot(self, snapshot: PromptTemplateSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "templates": [self._serialize_template(template) for template in snapshot.templates]
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_template(self, template: PromptTemplateRecord) -> dict:
        return {
            "name": template.name,
            "description": template.description,
            "body": template.body,
            "target_context": template.target_context,
            "current_version": template.current_version,
            "version_history": [
                {
                    "version": item.version,
                    "body": item.body,
                    "timestamp": item.timestamp.isoformat(),
                }
                for item in template.version_history
            ],
        }

    def _deserialize_template(self, payload: dict) -> PromptTemplateRecord:
        return PromptTemplateRecord(
            name=payload["name"],
            description=payload["description"],
            body=payload["body"],
            target_context=payload["target_context"],
            current_version=int(payload.get("current_version", 1)),
            version_history=[
                PromptTemplateVersion(
                    version=int(item["version"]),
                    body=item["body"],
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                )
                for item in payload.get("version_history", [])
            ],
        )
