from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from desktop_automation_perception.contracts import BrowserLauncher
from desktop_automation_perception.models import (
    BrowserProfileRecord,
    BrowserProfileResult,
    BrowserProfileSnapshot,
    BrowserSessionRecord,
)


@dataclass(slots=True)
class SubprocessBrowserLauncher:
    def launch(self, browser_executable: str, profile_directory: str, application: str | None = None) -> int | None:
        import subprocess

        command = [browser_executable, f"--user-data-dir={profile_directory}"]
        if application:
            command.append(application)
        process = subprocess.Popen(command)
        return int(process.pid)

    def close(self, process_id: int | None, profile_directory: str) -> bool:
        if process_id is None:
            return False
        import os
        import signal

        try:
            os.kill(process_id, signal.SIGTERM)
            return True
        except OSError:
            return False


@dataclass(slots=True)
class BrowserProfileSwitcher:
    storage_path: str
    launcher: BrowserLauncher
    account_verifier: Callable[[str, str], bool]

    def list_profiles(self) -> list[BrowserProfileRecord]:
        return self._load_snapshot().profiles

    def get_profile(self, account_name: str) -> BrowserProfileResult:
        snapshot = self._load_snapshot()
        profile = self._find_profile(snapshot, account_name)
        if profile is None:
            return BrowserProfileResult(succeeded=False, reason="Browser profile not found.")
        session = self._find_active_session(snapshot, account_name)
        return BrowserProfileResult(succeeded=True, profile=profile, session=session)

    def create_profile(
        self,
        *,
        account_name: str,
        profile_directory: str,
        browser_executable: str,
        application: str | None = None,
    ) -> BrowserProfileResult:
        snapshot = self._load_snapshot()
        profile_path = Path(profile_directory)
        profile_path.mkdir(parents=True, exist_ok=True)

        profile = BrowserProfileRecord(
            account_name=account_name,
            profile_directory=str(profile_path),
            browser_executable=browser_executable,
            application=application,
        )

        existing = [item for item in snapshot.profiles if item.account_name.casefold() != account_name.casefold()]
        existing.append(profile)
        snapshot.profiles = existing
        self._save_snapshot(snapshot)
        return BrowserProfileResult(succeeded=True, profile=profile)

    def launch_profile(self, account_name: str) -> BrowserProfileResult:
        snapshot = self._load_snapshot()
        profile = self._find_profile(snapshot, account_name)
        if profile is None:
            return BrowserProfileResult(succeeded=False, reason="Browser profile not found.")

        process_id = self.launcher.launch(
            browser_executable=profile.browser_executable,
            profile_directory=profile.profile_directory,
            application=profile.application,
        )
        if not self.account_verifier(account_name, profile.profile_directory):
            return BrowserProfileResult(succeeded=False, profile=profile, reason="Launched profile did not verify the expected account.")

        launched_at = datetime.now(timezone.utc)
        updated_profile = BrowserProfileRecord(
            account_name=profile.account_name,
            profile_directory=profile.profile_directory,
            browser_executable=profile.browser_executable,
            application=profile.application,
            created_at=profile.created_at,
            last_launched_at=launched_at,
            persistent_session=True,
        )
        session = BrowserSessionRecord(
            account_name=account_name,
            profile_directory=profile.profile_directory,
            launched_at=launched_at,
            browser_process_id=process_id,
            active=True,
        )

        snapshot.profiles = [
            updated_profile if item.account_name.casefold() == account_name.casefold() else item
            for item in snapshot.profiles
        ]
        snapshot.sessions = [
            BrowserSessionRecord(
                account_name=item.account_name,
                profile_directory=item.profile_directory,
                launched_at=item.launched_at,
                browser_process_id=item.browser_process_id,
                active=False,
            )
            for item in snapshot.sessions
            if item.account_name.casefold() != account_name.casefold()
        ] + [session]
        self._save_snapshot(snapshot)
        return BrowserProfileResult(succeeded=True, profile=updated_profile, session=session)

    def switch_profile(self, target_account_name: str) -> BrowserProfileResult:
        snapshot = self._load_snapshot()
        active_session = next((session for session in snapshot.sessions if session.active), None)
        if active_session is not None:
            self.launcher.close(active_session.browser_process_id, active_session.profile_directory)
            snapshot.sessions = [
                BrowserSessionRecord(
                    account_name=session.account_name,
                    profile_directory=session.profile_directory,
                    launched_at=session.launched_at,
                    browser_process_id=session.browser_process_id,
                    active=False if session.account_name == active_session.account_name else session.active,
                )
                for session in snapshot.sessions
            ]
            self._save_snapshot(snapshot)

        return self.launch_profile(target_account_name)

    def mark_session_persistent(self, account_name: str, persistent: bool = True) -> BrowserProfileResult:
        snapshot = self._load_snapshot()
        profile = self._find_profile(snapshot, account_name)
        if profile is None:
            return BrowserProfileResult(succeeded=False, reason="Browser profile not found.")

        updated = BrowserProfileRecord(
            account_name=profile.account_name,
            profile_directory=profile.profile_directory,
            browser_executable=profile.browser_executable,
            application=profile.application,
            created_at=profile.created_at,
            last_launched_at=profile.last_launched_at,
            persistent_session=persistent,
        )
        snapshot.profiles = [
            updated if item.account_name.casefold() == account_name.casefold() else item
            for item in snapshot.profiles
        ]
        self._save_snapshot(snapshot)
        return BrowserProfileResult(succeeded=True, profile=updated)

    def _find_profile(self, snapshot: BrowserProfileSnapshot, account_name: str) -> BrowserProfileRecord | None:
        for profile in snapshot.profiles:
            if profile.account_name.casefold() == account_name.casefold():
                return profile
        return None

    def _find_active_session(self, snapshot: BrowserProfileSnapshot, account_name: str) -> BrowserSessionRecord | None:
        for session in snapshot.sessions:
            if session.account_name.casefold() == account_name.casefold() and session.active:
                return session
        return None

    def _load_snapshot(self) -> BrowserProfileSnapshot:
        path = Path(self.storage_path)
        if not path.exists():
            return BrowserProfileSnapshot()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return BrowserProfileSnapshot(
            profiles=[self._deserialize_profile(item) for item in payload.get("profiles", [])],
            sessions=[self._deserialize_session(item) for item in payload.get("sessions", [])],
        )

    def _save_snapshot(self, snapshot: BrowserProfileSnapshot) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": [self._serialize_profile(profile) for profile in snapshot.profiles],
            "sessions": [self._serialize_session(session) for session in snapshot.sessions],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _serialize_profile(self, profile: BrowserProfileRecord) -> dict:
        return {
            "account_name": profile.account_name,
            "profile_directory": profile.profile_directory,
            "browser_executable": profile.browser_executable,
            "application": profile.application,
            "created_at": profile.created_at.isoformat(),
            "last_launched_at": profile.last_launched_at.isoformat() if profile.last_launched_at else None,
            "persistent_session": profile.persistent_session,
        }

    def _deserialize_profile(self, payload: dict) -> BrowserProfileRecord:
        return BrowserProfileRecord(
            account_name=payload["account_name"],
            profile_directory=payload["profile_directory"],
            browser_executable=payload["browser_executable"],
            application=payload.get("application"),
            created_at=datetime.fromisoformat(payload["created_at"]),
            last_launched_at=datetime.fromisoformat(payload["last_launched_at"]) if payload.get("last_launched_at") else None,
            persistent_session=bool(payload.get("persistent_session", False)),
        )

    def _serialize_session(self, session: BrowserSessionRecord) -> dict:
        return {
            "account_name": session.account_name,
            "profile_directory": session.profile_directory,
            "launched_at": session.launched_at.isoformat(),
            "browser_process_id": session.browser_process_id,
            "active": session.active,
        }

    def _deserialize_session(self, payload: dict) -> BrowserSessionRecord:
        return BrowserSessionRecord(
            account_name=payload["account_name"],
            profile_directory=payload["profile_directory"],
            launched_at=datetime.fromisoformat(payload["launched_at"]),
            browser_process_id=payload.get("browser_process_id"),
            active=bool(payload.get("active", True)),
        )
