from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_agent.models import (
    CredentialAccessEvent,
    CredentialKind,
    CredentialRecord,
    CredentialVaultResult,
    CredentialVaultSnapshot,
)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class CRYPTPROTECT_PROMPTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("dwPromptFlags", ctypes.wintypes.DWORD),
        ("hwndApp", ctypes.wintypes.HWND),
        ("szPrompt", ctypes.wintypes.LPWSTR),
    ]


@dataclass(slots=True)
class DPAPICipher:
    def encrypt(self, value: str) -> str:
        data = value.encode("utf-8")
        in_blob = self._to_blob(data)
        out_blob = DATA_BLOB()
        prompt = CRYPTPROTECT_PROMPTSTRUCT()
        prompt.cbSize = ctypes.sizeof(CRYPTPROTECT_PROMPTSTRUCT)

        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            ctypes.byref(prompt),
            0,
            ctypes.byref(out_blob),
        ):
            raise OSError("Unable to encrypt credential with DPAPI.")

        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return base64.b64encode(encrypted).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)

    def decrypt(self, encrypted_value: str) -> str:
        raw = base64.b64decode(encrypted_value.encode("ascii"))
        in_blob = self._to_blob(raw)
        out_blob = DATA_BLOB()
        prompt = CRYPTPROTECT_PROMPTSTRUCT()
        prompt.cbSize = ctypes.sizeof(CRYPTPROTECT_PROMPTSTRUCT)

        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            ctypes.byref(prompt),
            0,
            ctypes.byref(out_blob),
        ):
            raise OSError("Unable to decrypt credential with DPAPI.")

        try:
            decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return decrypted.decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)

    def _to_blob(self, data: bytes) -> DATA_BLOB:
        buffer = (ctypes.c_byte * len(data))(*data)
        return DATA_BLOB(len(data), buffer)


@dataclass(slots=True)
class CredentialVault:
    storage_path: str
    cipher: object | None = None
    near_expiry_window: timedelta = timedelta(hours=24)
    refresh_callback: Callable[[str, CredentialKind], str | None] | None = None
    alert_callback: Callable[[str, CredentialKind, datetime | None], None] | None = None
    sensitive_data_protector: object | None = None

    def __post_init__(self) -> None:
        if self.cipher is None:
            self.cipher = DPAPICipher()

    def store_credential(
        self,
        *,
        account_identifier: str,
        kind: CredentialKind,
        value: str,
        expires_at: datetime | None = None,
    ) -> CredentialVaultResult:
        snapshot = self._load_snapshot()
        encrypted = self.cipher.encrypt(value)
        record = CredentialRecord(
            account_identifier=account_identifier,
            kind=kind,
            encrypted_value=encrypted,
            expires_at=expires_at,
            updated_at=datetime.now(timezone.utc),
        )

        updated = []
        replaced = False
        for existing in snapshot.credentials:
            if existing.account_identifier.casefold() == account_identifier.casefold() and existing.kind is kind:
                updated.append(record)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(record)
        snapshot.credentials = updated
        snapshot.access_log.append(
            CredentialAccessEvent(
                account_identifier=account_identifier,
                kind=kind,
                action="store",
                timestamp=datetime.now(timezone.utc),
            )
        )
        self._audit_sensitive_access(
            location="credential_vault",
            action="store",
            account_identifier=account_identifier,
            kind=kind,
        )
        self._save_snapshot(snapshot)
        return CredentialVaultResult(succeeded=True, expires_at=expires_at)

    def retrieve_credential(self, account_identifier: str, kind: CredentialKind) -> CredentialVaultResult:
        snapshot = self._load_snapshot()
        record = self._find_record(snapshot, account_identifier, kind)
        if record is None:
            return CredentialVaultResult(succeeded=False, reason="Credential not found.")

        if self._is_near_expiry(record.expires_at):
            refreshed = self.refresh_callback(account_identifier, kind) if self.refresh_callback else None
            if refreshed is not None:
                self.store_credential(
                    account_identifier=account_identifier,
                    kind=kind,
                    value=refreshed,
                    expires_at=record.expires_at,
                )
                snapshot = self._load_snapshot()
                record = self._find_record(snapshot, account_identifier, kind)
                if record is None:
                    return CredentialVaultResult(succeeded=False, reason="Credential refresh did not persist.")
            elif self.alert_callback is not None:
                self.alert_callback(account_identifier, kind, record.expires_at)
                snapshot.access_log.append(
                    CredentialAccessEvent(
                        account_identifier=account_identifier,
                        kind=kind,
                        action="expiry_alert",
                        timestamp=datetime.now(timezone.utc),
                        detail="Credential is near expiry.",
                    )
                )

        value = self.cipher.decrypt(record.encrypted_value)
        snapshot.access_log.append(
            CredentialAccessEvent(
                account_identifier=account_identifier,
                kind=kind,
                action="retrieve",
                timestamp=datetime.now(timezone.utc),
            )
        )
        self._audit_sensitive_access(
            location="credential_vault",
            action="retrieve",
            account_identifier=account_identifier,
            kind=kind,
        )
        self._save_snapshot(snapshot)
        return CredentialVaultResult(succeeded=True, value=value, expires_at=record.expires_at)

    def rotate_credential(
        self,
        *,
        account_identifier: str,
        kind: CredentialKind,
        new_value: str,
        expires_at: datetime | None = None,
    ) -> CredentialVaultResult:
        result = self.store_credential(
            account_identifier=account_identifier,
            kind=kind,
            value=new_value,
            expires_at=expires_at,
        )
        snapshot = self._load_snapshot()
        snapshot.access_log.append(
            CredentialAccessEvent(
                account_identifier=account_identifier,
                kind=kind,
                action="rotate",
                timestamp=datetime.now(timezone.utc),
            )
        )
        self._audit_sensitive_access(
            location="credential_vault",
            action="rotate",
            account_identifier=account_identifier,
            kind=kind,
        )
        self._save_snapshot(snapshot)
        return result

    def get_access_log(self, account_identifier: str | None = None) -> list[CredentialAccessEvent]:
        snapshot = self._load_snapshot()
        if account_identifier is None:
            return snapshot.access_log
        return [
            event
            for event in snapshot.access_log
            if event.account_identifier.casefold() == account_identifier.casefold()
        ]

    def _is_near_expiry(self, expires_at: datetime | None) -> bool:
        if expires_at is None:
            return False
        return expires_at <= datetime.now(timezone.utc) + self.near_expiry_window

    def _find_record(
        self,
        snapshot: CredentialVaultSnapshot,
        account_identifier: str,
        kind: CredentialKind,
    ) -> CredentialRecord | None:
        for record in snapshot.credentials:
            if record.account_identifier.casefold() == account_identifier.casefold() and record.kind is kind:
                return record
        return None

    def _load_snapshot(self) -> CredentialVaultSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return CredentialVaultSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CredentialVaultSnapshot(
            credentials=[self._deserialize_record(item) for item in payload.get("credentials", [])],
            access_log=[self._deserialize_event(item) for item in payload.get("access_log", [])],
        )

    def _save_snapshot(self, snapshot: CredentialVaultSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "credentials": [self._serialize_record(record) for record in snapshot.credentials],
            "access_log": [self._serialize_event(event) for event in snapshot.access_log],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_record(self, record: CredentialRecord) -> dict:
        return {
            "account_identifier": record.account_identifier,
            "kind": record.kind.value,
            "encrypted_value": record.encrypted_value,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "updated_at": record.updated_at.isoformat(),
        }

    def _deserialize_record(self, payload: dict) -> CredentialRecord:
        return CredentialRecord(
            account_identifier=payload["account_identifier"],
            kind=CredentialKind(payload["kind"]),
            encrypted_value=payload["encrypted_value"],
            expires_at=datetime.fromisoformat(payload["expires_at"]) if payload.get("expires_at") else None,
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )

    def _serialize_event(self, event: CredentialAccessEvent) -> dict:
        return {
            "account_identifier": event.account_identifier,
            "kind": event.kind.value,
            "action": event.action,
            "timestamp": event.timestamp.isoformat(),
            "detail": event.detail,
        }

    def _deserialize_event(self, payload: dict) -> CredentialAccessEvent:
        return CredentialAccessEvent(
            account_identifier=payload["account_identifier"],
            kind=CredentialKind(payload["kind"]),
            action=payload["action"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            detail=payload.get("detail"),
        )

    def _audit_sensitive_access(
        self,
        *,
        location: str,
        action: str,
        account_identifier: str,
        kind: CredentialKind,
    ) -> None:
        if self.sensitive_data_protector is None:
            return
        self.sensitive_data_protector.audit_access(
            location=location,
            action=action,
            detail=f"{kind.value} credential accessed.",
            metadata={
                "account_identifier": account_identifier,
                "kind": kind.value,
            },
        )
