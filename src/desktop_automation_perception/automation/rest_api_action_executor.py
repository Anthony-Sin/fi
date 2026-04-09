from __future__ import annotations

from desktop_automation_perception._time import utc_now

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from desktop_automation_perception.models import (
    APIAuthType,
    AllowlistCheckRequest,
    RESTAPICallLog,
    RESTAPIExecutorResult,
    RESTAPIMethod,
    RESTAPIRequest,
    RESTAPIResponse,
    ResponseValidationMode,
    ResponseValidationResult,
    RetryConfiguration,
    RetryExceptionRule,
    RetryFailureResult,
    RetryDisposition,
)
from desktop_automation_perception.resilience.retry_engine import (
    ExponentialBackoffRetryEngine,
    RetryExhaustedError,
)


class TransientAPIError(Exception):
    pass


@dataclass(slots=True)
class RequestsRESTAPIBackend:
    def request(
        self,
        *,
        method: str,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> RESTAPIResponse:
        import requests

        response = requests.request(
            method=method,
            url=endpoint,
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
        body_text = response.text
        parsed_body: object | None
        try:
            parsed_body = response.json()
        except ValueError:
            parsed_body = None
        return RESTAPIResponse(
            status_code=int(response.status_code),
            headers=dict(response.headers),
            body_text=body_text,
            parsed_body=parsed_body,
        )


@dataclass(slots=True)
class RESTAPIActionExecutor:
    backend: object | None = None
    retry_engine: ExponentialBackoffRetryEngine[RESTAPIResponse] | None = None
    retry_configuration: RetryConfiguration | None = None
    audit_logger: object | None = None
    allowlist_enforcer: object | None = None
    log_storage_path: str | None = None
    monotonic_fn: Callable[[], float] = monotonic
    now_fn: Callable[[], datetime] = utc_now

    def execute(
        self,
        request: RESTAPIRequest,
        *,
        workflow_id: str | None = None,
        step_name: str = "rest_api_action",
    ) -> RESTAPIExecutorResult:
        allowlist_result = self._check_allowlist(request=request, workflow_id=workflow_id, step_name=step_name)
        if allowlist_result is not None:
            return allowlist_result
        started_at = self.monotonic_fn()

        try:
            response = self._run_with_retry(lambda: self._perform_request(request))
            latency_seconds = self.monotonic_fn() - started_at
            validation = self._validate_response(response, request.expected_schema)
            success = 200 <= response.status_code < 300 and validation.succeeded
            reason = None
            if not (200 <= response.status_code < 300):
                reason = f"API returned non-success status code {response.status_code}."
            elif not validation.succeeded:
                reason = validation.reason

            log_entry = RESTAPICallLog(
                timestamp=self.now_fn(),
                endpoint=request.endpoint,
                method=request.method,
                status_code=response.status_code,
                latency_seconds=latency_seconds,
                success=success,
                detail=reason,
            )
            self._log_call(request=request, response=response, log_entry=log_entry, workflow_id=workflow_id, step_name=step_name)
            return RESTAPIExecutorResult(
                succeeded=success,
                request=request,
                response=response,
                validation=validation,
                log_entry=log_entry,
                reason=reason,
            )
        except RetryExhaustedError as exc:
            latency_seconds = self.monotonic_fn() - started_at
            log_entry = RESTAPICallLog(
                timestamp=self.now_fn(),
                endpoint=request.endpoint,
                method=request.method,
                status_code=None,
                latency_seconds=latency_seconds,
                success=False,
                detail=exc.failure.reason,
            )
            self._log_call(request=request, response=None, log_entry=log_entry, workflow_id=workflow_id, step_name=step_name)
            return RESTAPIExecutorResult(
                succeeded=False,
                request=request,
                log_entry=log_entry,
                retry_failure=exc.failure,
                reason=exc.failure.reason,
            )
        except Exception as exc:
            latency_seconds = self.monotonic_fn() - started_at
            log_entry = RESTAPICallLog(
                timestamp=self.now_fn(),
                endpoint=request.endpoint,
                method=request.method,
                status_code=None,
                latency_seconds=latency_seconds,
                success=False,
                detail=str(exc),
            )
            self._log_call(request=request, response=None, log_entry=log_entry, workflow_id=workflow_id, step_name=step_name)
            return RESTAPIExecutorResult(
                succeeded=False,
                request=request,
                log_entry=log_entry,
                reason=str(exc),
            )

    def _check_allowlist(
        self,
        *,
        request: RESTAPIRequest,
        workflow_id: str | None,
        step_name: str,
    ) -> RESTAPIExecutorResult | None:
        if self.allowlist_enforcer is None:
            return None
        decision = self.allowlist_enforcer.evaluate(
            AllowlistCheckRequest(
                workflow_id=workflow_id,
                step_name=step_name,
                action_type="rest_api_call",
                url=request.endpoint,
                context_data={"method": request.method.value},
            )
        )
        if decision.allowed:
            return None
        log_entry = RESTAPICallLog(
            timestamp=self.now_fn(),
            endpoint=request.endpoint,
            method=request.method,
            status_code=None,
            latency_seconds=0.0,
            success=False,
            detail=decision.reason,
        )
        self._log_call(request=request, response=None, log_entry=log_entry, workflow_id=workflow_id, step_name=step_name)
        return RESTAPIExecutorResult(
            succeeded=False,
            request=request,
            log_entry=log_entry,
            reason=decision.reason,
        )

    def list_logs(self) -> list[RESTAPICallLog]:
        if self.log_storage_path is None:
            return []
        path = Path(self.log_storage_path)
        if not path.exists():
            return []
        entries: list[RESTAPICallLog] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = line.strip()
                if not payload:
                    continue
                raw = json.loads(payload)
                entries.append(
                    RESTAPICallLog(
                        timestamp=datetime.fromisoformat(raw["timestamp"]),
                        endpoint=raw["endpoint"],
                        method=RESTAPIMethod(raw["method"]),
                        status_code=None if raw.get("status_code") is None else int(raw["status_code"]),
                        latency_seconds=float(raw.get("latency_seconds", 0.0)),
                        success=bool(raw.get("success", False)),
                        detail=raw.get("detail"),
                    )
                )
        return entries

    def _perform_request(self, request: RESTAPIRequest) -> RESTAPIResponse:
        headers = self._build_headers(request)
        response = self._backend().request(
            method=request.method.value,
            endpoint=request.endpoint,
            headers=headers,
            payload=None if request.method is RESTAPIMethod.GET else request.payload,
            timeout_seconds=request.timeout_seconds,
        )
        if response.status_code >= 500:
            raise TransientAPIError(f"Transient API server error: {response.status_code}")
        return response

    def _build_headers(self, request: RESTAPIRequest) -> dict[str, str]:
        headers = dict(request.headers)
        if request.auth_type is APIAuthType.BEARER and request.auth_value is not None:
            headers["Authorization"] = f"Bearer {request.auth_value}"
        elif request.auth_type is APIAuthType.API_KEY and request.auth_value is not None:
            headers[request.api_key_header_name] = request.auth_value
        if request.payload is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        return headers

    def _validate_response(
        self,
        response: RESTAPIResponse,
        expected_schema: dict[str, str],
    ) -> ResponseValidationResult:
        if not expected_schema:
            return ResponseValidationResult(
                succeeded=True,
                mode=ResponseValidationMode.NONE,
                parsed_payload=response.parsed_body,
            )
        payload = response.parsed_body
        if not isinstance(payload, dict):
            return ResponseValidationResult(
                succeeded=False,
                mode=ResponseValidationMode.JSON_SCHEMA_LITE,
                reason="Expected a JSON object response for schema validation.",
                parsed_payload=payload,
            )
        for key, expected_type in expected_schema.items():
            if key not in payload:
                return ResponseValidationResult(
                    succeeded=False,
                    mode=ResponseValidationMode.JSON_SCHEMA_LITE,
                    reason=f"Missing required response field {key!r}.",
                    parsed_payload=payload,
                )
            if not self._matches_type(payload[key], expected_type):
                return ResponseValidationResult(
                    succeeded=False,
                    mode=ResponseValidationMode.JSON_SCHEMA_LITE,
                    reason=f"Response field {key!r} does not match expected type {expected_type!r}.",
                    parsed_payload=payload,
                )
        return ResponseValidationResult(
            succeeded=True,
            mode=ResponseValidationMode.JSON_SCHEMA_LITE,
            parsed_payload=payload,
        )

    def _matches_type(self, value: Any, expected_type: str) -> bool:
        return {
            "str": isinstance(value, str),
            "int": isinstance(value, int) and not isinstance(value, bool),
            "float": isinstance(value, (int, float)) and not isinstance(value, bool),
            "bool": isinstance(value, bool),
            "dict": isinstance(value, dict),
            "list": isinstance(value, list),
        }.get(expected_type, True)

    def _run_with_retry(self, action: Callable[[], RESTAPIResponse]) -> RESTAPIResponse:
        engine = self.retry_engine or ExponentialBackoffRetryEngine[RESTAPIResponse]()
        configuration = self.retry_configuration or RetryConfiguration(
            max_retry_count=2,
            initial_delay_seconds=0.5,
            backoff_multiplier=2.0,
            max_delay_seconds=4.0,
            exception_rules=[
                RetryExceptionRule(exception_type_name="TransientAPIError", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="Timeout", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="TimeoutError", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="ConnectionError", disposition=RetryDisposition.RETRY),
            ],
        )
        return engine.run(action, configuration=configuration)

    def _backend(self):
        return self.backend or RequestsRESTAPIBackend()

    def _log_call(
        self,
        *,
        request: RESTAPIRequest,
        response: RESTAPIResponse | None,
        log_entry: RESTAPICallLog,
        workflow_id: str | None,
        step_name: str,
    ) -> None:
        if self.audit_logger is not None and workflow_id is not None:
            self.audit_logger.log_action(
                workflow_id=workflow_id,
                step_name=step_name,
                action_type="rest_api_call",
                target_element=request.endpoint,
                input_data={
                    "method": request.method.value,
                    "headers": self._sanitize_headers(self._build_headers(request)),
                    "payload": request.payload,
                },
                output_data={
                    "status_code": None if response is None else response.status_code,
                    "latency_seconds": log_entry.latency_seconds,
                    "detail": log_entry.detail,
                },
                duration_seconds=log_entry.latency_seconds,
                success=log_entry.success,
                timestamp=log_entry.timestamp,
            )
        if self.log_storage_path is not None:
            path = Path(self.log_storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": log_entry.timestamp.isoformat(),
                "endpoint": log_entry.endpoint,
                "method": log_entry.method.value,
                "status_code": log_entry.status_code,
                "latency_seconds": log_entry.latency_seconds,
                "success": log_entry.success,
                "detail": log_entry.detail,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True))
                handle.write("\n")

    def _sanitize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            if any(token in key.casefold() for token in ("authorization", "api-key", "api_key", "token")):
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = value
        return sanitized


