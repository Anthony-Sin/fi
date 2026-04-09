from __future__ import annotations

from desktop_automation_agent._time import utc_now

import json
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable

from desktop_automation_agent.models import (
    SandboxAccessAttempt,
    SandboxAccessType,
    SandboxIsolationMode,
    SandboxIsolationPolicy,
    SandboxIsolationResult,
    SandboxViolationRecord,
    SandboxWorkerLaunchSpec,
    WorkerExecutionMode,
    WorkerSessionContext,
)


class InMemorySandboxWorkerRuntime:
    def __init__(self, spec: SandboxWorkerLaunchSpec):
        self.spec = spec
        self.alive = True
        self.started = False
        self.assigned_tasks: list[str] = []

    def start(self) -> None:
        self.started = True
        self.alive = True

    def stop(self) -> None:
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    def assign_task(self, task: object) -> None:
        self.assigned_tasks.append(getattr(task, "task_id", "unknown"))


class InMemorySandboxBackend:
    def launch_worker(self, spec: SandboxWorkerLaunchSpec) -> object:
        return InMemorySandboxWorkerRuntime(spec)

    def terminate_worker(self, runtime: object) -> bool:
        stop = getattr(runtime, "stop", None)
        if callable(stop):
            stop()
        return True


@dataclass(slots=True)
class SandboxIsolationManager:
    policy_path: str
    violation_log_path: str
    backend: object | None = None
    audit_logger: object | None = None
    now_fn: Callable[[], datetime] = utc_now
    _active_specs: dict[str, SandboxWorkerLaunchSpec] = field(default_factory=dict, init=False, repr=False)
    _active_runtimes: dict[str, object] = field(default_factory=dict, init=False, repr=False)
    _policy_cache: SandboxIsolationPolicy | None = field(default=None, init=False, repr=False)
    _policy_marker: tuple[int, int] | None = field(default=None, init=False, repr=False)

    def launch_worker(
        self,
        *,
        worker_id: str,
        session_context: WorkerSessionContext,
        execution_mode: WorkerExecutionMode,
        isolation_mode: SandboxIsolationMode | None = None,
    ) -> SandboxIsolationResult:
        policy = self.load_policy()
        resolved_mode = isolation_mode or policy.default_isolation_mode
        spec = SandboxWorkerLaunchSpec(
            worker_id=worker_id,
            session_id=session_context.session_id,
            execution_mode=execution_mode,
            isolation_mode=resolved_mode,
            os_user_account=self._select_os_user(policy, resolved_mode),
            virtualenv_path=self._select_virtualenv(policy, resolved_mode),
            metadata=dict(session_context.metadata),
        )
        runtime = self._backend().launch_worker(spec)
        self._active_specs[worker_id] = spec
        self._active_runtimes[worker_id] = runtime
        return SandboxIsolationResult(
            succeeded=True,
            spec=spec,
            policy=policy,
            runtime=runtime,
        )

    def terminate_worker(self, worker_id: str, *, reason: str | None = None) -> SandboxIsolationResult:
        spec = self._active_specs.get(worker_id)
        runtime = self._active_runtimes.get(worker_id)
        if spec is None or runtime is None:
            return SandboxIsolationResult(succeeded=False, reason="Worker is not managed by sandbox isolation manager.")
        self._backend().terminate_worker(runtime)
        if reason is not None:
            self._log_event(
                worker_id=worker_id,
                session_id=spec.session_id,
                access_type=SandboxAccessType.RESOURCE,
                target="worker_runtime",
                allowed=False,
                detail=reason,
            )
        return SandboxIsolationResult(succeeded=True, spec=spec, runtime=runtime)

    def check_network_access(self, worker_id: str, hostname: str) -> SandboxIsolationResult:
        policy = self.load_policy()
        normalized = hostname.casefold()
        allowed = any(fnmatchcase(normalized, pattern) for pattern in policy.hostname_allowlist)
        return self._record_attempt(
            worker_id=worker_id,
            access_type=SandboxAccessType.NETWORK,
            target=hostname,
            allowed=allowed,
            detail=None if allowed else "Hostname is not in sandbox network allowlist.",
        )

    def check_file_write(self, worker_id: str, file_path: str) -> SandboxIsolationResult:
        policy = self.load_policy()
        normalized = self._normalize_path(file_path)
        allowed = any(self._path_within(normalized, allowed_path) for allowed_path in policy.writable_directories)
        return self._record_attempt(
            worker_id=worker_id,
            access_type=SandboxAccessType.FILE_WRITE,
            target=file_path,
            allowed=allowed,
            detail=None if allowed else "File path is outside permitted sandbox directories.",
        )

    def check_resource_access(self, worker_id: str, resource_name: str) -> SandboxIsolationResult:
        policy = self.load_policy()
        normalized = resource_name.casefold()
        allowed = not any(fnmatchcase(normalized, pattern) for pattern in policy.restricted_resources)
        return self._record_attempt(
            worker_id=worker_id,
            access_type=SandboxAccessType.RESOURCE,
            target=resource_name,
            allowed=allowed,
            detail=None if allowed else "Access to restricted resource was denied.",
        )

    def list_violations(self) -> list[SandboxViolationRecord]:
        path = Path(self.violation_log_path)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            SandboxViolationRecord(
                worker_id=item["worker_id"],
                session_id=item["session_id"],
                access_type=SandboxAccessType(item["access_type"]),
                target=item["target"],
                timestamp=datetime.fromisoformat(item["timestamp"]),
                action_taken=item.get("action_taken", "terminated"),
                detail=item.get("detail"),
            )
            for item in payload.get("violations", [])
        ]

    def load_policy(self, *, force: bool = False) -> SandboxIsolationPolicy:
        path = Path(self.policy_path)
        if not path.exists():
            raise FileNotFoundError(f"Sandbox policy file does not exist: {self.policy_path}")
        stat = path.stat()
        marker = (stat.st_mtime_ns, stat.st_size)
        if not force and self._policy_cache is not None and self._policy_marker == marker:
            return self._policy_cache

        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = SandboxIsolationPolicy(
            hostname_allowlist=tuple(str(item).strip().casefold() for item in payload.get("hostname_allowlist", []) if str(item).strip()),
            writable_directories=tuple(
                self._normalize_path(str(item)) for item in payload.get("writable_directories", []) if str(item).strip()
            ),
            restricted_resources=tuple(str(item).strip().casefold() for item in payload.get("restricted_resources", []) if str(item).strip()),
            os_user_accounts=tuple(str(item).strip() for item in payload.get("os_user_accounts", []) if str(item).strip()),
            virtualenv_paths=tuple(str(item).strip() for item in payload.get("virtualenv_paths", []) if str(item).strip()),
            default_isolation_mode=SandboxIsolationMode(
                payload.get("default_isolation_mode", SandboxIsolationMode.VIRTUAL_ENVIRONMENT.value)
            ),
        )
        self._policy_cache = policy
        self._policy_marker = marker
        return policy

    def _record_attempt(
        self,
        *,
        worker_id: str,
        access_type: SandboxAccessType,
        target: str,
        allowed: bool,
        detail: str | None,
    ) -> SandboxIsolationResult:
        spec = self._active_specs.get(worker_id)
        if spec is None:
            return SandboxIsolationResult(succeeded=False, reason="Worker is not managed by sandbox isolation manager.")
        attempt = SandboxAccessAttempt(
            worker_id=worker_id,
            session_id=spec.session_id,
            access_type=access_type,
            target=target,
            timestamp=self.now_fn(),
            allowed=allowed,
            detail=detail,
        )
        if allowed:
            return SandboxIsolationResult(
                succeeded=True,
                spec=spec,
                attempt=attempt,
                policy=self._policy_cache,
            )

        runtime = self._active_runtimes.get(worker_id)
        if runtime is not None:
            self._backend().terminate_worker(runtime)
        violation = SandboxViolationRecord(
            worker_id=worker_id,
            session_id=spec.session_id,
            access_type=access_type,
            target=target,
            timestamp=attempt.timestamp,
            detail=detail,
        )
        self._append_violation(violation)
        self._log_event(
            worker_id=worker_id,
            session_id=spec.session_id,
            access_type=access_type,
            target=target,
            allowed=False,
            detail=detail,
        )
        return SandboxIsolationResult(
            succeeded=False,
            spec=spec,
            attempt=attempt,
            violation=violation,
            violations=[violation],
            policy=self._policy_cache,
            reason=detail,
        )

    def _append_violation(self, violation: SandboxViolationRecord) -> None:
        path = Path(self.violation_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"violations": []}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("violations", []).append(
            {
                "worker_id": violation.worker_id,
                "session_id": violation.session_id,
                "access_type": violation.access_type.value,
                "target": violation.target,
                "timestamp": violation.timestamp.isoformat(),
                "action_taken": violation.action_taken,
                "detail": violation.detail,
            }
        )
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _log_event(
        self,
        *,
        worker_id: str,
        session_id: str,
        access_type: SandboxAccessType,
        target: str,
        allowed: bool,
        detail: str | None,
    ) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log_action(
            workflow_id=session_id,
            step_name="sandbox_isolation_manager",
            action_type="sandbox_violation" if not allowed else "sandbox_access",
            target_element=target,
            input_data={
                "worker_id": worker_id,
                "access_type": access_type.value,
                "target": target,
            },
            output_data={
                "allowed": allowed,
                "detail": detail,
            },
            success=allowed,
        )

    def _select_os_user(self, policy: SandboxIsolationPolicy, mode: SandboxIsolationMode) -> str | None:
        if mode is not SandboxIsolationMode.OS_USER or not policy.os_user_accounts:
            return None
        return policy.os_user_accounts[0]

    def _select_virtualenv(self, policy: SandboxIsolationPolicy, mode: SandboxIsolationMode) -> str | None:
        if mode is not SandboxIsolationMode.VIRTUAL_ENVIRONMENT or not policy.virtualenv_paths:
            return None
        return policy.virtualenv_paths[0]

    def _path_within(self, candidate: str, root: str) -> bool:
        normalized_root = root.rstrip("/")
        return candidate == normalized_root or candidate.startswith(f"{normalized_root}/")

    def _normalize_path(self, value: str) -> str:
        return str(Path(value)).replace("\\", "/").casefold()

    def _backend(self):
        return self.backend or InMemorySandboxBackend()


