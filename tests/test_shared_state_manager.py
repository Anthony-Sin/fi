from pathlib import Path

from desktop_automation_agent.models import SharedStateConflictPolicy
from desktop_automation_agent.shared_state_manager import SharedStateManager


def test_shared_state_manager_supports_typed_fields_and_snapshot(tmp_path):
    manager = SharedStateManager(storage_path=str(Path(tmp_path) / "shared_state.json"))
    manager.define_field(
        field_name="current_application",
        field_type="string",
        initial_value="ChatGPT",
        read_agents=["planner", "executor"],
        write_agents=["executor"],
    )

    snapshot = manager.snapshot()

    assert snapshot.succeeded is True
    assert snapshot.snapshot is not None
    assert snapshot.snapshot.fields[0].field_name == "current_application"
    assert snapshot.snapshot.fields[0].field_type == "string"


def test_shared_state_manager_enforces_read_write_permissions(tmp_path):
    manager = SharedStateManager(storage_path=str(Path(tmp_path) / "shared_state.json"))
    manager.define_field(
        field_name="secret_token",
        field_type="string",
        initial_value="abc",
        read_agents=["agent-a"],
        write_agents=["agent-a"],
    )

    denied_read = manager.read_field(agent_id="agent-b", field_name="secret_token")
    denied_write = manager.write_field(agent_id="agent-b", field_name="secret_token", value="xyz")

    assert denied_read.succeeded is False
    assert denied_write.succeeded is False
    assert denied_write.write_log is not None
    assert denied_write.write_log.accepted is False


def test_shared_state_manager_logs_successful_writes_with_agent_and_timestamp(tmp_path):
    manager = SharedStateManager(storage_path=str(Path(tmp_path) / "shared_state.json"))
    manager.define_field(
        field_name="status",
        field_type="string",
        initial_value="pending",
        write_agents=["agent-a"],
    )

    result = manager.write_field(agent_id="agent-a", field_name="status", value="done")
    logs = manager.list_write_logs()

    assert result.succeeded is True
    assert result.state_field is not None
    assert result.state_field.value == "done"
    assert len(logs) == 1
    assert logs[0].agent_id == "agent-a"


def test_shared_state_manager_supports_priority_based_conflict_resolution(tmp_path):
    manager = SharedStateManager(
        storage_path=str(Path(tmp_path) / "shared_state.json"),
        conflict_policy=SharedStateConflictPolicy.PRIORITY_BASED,
        agent_priorities={"agent-a": 1, "agent-b": 5},
    )
    manager.define_field(field_name="plan", field_type="string", initial_value="draft")

    first = manager.write_field(agent_id="agent-b", field_name="plan", value="approved")
    second = manager.write_field(agent_id="agent-a", field_name="plan", value="reverted")

    assert first.succeeded is True
    assert second.succeeded is False
    assert second.reason == "Write rejected by priority-based conflict resolution."
    current = manager.read_field(agent_id="agent-a", field_name="plan")
    assert current.state_field is not None
    assert current.state_field.value == "approved"


def test_shared_state_manager_supports_manual_conflict_resolution(tmp_path):
    reviewed = []
    manager = SharedStateManager(
        storage_path=str(Path(tmp_path) / "shared_state.json"),
        conflict_policy=SharedStateConflictPolicy.MANUAL_RESOLUTION,
        manual_resolution_callback=lambda field, value, agent_id: reviewed.append((field.field_name, value, agent_id)) or False,
    )
    manager.define_field(field_name="notes", field_type="string", initial_value="v1")
    manager.write_field(agent_id="agent-a", field_name="notes", value="v2")

    result = manager.write_field(agent_id="agent-b", field_name="notes", value="v3")

    assert reviewed == [("notes", "v3", "agent-b")]
    assert result.succeeded is False
    assert result.reason == "Write rejected during manual conflict resolution."


def test_shared_state_manager_last_write_wins_by_default(tmp_path):
    manager = SharedStateManager(storage_path=str(Path(tmp_path) / "shared_state.json"))
    manager.define_field(field_name="counter", field_type="integer", initial_value=1)
    manager.write_field(agent_id="agent-a", field_name="counter", value=2)
    manager.write_field(agent_id="agent-b", field_name="counter", value=3)

    current = manager.read_field(agent_id="agent-a", field_name="counter")

    assert current.succeeded is True
    assert current.state_field is not None
    assert current.state_field.value == 3
