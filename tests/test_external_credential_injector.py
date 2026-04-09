from datetime import datetime, timedelta, timezone
from pathlib import Path

from desktop_automation_agent.accounts import ExternalCredentialInjector
from desktop_automation_agent.models import WorkflowContext
from desktop_automation_agent.workflow_audit_logger import WorkflowAuditLogger


class FakeVaultBackend:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def fetch_secret(self, *, endpoint, headers, timeout_seconds):
        secret_name = endpoint.rsplit("/", 1)[-1]
        self.calls.append(
            {
                "endpoint": endpoint,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses[secret_name].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_external_credential_injector_fetches_secret_and_injects_secure_handle(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    backend = FakeVaultBackend(
        {
            "crm-password": [
                {
                    "value": "super-secret",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                }
            ]
        }
    )
    context = WorkflowContext()
    injector = ExternalCredentialInjector(
        base_url="https://vault.example",
        service_account_token="svc-token",
        backend=backend,
        now_fn=lambda: now,
    )

    result = injector.inject_into_context(
        secret_name="crm-password",
        context=context,
        context_key="crm_password",
    )

    assert result.succeeded is True
    assert result.cached is False
    assert result.context_key == "crm_password"
    assert context.shared_data["crm_password"] == "vault://crm-password"
    assert context.secure_data["crm_password"].reveal() == "super-secret"
    assert backend.calls == [
        {
            "endpoint": "https://vault.example/secrets/crm-password",
            "headers": {"Authorization": "Bearer svc-token"},
            "timeout_seconds": 30.0,
        }
    ]


def test_external_credential_injector_reuses_session_cache_until_expiry():
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    backend = FakeVaultBackend(
        {
            "erp-token": [
                {
                    "data": {
                        "secret": "token-1",
                        "expires_at": (now + timedelta(minutes=10)).isoformat(),
                    }
                }
            ]
        }
    )
    injector = ExternalCredentialInjector(
        base_url="https://vault.example",
        service_account_token="svc-token",
        backend=backend,
        now_fn=lambda: now,
    )

    first = injector.retrieve_credential(secret_name="erp-token")
    second = injector.retrieve_credential(secret_name="erp-token")

    assert first.succeeded is True
    assert second.succeeded is True
    assert second.cached is True
    assert first.credential is second.credential
    assert len(backend.calls) == 1


def test_external_credential_injector_invalidates_expired_cache_and_refetches():
    current_time = {"value": datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)}
    backend = FakeVaultBackend(
        {
            "sheet-key": [
                {
                    "value": "first-key",
                    "expires_at": (current_time["value"] + timedelta(minutes=5)).isoformat(),
                },
                {
                    "value": "second-key",
                    "expires_at": (current_time["value"] + timedelta(minutes=25)).isoformat(),
                },
            ]
        }
    )
    injector = ExternalCredentialInjector(
        base_url="https://vault.example",
        service_account_token="svc-token",
        backend=backend,
        now_fn=lambda: current_time["value"],
    )

    first = injector.retrieve_credential(secret_name="sheet-key")
    original_buffer = first.credential
    current_time["value"] = current_time["value"] + timedelta(minutes=6)
    second = injector.retrieve_credential(secret_name="sheet-key")

    assert first.succeeded is True
    assert second.succeeded is True
    assert second.cached is False
    assert second.credential is not original_buffer
    assert second.credential is not None and second.credential.reveal() == "second-key"
    assert original_buffer is not None and original_buffer.zeroized is True
    assert original_buffer.is_available() is False
    assert len(backend.calls) == 2


def test_external_credential_injector_invalidates_cache_on_error_and_never_logs_secret_values(tmp_path):
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    logger = WorkflowAuditLogger(storage_path=str(Path(tmp_path) / "vault_audit.jsonl"))
    backend = FakeVaultBackend(
        {
            "portal-password": [
                {
                    "value": "very-secret",
                    "expires_at": (now + timedelta(minutes=1)).isoformat(),
                },
                RuntimeError("vault unavailable"),
            ]
        }
    )
    injector = ExternalCredentialInjector(
        base_url="https://vault.example",
        service_account_token="svc-token",
        backend=backend,
        audit_logger=logger,
        now_fn=lambda: now,
    )

    first = injector.retrieve_credential(secret_name="portal-password")
    original_buffer = first.credential
    failed = injector.retrieve_credential(secret_name="portal-password", force_refresh=True)
    audit_text = Path(tmp_path, "vault_audit.jsonl").read_text(encoding="utf-8")

    assert first.succeeded is True
    assert failed.succeeded is False
    assert original_buffer is not None and original_buffer.zeroized is True
    assert "very-secret" not in audit_text
    assert "svc-token" not in audit_text
    assert all("very-secret" not in (event.detail or "") for event in injector.list_access_log())


def test_external_credential_injector_close_zeroizes_cached_secrets_and_service_token():
    now = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    backend = FakeVaultBackend({"acct-cookie": [{"credential": "cookie-123"}]})
    injector = ExternalCredentialInjector(
        base_url="https://vault.example",
        service_account_token="svc-token",
        backend=backend,
        now_fn=lambda: now,
    )

    result = injector.retrieve_credential(secret_name="acct-cookie")
    cached = result.credential
    token_buffer = injector.service_account_token
    injector.close()

    assert result.succeeded is True
    assert cached is not None and cached.zeroized is True
    assert token_buffer.zeroized is True
