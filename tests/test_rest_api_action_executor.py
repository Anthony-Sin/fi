from datetime import datetime

from desktop_automation_agent.automation import RESTAPIActionExecutor
from desktop_automation_agent.allowlist_enforcer import ActionAllowlistEnforcer
from desktop_automation_agent.models import (
    APIAuthType,
    RESTAPIMethod,
    RESTAPIRequest,
    RESTAPIResponse,
    RetryConfiguration,
)


class TimeoutError(Exception):
    pass


class FakeBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *, method, endpoint, headers, payload, timeout_seconds):
        self.calls.append(
            {
                "method": method,
                "endpoint": endpoint,
                "headers": dict(headers),
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeAuditLogger:
    def __init__(self):
        self.entries = []

    def log_action(self, **kwargs):
        self.entries.append(kwargs)
        return type("Result", (), {"succeeded": True})()


def test_rest_api_executor_applies_bearer_auth_parses_json_and_validates_schema(tmp_path):
    backend = FakeBackend(
        [
            RESTAPIResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body_text='{"status":"ok","count":2}',
                parsed_body={"status": "ok", "count": 2},
            )
        ]
    )
    audit_logger = FakeAuditLogger()
    executor = RESTAPIActionExecutor(
        backend=backend,
        audit_logger=audit_logger,
        log_storage_path=str(tmp_path / "api.log"),
        monotonic_fn=lambda: 10.0,
        now_fn=lambda: datetime(2026, 4, 8, 12, 0, 0),
    )

    result = executor.execute(
        RESTAPIRequest(
            endpoint="https://api.example.com/items",
            method=RESTAPIMethod.POST,
            headers={"Accept": "application/json"},
            payload={"name": "widget"},
            auth_type=APIAuthType.BEARER,
            auth_value="secret-token",
            expected_schema={"status": "str", "count": "int"},
        ),
        workflow_id="wf-1",
    )

    assert result.succeeded is True
    assert result.response is not None and result.response.parsed_body == {"status": "ok", "count": 2}
    assert backend.calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert audit_logger.entries[0]["output_data"]["status_code"] == 200


def test_rest_api_executor_applies_api_key_header_and_logs_calls(tmp_path):
    backend = FakeBackend(
        [
            RESTAPIResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body_text='{"accepted":true}',
                parsed_body={"accepted": True},
            )
        ]
    )
    times = iter([1.0, 3.5])
    executor = RESTAPIActionExecutor(
        backend=backend,
        log_storage_path=str(tmp_path / "api_key.log"),
        monotonic_fn=lambda: next(times),
        now_fn=lambda: datetime(2026, 4, 8, 12, 0, 1),
    )

    result = executor.execute(
        RESTAPIRequest(
            endpoint="https://api.example.com/submit",
            method=RESTAPIMethod.POST,
            payload={"id": 7},
            auth_type=APIAuthType.API_KEY,
            auth_value="api-key-123",
            api_key_header_name="X-Service-Key",
        )
    )
    logs = executor.list_logs()

    assert result.succeeded is True
    assert backend.calls[0]["headers"]["X-Service-Key"] == "api-key-123"
    assert logs[0].status_code == 200
    assert logs[0].latency_seconds == 2.5


def test_rest_api_executor_retries_on_transient_5xx_and_succeeds():
    backend = FakeBackend(
        [
            RESTAPIResponse(status_code=503, parsed_body={"status": "retry"}),
            RESTAPIResponse(status_code=200, parsed_body={"status": "ok"}),
        ]
    )
    executor = RESTAPIActionExecutor(
        backend=backend,
        retry_configuration=RetryConfiguration(max_retry_count=1, initial_delay_seconds=0.0, max_delay_seconds=0.0),
    )

    result = executor.execute(
        RESTAPIRequest(
            endpoint="https://api.example.com/health",
            method=RESTAPIMethod.GET,
            expected_schema={"status": "str"},
        )
    )

    assert result.succeeded is True
    assert len(backend.calls) == 2
    assert result.response is not None and result.response.status_code == 200


def test_rest_api_executor_retries_on_timeout_and_returns_retry_failure():
    backend = FakeBackend([TimeoutError("Request timed out"), TimeoutError("Request timed out")])
    executor = RESTAPIActionExecutor(
        backend=backend,
        retry_configuration=RetryConfiguration(max_retry_count=1, initial_delay_seconds=0.0, max_delay_seconds=0.0),
    )

    result = executor.execute(
        RESTAPIRequest(
            endpoint="https://api.example.com/slow",
            method=RESTAPIMethod.GET,
        )
    )

    assert result.succeeded is False
    assert result.retry_failure is not None
    assert result.reason == "Retry attempts exhausted."


def test_rest_api_executor_fails_when_schema_validation_does_not_match():
    backend = FakeBackend(
        [
            RESTAPIResponse(
                status_code=200,
                parsed_body={"status": "ok", "count": "two"},
            )
        ]
    )
    executor = RESTAPIActionExecutor(backend=backend)

    result = executor.execute(
        RESTAPIRequest(
            endpoint="https://api.example.com/items",
            method=RESTAPIMethod.GET,
            expected_schema={"status": "str", "count": "int"},
        )
    )

    assert result.succeeded is False
    assert result.validation is not None
    assert result.validation.succeeded is False
    assert "count" in (result.reason or "")


def test_rest_api_executor_blocks_disallowed_endpoint(tmp_path):
    backend = FakeBackend([])
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text(
        '{"action_types":["rest_api_call"],"applications":["*"],"urls":["https://api.safe.example/*"],"file_paths":["C:/safe/*"]}',
        encoding="utf-8",
    )
    executor = RESTAPIActionExecutor(
        backend=backend,
        allowlist_enforcer=ActionAllowlistEnforcer(config_path=str(allowlist_path)),
    )

    result = executor.execute(
        RESTAPIRequest(
            endpoint="https://api.blocked.example/items",
            method=RESTAPIMethod.GET,
        ),
        workflow_id="wf-api",
    )

    assert result.succeeded is False
    assert backend.calls == []
    assert "allowlist" in (result.reason or "")
