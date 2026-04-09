from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from pathlib import Path
from typing import Any

from desktop_automation_agent.automation.graph_workflow_planner import GraphBasedWorkflowPlanner
from desktop_automation_agent.models import (
    WorkflowGraphDefinition,
    WorkflowGraphEdge,
    WorkflowGraphEdgeType,
    WorkflowGraphNode,
    WorkflowTemplateCompositionComponent,
    WorkflowTemplateDocument,
    WorkflowTemplateLibraryResult,
    WorkflowTemplateLibrarySnapshot,
    WorkflowTemplateParameter,
    WorkflowTemplateSearchMatch,
    WorkflowTemplateVersion,
)


@dataclass(slots=True)
class WorkflowTemplateLibrary:
    storage_path: str
    graph_planner: GraphBasedWorkflowPlanner

    def create_template(
        self,
        *,
        template_id: str,
        name: str,
        description: str,
        author: str,
        workflow_graph: WorkflowGraphDefinition,
        parameters: list[WorkflowTemplateParameter] | None = None,
        application: str | None = None,
        task_type: str | None = None,
        keywords: list[str] | None = None,
        change_description: str = "Initial template version",
        timestamp: datetime | None = None,
    ) -> WorkflowTemplateLibraryResult:
        snapshot = self._load_snapshot()
        existing = self._find_template(snapshot.templates, template_id)
        if existing is not None:
            return WorkflowTemplateLibraryResult(succeeded=False, reason="Template already exists.")
        version = WorkflowTemplateVersion(
            version_number=1,
            author=author,
            timestamp=timestamp or utc_now(),
            change_description=change_description,
            workflow_graph=workflow_graph,
            parameters=list(parameters or []),
        )
        template = WorkflowTemplateDocument(
            template_id=template_id,
            name=name,
            description=description,
            application=application,
            task_type=task_type,
            keywords=list(keywords or []),
            current_version_number=1,
            versions=[version],
            updated_at=version.timestamp,
        )
        snapshot.templates.append(template)
        self._save_snapshot(snapshot)
        return WorkflowTemplateLibraryResult(succeeded=True, template=template, templates=list(snapshot.templates))

    def create_template_version(
        self,
        *,
        template_id: str,
        author: str,
        change_description: str,
        workflow_graph: WorkflowGraphDefinition,
        parameters: list[WorkflowTemplateParameter] | None = None,
        timestamp: datetime | None = None,
    ) -> WorkflowTemplateLibraryResult:
        snapshot = self._load_snapshot()
        existing = self._find_template(snapshot.templates, template_id)
        if existing is None:
            return WorkflowTemplateLibraryResult(succeeded=False, reason="Template not found.")
        next_version = 1 + max((item.version_number for item in existing.versions), default=0)
        version = WorkflowTemplateVersion(
            version_number=next_version,
            author=author,
            timestamp=timestamp or utc_now(),
            change_description=change_description,
            workflow_graph=workflow_graph,
            parameters=list(parameters or self._current_version(existing).parameters),
        )
        updated = WorkflowTemplateDocument(
            template_id=existing.template_id,
            name=existing.name,
            description=existing.description,
            application=existing.application,
            task_type=existing.task_type,
            keywords=list(existing.keywords),
            current_version_number=next_version,
            versions=existing.versions + [version],
            updated_at=version.timestamp,
        )
        snapshot.templates = [
            updated if item.template_id.casefold() == template_id.casefold() else item
            for item in snapshot.templates
        ]
        self._save_snapshot(snapshot)
        return WorkflowTemplateLibraryResult(succeeded=True, template=updated, templates=list(snapshot.templates))

    def get_template(
        self,
        template_id: str,
        *,
        version_number: int | None = None,
    ) -> WorkflowTemplateLibraryResult:
        template = self._find_template(self._load_snapshot().templates, template_id)
        if template is None:
            return WorkflowTemplateLibraryResult(succeeded=False, reason="Template not found.")
        if version_number is None:
            return WorkflowTemplateLibraryResult(succeeded=True, template=template)
        version = next((item for item in template.versions if item.version_number == version_number), None)
        if version is None:
            return WorkflowTemplateLibraryResult(succeeded=False, reason="Template version not found.")
        historical = WorkflowTemplateDocument(
            template_id=template.template_id,
            name=template.name,
            description=template.description,
            application=template.application,
            task_type=template.task_type,
            keywords=list(template.keywords),
            current_version_number=version.version_number,
            versions=[version],
            updated_at=version.timestamp,
        )
        return WorkflowTemplateLibraryResult(succeeded=True, template=historical)

    def search_templates(
        self,
        *,
        keyword: str | None = None,
        application: str | None = None,
        task_type: str | None = None,
        limit: int = 10,
    ) -> WorkflowTemplateLibraryResult:
        matches: list[WorkflowTemplateSearchMatch] = []
        keyword_vector = self._vectorize(keyword or "")
        for template in self._load_snapshot().templates:
            if application is not None and (template.application or "").casefold() != application.casefold():
                continue
            if task_type is not None and (template.task_type or "").casefold() != task_type.casefold():
                continue
            score = self._search_score(template, keyword_vector, keyword)
            if keyword and score <= 0.0:
                continue
            matches.append(
                WorkflowTemplateSearchMatch(
                    template_id=template.template_id,
                    score=score if keyword else 1.0,
                    template=template,
                )
            )
        matches.sort(key=lambda item: (item.score, item.template.updated_at), reverse=True)
        return WorkflowTemplateLibraryResult(succeeded=True, matches=matches[:limit])

    def compose_workflow(
        self,
        *,
        workflow_id: str,
        components: list[WorkflowTemplateCompositionComponent],
        version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowTemplateLibraryResult:
        nodes: list[WorkflowGraphNode] = []
        edges: list[WorkflowGraphEdge] = []
        entry_node_ids: list[str] = []
        previous_terminal_ids: list[str] = []

        for index, component in enumerate(components, start=1):
            template_result = self.get_template(component.template_id, version_number=component.version_number)
            if not template_result.succeeded or template_result.template is None:
                return WorkflowTemplateLibraryResult(succeeded=False, reason=template_result.reason or "Template not found.")
            template = template_result.template
            version_record = template.versions[0] if component.version_number is not None else self._current_version(template)
            prefix = component.node_prefix or f"{component.template_id}_{index}"
            parameter_values = self._resolved_parameters(version_record.parameters, component.parameter_values)

            source_graph = version_record.workflow_graph
            remapped_entry_ids = [f"{prefix}__{node_id}" for node_id in (source_graph.entry_node_ids or self._entry_nodes(source_graph))]
            if not entry_node_ids:
                entry_node_ids.extend(remapped_entry_ids)

            node_id_map = {node.node_id: f"{prefix}__{node.node_id}" for node in source_graph.nodes}
            remapped_nodes = [
                WorkflowGraphNode(
                    node_id=node_id_map[node.node_id],
                    step_name=self._substitute(node.step_name, parameter_values),
                    node_type=node.node_type,
                    application_name=self._substitute(node.application_name, parameter_values),
                    step_payload=self._substitute(node.step_payload, parameter_values),
                    wait_for_all_predecessors=node.wait_for_all_predecessors,
                    metadata=self._substitute(node.metadata, parameter_values),
                )
                for node in source_graph.nodes
            ]
            remapped_edges = [
                WorkflowGraphEdge(
                    edge_id=f"{prefix}__{edge.edge_id}",
                    source_node_id=node_id_map[edge.source_node_id],
                    target_node_id=node_id_map[edge.target_node_id],
                    edge_type=edge.edge_type,
                    condition=edge.condition,
                    loop_id=f"{prefix}__{edge.loop_id}" if edge.loop_id else None,
                    max_iterations=edge.max_iterations,
                    metadata=self._substitute(edge.metadata, parameter_values),
                )
                for edge in source_graph.edges
            ]
            nodes.extend(remapped_nodes)
            edges.extend(remapped_edges)

            terminal_ids = self._terminal_nodes(source_graph)
            remapped_terminal_ids = [node_id_map[node_id] for node_id in terminal_ids]
            if previous_terminal_ids:
                for terminal_id in previous_terminal_ids:
                    for next_entry_id in remapped_entry_ids:
                        edges.append(
                            WorkflowGraphEdge(
                                edge_id=f"compose__{terminal_id}__{next_entry_id}",
                                source_node_id=terminal_id,
                                target_node_id=next_entry_id,
                                edge_type=WorkflowGraphEdgeType.SEQUENTIAL,
                            )
                        )
            previous_terminal_ids = remapped_terminal_ids

        workflow_graph = WorkflowGraphDefinition(
            workflow_id=workflow_id,
            version=version,
            entry_node_ids=tuple(entry_node_ids),
            nodes=nodes,
            edges=edges,
            metadata=dict(metadata or {}),
        )
        return WorkflowTemplateLibraryResult(succeeded=True, workflow_graph=workflow_graph)

    def list_templates(self) -> WorkflowTemplateLibraryResult:
        snapshot = self._load_snapshot()
        return WorkflowTemplateLibraryResult(succeeded=True, templates=list(snapshot.templates))

    def _load_snapshot(self) -> WorkflowTemplateLibrarySnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return WorkflowTemplateLibrarySnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return WorkflowTemplateLibrarySnapshot(
            templates=[self._deserialize_template(item) for item in payload.get("templates", [])]
        )

    def _save_snapshot(self, snapshot: WorkflowTemplateLibrarySnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"templates": [self._serialize_template(item) for item in snapshot.templates]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_template(self, template: WorkflowTemplateDocument) -> dict[str, Any]:
        return {
            "template_id": template.template_id,
            "name": template.name,
            "description": template.description,
            "application": template.application,
            "task_type": template.task_type,
            "keywords": list(template.keywords),
            "current_version_number": template.current_version_number,
            "updated_at": template.updated_at.isoformat(),
            "versions": [
                {
                    "version_number": version.version_number,
                    "author": version.author,
                    "timestamp": version.timestamp.isoformat(),
                    "change_description": version.change_description,
                    "workflow_graph": json.loads(self.graph_planner.to_json(version.workflow_graph)),
                    "parameters": [
                        {
                            "name": parameter.name,
                            "description": parameter.description,
                            "default_value": parameter.default_value,
                            "required": parameter.required,
                        }
                        for parameter in version.parameters
                    ],
                }
                for version in template.versions
            ],
        }

    def _deserialize_template(self, payload: dict[str, Any]) -> WorkflowTemplateDocument:
        return WorkflowTemplateDocument(
            template_id=payload["template_id"],
            name=payload["name"],
            description=payload["description"],
            application=payload.get("application"),
            task_type=payload.get("task_type"),
            keywords=list(payload.get("keywords", [])),
            current_version_number=int(payload.get("current_version_number", 1)),
            updated_at=datetime.fromisoformat(payload["updated_at"]) if payload.get("updated_at") else utc_now(),
            versions=[
                WorkflowTemplateVersion(
                    version_number=int(version["version_number"]),
                    author=version["author"],
                    timestamp=datetime.fromisoformat(version["timestamp"]),
                    change_description=version["change_description"],
                    workflow_graph=self.graph_planner.from_json(version["workflow_graph"]),
                    parameters=[
                        WorkflowTemplateParameter(
                            name=parameter["name"],
                            description=parameter["description"],
                            default_value=parameter.get("default_value"),
                            required=bool(parameter.get("required", False)),
                        )
                        for parameter in version.get("parameters", [])
                    ],
                )
                for version in payload.get("versions", [])
            ],
        )

    def _find_template(
        self,
        templates: list[WorkflowTemplateDocument],
        template_id: str,
    ) -> WorkflowTemplateDocument | None:
        return next((item for item in templates if item.template_id.casefold() == template_id.casefold()), None)

    def _current_version(self, template: WorkflowTemplateDocument) -> WorkflowTemplateVersion:
        return next(
            item for item in template.versions if item.version_number == template.current_version_number
        )

    def _vectorize(self, text: str) -> Counter[str]:
        return Counter(re.findall(r"[a-z0-9]+", text.casefold()))

    def _search_score(
        self,
        template: WorkflowTemplateDocument,
        keyword_vector: Counter[str],
        keyword: str | None,
    ) -> float:
        if not keyword:
            return 1.0
        current = self._current_version(template)
        haystack = " ".join(
            [
                template.name,
                template.description,
                template.application or "",
                template.task_type or "",
                " ".join(template.keywords),
                current.change_description,
                " ".join(parameter.name for parameter in current.parameters),
            ]
        )
        template_vector = self._vectorize(haystack)
        numerator = sum(keyword_vector[token] * template_vector[token] for token in keyword_vector.keys() & template_vector.keys())
        if numerator == 0:
            return 0.0
        keyword_norm = sqrt(sum(value * value for value in keyword_vector.values()))
        template_norm = sqrt(sum(value * value for value in template_vector.values()))
        if keyword_norm == 0.0 or template_norm == 0.0:
            return 0.0
        return numerator / (keyword_norm * template_norm)

    def _resolved_parameters(
        self,
        parameters: list[WorkflowTemplateParameter],
        provided: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = {parameter.name: parameter.default_value for parameter in parameters if parameter.default_value is not None}
        resolved.update(provided)
        for parameter in parameters:
            if parameter.required and parameter.name not in resolved:
                raise ValueError(f"Missing required template parameter: {parameter.name}")
        return resolved

    def _substitute(self, value: Any, parameters: dict[str, Any]) -> Any:
        if isinstance(value, str):
            rendered = value
            for key, parameter_value in parameters.items():
                rendered = rendered.replace(f"{{{{{key}}}}}", "" if parameter_value is None else str(parameter_value))
            return rendered
        if isinstance(value, dict):
            return {key: self._substitute(item, parameters) for key, item in value.items()}
        if isinstance(value, list):
            return [self._substitute(item, parameters) for item in value]
        if isinstance(value, tuple):
            return tuple(self._substitute(item, parameters) for item in value)
        return value

    def _entry_nodes(self, graph: WorkflowGraphDefinition) -> list[str]:
        targeted = {edge.target_node_id for edge in graph.edges}
        return [node.node_id for node in graph.nodes if node.node_id not in targeted]

    def _terminal_nodes(self, graph: WorkflowGraphDefinition) -> list[str]:
        outgoing = {edge.source_node_id for edge in graph.edges}
        return [node.node_id for node in graph.nodes if node.node_id not in outgoing]


