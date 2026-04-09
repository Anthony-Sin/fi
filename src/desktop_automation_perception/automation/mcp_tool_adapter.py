from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from desktop_automation_perception.context import CaptureContext
from desktop_automation_perception.models import (
    AIInterfaceElementSelector,
    AccountExecutionMode,
    AllowlistCheckRequest,
    ApplicationLaunchMode,
    ApplicationLaunchRequest,
    InputAction,
    InputActionType,
    InputTarget,
    LocatorTarget,
    NavigationSequenceMode,
    NavigationStep,
    NavigationStepActionType,
    PaginationAdvanceMode,
    PaginationConfiguration,
    RotationTask,
    ScreenCheckType,
    ScreenVerificationCheck,
    SelectorStrategy,
    StructuredDataExtractionConfiguration,
    StructuredDataExtractionMode,
    StructuredDataFieldSchema,
    StructuredDataFieldType,
    StructuredDataSchema,
    WindowReference,
)


def _schema_enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _bounds_schema() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}


def _point_schema() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}


def _selector_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "role": {"type": "string"},
            "value": {"type": "string"},
            "target_text": {"type": "string"},
            "template_name": {"type": "string"},
            "template_path": {"type": "string"},
            "bounds": _bounds_schema(),
            "region_of_interest": _bounds_schema(),
            "window_title": {"type": "string"},
            "process_name": {"type": "string"},
            "strategies": {
                "type": "array",
                "items": _schema_enum("accessibility", "ocr", "template_match", "direct_bounds"),
            },
            "threshold": {"type": "number"},
            "required": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


def _field_schema_definition() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "field_name": {"type": "string"},
            "field_type": _schema_enum("string", "integer", "number", "boolean", "date"),
            "source_name": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "selector": _selector_schema(),
            "required": {"type": "boolean"},
            "column_index": {"type": "integer"},
        },
        "required": ["field_name"],
        "additionalProperties": False,
    }


def _verification_check_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "check_id": {"type": "string"},
            "check_type": _schema_enum(
                "text_present",
                "image_present",
                "active_window",
                "element_value",
                "loading_absent",
                "modal_absent",
            ),
            "timeout_seconds": {"type": "number"},
            "polling_interval_seconds": {"type": "number"},
            "target_text": {"type": "string"},
            "template_name": {"type": "string"},
            "template_path": {"type": "string"},
            "threshold": {"type": "number"},
            "window_title": {"type": "string"},
            "process_name": {"type": "string"},
            "element_name": {"type": "string"},
            "element_role": {"type": "string"},
            "expected_value": {"type": "string"},
            "region_of_interest": _bounds_schema(),
        },
        "required": ["check_id", "check_type"],
        "additionalProperties": False,
    }


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowlist_action_type: str


@dataclass(slots=True)
class MCPToolCallRequest:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


class MCPToolAdapter:
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
        workflow_id: str | None = None,
        step_name_prefix: str = "mcp",
        task_id_factory: Callable[[], str] | None = None,
    ):
        self._screenshot_backend = screenshot_backend
        self._perception_engine = perception_engine
        self._element_locator = element_locator
        self._input_runner = input_runner
        self._account_switcher = account_switcher
        self._data_extractor = data_extractor
        self._navigator = navigator
        self._allowlist_enforcer = allowlist_enforcer
        self._workflow_id = workflow_id
        self._step_name_prefix = step_name_prefix
        self._task_id_factory = task_id_factory or (lambda: "mcp-account-switch")
        self._tools = {
            tool.name: tool
            for tool in (
                self._build_take_screenshot_tool(),
                self._build_find_element_tool(),
                self._build_click_tool(),
                self._build_type_tool(),
                self._build_switch_account_tool(),
                self._build_read_data_tool(),
                self._build_navigate_tool(),
            )
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema,
            }
            for tool in self._tools.values()
        ]

    def handle_tool_call(self, request: MCPToolCallRequest | dict[str, Any]) -> dict[str, Any]:
        parsed_request = self._coerce_request(request)
        tool = self._tools.get(parsed_request.tool_name)
        if tool is None:
            return self._error_response(parsed_request.tool_name, f"Unknown MCP tool {parsed_request.tool_name!r}.", call_id=parsed_request.call_id)

        validation_error = self._validate_arguments(tool.input_schema, parsed_request.arguments)
        if validation_error is not None:
            return self._error_response(tool.name, validation_error, call_id=parsed_request.call_id)

        allowlist_result = self._enforce_allowlist(tool, parsed_request.arguments)
        if allowlist_result is not None and not allowlist_result.allowed:
            return self._error_response(
                tool.name,
                allowlist_result.reason or "MCP tool call rejected by allowlist.",
                call_id=parsed_request.call_id,
                payload={"allowlist": self._serialize(allowlist_result)},
            )

        try:
            if tool.name == "take_screenshot":
                payload = self._handle_take_screenshot(parsed_request.arguments)
            elif tool.name == "find_element":
                payload = self._handle_find_element(parsed_request.arguments)
            elif tool.name == "click":
                payload = self._handle_click(parsed_request.arguments)
            elif tool.name == "type":
                payload = self._handle_type(parsed_request.arguments)
            elif tool.name == "switch_account":
                payload = self._handle_switch_account(parsed_request.arguments)
            elif tool.name == "read_data":
                payload = self._handle_read_data(parsed_request.arguments)
            else:
                payload = self._handle_navigate(parsed_request.arguments)
        except Exception as exc:
            return self._error_response(tool.name, str(exc), call_id=parsed_request.call_id)

        return self._success_response(tool.name, payload, call_id=parsed_request.call_id)

    def handle_mcp_request(self, request: MCPToolCallRequest | dict[str, Any]) -> dict[str, Any]:
        return self.handle_tool_call(request)

    def _handle_take_screenshot(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._screenshot_backend is None:
            raise ValueError("Screenshot backend is not configured.")
        screenshot_path = self._screenshot_backend.capture_screenshot_to_path(
            path=arguments.get("path"),
            monitor_id=arguments.get("monitor_id"),
        )
        return {"succeeded": True, "screenshot_path": screenshot_path, "monitor_id": arguments.get("monitor_id")}

    def _handle_find_element(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._perception_engine is None or self._element_locator is None:
            raise ValueError("Perception engine and element locator must both be configured.")

        context = CaptureContext(
            screenshot_path=Path(arguments["screenshot_path"]) if arguments.get("screenshot_path") else None,
            template_paths=[Path(path) for path in arguments.get("template_paths", [])],
            metadata=dict(arguments.get("metadata", {})),
        )
        desktop_state = self._perception_engine.capture_state(context)
        result = self._element_locator.locate(
            desktop_state,
            LocatorTarget(
                text=arguments.get("text"),
                template_name=arguments.get("template_name"),
                element_type=arguments.get("element_type"),
                monitor_id=arguments.get("monitor_id"),
            ),
            confidence_threshold=arguments.get("confidence_threshold"),
            monitor_id=arguments.get("monitor_id"),
        )
        return {
            "succeeded": result.succeeded,
            "desktop_state": desktop_state.best_summary(),
            "result": self._serialize(result),
            "reason": getattr(result, "reason", None),
        }

    def _handle_click(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._input_runner is None:
            raise ValueError("Input runner is not configured.")
        action = InputAction(
            action_type=InputActionType.CLICK,
            target=self._build_input_target(arguments),
            position=self._tuple_or_none(arguments.get("position")),
            button=str(arguments.get("button", "left")),
            monitor_id=arguments.get("monitor_id"),
            context_tags=tuple(arguments.get("context_tags", [])),
        )
        return self._serialize(self._input_runner.run([action]))

    def _handle_type(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._input_runner is None:
            raise ValueError("Input runner is not configured.")
        action = InputAction(
            action_type=InputActionType.TYPE_TEXT,
            target=self._build_input_target(arguments),
            text=str(arguments["text"]),
            monitor_id=arguments.get("monitor_id"),
            context_tags=tuple(arguments.get("context_tags", [])),
        )
        return self._serialize(self._input_runner.run([action]))

    def _handle_switch_account(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._account_switcher is None:
            raise ValueError("Account switcher is not configured.")

        account_name = str(arguments["account_name"])
        if hasattr(self._account_switcher, "switch_profile"):
            return self._serialize(self._account_switcher.switch_profile(account_name))

        if hasattr(self._account_switcher, "execute"):
            result = self._account_switcher.execute(
                [
                    RotationTask(
                        task_id=str(arguments.get("task_id", self._task_id_factory())),
                        required_account=account_name,
                        payload=dict(arguments.get("payload", {})),
                    )
                ],
                mode=AccountExecutionMode(arguments.get("mode", "sequential")),
                minimum_reuse_interval=timedelta(seconds=float(arguments.get("minimum_reuse_interval_seconds", 60.0))),
                unhealthy_threshold=float(arguments.get("unhealthy_threshold", 0.5)),
            )
            return self._serialize(result)

        raise ValueError("Configured account switcher does not support switch_profile() or execute().")

    def _handle_read_data(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._data_extractor is None:
            raise ValueError("Structured data extractor is not configured.")
        configuration = StructuredDataExtractionConfiguration(
            mode=StructuredDataExtractionMode(arguments["mode"]),
            schema=StructuredDataSchema(
                schema_name=str(arguments["schema"]["schema_name"]),
                fields=[
                    StructuredDataFieldSchema(
                        field_name=str(field_payload["field_name"]),
                        field_type=StructuredDataFieldType(field_payload.get("field_type", "string")),
                        source_name=field_payload.get("source_name"),
                        aliases=tuple(field_payload.get("aliases", [])),
                        selector=self._build_selector(field_payload.get("selector")),
                        required=bool(field_payload.get("required", False)),
                        column_index=field_payload.get("column_index"),
                    )
                    for field_payload in arguments["schema"]["fields"]
                ],
            ),
            table_selector=self._build_selector(arguments.get("table_selector")),
            form_selector=self._build_selector(arguments.get("form_selector")),
            text_block_selector=self._build_selector(arguments.get("text_block_selector")),
            pagination=self._build_pagination(arguments.get("pagination")),
            ocr_language=str(arguments.get("ocr_language", "eng")),
            minimum_ocr_confidence=float(arguments.get("minimum_ocr_confidence", 0.0)),
            has_header_row=bool(arguments.get("has_header_row", True)),
            max_rows_per_page=arguments.get("max_rows_per_page"),
            row_merge_tolerance=int(arguments.get("row_merge_tolerance", 12)),
        )
        return self._serialize(self._data_extractor.extract(configuration))

    def _handle_navigate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._navigator is None:
            raise ValueError("Navigation sequencer is not configured.")
        steps = [self._build_navigation_step(step_payload) for step_payload in arguments["steps"]]
        return self._serialize(self._navigator.run(steps, mode=NavigationSequenceMode(arguments.get("mode", "strict"))))

    def _build_navigation_step(self, payload: dict[str, Any]) -> NavigationStep:
        input_data = dict(payload.get("input_data", {}))
        if "position" in input_data:
            input_data["position"] = self._tuple_or_none(input_data["position"])
        if "element_bounds" in input_data:
            input_data["element_bounds"] = self._tuple_or_none(input_data["element_bounds"])
        if "launch_request" in input_data and input_data["launch_request"] is not None:
            input_data["launch_request"] = self._build_launch_request(input_data["launch_request"])
        return NavigationStep(
            step_id=str(payload["step_id"]),
            action_type=NavigationStepActionType(payload["action_type"]),
            target_description=str(payload["target_description"]),
            input_data=input_data,
            preconditions=[self._build_verification_check(item) for item in payload.get("preconditions", [])],
            expected_post_action_state=[self._build_verification_check(item) for item in payload.get("expected_post_action_state", [])],
            timeout_seconds=float(payload.get("timeout_seconds", 5.0)),
            optional=bool(payload.get("optional", False)),
        )

    def _build_launch_request(self, payload: dict[str, Any]) -> ApplicationLaunchRequest:
        return ApplicationLaunchRequest(
            application_name=str(payload["application_name"]),
            launch_mode=ApplicationLaunchMode(payload["launch_mode"]),
            executable_path=payload.get("executable_path"),
            start_menu_query=payload.get("start_menu_query"),
            url=payload.get("url"),
            arguments=tuple(payload.get("arguments", [])),
            url_parameters=dict(payload.get("url_parameters", {})),
            timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
            retry_attempts=int(payload.get("retry_attempts", 1)),
            retry_delay_seconds=float(payload.get("retry_delay_seconds", 1.0)),
            escalate_on_failure=bool(payload.get("escalate_on_failure", True)),
        )

    def _build_verification_check(self, payload: dict[str, Any]) -> ScreenVerificationCheck:
        return ScreenVerificationCheck(
            check_id=str(payload["check_id"]),
            check_type=ScreenCheckType(payload["check_type"]),
            timeout_seconds=float(payload.get("timeout_seconds", 3.0)),
            polling_interval_seconds=float(payload.get("polling_interval_seconds", 0.25)),
            target_text=payload.get("target_text"),
            template_name=payload.get("template_name"),
            template_path=payload.get("template_path"),
            threshold=float(payload.get("threshold", 0.8)),
            window_title=payload.get("window_title"),
            process_name=payload.get("process_name"),
            element_name=payload.get("element_name"),
            element_role=payload.get("element_role"),
            expected_value=payload.get("expected_value"),
            region_of_interest=self._tuple_or_none(payload.get("region_of_interest")),
        )

    def _build_input_target(self, arguments: dict[str, Any]) -> InputTarget | None:
        bounds = self._tuple_or_none(arguments.get("target_bounds"))
        window_title = arguments.get("window_title")
        window_handle = arguments.get("window_handle")
        monitor_id = arguments.get("monitor_id")
        if bounds is None and window_title is None and window_handle is None and monitor_id is None:
            return None
        return InputTarget(
            window=WindowReference(title=window_title, handle=window_handle)
            if window_title is not None or window_handle is not None
            else None,
            element_bounds=bounds,
            monitor_id=monitor_id,
        )

    def _build_selector(self, payload: dict[str, Any] | None) -> AIInterfaceElementSelector | None:
        if payload is None:
            return None
        if payload.get("strategies") is not None:
            strategies = tuple(SelectorStrategy(item) for item in payload["strategies"])
        else:
            strategies = (SelectorStrategy.ACCESSIBILITY, SelectorStrategy.OCR, SelectorStrategy.TEMPLATE_MATCH)
        return AIInterfaceElementSelector(
            name=payload.get("name"),
            role=payload.get("role"),
            value=payload.get("value"),
            target_text=payload.get("target_text"),
            template_name=payload.get("template_name"),
            template_path=payload.get("template_path"),
            bounds=self._tuple_or_none(payload.get("bounds")),
            region_of_interest=self._tuple_or_none(payload.get("region_of_interest")),
            window_title=payload.get("window_title"),
            process_name=payload.get("process_name"),
            strategies=strategies,
            threshold=float(payload.get("threshold", 0.8)),
            required=bool(payload.get("required", True)),
        )

    def _build_pagination(self, payload: dict[str, Any] | None) -> PaginationConfiguration | None:
        if payload is None:
            return None
        return PaginationConfiguration(
            next_page_selector=self._build_selector(payload["next_page_selector"]),
            disabled_selector=self._build_selector(payload.get("disabled_selector")),
            max_pages=int(payload.get("max_pages", 1)),
            advance_mode=PaginationAdvanceMode(payload.get("advance_mode", "click")),
            advance_hotkey=tuple(payload.get("advance_hotkey", ["pagedown"])),
        )

    def _enforce_allowlist(self, tool: MCPToolDefinition, arguments: dict[str, Any]) -> Any | None:
        if self._allowlist_enforcer is None:
            return None
        return self._allowlist_enforcer.evaluate(
            AllowlistCheckRequest(
                workflow_id=self._workflow_id,
                step_name=f"{self._step_name_prefix}:{tool.name}",
                action_type=tool.allowlist_action_type,
                application_name=arguments.get("application_name") or arguments.get("window_title") or arguments.get("application"),
                url=arguments.get("url"),
                file_path=arguments.get("path"),
                context_data={"tool_name": tool.name},
            )
        )

    def _coerce_request(self, request: MCPToolCallRequest | dict[str, Any]) -> MCPToolCallRequest:
        if isinstance(request, MCPToolCallRequest):
            return request
        tool_name = request.get("tool_name") or request.get("name") or request.get("tool")
        if not tool_name:
            raise ValueError("MCP tool call request must include a tool name.")
        arguments = request.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("MCP tool call arguments must be an object.")
        return MCPToolCallRequest(tool_name=str(tool_name), arguments=arguments, call_id=request.get("call_id"))

    def _validate_arguments(self, schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
        required = schema.get("required", [])
        missing = [name for name in required if name not in arguments]
        if missing:
            return f"Missing required argument(s): {', '.join(missing)}."
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            unexpected = sorted(key for key in arguments if key not in allowed)
            if unexpected:
                return f"Unexpected argument(s): {', '.join(unexpected)}."
        return None

    def _success_response(self, tool_name: str, payload: dict[str, Any], *, call_id: str | None) -> dict[str, Any]:
        response = {
            "tool": tool_name,
            "isError": False,
            "structuredContent": payload,
            "content": [{"type": "text", "text": tool_name}],
        }
        if call_id is not None:
            response["call_id"] = call_id
        return response

    def _error_response(self, tool_name: str, message: str, *, call_id: str | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = {
            "tool": tool_name,
            "isError": True,
            "structuredContent": {"succeeded": False, "reason": message, **(payload or {})},
            "content": [{"type": "text", "text": message}],
        }
        if call_id is not None:
            response["call_id"] = call_id
        return response

    def _serialize(self, value: Any) -> Any:
        if is_dataclass(value):
            return {item.name: self._serialize(getattr(value, item.name)) for item in fields(value)}
        if isinstance(value, Enum):
            return value.value
        if value is None or isinstance(value, (str, bytes, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if hasattr(value, "isoformat") and callable(value.isoformat):
            try:
                return value.isoformat()
            except TypeError:
                pass
        if isinstance(value, dict):
            return {str(key): self._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serialize(item) for item in value]
        if hasattr(value, "__dict__"):
            public_items = {str(key): self._serialize(item) for key, item in vars(value).items() if not key.startswith("_")}
            if public_items:
                return public_items
        public_attributes: dict[str, Any] = {}
        for attribute_name in dir(value):
            if attribute_name.startswith("_"):
                continue
            try:
                attribute_value = getattr(value, attribute_name)
            except Exception:
                continue
            if callable(attribute_value):
                continue
            public_attributes[attribute_name] = self._serialize(attribute_value)
        if public_attributes:
            return public_attributes
        return value

    def _tuple_or_none(self, value: Any) -> tuple[Any, ...] | None:
        if value is None:
            return None
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        raise ValueError("Expected a list or tuple for coordinate values.")

    def _build_take_screenshot_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="take_screenshot",
            description="Capture a desktop screenshot and return the saved path.",
            allowlist_action_type="take_screenshot",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "monitor_id": {"type": "string"}},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "succeeded": {"type": "boolean"},
                    "screenshot_path": {"type": "string"},
                    "monitor_id": {"type": ["string", "null"]},
                },
                "required": ["succeeded", "screenshot_path"],
                "additionalProperties": True,
            },
        )

    def _build_find_element_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="find_element",
            description="Capture or inspect the current desktop state and locate a matching UI element.",
            allowlist_action_type="find_element",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "template_name": {"type": "string"},
                    "element_type": {"type": "string"},
                    "monitor_id": {"type": "string"},
                    "confidence_threshold": {"type": "number"},
                    "screenshot_path": {"type": "string"},
                    "template_paths": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": "object"},
                    "application_name": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "succeeded": {"type": "boolean"},
                    "desktop_state": {"type": "object"},
                    "result": {"type": "object"},
                    "reason": {"type": ["string", "null"]},
                },
                "required": ["succeeded", "result"],
                "additionalProperties": True,
            },
        )

    def _build_click_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="click",
            description="Click a screen position or target element through the automation input runner.",
            allowlist_action_type="click",
            input_schema={
                "type": "object",
                "properties": {
                    "position": _point_schema(),
                    "target_bounds": _bounds_schema(),
                    "window_title": {"type": "string"},
                    "window_handle": {"type": "integer"},
                    "monitor_id": {"type": "string"},
                    "button": _schema_enum("left", "right", "middle"),
                    "context_tags": {"type": "array", "items": {"type": "string"}},
                    "application_name": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "succeeded": {"type": "boolean"},
                    "logs": {"type": "array"},
                    "failure_reason": {"type": ["string", "null"]},
                },
                "required": ["succeeded", "logs"],
                "additionalProperties": True,
            },
        )

    def _build_type_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="type",
            description="Type text into the focused target through the automation input runner.",
            allowlist_action_type="type_text",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_bounds": _bounds_schema(),
                    "window_title": {"type": "string"},
                    "window_handle": {"type": "integer"},
                    "monitor_id": {"type": "string"},
                    "context_tags": {"type": "array", "items": {"type": "string"}},
                    "application_name": {"type": "string"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "succeeded": {"type": "boolean"},
                    "logs": {"type": "array"},
                    "failure_reason": {"type": ["string", "null"]},
                },
                "required": ["succeeded", "logs"],
                "additionalProperties": True,
            },
        )

    def _build_switch_account_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="switch_account",
            description="Switch the automation session to a different account or profile.",
            allowlist_action_type="switch_account",
            input_schema={
                "type": "object",
                "properties": {
                    "account_name": {"type": "string"},
                    "mode": _schema_enum("sequential", "parallel"),
                    "task_id": {"type": "string"},
                    "payload": {"type": "object"},
                    "minimum_reuse_interval_seconds": {"type": "number"},
                    "unhealthy_threshold": {"type": "number"},
                    "application_name": {"type": "string"},
                },
                "required": ["account_name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"succeeded": {"type": "boolean"}, "reason": {"type": ["string", "null"]}},
                "required": ["succeeded"],
                "additionalProperties": True,
            },
        )

    def _build_read_data_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="read_data",
            description="Extract structured data from the current application using accessibility and OCR modules.",
            allowlist_action_type="read_data",
            input_schema={
                "type": "object",
                "properties": {
                    "mode": _schema_enum("table", "form", "text_block"),
                    "schema": {
                        "type": "object",
                        "properties": {
                            "schema_name": {"type": "string"},
                            "fields": {"type": "array", "items": _field_schema_definition()},
                        },
                        "required": ["schema_name", "fields"],
                        "additionalProperties": False,
                    },
                    "table_selector": _selector_schema(),
                    "form_selector": _selector_schema(),
                    "text_block_selector": _selector_schema(),
                    "pagination": {
                        "type": "object",
                        "properties": {
                            "next_page_selector": _selector_schema(),
                            "disabled_selector": _selector_schema(),
                            "max_pages": {"type": "integer"},
                            "advance_mode": _schema_enum("click", "hotkey"),
                            "advance_hotkey": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["next_page_selector"],
                        "additionalProperties": False,
                    },
                    "ocr_language": {"type": "string"},
                    "minimum_ocr_confidence": {"type": "number"},
                    "has_header_row": {"type": "boolean"},
                    "max_rows_per_page": {"type": "integer"},
                    "row_merge_tolerance": {"type": "integer"},
                    "application_name": {"type": "string"},
                },
                "required": ["mode", "schema"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "succeeded": {"type": "boolean"},
                    "records": {"type": "array"},
                    "page_results": {"type": "array"},
                    "reason": {"type": ["string", "null"]},
                },
                "required": ["succeeded", "records", "page_results"],
                "additionalProperties": True,
            },
        )

    def _build_navigate_tool(self) -> MCPToolDefinition:
        return MCPToolDefinition(
            name="navigate",
            description="Execute a navigation sequence through the automation navigator.",
            allowlist_action_type="navigate",
            input_schema={
                "type": "object",
                "properties": {
                    "mode": _schema_enum("strict", "lenient"),
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step_id": {"type": "string"},
                                "action_type": _schema_enum("click", "type", "navigate", "scroll", "wait", "verify"),
                                "target_description": {"type": "string"},
                                "input_data": {"type": "object"},
                                "preconditions": {"type": "array", "items": _verification_check_schema()},
                                "expected_post_action_state": {"type": "array", "items": _verification_check_schema()},
                                "timeout_seconds": {"type": "number"},
                                "optional": {"type": "boolean"},
                            },
                            "required": ["step_id", "action_type", "target_description"],
                            "additionalProperties": False,
                        },
                    },
                    "application_name": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "succeeded": {"type": "boolean"},
                    "mode": _schema_enum("strict", "lenient"),
                    "outcomes": {"type": "array"},
                    "failed_step_id": {"type": ["string", "null"]},
                    "reason": {"type": ["string", "null"]},
                },
                "required": ["succeeded", "mode", "outcomes"],
                "additionalProperties": True,
            },
        )
