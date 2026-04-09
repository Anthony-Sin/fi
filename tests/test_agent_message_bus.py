from pathlib import Path

from desktop_automation_perception.models import BusRecipientKind
from desktop_automation_perception.agent_message_bus import AgentMessageBus


def test_agent_message_bus_supports_topic_publish_subscribe(tmp_path):
    bus = AgentMessageBus(storage_path=str(Path(tmp_path) / "bus.json"))
    bus.subscribe(agent_id="agent-b", topic="planning")
    bus.publish(
        sender_id="agent-a",
        topic="planning",
        message_type="plan_ready",
        payload={"step": "draft"},
        correlation_id="corr-1",
    )

    result = bus.receive_for_agent(agent_id="agent-b")

    assert result.succeeded is True
    assert len(result.messages) == 1
    assert result.messages[0].recipient_kind is BusRecipientKind.TOPIC
    assert result.messages[0].payload["step"] == "draft"


def test_agent_message_bus_supports_direct_messages(tmp_path):
    bus = AgentMessageBus(storage_path=str(Path(tmp_path) / "bus.json"))
    bus.send_direct(
        sender_id="agent-a",
        recipient_id="agent-c",
        message_type="request",
        payload={"need": "review"},
        correlation_id="corr-2",
    )

    result = bus.receive_for_agent(agent_id="agent-c")

    assert len(result.messages) == 1
    assert result.messages[0].recipient_kind is BusRecipientKind.DIRECT
    assert result.messages[0].recipient_id == "agent-c"


def test_agent_message_bus_replays_missed_topic_messages_for_late_subscriber(tmp_path):
    bus = AgentMessageBus(storage_path=str(Path(tmp_path) / "bus.json"))
    bus.publish(
        sender_id="agent-a",
        topic="status",
        message_type="started",
        payload={"value": 1},
        correlation_id="corr-3",
    )
    bus.subscribe(agent_id="agent-b", topic="status")

    result = bus.receive_for_agent(agent_id="agent-b")

    assert len(result.messages) == 1
    assert result.messages[0].message_type == "started"


def test_agent_message_bus_enforces_ordering_within_correlation_chain(tmp_path):
    bus = AgentMessageBus(storage_path=str(Path(tmp_path) / "bus.json"))
    bus.subscribe(agent_id="agent-b", topic="planning")
    bus.publish(
        sender_id="agent-a",
        topic="planning",
        message_type="one",
        payload={"idx": 1},
        correlation_id="corr-4",
    )
    bus.publish(
        sender_id="agent-a",
        topic="planning",
        message_type="two",
        payload={"idx": 2},
        correlation_id="corr-4",
    )
    bus.publish(
        sender_id="agent-a",
        topic="planning",
        message_type="other",
        payload={"idx": 1},
        correlation_id="corr-5",
    )

    result = bus.receive_for_agent(agent_id="agent-b")
    corr4 = [message for message in result.messages if message.correlation_id == "corr-4"]

    assert [message.correlation_sequence for message in corr4] == [1, 2]


def test_agent_message_bus_persists_message_history_and_subscription_progress(tmp_path):
    bus = AgentMessageBus(storage_path=str(Path(tmp_path) / "bus.json"))
    bus.subscribe(agent_id="agent-b", topic="updates")
    bus.publish(
        sender_id="agent-a",
        topic="updates",
        message_type="first",
        payload={},
        correlation_id="corr-6",
    )
    first = bus.receive_for_agent(agent_id="agent-b")
    second = bus.receive_for_agent(agent_id="agent-b")
    history = bus.list_messages(correlation_id="corr-6")

    assert len(first.messages) == 1
    assert second.messages == []
    assert len(history) == 1
    assert history[0].message_type == "first"
