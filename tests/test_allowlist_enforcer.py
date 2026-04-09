import json
from datetime import datetime
from pathlib import Path

from desktop_automation_agent.allowlist_enforcer import ActionAllowlistEnforcer
from desktop_automation_agent.models import (
    AllowlistCheckRequest,
    EscalationResolution,
    EscalationResponse,
)
from desktop_automation_agent.resilience import EscalationManager


class FakeAuditLogger:
    def __init__(self):
        self.entries = []

    def log_action(self, **kwargs):
        self.entries.append(kwargs)
        return type("Result", (), {"succeeded": True})()


def write_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_allowlist_enforcer_blocks_logs_and_escalates(tmp_path):
    config_path = Path(tmp_path) / "allowlist.json"
    write_config(
        config_path,
        {
            "action_types": ["click", "rest_api_call"],
            "applications": ["writer", "calc"],
            "urls": ["https://api.example.com/*"],
            "file_paths": [str(tmp_path / "safe" / "*")],
        },
    )
    audit_logger = FakeAuditLogger()
    escalation_manager = EscalationManager(
        storage_path=str(Path(tmp_path) / "escalations.json"),
        operator_channel="ops",
        response_callback=lambda request: EscalationResponse(
            escalation_id=request.escalation_id,
            resolution=EscalationResolution.ABORT,
            operator_id="op-1",
            responded_at=datetime(2026, 4, 8, 12, 0),
            reason="Outside approved scope.",
        ),
    )
    enforcer = ActionAllowlistEnforcer(
        config_path=str(config_path),
        audit_logger=audit_logger,
        escalation_manager=escalation_manager,
    )

    result = enforcer.evaluate(
        AllowlistCheckRequest(
            workflow_id="wf-allowlist",
            step_name="dangerous-step",
            action_type="delete",
            application_name="admin-console",
            file_path=str(tmp_path / "unsafe" / "record.json"),
        )
    )

    snapshot = escalation_manager.inspect().snapshot

    assert result.succeeded is False
    assert result.allowed is False
    assert [scope.value for scope in result.violated_scopes] == ["action_type", "application", "file_path"]
    assert audit_logger.entries[0]["action_type"] == "allowlist_blocked"
    assert snapshot is not None
    assert snapshot.records[0].trigger_type.value == "allowlist_violation"


def test_allowlist_enforcer_hot_reloads_updated_configuration(tmp_path):
    config_path = Path(tmp_path) / "allowlist.json"
    write_config(
        config_path,
        {
            "action_types": ["rest_api_call"],
            "applications": ["chat-web"],
            "urls": ["https://api.one.example/*"],
            "file_paths": [str(tmp_path / "safe" / "*")],
        },
    )
    enforcer = ActionAllowlistEnforcer(config_path=str(config_path))

    first = enforcer.evaluate(
        AllowlistCheckRequest(
            action_type="rest_api_call",
            application_name="chat-web",
            url="https://api.one.example/v1/status",
        )
    )

    write_config(
        config_path,
        {
            "action_types": ["rest_api_call"],
            "applications": ["chat-web"],
            "urls": ["https://api.two.example/*"],
            "file_paths": [str(tmp_path / "safe" / "*")],
        },
    )

    second = enforcer.evaluate(
        AllowlistCheckRequest(
            action_type="rest_api_call",
            application_name="chat-web",
            url="https://api.two.example/v1/status",
        )
    )

    assert first.allowed is True
    assert second.allowed is True
