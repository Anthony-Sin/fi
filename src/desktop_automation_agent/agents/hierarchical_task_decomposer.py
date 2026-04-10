from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from desktop_automation_agent.models import (
    OrchestratorSubtask,
    OrchestratorTaskPlan,
    TaskDecompositionLevel,
    TaskDecompositionNode,
    TaskDecompositionResult,
    TaskDecompositionTree,
)


@dataclass(slots=True)
class HierarchicalTaskDecomposer:
    max_depth: int = 3
    execution_expansion_depth: int | None = None

    def decompose(
        self,
        task_description: str,
        *,
        max_depth: int | None = None,
        execution_expansion_depth: int | None = None,
    ) -> TaskDecompositionResult:
        effective_max_depth = max(1, max_depth if max_depth is not None else self.max_depth)
        effective_execution_depth = execution_expansion_depth
        if effective_execution_depth is None:
            configured = self.execution_expansion_depth if self.execution_expansion_depth is not None else effective_max_depth
            effective_execution_depth = min(effective_max_depth, max(1, configured))
        else:
            effective_execution_depth = min(effective_max_depth, max(1, effective_execution_depth))

        normalized = self._normalize(task_description)
        if not normalized:
            return TaskDecompositionResult(succeeded=False, reason="Task description is empty.")

        phase_segments = self._split_phases(normalized)
        root_nodes = [
            self._build_node(
                title=segment,
                description=segment,
                level=TaskDecompositionLevel.PHASE,
                depth=1,
                ordinal=index,
                max_depth=effective_max_depth,
                expand_to_depth=effective_execution_depth,
            )
            for index, segment in enumerate(phase_segments, start=1)
        ]
        tree = TaskDecompositionTree(
            task_description=normalized,
            max_depth=effective_max_depth,
            fully_decomposed=self._tree_fully_decomposed(root_nodes),
            root_nodes=root_nodes,
            metadata={"execution_expansion_depth": effective_execution_depth},
        )
        return TaskDecompositionResult(succeeded=True, tree=tree)

    def to_orchestrator_plan(self, tree: TaskDecompositionTree) -> OrchestratorTaskPlan:
        subtasks: list[OrchestratorSubtask] = []
        frontier = self._collect_frontier_nodes(tree.root_nodes)
        prior_subtask_id: str | None = None
        for index, node in enumerate(frontier, start=1):
            subtask_id = f"decomp-subtask-{index}"
            subtasks.append(
                OrchestratorSubtask(
                    subtask_id=subtask_id,
                    description=node.description,
                    responsible_module=self._infer_module(node.description),
                    required_inputs=[] if prior_subtask_id is None else [f"{prior_subtask_id}_result"],
                    expected_outputs=[f"{subtask_id}_result"],
                    dependency_ids=[] if prior_subtask_id is None else [prior_subtask_id],
                )
            )
            prior_subtask_id = subtask_id
        return OrchestratorTaskPlan(
            task_description=tree.task_description,
            subtasks=subtasks,
            decomposition_tree=tree,
        )

    def serialize_tree(self, tree: TaskDecompositionTree) -> dict[str, Any]:
        return {
            "task_description": tree.task_description,
            "max_depth": tree.max_depth,
            "fully_decomposed": tree.fully_decomposed,
            "metadata": dict(tree.metadata),
            "root_nodes": [self._serialize_node(node) for node in tree.root_nodes],
        }

    def deserialize_tree(self, payload: dict[str, Any] | str) -> TaskDecompositionTree:
        raw = json.loads(payload) if isinstance(payload, str) else dict(payload)
        return TaskDecompositionTree(
            task_description=raw["task_description"],
            max_depth=int(raw["max_depth"]),
            fully_decomposed=bool(raw.get("fully_decomposed", False)),
            metadata=dict(raw.get("metadata", {})),
            root_nodes=[self._deserialize_node(item) for item in raw.get("root_nodes", [])],
        )

    def to_json(self, tree: TaskDecompositionTree) -> str:
        return json.dumps(self.serialize_tree(tree), indent=2, sort_keys=True)

    def from_json(self, payload: str) -> TaskDecompositionTree:
        return self.deserialize_tree(payload)

    def _build_node(
        self,
        *,
        title: str,
        description: str,
        level: TaskDecompositionLevel,
        depth: int,
        ordinal: int,
        max_depth: int,
        expand_to_depth: int,
    ) -> TaskDecompositionNode:
        node_id = f"{level.value}-{depth}-{ordinal}"
        if depth >= max_depth:
            return TaskDecompositionNode(
                node_id=node_id,
                title=title,
                description=description,
                level=level,
                depth=depth,
                abstract=False,
            )

        next_level = self._next_level(level)
        should_expand = depth < expand_to_depth
        children: list[TaskDecompositionNode] = []
        abstract = False
        if should_expand:
            segments = self._segments_for_level(description, next_level)
            if segments:
                children = [
                    self._build_node(
                        title=segment,
                        description=segment,
                        level=next_level,
                        depth=depth + 1,
                        ordinal=index,
                        max_depth=max_depth,
                        expand_to_depth=expand_to_depth,
                    )
                    for index, segment in enumerate(segments, start=1)
                ]
        else:
            abstract = depth < max_depth

        return TaskDecompositionNode(
            node_id=node_id,
            title=title,
            description=description,
            level=level,
            depth=depth,
            abstract=abstract,
            children=children,
            metadata={"expandable": depth < max_depth},
        )

    def _segments_for_level(self, description: str, level: TaskDecompositionLevel) -> list[str]:
        if level is TaskDecompositionLevel.TASK:
            return self._split_tasks(description)
        if level is TaskDecompositionLevel.STEP:
            return self._split_steps(description)
        return [description]

    def _split_phases(self, description: str) -> list[str]:
        segments = re.split(r"\b(?:then|after that|next|finally)\b", description, flags=re.IGNORECASE)
        cleaned = [segment.strip(" ,.;") for segment in segments if segment.strip(" ,.;")]
        return cleaned or [description]

    def _split_tasks(self, description: str) -> list[str]:
        segments = re.split(r"\b(?:and|while|plus|with)\b", description, flags=re.IGNORECASE)
        cleaned = [segment.strip(" ,.;") for segment in segments if segment.strip(" ,.;")]
        return cleaned or [description]

    def _split_steps(self, description: str) -> list[str]:
        clauses = re.split(r",|;|\b(?:by|using|before|after)\b", description, flags=re.IGNORECASE)
        cleaned = [self._ensure_verb(segment.strip(" ,.;")) for segment in clauses if segment.strip(" ,.;")]
        return cleaned or [description]

    def _collect_frontier_nodes(self, nodes: list[TaskDecompositionNode]) -> list[TaskDecompositionNode]:
        frontier: list[TaskDecompositionNode] = []
        for node in nodes:
            if node.abstract or not node.children:
                frontier.append(node)
                continue
            frontier.extend(self._collect_frontier_nodes(node.children))
        return frontier

    def _tree_fully_decomposed(self, nodes: list[TaskDecompositionNode]) -> bool:
        for node in nodes:
            if node.abstract:
                return False
            if node.children and not self._tree_fully_decomposed(node.children):
                return False
        return True

    def _serialize_node(self, node: TaskDecompositionNode) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "title": node.title,
            "description": node.description,
            "level": node.level.value,
            "depth": node.depth,
            "abstract": node.abstract,
            "metadata": dict(node.metadata),
            "children": [self._serialize_node(child) for child in node.children],
        }

    def _deserialize_node(self, payload: dict[str, Any]) -> TaskDecompositionNode:
        return TaskDecompositionNode(
            node_id=payload["node_id"],
            title=payload["title"],
            description=payload["description"],
            level=TaskDecompositionLevel(payload["level"]),
            depth=int(payload["depth"]),
            abstract=bool(payload.get("abstract", False)),
            metadata=dict(payload.get("metadata", {})),
            children=[self._deserialize_node(child) for child in payload.get("children", [])],
        )

    def _next_level(self, level: TaskDecompositionLevel) -> TaskDecompositionLevel:
        if level is TaskDecompositionLevel.PHASE:
            return TaskDecompositionLevel.TASK
        return TaskDecompositionLevel.STEP

    def _infer_module(self, description: str) -> str:
        normalized = description.casefold()
        if any(token in normalized for token in ("launch", "open", "start app", "browser", "go to", "navigate to")):
            return "application_launcher"
        if any(token in normalized for token in ("account", "login", "profile", "credential", "session")):
            return "account_rotation_orchestrator"
        if any(token in normalized for token in ("chat", "prompt", "ai", "llm", "assistant", "ask")):
            return "ai_interface_navigator"
        if any(token in normalized for token in ("extract", "collect", "read", "capture")):
            return "structured_data_extractor"
        if any(token in normalized for token in ("fill", "enter", "submit", "form")):
            return "form_automation"
        if any(token in normalized for token in ("navigate", "click", "scroll", "verify", "wait")):
            return "navigation_step_sequencer"
        if any(token in normalized for token in ("switch", "handoff", "clipboard", "workflow")):
            return "multi_application_workflow_coordinator"
        return "desktop_automation"

    def _normalize(self, task_description: str) -> str:
        return re.sub(r"\s+", " ", task_description.strip())

    def _ensure_verb(self, text: str) -> str:
        if not text:
            return text
        lowered = text.casefold()
        if any(lowered.startswith(prefix) for prefix in ("open", "launch", "click", "type", "fill", "wait", "verify", "collect", "extract", "submit", "navigate")):
            return text
        return f"Perform {text}"
