import json
from pathlib import Path

from desktop_automation_perception.agents import TaskQueueManager
from desktop_automation_perception.automation import (
    ExternalWorkflowTriggerReceiver,
    TriggerAuthenticationConfiguration,
)
from desktop_automation_perception.models import RateLimitRule, RateLimitScope, RateLimitWindow
from desktop_automation_perception.rate_limiter import RateLimiter
from desktop_automation_perception.workflow_audit_logger import WorkflowAuditLogger


def make_payload(**overrides):
    payload = {
        "source": {"system": "crm", "caller_id": "crm-prod"},
        "workflow": {
            "workflow_id": "wf-onboarding",
            "workflow_version_number": 3,
            "workflow_name": "Customer Onboarding",
            "specification": {"entrypoint": "start", "steps": ["open", "submit"]},
        },
        "parameters": {"customer_id": "cust-123", "region": "us-east-1"},
        "priority": "high",
        "required_application": "crm-console",
    }
    payload.update(overrides)
    return payload


def test_webhook_receiver_accepts_valid_shared_secret_request_and_enqueues(tmp_path):
    queue = TaskQueueManager(storage_path=str(Path(tmp_path) / "queue.json"))
    audit = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))
    receiver = ExternalWorkflowTriggerReceiver(
        task_queue_manager=queue,
        audit_logger=audit,
        authentication=TriggerAuthenticationConfiguration(shared_secret="top-secret"),
    )

    response = receiver.receive_http_webhook(
        method="POST",
        headers={"X-Webhook-Secret": "top-secret"},
        body=json.dumps(make_payload()),
        remote_address="10.0.0.1",
    )

    queued = queue.peek()
    logs = audit.list_logs()

    assert response["status_code"] == 202
    assert response["body"]["accepted"] is True
    assert response["body"]["execution_id"] is not None
    assert queued.task is not None
    assert queued.task.task_id == response["body"]["execution_id"]
    assert queued.task.input_payload["workflow"]["workflow_id"] == "wf-onboarding"
    assert logs[-1].target_element == "crm-prod"
    assert logs[-1].input_data["parameter_count"] == 2


def test_webhook_receiver_accepts_api_key_authentication(tmp_path):
    queue = TaskQueueManager(storage_path=str(Path(tmp_path) / "queue.json"))
    receiver = ExternalWorkflowTriggerReceiver(
        task_queue_manager=queue,
        authentication=TriggerAuthenticationConfiguration(api_keys=("key-123",)),
    )

    response = receiver.receive_http_webhook(
        method="POST",
        headers={"Authorization": "Bearer key-123"},
        body=json.dumps(make_payload()),
    )

    assert response["status_code"] == 202
    assert response["body"]["accepted"] is True


def test_webhook_receiver_rejects_invalid_schema_and_logs_failure(tmp_path):
    queue = TaskQueueManager(storage_path=str(Path(tmp_path) / "queue.json"))
    audit = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "audit.jsonl"))
    receiver = ExternalWorkflowTriggerReceiver(
        task_queue_manager=queue,
        audit_logger=audit,
        authentication=TriggerAuthenticationConfiguration(shared_secret="top-secret"),
    )

    response = receiver.receive_http_webhook(
        method="POST",
        headers={"X-Webhook-Secret": "top-secret"},
        body=json.dumps({"workflow": {"workflow_id": ""}}),
        remote_address="10.0.0.2",
    )

    logs = audit.list_logs()

    assert response["status_code"] == 400
    assert response["body"]["accepted"] is False
    assert "workflow.workflow_id" in (response["body"]["reason"] or "")
    assert logs[-1].success is False


def test_webhook_receiver_enforces_rate_limits_per_caller(tmp_path):
    queue = TaskQueueManager(storage_path=str(Path(tmp_path) / "queue.json"))
    limiter = RateLimiter(
        storage_path=str(Path(tmp_path) / "rate.json"),
        rules=[
            RateLimitRule(
                scope=RateLimitScope.ACCOUNT,
                key="crm-prod",
                limit=1,
                window=RateLimitWindow.MINUTE,
            )
        ],
    )
    receiver = ExternalWorkflowTriggerReceiver(
        task_queue_manager=queue,
        rate_limiter=limiter,
        authentication=TriggerAuthenticationConfiguration(shared_secret="top-secret"),
    )

    first = receiver.receive_http_webhook(
        method="POST",
        headers={"X-Webhook-Secret": "top-secret"},
        body=json.dumps(make_payload()),
    )
    second = receiver.receive_http_webhook(
        method="POST",
        headers={"X-Webhook-Secret": "top-secret"},
        body=json.dumps(make_payload(parameters={"customer_id": "cust-456"})),
    )

    assert first["status_code"] == 202
    assert second["status_code"] == 429
    assert second["body"]["accepted"] is False
    assert queue.inspect().tasks[0].input_payload["parameters"]["customer_id"] == "cust-123"


def test_webhook_receiver_rejects_invalid_method_and_bad_auth(tmp_path):
    queue = TaskQueueManager(storage_path=str(Path(tmp_path) / "queue.json"))
    receiver = ExternalWorkflowTriggerReceiver(
        task_queue_manager=queue,
        authentication=TriggerAuthenticationConfiguration(shared_secret="top-secret"),
    )

    wrong_method = receiver.receive_http_webhook(
        method="GET",
        headers={},
        body=json.dumps(make_payload()),
    )
    wrong_secret = receiver.receive_http_webhook(
        method="POST",
        headers={"X-Webhook-Secret": "wrong"},
        body=json.dumps(make_payload()),
    )

    assert wrong_method["status_code"] == 405
    assert wrong_secret["status_code"] == 401
