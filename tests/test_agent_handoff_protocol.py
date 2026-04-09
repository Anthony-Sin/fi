from datetime import datetime, timedelta
from pathlib import Path

from desktop_automation_agent.agent_handoff_protocol import AgentHandoffProtocol
from desktop_automation_agent.agent_message_bus import AgentMessageBus
from desktop_automation_agent.models import AgentHandoffContext, AgentHandoffReason


def test_agent_handoff_protocol_records_context_reason_and_instructions(tmp_path):
    protocol = AgentHandoffProtocol(storage_path=str(Path(tmp_path) / "handoffs.json"))

    result = protocol.create_handoff(
        sender_agent_id="planner",
        receiver_agent_id="executor",
        context=AgentHandoffContext(
            current_step="submit-prompt",
            collected_data={"prompt": "hello"},
            plan_description="Launch app -> submit prompt -> verify response",
        ),
        reason=AgentHandoffReason.COMPLETION,
        special_instructions="Continue with validation.",
    )

    assert result.succeeded is True
    assert result.handoff is not None
    assert result.handoff.context.current_step == "submit-prompt"
    assert result.handoff.reason is AgentHandoffReason.COMPLETION
    assert result.handoff.special_instructions == "Continue with validation."


def test_agent_handoff_protocol_tracks_overlap_period(tmp_path):
    protocol = AgentHandoffProtocol(storage_path=str(Path(tmp_path) / "handoffs.json"))

    handoff = protocol.create_handoff(
        sender_agent_id="planner",
        receiver_agent_id="executor",
        context=AgentHandoffContext(current_step="step-1"),
        reason=AgentHandoffReason.ERROR,
        overlap_seconds=60,
    ).handoff

    active = protocol.active_overlap_handoffs(agent_id="planner", as_of=datetime.utcnow() + timedelta(seconds=10))

    assert handoff is not None
    assert handoff.overlap_until is not None
    assert len(active.handoffs) == 1
    assert active.handoffs[0].handoff_id == handoff.handoff_id


def test_agent_handoff_protocol_logs_to_workflow_audit_trail(tmp_path):
    protocol = AgentHandoffProtocol(storage_path=str(Path(tmp_path) / "handoffs.json"))
    protocol.create_handoff(
        sender_agent_id="planner",
        receiver_agent_id="executor",
        context=AgentHandoffContext(current_step="step-1"),
        reason=AgentHandoffReason.CAPABILITY_MISMATCH,
    )

    handoffs = protocol.list_handoffs()

    assert len(handoffs) == 1
    assert handoffs[0].reason is AgentHandoffReason.CAPABILITY_MISMATCH


def test_agent_handoff_protocol_notifies_receiver_via_message_bus(tmp_path):
    bus = AgentMessageBus(storage_path=str(Path(tmp_path) / "bus.json"))
    protocol = AgentHandoffProtocol(
        storage_path=str(Path(tmp_path) / "handoffs.json"),
        message_bus=bus,
    )

    protocol.create_handoff(
        sender_agent_id="planner",
        receiver_agent_id="executor",
        context=AgentHandoffContext(current_step="step-2", collected_data={"x": "1"}),
        reason=AgentHandoffReason.ERROR,
        special_instructions="Investigate the timeout.",
        correlation_id="workflow-1",
    )

    messages = bus.receive_for_agent(agent_id="executor").messages

    assert len(messages) == 1
    assert messages[0].message_type == "agent_handoff"
    assert messages[0].payload["current_step"] == "step-2"
    assert messages[0].payload["special_instructions"] == "Investigate the timeout."
