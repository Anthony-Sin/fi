from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from desktop_automation_perception.automation.mcp_tool_adapter import MCPToolAdapter
from desktop_automation_perception.models import (
    CheckpointResumePolicy,
    UIStateFingerprint,
    WorkflowContext,
    WorkflowStepResult,
)


@dataclass(frozen=True, slots=True)
class LangGraphNodeDefinition:
    name: str
    description: str
    input_state_key: str
    output_state_key: str


class LangGraphStateConnector:
    def __init__(
        self,
        *,
        screenshot_backend: object | None = None,
        perception_engine: object | None = None,
        element_locator: object | None = None,
        input_runner: object | None = None,
        account_switcher: object | None = None,
        data_extractor: object | None = None,
        navigator: object | None = None,
        allowlist_enforcer: object | None = None,
        checkpoint_manager: object | None = None,
        workflow_id: str | None = None,
        pipeline_state_key: str = "automation_pipeline",
        inputs_state_key: str = "automation_inputs",
        outputs_state_key: str = "automation_outputs",
    ):
        self._pipeline_state_key = pipeline_state_key
        self._inputs_state_key = inputs_state_key
        self._outputs_state_key = outputs_state_key
        self._workflow_id = workflow_id
        self._checkpoint_manager = checkpoint_manager
        self._tool_adapter = MCPToolAdapter(
            screenshot_backend=screenshot_backend,
            perception_engine=perception_engine,
            element_locator=element_locator,
            input_runner=input_runner,
            account_switcher=account_switcher,
            data_extractor=data_extractor,
            navigator=navigator,
            allowlist_enforcer=allowlist_enforcer,
            workflow_id=workflow_id,
            step_name_prefix="langgraph",
        )
        self._definitions = {
            name: LangGraphNodeDefinition(
                name=name,
                description=description,
                input_state_key=f"{self._inputs_state_key}.{name}",
                output_state_key=f"{self._outputs_state_key}.{name}",
            )
            for name, description in (
                ("take_screenshot", "Capture a screenshot and write the saved path into LangGraph state."),
                ("find_element", "Locate a UI element from the current desktop state and write the match into state."),
                ("click", "Execute a click action from arguments stored in LangGraph state."),
                ("type", "Execute a text entry action from arguments stored in LangGraph state."),
                ("switch_account", "Switch the automation pipeline to a different account or browser profile."),
                ("read_data", "Extract structured data and write the extracted records into state."),
                ("navigate", "Execute a navigation sequence and write the navigation result into state."),
            )
        }

    def create_initial_state(
        self,
        *,
        workflow_id: str | None = None,
        workflow_context: WorkflowContext | None = None,
        account_context: dict[str, str] | None = None,
        collected_data: dict[str, str] | None = None,
        step_results: list[WorkflowStepResult] | None = None,
        ui_state_fingerprint: UIStateFingerprint | None = None,
        extra_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = dict(extra_state or {})
        state.setdefault(self._inputs_state_key, {})
        state.setdefault(self._outputs_state_key, {})
        state[self._pipeline_state_key] = self._build_pipeline_state(
            workflow_id=workflow_id or self._workflow_id,
            workflow_context=workflow_context,
            account_context=account_context,
            collected_data=collected_data,
            step_results=step_results,
            ui_state_fingerprint=ui_state_fingerprint,
        )
        return state

    def list_nodes(self) -> list[LangGraphNodeDefinition]:
        return list(self._definitions.values())

    def build_nodes(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {name: self.make_node(name) for name in self._definitions}

    def make_node(
        self,
        name: str,
        *,
        input_state_key: str | None = None,
        output_state_key: str | None = None,
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        if name not in self._definitions:
            raise ValueError(f"Unknown LangGraph automation node {name!r}.")

        definition = self._definitions[name]
        effective_input_key = input_state_key or definition.input_state_key
        effective_output_key = output_state_key or definition.output_state_key

        def _node(state: dict[str, Any]) -> dict[str, Any]:
            next_state = self._ensure_state_shape(state)
            arguments = self._read_path(next_state, effective_input_key, required=True)
            if not isinstance(arguments, dict):
                raise ValueError(f"LangGraph node input at {effective_input_key!r} must be an object.")

            response = self._tool_adapter.handle_tool_call(
                {
                    "tool_name": name,
                    "arguments": arguments,
                }
            )
            result = deepcopy(response["structuredContent"])
            self._write_path(next_state, effective_output_key, result)
            self._record_node_result(next_state, name=name, arguments=arguments, result=result, is_error=bool(response["isError"]))
            return next_state

        return _node

    def serialize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._serialize(self._ensure_state_shape(state))

    def restore_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        restored = deepcopy(payload)
        pipeline = restored.setdefault(self._pipeline_state_key, self._build_pipeline_state())
        pipeline.setdefault("workflow_context", self._serialize_workflow_context(WorkflowContext()))
        pipeline.setdefault("account_context", {})
        pipeline.setdefault("collected_data", {})
        pipeline.setdefault("step_results", [])
        pipeline.setdefault("ui_state_fingerprint", None)
        pipeline.setdefault("last_result", None)
        pipeline.setdefault("last_error", None)
        restored.setdefault(self._inputs_state_key, {})
        restored.setdefault(self._outputs_state_key, {})
        return restored

    def save_checkpoint(
        self,
        state: dict[str, Any],
        *,
        workflow_id: str | None = None,
        step_index: int | None = None,
    ) -> dict[str, Any]:
        if self._checkpoint_manager is None:
            raise ValueError("Checkpoint manager is not configured.")
        next_state = self._ensure_state_shape(state)
        pipeline = next_state[self._pipeline_state_key]
        effective_workflow_id = workflow_id or pipeline.get("workflow_id") or self._workflow_id
        if effective_workflow_id is None:
            raise ValueError("A workflow_id is required to save a LangGraph checkpoint.")
        workflow_context = self._deserialize_workflow_context(pipeline.get("workflow_context"))
        checkpoint = self._checkpoint_manager.save_checkpoint(
            workflow_id=effective_workflow_id,
            step_index=step_index if step_index is not None else workflow_context.step_number,
            workflow_context=workflow_context,
            account_context=dict(pipeline.get("account_context", {})),
            collected_data=dict(pipeline.get("collected_data", {})),
            step_outcomes=[self._deserialize_step_result(item) for item in pipeline.get("step_results", [])],
            ui_state_fingerprint=self._deserialize_ui_state_fingerprint(pipeline.get("ui_state_fingerprint")),
        )
        payload = self._serialize(checkpoint)
        pipeline["last_checkpoint"] = payload
        return payload

    def restore_checkpoint(
        self,
        *,
        workflow_id: str | None = None,
        policy: CheckpointResumePolicy = CheckpointResumePolicy.AUTO_RESUME,
        base_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._checkpoint_manager is None:
            raise ValueError("Checkpoint manager is not configured.")
        effective_workflow_id = workflow_id or self._workflow_id
        if effective_workflow_id is None:
            raise ValueError("A workflow_id is required to restore a LangGraph checkpoint.")

        restored = self._checkpoint_manager.restore_or_restart(workflow_id=effective_workflow_id, policy=policy)
        state = self._ensure_state_shape(base_state or {})
        pipeline = state[self._pipeline_state_key]
        pipeline["last_checkpoint_restore"] = self._serialize(restored)
        if not restored.succeeded or restored.checkpoint is None:
            pipeline["workflow_id"] = effective_workflow_id
            return state

        checkpoint = restored.checkpoint
        pipeline["workflow_id"] = checkpoint.workflow_id
        pipeline["workflow_context"] = self._serialize_workflow_context(checkpoint.workflow_context)
        pipeline["account_context"] = dict(checkpoint.account_context)
        pipeline["collected_data"] = dict(checkpoint.collected_data)
        pipeline["step_results"] = [self._serialize_step_result(item) for item in checkpoint.step_outcomes]
        pipeline["ui_state_fingerprint"] = self._serialize_ui_state_fingerprint(checkpoint.ui_state_fingerprint)
        pipeline["last_checkpoint"] = self._serialize(checkpoint)
        return state

    def _ensure_state_shape(self, state: dict[str, Any]) -> dict[str, Any]:
        next_state = deepcopy(state)
        next_state.setdefault(self._inputs_state_key, {})
        next_state.setdefault(self._outputs_state_key, {})
        next_state.setdefault(self._pipeline_state_key, self._build_pipeline_state())
        pipeline = next_state[self._pipeline_state_key]
        pipeline.setdefault("workflow_id", self._workflow_id)
        pipeline.setdefault("workflow_context", self._serialize_workflow_context(WorkflowContext()))
        pipeline.setdefault("account_context", {})
        pipeline.setdefault("collected_data", {})
        pipeline.setdefault("step_results", [])
        pipeline.setdefault("ui_state_fingerprint", None)
        pipeline.setdefault("last_result", None)
        pipeline.setdefault("last_error", None)
        return next_state

    def _record_node_result(
        self,
        state: dict[str, Any],
        *,
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        is_error: bool,
    ) -> None:
        pipeline = state[self._pipeline_state_key]
        context_payload = pipeline["workflow_context"]
        context_payload["step_number"] = int(context_payload.get("step_number", 0)) + 1
        application_name = (
            arguments.get("application_name")
            or arguments.get("window_title")
            or context_payload.get("current_application")
        )
        context_payload["current_application"] = application_name
        if application_name and application_name not in context_payload["active_applications"]:
            context_payload["active_applications"].append(application_name)
        stored_result = self._stable_json(result)
        context_payload["shared_data"][f"{name}_result"] = stored_result
        pipeline["collected_data"][f"{name}_result"] = stored_result
        pipeline["last_result"] = result
        pipeline["last_error"] = result.get("reason") if is_error else None
        pipeline["step_results"].append(
            self._serialize_step_result(
                WorkflowStepResult(
                    step_id=name,
                    application_name=application_name or name,
                    succeeded=not is_error and bool(result.get("succeeded", False)),
                    dry_run=bool(arguments.get("dry_run", False)),
                    context_snapshot=self._deserialize_workflow_context(context_payload),
                    reason=result.get("reason") or result.get("failure_reason"),
                )
            )
        )

    def _read_path(self, payload: dict[str, Any], path: str, *, required: bool = False) -> Any:
        current: Any = payload
        for segment in path.split("."):
            if not isinstance(current, dict) or segment not in current:
                if required:
                    raise ValueError(f"Missing required LangGraph state input at {path!r}.")
                return None
            current = current[segment]
        return current

    def _write_path(self, payload: dict[str, Any], path: str, value: Any) -> None:
        segments = path.split(".")
        current = payload
        for segment in segments[:-1]:
            current = current.setdefault(segment, {})
        current[segments[-1]] = value

    def _build_pipeline_state(
        self,
        *,
        workflow_id: str | None = None,
        workflow_context: WorkflowContext | None = None,
        account_context: dict[str, str] | None = None,
        collected_data: dict[str, str] | None = None,
        step_results: list[WorkflowStepResult] | None = None,
        ui_state_fingerprint: UIStateFingerprint | None = None,
    ) -> dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "workflow_context": self._serialize_workflow_context(workflow_context or WorkflowContext()),
            "account_context": dict(account_context or {}),
            "collected_data": dict(collected_data or {}),
            "step_results": [self._serialize_step_result(item) for item in (step_results or [])],
            "ui_state_fingerprint": self._serialize_ui_state_fingerprint(ui_state_fingerprint),
            "last_result": None,
            "last_error": None,
        }

    def _serialize_workflow_context(self, context: WorkflowContext) -> dict[str, Any]:
        return {
            "current_application": context.current_application,
            "step_number": context.step_number,
            "shared_data": dict(context.shared_data),
            "secure_data_keys": list(context.secure_data.keys()),
            "active_applications": list(context.active_applications),
            "application_signatures": dict(context.application_signatures),
        }

    def _deserialize_workflow_context(self, payload: dict[str, Any] | None) -> WorkflowContext:
        payload = payload or {}
        return WorkflowContext(
            current_application=payload.get("current_application"),
            step_number=int(payload.get("step_number", 0)),
            shared_data=dict(payload.get("shared_data", {})),
            active_applications=list(payload.get("active_applications", [])),
            application_signatures=dict(payload.get("application_signatures", {})),
        )

    def _serialize_step_result(self, outcome: WorkflowStepResult) -> dict[str, Any]:
        return {
            "step_id": outcome.step_id,
            "application_name": outcome.application_name,
            "succeeded": outcome.succeeded,
            "dry_run": outcome.dry_run,
            "context_snapshot": self._serialize_workflow_context(outcome.context_snapshot)
            if outcome.context_snapshot is not None
            else None,
            "reason": outcome.reason,
        }

    def _deserialize_step_result(self, payload: dict[str, Any]) -> WorkflowStepResult:
        return WorkflowStepResult(
            step_id=payload["step_id"],
            application_name=payload["application_name"],
            succeeded=bool(payload["succeeded"]),
            dry_run=bool(payload.get("dry_run", False)),
            context_snapshot=self._deserialize_workflow_context(payload.get("context_snapshot"))
            if payload.get("context_snapshot") is not None
            else None,
            reason=payload.get("reason"),
        )

    def _serialize_ui_state_fingerprint(self, fingerprint: UIStateFingerprint | None) -> dict[str, Any] | None:
        if fingerprint is None:
            return None
        return {
            "window_title_hash": fingerprint.window_title_hash,
            "landmark_positions": {
                name: [position[0], position[1]] for name, position in fingerprint.landmark_positions.items()
            },
            "pixel_histogram": list(fingerprint.pixel_histogram),
            "screen_size": [fingerprint.screen_size[0], fingerprint.screen_size[1]],
            "window_count": fingerprint.window_count,
            "captured_at": fingerprint.captured_at.isoformat(),
        }

    def _deserialize_ui_state_fingerprint(self, payload: dict[str, Any] | None) -> UIStateFingerprint | None:
        if payload is None:
            return None
        screen_size = payload.get("screen_size", [0, 0])
        return UIStateFingerprint(
            window_title_hash=payload.get("window_title_hash", ""),
            landmark_positions={
                name: (float(position[0]), float(position[1]))
                for name, position in dict(payload.get("landmark_positions", {})).items()
            },
            pixel_histogram=tuple(float(value) for value in payload.get("pixel_histogram", [])),
            screen_size=(int(screen_size[0]), int(screen_size[1])),
            window_count=int(payload.get("window_count", 0)),
            captured_at=datetime.fromisoformat(payload["captured_at"])
            if payload.get("captured_at")
            else datetime.now(timezone.utc),
        )

    def _serialize(self, value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return {
                key: self._serialize(getattr(value, key))
                for key in value.__dataclass_fields__
            }
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serialize(item) for item in value]
        if hasattr(value, "value") and not isinstance(value, (str, bytes, int, float, bool)):
            enum_value = getattr(value, "value", None)
            if isinstance(enum_value, (str, int, float, bool)):
                return enum_value
        return value

    def _stable_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(self._serialize(payload), sort_keys=True)
