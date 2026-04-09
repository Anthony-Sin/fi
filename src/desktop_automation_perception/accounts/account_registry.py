from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from desktop_automation_perception.models import (
    AccountRecord,
    AccountRegistryResult,
    AccountRegistrySnapshot,
    AccountUsageEvent,
)


@dataclass(slots=True)
class AccountRegistry:
    storage_path: str

    def list_accounts(self, *, active_only: bool = False) -> list[AccountRecord]:
        snapshot = self._load_snapshot()
        if not active_only:
            return snapshot.accounts
        return [account for account in snapshot.accounts if account.active]

    def get_account_by_name(self, name: str) -> AccountRegistryResult:
        snapshot = self._load_snapshot()
        for account in snapshot.accounts:
            if account.name.casefold() == name.casefold():
                return AccountRegistryResult(succeeded=True, account=account)
        return AccountRegistryResult(succeeded=False, reason="Account not found.")

    def get_accounts_by_type(self, account_type: str, *, active_only: bool = False) -> list[AccountRecord]:
        snapshot = self._load_snapshot()
        return [
            account
            for account in snapshot.accounts
            if account.account_type.casefold() == account_type.casefold()
            and (account.active or not active_only)
        ]

    def upsert_account(self, account: AccountRecord) -> AccountRegistryResult:
        snapshot = self._load_snapshot()
        updated_accounts: list[AccountRecord] = []
        replaced = False

        for existing in snapshot.accounts:
            if existing.name.casefold() == account.name.casefold():
                updated_accounts.append(account)
                replaced = True
            else:
                updated_accounts.append(existing)

        if not replaced:
            updated_accounts.append(account)

        snapshot.accounts = updated_accounts
        self._save_snapshot(snapshot)
        return AccountRegistryResult(succeeded=True, account=account)

    def set_account_active(self, name: str, active: bool) -> AccountRegistryResult:
        snapshot = self._load_snapshot()
        for index, account in enumerate(snapshot.accounts):
            if account.name.casefold() == name.casefold():
                updated = AccountRecord(
                    name=account.name,
                    credential_reference=account.credential_reference,
                    account_type=account.account_type,
                    application=account.application,
                    pacing_profile_id=account.pacing_profile_id,
                    last_used_at=account.last_used_at,
                    active=active,
                    health_score=account.health_score,
                )
                snapshot.accounts[index] = updated
                snapshot.usage_history.append(
                    AccountUsageEvent(
                        account_name=updated.name,
                        action="set_active" if active else "set_inactive",
                        timestamp=datetime.now(timezone.utc),
                    )
                )
                self._save_snapshot(snapshot)
                return AccountRegistryResult(succeeded=True, account=updated)
        return AccountRegistryResult(succeeded=False, reason="Account not found.")

    def update_last_used(self, name: str, timestamp: datetime | None = None) -> AccountRegistryResult:
        snapshot = self._load_snapshot()
        timestamp = timestamp or datetime.now(timezone.utc)
        for index, account in enumerate(snapshot.accounts):
            if account.name.casefold() == name.casefold():
                updated = AccountRecord(
                    name=account.name,
                    credential_reference=account.credential_reference,
                    account_type=account.account_type,
                    application=account.application,
                    pacing_profile_id=account.pacing_profile_id,
                    last_used_at=timestamp,
                    active=account.active,
                    health_score=account.health_score,
                )
                snapshot.accounts[index] = updated
                snapshot.usage_history.append(
                    AccountUsageEvent(
                        account_name=updated.name,
                        action="update_last_used",
                        timestamp=timestamp,
                    )
                )
                self._save_snapshot(snapshot)
                return AccountRegistryResult(succeeded=True, account=updated)
        return AccountRegistryResult(succeeded=False, reason="Account not found.")

    def log_account_usage(self, name: str, action: str, detail: str | None = None) -> AccountRegistryResult:
        snapshot = self._load_snapshot()
        account = next((item for item in snapshot.accounts if item.name.casefold() == name.casefold()), None)
        if account is None:
            return AccountRegistryResult(succeeded=False, reason="Account not found.")

        snapshot.usage_history.append(
            AccountUsageEvent(
                account_name=account.name,
                action=action,
                timestamp=datetime.now(timezone.utc),
                detail=detail,
            )
        )
        self._save_snapshot(snapshot)
        return AccountRegistryResult(succeeded=True, account=account)

    def get_usage_history(self, name: str | None = None) -> list[AccountUsageEvent]:
        snapshot = self._load_snapshot()
        if name is None:
            return snapshot.usage_history
        return [event for event in snapshot.usage_history if event.account_name.casefold() == name.casefold()]

    def _load_snapshot(self) -> AccountRegistrySnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return AccountRegistrySnapshot()

        payload = json.loads(path.read_text(encoding="utf-8"))
        return AccountRegistrySnapshot(
            accounts=[self._deserialize_account(item) for item in payload.get("accounts", [])],
            usage_history=[self._deserialize_usage(item) for item in payload.get("usage_history", [])],
        )

    def _save_snapshot(self, snapshot: AccountRegistrySnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "accounts": [self._serialize_account(account) for account in snapshot.accounts],
            "usage_history": [self._serialize_usage(event) for event in snapshot.usage_history],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_account(self, account: AccountRecord) -> dict:
        payload = asdict(account)
        payload["last_used_at"] = account.last_used_at.isoformat() if account.last_used_at is not None else None
        return payload

    def _deserialize_account(self, payload: dict) -> AccountRecord:
        return AccountRecord(
            name=payload["name"],
            credential_reference=payload["credential_reference"],
            account_type=payload["account_type"],
            application=payload["application"],
            pacing_profile_id=payload.get("pacing_profile_id"),
            last_used_at=datetime.fromisoformat(payload["last_used_at"]) if payload.get("last_used_at") else None,
            active=bool(payload.get("active", True)),
            health_score=float(payload.get("health_score", 1.0)),
        )

    def _serialize_usage(self, event: AccountUsageEvent) -> dict:
        return {
            "account_name": event.account_name,
            "action": event.action,
            "timestamp": event.timestamp.isoformat(),
            "detail": event.detail,
        }

    def _deserialize_usage(self, payload: dict) -> AccountUsageEvent:
        return AccountUsageEvent(
            account_name=payload["account_name"],
            action=payload["action"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            detail=payload.get("detail"),
        )
