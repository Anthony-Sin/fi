from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from desktop_automation_perception.models import (
    WorkflowGraphCondition,
    WorkflowGraphConditionOperator,
    WorkflowGraphDefinition,
    WorkflowGraphEdge,
    WorkflowGraphEdgeType,
    WorkflowGraphNode,
    WorkflowGraphNodeType,
    WorkflowGraphPlanResult,
    WorkflowGraphState,
)


@dataclass(slots=True)
class GraphBasedWorkflowPlanner:
    def initialize_state(self, graph: WorkflowGraphDefinition) -> WorkflowGraphPlanResult:
        validation = self.validate(graph)
        if not validation.succeeded:
            return validation
        entry_node_ids = graph.entry_node_ids or tuple(self._find_entry_nodes(graph))
        state = WorkflowGraphState(ready_node_ids=tuple(entry_node_ids))
        return WorkflowGraphPlanResult(
            succeeded=True,
            ready_nodes=self._nodes_by_id(graph, state.ready_node_ids),
            state=state,
        )

    def get_ready_nodes(
        self,
        graph: WorkflowGraphDefinition,
        state: WorkflowGraphState,
    ) -> WorkflowGraphPlanResult:
        validation = self.validate(graph)
        if not validation.succeeded:
            return validation
        return WorkflowGraphPlanResult(
            succeeded=True,
            ready_nodes=self._nodes_by_id(graph, state.ready_node_ids),
            state=state,
        )

    def complete_step(
        self,
        graph: WorkflowGraphDefinition,
        state: WorkflowGraphState,
        node_id: str,
        output: dict[str, Any] | None = None,
    ) -> WorkflowGraphPlanResult:
        validation = self.validate(graph)
        if not validation.succeeded:
            return validation
        node = self._node_lookup(graph).get(node_id)
        if node is None:
            return WorkflowGraphPlanResult(succeeded=False, state=state, reason="Step node was not found.")

        ready_node_ids = list(dict.fromkeys(item for item in state.ready_node_ids if item != node_id))
        completed_node_ids = list(dict.fromkeys([*state.completed_node_ids, node_id]))
        node_outputs = dict(state.node_outputs)
        node_outputs[node_id] = dict(output or {})
        execution_counts = dict(state.execution_counts)
        execution_counts[node_id] = execution_counts.get(node_id, 0) + 1
        loop_iterations = dict(state.loop_iterations)

        for edge in self._outgoing_edges(graph, node_id):
            if not self._edge_allows_transition(edge, node_outputs[node_id]):
                continue
            if edge.loop_id is not None and edge.max_iterations is not None:
                current_iterations = loop_iterations.get(edge.loop_id, 0)
                if current_iterations >= edge.max_iterations:
                    continue
                loop_iterations[edge.loop_id] = current_iterations + 1

            target = self._node_lookup(graph)[edge.target_node_id]
            if target.node_type is WorkflowGraphNodeType.MERGE or target.wait_for_all_predecessors:
                if not self._merge_ready(graph, target, completed_node_ids):
                    continue
            if edge.target_node_id not in ready_node_ids:
                ready_node_ids.append(edge.target_node_id)

        new_state = WorkflowGraphState(
            ready_node_ids=tuple(ready_node_ids),
            completed_node_ids=tuple(completed_node_ids),
            node_outputs=node_outputs,
            execution_counts=execution_counts,
            loop_iterations=loop_iterations,
        )
        return WorkflowGraphPlanResult(
            succeeded=True,
            ready_nodes=self._nodes_by_id(graph, new_state.ready_node_ids),
            state=new_state,
        )

    def validate(self, graph: WorkflowGraphDefinition) -> WorkflowGraphPlanResult:
        node_lookup = self._node_lookup(graph)
        if not graph.nodes:
            return WorkflowGraphPlanResult(succeeded=False, reason="Workflow graph must define at least one node.")
        if len(node_lookup) != len(graph.nodes):
            return WorkflowGraphPlanResult(succeeded=False, reason="Workflow graph contains duplicate node identifiers.")
        if graph.entry_node_ids:
            missing_entries = [item for item in graph.entry_node_ids if item not in node_lookup]
            if missing_entries:
                return WorkflowGraphPlanResult(
                    succeeded=False,
                    reason=f"Workflow graph contains unknown entry nodes: {', '.join(missing_entries)}.",
                )
        edge_ids: set[str] = set()
        for edge in graph.edges:
            if edge.edge_id in edge_ids:
                return WorkflowGraphPlanResult(succeeded=False, reason="Workflow graph contains duplicate edge identifiers.")
            edge_ids.add(edge.edge_id)
            if edge.source_node_id not in node_lookup or edge.target_node_id not in node_lookup:
                return WorkflowGraphPlanResult(
                    succeeded=False,
                    reason=f"Edge {edge.edge_id!r} references a node that does not exist.",
                )
        return WorkflowGraphPlanResult(succeeded=True)

    def to_json(self, graph: WorkflowGraphDefinition) -> str:
        payload = {
            "workflow_id": graph.workflow_id,
            "version": graph.version,
            "entry_node_ids": list(graph.entry_node_ids),
            "metadata": dict(graph.metadata),
            "nodes": [self._serialize_node(node) for node in graph.nodes],
            "edges": [self._serialize_edge(edge) for edge in graph.edges],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def from_json(self, payload: str | dict[str, Any]) -> WorkflowGraphDefinition:
        raw = json.loads(payload) if isinstance(payload, str) else dict(payload)
        return WorkflowGraphDefinition(
            workflow_id=raw["workflow_id"],
            version=int(raw.get("version", 1)),
            entry_node_ids=tuple(raw.get("entry_node_ids", [])),
            nodes=[self._deserialize_node(item) for item in raw.get("nodes", [])],
            edges=[self._deserialize_edge(item) for item in raw.get("edges", [])],
            metadata=dict(raw.get("metadata", {})),
        )

    def _edge_allows_transition(self, edge: WorkflowGraphEdge, output: dict[str, Any]) -> bool:
        if edge.condition is None:
            return True
        actual = output if edge.condition.output_key is None else output.get(edge.condition.output_key)
        operator = edge.condition.operator
        expected = edge.condition.expected_value

        if operator is WorkflowGraphConditionOperator.EQUALS:
            return actual == expected
        if operator is WorkflowGraphConditionOperator.NOT_EQUALS:
            return actual != expected
        if operator is WorkflowGraphConditionOperator.GREATER_THAN:
            return actual is not None and actual > expected
        if operator is WorkflowGraphConditionOperator.GREATER_OR_EQUAL:
            return actual is not None and actual >= expected
        if operator is WorkflowGraphConditionOperator.LESS_THAN:
            return actual is not None and actual < expected
        if operator is WorkflowGraphConditionOperator.LESS_OR_EQUAL:
            return actual is not None and actual <= expected
        if operator is WorkflowGraphConditionOperator.TRUTHY:
            return bool(actual)
        if operator is WorkflowGraphConditionOperator.FALSY:
            return not bool(actual)
        if operator is WorkflowGraphConditionOperator.CONTAINS:
            return actual is not None and expected in actual
        if operator is WorkflowGraphConditionOperator.IN:
            return actual in expected if expected is not None else False
        return False

    def _merge_ready(
        self,
        graph: WorkflowGraphDefinition,
        target: WorkflowGraphNode,
        completed_node_ids: list[str],
    ) -> bool:
        predecessors = {
            edge.source_node_id
            for edge in graph.edges
            if edge.target_node_id == target.node_id
        }
        return predecessors.issubset(set(completed_node_ids))

    def _find_entry_nodes(self, graph: WorkflowGraphDefinition) -> list[str]:
        targeted = {edge.target_node_id for edge in graph.edges}
        return [node.node_id for node in graph.nodes if node.node_id not in targeted]

    def _node_lookup(self, graph: WorkflowGraphDefinition) -> dict[str, WorkflowGraphNode]:
        return {node.node_id: node for node in graph.nodes}

    def _nodes_by_id(self, graph: WorkflowGraphDefinition, node_ids: tuple[str, ...] | list[str]) -> list[WorkflowGraphNode]:
        lookup = self._node_lookup(graph)
        return [lookup[node_id] for node_id in node_ids if node_id in lookup]

    def _outgoing_edges(self, graph: WorkflowGraphDefinition, source_node_id: str) -> list[WorkflowGraphEdge]:
        return [edge for edge in graph.edges if edge.source_node_id == source_node_id]

    def _serialize_node(self, node: WorkflowGraphNode) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "step_name": node.step_name,
            "node_type": node.node_type.value,
            "application_name": node.application_name,
            "step_payload": dict(node.step_payload),
            "wait_for_all_predecessors": node.wait_for_all_predecessors,
            "metadata": dict(node.metadata),
        }

    def _deserialize_node(self, payload: dict[str, Any]) -> WorkflowGraphNode:
        return WorkflowGraphNode(
            node_id=payload["node_id"],
            step_name=payload["step_name"],
            node_type=WorkflowGraphNodeType(payload.get("node_type", WorkflowGraphNodeType.STEP.value)),
            application_name=payload.get("application_name"),
            step_payload=dict(payload.get("step_payload", {})),
            wait_for_all_predecessors=bool(payload.get("wait_for_all_predecessors", False)),
            metadata=dict(payload.get("metadata", {})),
        )

    def _serialize_edge(self, edge: WorkflowGraphEdge) -> dict[str, Any]:
        return {
            "edge_id": edge.edge_id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "edge_type": edge.edge_type.value,
            "condition": None if edge.condition is None else self._serialize_condition(edge.condition),
            "loop_id": edge.loop_id,
            "max_iterations": edge.max_iterations,
            "metadata": dict(edge.metadata),
        }

    def _deserialize_edge(self, payload: dict[str, Any]) -> WorkflowGraphEdge:
        condition_payload = payload.get("condition")
        return WorkflowGraphEdge(
            edge_id=payload["edge_id"],
            source_node_id=payload["source_node_id"],
            target_node_id=payload["target_node_id"],
            edge_type=WorkflowGraphEdgeType(payload.get("edge_type", WorkflowGraphEdgeType.SEQUENTIAL.value)),
            condition=None if condition_payload is None else self._deserialize_condition(condition_payload),
            loop_id=payload.get("loop_id"),
            max_iterations=payload.get("max_iterations"),
            metadata=dict(payload.get("metadata", {})),
        )

    def _serialize_condition(self, condition: WorkflowGraphCondition) -> dict[str, Any]:
        return {
            "output_key": condition.output_key,
            "operator": condition.operator.value,
            "expected_value": condition.expected_value,
        }

    def _deserialize_condition(self, payload: dict[str, Any]) -> WorkflowGraphCondition:
        return WorkflowGraphCondition(
            output_key=payload.get("output_key"),
            operator=WorkflowGraphConditionOperator(
                payload.get("operator", WorkflowGraphConditionOperator.EQUALS.value)
            ),
            expected_value=payload.get("expected_value"),
        )
