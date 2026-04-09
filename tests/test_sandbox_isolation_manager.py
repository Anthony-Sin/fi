import json
from datetime import datetime, timezone
from pathlib import Path

from desktop_automation_perception.models import SandboxIsolationMode, WorkerExecutionMode, WorkerSessionContext
from desktop_automation_perception.sandbox_isolation_manager import SandboxIsolationManager


class FakeSandboxRuntime:
    def __init__(self, spec):
        self.spec = spec
        self.alive = True
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        self.alive = True

    def stop(self):
        self.stopped += 1
        self.alive = False

    def is_alive(self):
        return self.alive


class FakeSandboxBackend:
    def __init__(self):
        self.launched_specs = []
        self.terminated = []

    def launch_worker(self, spec):
        self.launched_specs.append(spec)
        runtime = FakeSandboxRuntime(spec)
        runtime.start()
        return runtime

    def terminate_worker(self, runtime):
        runtime.stop()
        self.terminated.append(runtime.spec.worker_id)
        return True


def write_policy(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "hostname_allowlist": ["api.safe.example", "*.internal.example"],
                "writable_directories": ["C:/sandbox/work", "C:/sandbox/tmp"],
                "restricted_resources": ["registry:*", "device:*"],
                "os_user_accounts": ["sandbox-user"],
                "virtualenv_paths": ["C:/venvs/worker-a"],
                "default_isolation_mode": "virtual_environment",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_sandbox_manager_launches_worker_with_virtualenv_policy(tmp_path):
    backend = FakeSandboxBackend()
    policy_path = Path(tmp_path) / "sandbox_policy.json"
    write_policy(policy_path)
    manager = SandboxIsolationManager(
        policy_path=str(policy_path),
        violation_log_path=str(Path(tmp_path) / "violations.json"),
        backend=backend,
    )

    result = manager.launch_worker(
        worker_id="worker-1",
        session_context=WorkerSessionContext(
            worker_id="worker-1",
            session_id="worker-1-session",
            execution_mode=WorkerExecutionMode.PROCESS,
            created_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
        ),
        execution_mode=WorkerExecutionMode.PROCESS,
    )

    assert result.succeeded is True
    assert result.spec is not None
    assert result.spec.isolation_mode is SandboxIsolationMode.VIRTUAL_ENVIRONMENT
    assert result.spec.virtualenv_path == "C:/venvs/worker-a"
    assert backend.launched_specs[0].worker_id == "worker-1"


def test_sandbox_manager_terminates_worker_on_restricted_access(tmp_path):
    backend = FakeSandboxBackend()
    policy_path = Path(tmp_path) / "sandbox_policy.json"
    write_policy(policy_path)
    violations_path = Path(tmp_path) / "violations.json"
    manager = SandboxIsolationManager(
        policy_path=str(policy_path),
        violation_log_path=str(violations_path),
        backend=backend,
    )
    manager.launch_worker(
        worker_id="worker-2",
        session_context=WorkerSessionContext(
            worker_id="worker-2",
            session_id="worker-2-session",
            execution_mode=WorkerExecutionMode.PROCESS,
            created_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
        ),
        execution_mode=WorkerExecutionMode.PROCESS,
        isolation_mode=SandboxIsolationMode.OS_USER,
    )

    network_result = manager.check_network_access("worker-2", "api.blocked.example")
    file_result = manager.check_file_write("worker-2", "C:/outside/output.txt")
    resource_result = manager.check_resource_access("worker-2", "registry:hklm/software")
    violations = manager.list_violations()

    assert network_result.succeeded is False
    assert file_result.succeeded is False
    assert resource_result.succeeded is False
    assert backend.terminated[0] == "worker-2"
    assert len(violations) == 3
    assert violations[0].action_taken == "terminated"
