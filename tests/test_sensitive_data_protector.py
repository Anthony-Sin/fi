import json
from pathlib import Path

from desktop_automation_agent.resilience import SensitiveDataProtector


def test_sensitive_data_protector_sanitizes_payloads_and_audits_access(tmp_path):
    audit_path = Path(tmp_path) / "sensitive_audit.jsonl"
    protector = SensitiveDataProtector(
        sensitive_field_names=("password", "token", "ssn"),
        sensitive_value_patterns=(r"AKIA[0-9A-Z]{16}", r"\b\d{3}-\d{2}-\d{4}\b"),
        access_audit_path=str(audit_path),
    )

    payload = protector.sanitize_payload(
        {
            "username": "worker",
            "password": "secret",
            "notes": "Employee SSN 123-45-6789",
            "nested": {"session_token": "AKIAABCDEFGHIJKLMNOP"},
        }
    )
    event = protector.audit_access(
        location="credential_vault",
        action="retrieve",
        metadata={"password": "secret", "note": "123-45-6789"},
    )

    lines = audit_path.read_text(encoding="utf-8").splitlines()

    assert payload["password"] == "***SENSITIVE***"
    assert payload["notes"] == "***SENSITIVE***"
    assert payload["nested"]["session_token"] == "***SENSITIVE***"
    assert event.location == "credential_vault"
    assert json.loads(lines[0])["metadata"]["password"] == "***SENSITIVE***"


def test_sensitive_data_protector_masks_text_and_screenshot_files(tmp_path):
    protector = SensitiveDataProtector(
        sensitive_value_patterns=(r"secret-token-\d+",),
    )
    screenshot_path = Path(tmp_path) / "screen.png"
    screenshot_path.write_text("Visible secret-token-42 on screen", encoding="utf-8")

    text_result = protector.mask_text("prompt secret-token-42", location="report")
    file_result = protector.protect_screenshot_file(str(screenshot_path))

    assert text_result.text == "prompt ***SENSITIVE***"
    assert file_result.succeeded is True
    assert screenshot_path.read_text(encoding="utf-8") == "Visible ***SENSITIVE*** on screen"


def test_sensitive_data_protector_blocks_sensitive_prompts():
    protector = SensitiveDataProtector(
        sensitive_value_patterns=(r"super-secret",),
    )

    result = protector.validate_prompt("Send super-secret to the model", location="prompt_template:test")

    assert result.succeeded is False
    assert result.reason == "Prompt contains sensitive values and cannot be submitted."
