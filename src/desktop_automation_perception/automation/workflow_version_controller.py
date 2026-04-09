from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from desktop_automation_perception.automation.graph_workflow_planner import GraphBasedWorkflowPlanner
from desktop_automation_perception.models import (
    WorkflowDefinitionVersion,
    WorkflowGraphDefinition,
    WorkflowVersionControlResult,
    WorkflowVersionDiff,
    WorkflowVersionSnapshot,
    WorkflowVersionTag,
)


@dataclass(slots=True)
class WorkflowVersionController:
    storage_path: str
    graph_planner: GraphBasedWorkflowPlanner

    def create_version(
        self,
        *,
        workflow_id: str,
        author: str,
        change_description: str,
        workflow_graph: WorkflowGraphDefinition,
        tag: WorkflowVersionTag = WorkflowVersionTag.EXPERIMENTAL,
        activate: bool = False,
        timestamp: datetime | None = None,
    ) -> WorkflowVersionControlResult:
        snapshot = self._load_snapshot(workflow_id)
        next_version = 1 + max((item.version_number for item in snapshot.versions), default=0)
        version = WorkflowDefinitionVersion(
            workflow_id=workflow_id,
            version_number=next_version,
            author=author,
            timestamp=timestamp or utc_now(),
            change_description=change_description,
            workflow_graph=workflow_graph,
            tag=tag,
        )
        snapshot.versions.append(version)
        if activate or snapshot.active_version_number is None:
            snapshot.active_version_number = version.version_number
        self._save_snapshot(snapshot)
        return WorkflowVersionControlResult(
            succeeded=True,
            version=version,
            versions=list(snapshot.versions),
            snapshot=snapshot,
        )

    def list_versions(self, workflow_id: str) -> WorkflowVersionControlResult:
        snapshot = self._load_snapshot(workflow_id)
        return WorkflowVersionControlResult(
            succeeded=True,
            versions=list(snapshot.versions),
            snapshot=snapshot,
        )

    def get_active_version(self, workflow_id: str) -> WorkflowVersionControlResult:
        snapshot = self._load_snapshot(workflow_id)
        if snapshot.active_version_number is None:
            return WorkflowVersionControlResult(succeeded=False, snapshot=snapshot, reason="No active workflow version.")
        version = self._find_version(snapshot, snapshot.active_version_number)
        if version is None:
            return WorkflowVersionControlResult(succeeded=False, snapshot=snapshot, reason="Active version could not be found.")
        return WorkflowVersionControlResult(succeeded=True, version=version, snapshot=snapshot)

    def activate_version(self, workflow_id: str, version_number: int) -> WorkflowVersionControlResult:
        snapshot = self._load_snapshot(workflow_id)
        version = self._find_version(snapshot, version_number)
        if version is None:
            return WorkflowVersionControlResult(succeeded=False, snapshot=snapshot, reason="Workflow version was not found.")
        snapshot.active_version_number = version_number
        self._save_snapshot(snapshot)
        return WorkflowVersionControlResult(succeeded=True, version=version, snapshot=snapshot)

    def rollback_to_version(self, workflow_id: str, version_number: int) -> WorkflowVersionControlResult:
        result = self.activate_version(workflow_id, version_number)
        if result.succeeded and result.version is not None:
            result.version = WorkflowDefinitionVersion(
                workflow_id=result.version.workflow_id,
                version_number=result.version.version_number,
                author=result.version.author,
                timestamp=result.version.timestamp,
                change_description=result.version.change_description,
                workflow_graph=result.version.workflow_graph,
                tag=result.version.tag,
            )
        return result

    def tag_version(
        self,
        workflow_id: str,
        version_number: int,
        tag: WorkflowVersionTag,
    ) -> WorkflowVersionControlResult:
        snapshot = self._load_snapshot(workflow_id)
        updated_versions: list[WorkflowDefinitionVersion] = []
        selected: WorkflowDefinitionVersion | None = None
        for version in snapshot.versions:
            if version.version_number == version_number:
                selected = WorkflowDefinitionVersion(
                    workflow_id=version.workflow_id,
                    version_number=version.version_number,
                    author=version.author,
                    timestamp=version.timestamp,
                    change_description=version.change_description,
                    workflow_graph=version.workflow_graph,
                    tag=tag,
                )
                updated_versions.append(selected)
            else:
                updated_versions.append(version)
        if selected is None:
            return WorkflowVersionControlResult(succeeded=False, snapshot=snapshot, reason="Workflow version was not found.")
        snapshot.versions = updated_versions
        self._save_snapshot(snapshot)
        return WorkflowVersionControlResult(succeeded=True, version=selected, snapshot=snapshot)

    def diff_versions(
        self,
        workflow_id: str,
        from_version_number: int,
        to_version_number: int,
    ) -> WorkflowVersionControlResult:
        snapshot = self._load_snapshot(workflow_id)
        from_version = self._find_version(snapshot, from_version_number)
        to_version = self._find_version(snapshot, to_version_number)
        if from_version is None or to_version is None:
            return WorkflowVersionControlResult(succeeded=False, snapshot=snapshot, reason="One or both workflow versions were not found.")

        diff = WorkflowVersionDiff(
            workflow_id=workflow_id,
            from_version_number=from_version_number,
            to_version_number=to_version_number,
            added_node_ids=self._added_keys(
                self._node_payloads(from_version.workflow_graph),
                self._node_payloads(to_version.workflow_graph),
            ),
            removed_node_ids=self._added_keys(
                self._node_payloads(to_version.workflow_graph),
                self._node_payloads(from_version.workflow_graph),
            ),
            changed_node_ids=self._changed_keys(
                self._node_payloads(from_version.workflow_graph),
                self._node_payloads(to_version.workflow_graph),
            ),
            added_edge_ids=self._added_keys(
                self._edge_payloads(from_version.workflow_graph),
                self._edge_payloads(to_version.workflow_graph),
            ),
            removed_edge_ids=self._added_keys(
                self._edge_payloads(to_version.workflow_graph),
                self._edge_payloads(from_version.workflow_graph),
            ),
            changed_edge_ids=self._changed_keys(
                self._edge_payloads(from_version.workflow_graph),
                self._edge_payloads(to_version.workflow_graph),
            ),
        )
        return WorkflowVersionControlResult(succeeded=True, snapshot=snapshot, diff=diff)

    def _load_snapshot(self, workflow_id: str) -> WorkflowVersionSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return WorkflowVersionSnapshot(workflow_id=workflow_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        workflow_payload = payload.get("workflows", {}).get(workflow_id)
        if workflow_payload is None:
            return WorkflowVersionSnapshot(workflow_id=workflow_id)
        return WorkflowVersionSnapshot(
            workflow_id=workflow_id,
            active_version_number=workflow_payload.get("active_version_number"),
            versions=[self._deserialize_version(item) for item in workflow_payload.get("versions", [])],
        )

    def _save_snapshot(self, snapshot: WorkflowVersionSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"workflows": {}}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        workflows = dict(payload.get("workflows", {}))
        workflows[snapshot.workflow_id] = {
            "active_version_number": snapshot.active_version_number,
            "versions": [self._serialize_version(item) for item in snapshot.versions],
        }
        payload["workflows"] = workflows
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_version(self, version: WorkflowDefinitionVersion) -> dict:
        return {
            "workflow_id": version.workflow_id,
            "version_number": version.version_number,
            "author": version.author,
            "timestamp": version.timestamp.isoformat(),
            "change_description": version.change_description,
            "workflow_graph": json.loads(self.graph_planner.to_json(version.workflow_graph)),
            "tag": version.tag.value,
        }

    def _deserialize_version(self, payload: dict) -> WorkflowDefinitionVersion:
        return WorkflowDefinitionVersion(
            workflow_id=payload["workflow_id"],
            version_number=int(payload["version_number"]),
            author=payload["author"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            change_description=payload["change_description"],
            workflow_graph=self.graph_planner.from_json(payload["workflow_graph"]),
            tag=WorkflowVersionTag(payload.get("tag", WorkflowVersionTag.EXPERIMENTAL.value)),
        )

    def _find_version(self, snapshot: WorkflowVersionSnapshot, version_number: int) -> WorkflowDefinitionVersion | None:
        return next((item for item in snapshot.versions if item.version_number == version_number), None)

    def _node_payloads(self, graph: WorkflowGraphDefinition) -> dict[str, dict]:
        return {
            node.node_id: {
                "step_name": node.step_name,
                "node_type": node.node_type.value,
                "application_name": node.application_name,
                "step_payload": dict(node.step_payload),
                "wait_for_all_predecessors": node.wait_for_all_predecessors,
                "metadata": dict(node.metadata),
            }
            for node in graph.nodes
        }

    def _edge_payloads(self, graph: WorkflowGraphDefinition) -> dict[str, dict]:
        return {
            edge.edge_id: {
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "edge_type": edge.edge_type.value,
                "condition": None
                if edge.condition is None
                else {
                    "output_key": edge.condition.output_key,
                    "operator": edge.condition.operator.value,
                    "expected_value": edge.condition.expected_value,
                },
                "loop_id": edge.loop_id,
                "max_iterations": edge.max_iterations,
                "metadata": dict(edge.metadata),
            }
            for edge in graph.edges
        }

    def _added_keys(self, left: dict[str, dict], right: dict[str, dict]) -> list[str]:
        return sorted([key for key in right.keys() if key not in left])

    def _changed_keys(self, left: dict[str, dict], right: dict[str, dict]) -> list[str]:
        return sorted([key for key in left.keys() & right.keys() if left[key] != right[key]])


