from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from desktop_automation_perception.models import (
    SecureCredentialValue,
    VaultCredentialAccessEvent,
    VaultCredentialCacheEntry,
    VaultCredentialResult,
    WorkflowContext,
)


class VaultAPIError(Exception):
    pass


@dataclass(slots=True)
class RequestsVaultAPIBackend:
    def fetch_secret(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        import requests

        response = requests.get(endpoint, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise VaultAPIError("Vault API response must be a JSON object.")
        return payload


@dataclass(slots=True)
class ExternalCredentialInjector:
    base_url: str
    service_account_token: str | SecureCredentialValue
    backend: object | None = None
    audit_logger: object | None = None
    secret_endpoint_template: str = "/secrets/{secret_name}"
    timeout_seconds: float = 30.0
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _cache: dict[str, VaultCredentialCacheEntry] = field(default_factory=dict, init=False, repr=False)
    _access_log: list[VaultCredentialAccessEvent] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.service_account_token, str):
            self.service_account_token = SecureCredentialValue.from_plaintext(self.service_account_token)

    def retrieve_credential(
        self,
        *,
        secret_name: str,
        force_refresh: bool = False,
    ) -> VaultCredentialResult:
        cache_entry = self._cache.get(secret_name)
        if cache_entry is not None and (force_refresh or self._is_expired(cache_entry)):
            self.invalidate_secret(secret_name, reason="Cache expired or forced refresh.")
            cache_entry = None

        if cache_entry is not None and cache_entry.credential.is_available():
            event = self._record_event(
                secret_name=secret_name,
                action="cache_hit",
                cache_hit=True,
                expires_at=cache_entry.expires_at,
            )
            self._audit(
                secret_name=secret_name,
                action="vault_cache_hit",
                success=True,
                detail=None,
                output_data={"cached": True, "expires_at": self._isoformat(cache_entry.expires_at)},
            )
            return VaultCredentialResult(
                succeeded=True,
                secret_name=secret_name,
                credential=cache_entry.credential,
                expires_at=cache_entry.expires_at,
                cached=True,
                access_event=event,
            )

        try:
            payload = self._backend().fetch_secret(
                endpoint=self._build_secret_endpoint(secret_name),
                headers=self._authorization_headers(),
                timeout_seconds=self.timeout_seconds,
            )
            plain_value, expires_at = self._extract_secret(payload)
            secure_value = SecureCredentialValue.from_plaintext(plain_value)
            plain_value = ""
            self._cache[secret_name] = VaultCredentialCacheEntry(
                secret_name=secret_name,
                credential=secure_value,
                expires_at=expires_at,
                cached_at=self.now_fn(),
            )
            event = self._record_event(
                secret_name=secret_name,
                action="fetch",
                cache_hit=False,
                expires_at=expires_at,
            )
            self._audit(
                secret_name=secret_name,
                action="vault_fetch",
                success=True,
                detail=None,
                output_data={"cached": False, "expires_at": self._isoformat(expires_at)},
            )
            return VaultCredentialResult(
                succeeded=True,
                secret_name=secret_name,
                credential=secure_value,
                expires_at=expires_at,
                cached=False,
                access_event=event,
            )
        except Exception as exc:
            error_reason = f"{exc.__class__.__name__}: Vault API request failed."
            self.invalidate_secret(secret_name, reason="Fetch failed.")
            event = self._record_event(
                secret_name=secret_name,
                action="error",
                cache_hit=False,
                detail=error_reason,
            )
            self._audit(
                secret_name=secret_name,
                action="vault_fetch_failed",
                success=False,
                detail=error_reason,
                output_data={"cached": False},
            )
            return VaultCredentialResult(
                succeeded=False,
                secret_name=secret_name,
                access_event=event,
                reason=error_reason,
            )

    def inject_into_context(
        self,
        *,
        secret_name: str,
        context: WorkflowContext,
        context_key: str | None = None,
        force_refresh: bool = False,
    ) -> VaultCredentialResult:
        resolved_key = context_key or secret_name
        result = self.retrieve_credential(secret_name=secret_name, force_refresh=force_refresh)
        if not result.succeeded or result.credential is None:
            return VaultCredentialResult(
                succeeded=False,
                secret_name=secret_name,
                context_key=resolved_key,
                access_event=result.access_event,
                reason=result.reason,
            )

        previous = context.secure_data.get(resolved_key)
        if previous is not None and previous is not result.credential:
            previous.zeroize()

        context.secure_data[resolved_key] = result.credential
        context.shared_data[resolved_key] = f"vault://{secret_name}"
        self._audit(
            secret_name=secret_name,
            action="vault_inject",
            success=True,
            detail=None,
            output_data={
                "context_key": resolved_key,
                "cached": result.cached,
                "expires_at": self._isoformat(result.expires_at),
            },
        )
        return VaultCredentialResult(
            succeeded=True,
            secret_name=secret_name,
            credential=result.credential,
            expires_at=result.expires_at,
            cached=result.cached,
            context_key=resolved_key,
            access_event=result.access_event,
        )

    def invalidate_secret(self, secret_name: str, *, reason: str | None = None) -> None:
        cache_entry = self._cache.pop(secret_name, None)
        if cache_entry is None:
            return
        cache_entry.last_error = reason
        cache_entry.credential.zeroize()
        self._record_event(
            secret_name=secret_name,
            action="invalidate",
            cache_hit=False,
            expires_at=cache_entry.expires_at,
            detail=reason,
        )

    def list_access_log(self) -> list[VaultCredentialAccessEvent]:
        return list(self._access_log)

    def close(self) -> None:
        for secret_name in list(self._cache):
            self.invalidate_secret(secret_name, reason="Injector closed.")
        self.service_account_token.zeroize()

    def _backend(self):
        return self.backend or RequestsVaultAPIBackend()

    def _build_secret_endpoint(self, secret_name: str) -> str:
        return f"{self.base_url.rstrip('/')}{self.secret_endpoint_template.format(secret_name=secret_name)}"

    def _authorization_headers(self) -> dict[str, str]:
        token_value = self.service_account_token.reveal()
        try:
            return {"Authorization": f"Bearer {token_value}"}
        finally:
            token_value = ""

    def _extract_secret(self, payload: dict[str, Any]) -> tuple[str, datetime | None]:
        candidate_maps = [payload]
        data = payload.get("data")
        if isinstance(data, dict):
            candidate_maps.append(data)

        secret_value: str | None = None
        expires_at: datetime | None = None
        for candidate in candidate_maps:
            for key in ("value", "secret", "credential", "token"):
                raw = candidate.get(key)
                if isinstance(raw, str) and raw:
                    secret_value = raw
                    break
            if secret_value is not None:
                expires_at = self._parse_expiry(candidate)
                break

        if secret_value is None:
            raise VaultAPIError("Vault secret payload did not contain a supported secret value field.")
        return secret_value, expires_at

    def _parse_expiry(self, payload: dict[str, Any]) -> datetime | None:
        for key in ("expires_at", "expiry", "expiration"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw:
                return datetime.fromisoformat(raw)
        return None

    def _is_expired(self, cache_entry: VaultCredentialCacheEntry) -> bool:
        if cache_entry.expires_at is None:
            return False
        return cache_entry.expires_at <= self.now_fn()

    def _record_event(
        self,
        *,
        secret_name: str,
        action: str,
        cache_hit: bool,
        expires_at: datetime | None = None,
        detail: str | None = None,
    ) -> VaultCredentialAccessEvent:
        event = VaultCredentialAccessEvent(
            secret_name=secret_name,
            action=action,
            timestamp=self.now_fn(),
            cache_hit=cache_hit,
            expires_at=expires_at,
            detail=detail,
        )
        self._access_log.append(event)
        return event

    def _audit(
        self,
        *,
        secret_name: str,
        action: str,
        success: bool,
        detail: str | None,
        output_data: dict[str, Any],
    ) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log_action(
            workflow_id="vault-runtime",
            step_name=secret_name,
            action_type=action,
            target_element=self._build_secret_endpoint(secret_name),
            input_data={"secret_name": secret_name},
            output_data={"success": success, "detail": detail, **output_data},
            success=success,
            timestamp=self.now_fn(),
        )

    def _isoformat(self, value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()
