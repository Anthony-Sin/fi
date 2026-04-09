from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from desktop_automation_perception.models import AutomationTask, RateLimitRequest, TaskPriority


@dataclass(frozen=True, slots=True)
class TriggerAuthenticationConfiguration:
    shared_secret: str | None = None
    api_keys: tuple[str, ...] = ()
    shared_secret_header: str = "x-webhook-secret"
    api_key_header: str = "x-api-key"


class ExternalWorkflowTriggerReceiver:
    def __init__(
        self,
        *,
        task_queue_manager: object,
        audit_logger: object | None = None,
        rate_limiter: object | None = None,
        authentication: TriggerAuthenticationConfiguration | None = None,
        workflow_id: str = "external_trigger_receiver",
        enqueue_module_name: str = "workflow_execution",
    ):
        self._task_queue_manager = task_queue_manager
        self._audit_logger = audit_logger
        self._rate_limiter = rate_limiter
        self._authentication = authentication or TriggerAuthenticationConfiguration()
        self._workflow_id = workflow_id
        self._enqueue_module_name = enqueue_module_name

    def receive_http_webhook(
        self,
        *,
        method: str,
        headers: dict[str, str] | None,
        body: str | bytes,
        remote_address: str | None = None,
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        normalized_headers = self._normalize_headers(headers or {})
        caller_id = remote_address or "anonymous"
        payload: dict[str, Any] | None = None
        payload_summary: dict[str, Any] = {"source": caller_id}
        workflow_id = self._workflow_id
        response: dict[str, Any]

        try:
            if method.strip().upper() != "POST":
                response = self._response(405, accepted=False, reason="Webhook receiver only accepts POST requests.")
                return response

            payload = self._parse_body(body)
            caller_id = self._resolve_caller_id(payload, normalized_headers, remote_address)
            workflow_id = self._resolve_workflow_id(payload)
            payload_summary = self._payload_summary(payload, caller_id=caller_id)

            auth_error = self._authenticate(normalized_headers)
            if auth_error is not None:
                response = self._response(401, accepted=False, reason=auth_error)
                return response

            schema_error = self._validate_payload(payload)
            if schema_error is not None:
                response = self._response(400, accepted=False, reason=schema_error)
                return response

            execution_id = str(uuid4())
            rate_limit_error = self._check_rate_limit(
                caller_id=caller_id,
                execution_id=execution_id,
                workflow_id=workflow_id,
                payload_summary=payload_summary,
            )
            if rate_limit_error is not None:
                response = self._response(429, accepted=False, execution_id=execution_id, reason=rate_limit_error)
                return response

            task = self._build_task(payload, execution_id=execution_id)
            enqueue_result = self._task_queue_manager.enqueue(task)
            if not getattr(enqueue_result, "succeeded", False):
                response = self._response(
                    503,
                    accepted=False,
                    execution_id=execution_id,
                    reason=getattr(enqueue_result, "reason", "Failed to enqueue workflow execution request."),
                )
                return response

            response = self._response(
                202,
                accepted=True,
                execution_id=execution_id,
                reason=None,
                extra={
                    "workflow_id": workflow_id,
                    "caller_id": caller_id,
                    "queued_task_id": task.task_id,
                },
            )
            return response
        except ValueError as exc:
            response = self._response(400, accepted=False, reason=str(exc))
            return response
        finally:
            self._log_received_trigger(
                workflow_id=workflow_id,
                caller_id=caller_id,
                payload_summary=payload_summary,
                response=response if "response" in locals() else self._response(500, accepted=False, reason="Unhandled trigger receiver failure."),
                started_at=started_at,
            )

    def _parse_body(self, body: str | bytes) -> dict[str, Any]:
        raw = body.decode("utf-8") if isinstance(body, bytes) else body
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Webhook body must be valid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Webhook payload must be a JSON object.")
        return payload

    def _normalize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {str(key).strip().casefold(): str(value) for key, value in headers.items()}

    def _authenticate(self, headers: dict[str, str]) -> str | None:
        configured_secret = self._authentication.shared_secret
        configured_api_keys = {item for item in self._authentication.api_keys if item}
        if not configured_secret and not configured_api_keys:
            return None

        if configured_secret:
            provided_secret = headers.get(self._authentication.shared_secret_header.casefold()) or headers.get("x-shared-secret")
            if provided_secret == configured_secret:
                return None

        if configured_api_keys:
            provided_api_key = headers.get(self._authentication.api_key_header.casefold())
            authorization = headers.get("authorization", "")
            if provided_api_key in configured_api_keys:
                return None
            if authorization.lower().startswith("bearer ") and authorization[7:] in configured_api_keys:
                return None

        return "Webhook authentication failed."

    def _validate_payload(self, payload: dict[str, Any]) -> str | None:
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            return "Webhook payload must include a workflow object."
        workflow_id = workflow.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            return "workflow.workflow_id must be a non-empty string."
        version = workflow.get("workflow_version_number")
        if version is not None and not isinstance(version, int):
            return "workflow.workflow_version_number must be an integer when provided."
        specification = workflow.get("specification")
        if specification is not None and not isinstance(specification, dict):
            return "workflow.specification must be an object when provided."
        parameters = payload.get("parameters", workflow.get("parameters", {}))
        if not isinstance(parameters, dict):
            return "parameters must be an object when provided."
        source = payload.get("source")
        if source is not None and not isinstance(source, dict):
            return "source must be an object when provided."
        priority = payload.get("priority", "medium")
        if str(priority).casefold() not in {"low", "medium", "high", "critical"}:
            return "priority must be one of: low, medium, high, critical."
        return None

    def _resolve_caller_id(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        remote_address: str | None,
    ) -> str:
        source = payload.get("source", {})
        if isinstance(source, dict):
            for key in ("caller_id", "system", "name"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        header_value = headers.get("x-caller-id") or headers.get("x-source-system")
        if header_value:
            return header_value
        if remote_address:
            return remote_address
        return "anonymous"

    def _resolve_workflow_id(self, payload: dict[str, Any]) -> str:
        workflow = payload.get("workflow", {})
        if isinstance(workflow, dict):
            value = workflow.get("workflow_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return self._workflow_id

    def _payload_summary(self, payload: dict[str, Any], *, caller_id: str) -> dict[str, Any]:
        workflow = payload.get("workflow", {})
        parameters = payload.get("parameters", workflow.get("parameters", {})) if isinstance(workflow, dict) else {}
        specification = workflow.get("specification", {}) if isinstance(workflow, dict) else {}
        source = payload.get("source", {})
        return {
            "caller_id": caller_id,
            "workflow_id": workflow.get("workflow_id") if isinstance(workflow, dict) else None,
            "workflow_version_number": workflow.get("workflow_version_number") if isinstance(workflow, dict) else None,
            "workflow_name": workflow.get("workflow_name") if isinstance(workflow, dict) else None,
            "parameter_count": len(parameters) if isinstance(parameters, dict) else 0,
            "parameter_keys": sorted(parameters.keys()) if isinstance(parameters, dict) else [],
            "specification_keys": sorted(specification.keys()) if isinstance(specification, dict) else [],
            "source": source if isinstance(source, dict) else {},
        }

    def _check_rate_limit(
        self,
        *,
        caller_id: str,
        execution_id: str,
        workflow_id: str,
        payload_summary: dict[str, Any],
    ) -> str | None:
        if self._rate_limiter is None:
            return None
        result = self._rate_limiter.submit_request(
            RateLimitRequest(
                request_id=execution_id,
                account_name=caller_id,
                application_name="external_webhook",
                action_type="external_workflow_trigger",
                payload={"workflow_id": workflow_id, **payload_summary},
            )
        )
        if getattr(result, "allowed", False):
            return None
        return getattr(result, "reason", "Webhook request exceeded the configured rate limit.")

    def _build_task(self, payload: dict[str, Any], *, execution_id: str) -> AutomationTask:
        workflow = payload["workflow"]
        parameters = payload.get("parameters", workflow.get("parameters", {}))
        priority = TaskPriority(str(payload.get("priority", "medium")).casefold())
        return AutomationTask(
            task_id=execution_id,
            priority=priority,
            required_module=self._enqueue_module_name,
            required_account=payload.get("required_account"),
            required_account_type=payload.get("required_account_type"),
            required_application=payload.get("required_application"),
            input_payload={
                "execution_id": execution_id,
                "trigger_type": "external_webhook",
                "workflow": {
                    "workflow_id": workflow["workflow_id"],
                    "workflow_version_number": workflow.get("workflow_version_number"),
                    "workflow_name": workflow.get("workflow_name"),
                    "specification": dict(workflow.get("specification", {})),
                },
                "parameters": dict(parameters),
                "source": dict(payload.get("source", {})),
                "metadata": dict(payload.get("metadata", {})),
            },
        )

    def _response(
        self,
        status_code: int,
        *,
        accepted: bool,
        execution_id: str | None = None,
        reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {"accepted": accepted, "execution_id": execution_id, "reason": reason}
        if extra:
            body.update(extra)
        return {"status_code": status_code, "body": body}

    def _log_received_trigger(
        self,
        *,
        workflow_id: str,
        caller_id: str,
        payload_summary: dict[str, Any],
        response: dict[str, Any],
        started_at: datetime,
    ) -> None:
        if self._audit_logger is None:
            return
        finished_at = datetime.now(timezone.utc)
        self._audit_logger.log_action(
            workflow_id=workflow_id,
            step_name="webhook_trigger_receiver",
            action_type="receive_trigger",
            target_element=caller_id,
            input_data=payload_summary,
            output_data=response["body"],
            duration_seconds=(finished_at - started_at).total_seconds(),
            success=bool(response["body"].get("accepted", False)),
            timestamp=started_at.replace(tzinfo=None) if started_at.tzinfo is not None else started_at,
        )
