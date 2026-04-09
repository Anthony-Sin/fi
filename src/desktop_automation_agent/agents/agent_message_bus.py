from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from desktop_automation_agent.models import (
    AgentBusDeliveryResult,
    AgentBusMessage,
    AgentBusSubscription,
    BusRecipientKind,
)


@dataclass(slots=True)
class AgentMessageBus:
    storage_path: str

    def publish(
        self,
        *,
        sender_id: str,
        topic: str,
        message_type: str,
        payload: dict,
        correlation_id: str,
    ) -> AgentBusDeliveryResult:
        snapshot = self._load_snapshot()
        sequence = self._next_correlation_sequence(snapshot["messages"], correlation_id)
        message = AgentBusMessage(
            message_id=str(uuid4()),
            sender_id=sender_id,
            recipient_kind=BusRecipientKind.TOPIC,
            recipient_id=topic,
            message_type=message_type,
            payload=dict(payload),
            correlation_id=correlation_id,
            correlation_sequence=sequence,
        )
        snapshot["messages"].append(self._serialize_message(message))
        self._save_snapshot(snapshot)
        return AgentBusDeliveryResult(succeeded=True, message=message)

    def send_direct(
        self,
        *,
        sender_id: str,
        recipient_id: str,
        message_type: str,
        payload: dict,
        correlation_id: str,
    ) -> AgentBusDeliveryResult:
        snapshot = self._load_snapshot()
        sequence = self._next_correlation_sequence(snapshot["messages"], correlation_id)
        message = AgentBusMessage(
            message_id=str(uuid4()),
            sender_id=sender_id,
            recipient_kind=BusRecipientKind.DIRECT,
            recipient_id=recipient_id,
            message_type=message_type,
            payload=dict(payload),
            correlation_id=correlation_id,
            correlation_sequence=sequence,
        )
        snapshot["messages"].append(self._serialize_message(message))
        self._save_snapshot(snapshot)
        return AgentBusDeliveryResult(succeeded=True, message=message)

    def subscribe(
        self,
        *,
        agent_id: str,
        topic: str,
    ) -> AgentBusSubscription:
        snapshot = self._load_snapshot()
        subscription = self._find_subscription(snapshot["subscriptions"], agent_id, topic)
        if subscription is not None:
            return self._deserialize_subscription(subscription)
        record = AgentBusSubscription(agent_id=agent_id, topic=topic)
        snapshot["subscriptions"].append(self._serialize_subscription(record))
        self._save_snapshot(snapshot)
        return record

    def receive_for_agent(
        self,
        *,
        agent_id: str,
    ) -> AgentBusDeliveryResult:
        snapshot = self._load_snapshot()
        direct_messages = [
            self._deserialize_message(item)
            for item in snapshot["messages"]
            if item["recipient_kind"] == BusRecipientKind.DIRECT.value and item["recipient_id"] == agent_id
        ]

        subscriptions = [
            self._deserialize_subscription(item)
            for item in snapshot["subscriptions"]
            if item["agent_id"] == agent_id
        ]
        topic_messages: list[AgentBusMessage] = []
        updated_subscriptions: list[AgentBusSubscription] = []

        for subscription in subscriptions:
            pending = self._pending_topic_messages(snapshot["messages"], subscription)
            topic_messages.extend(pending)
            if pending:
                subscription.last_delivered_message_id = pending[-1].message_id
            updated_subscriptions.append(subscription)

        messages = sorted(
            direct_messages + topic_messages,
            key=lambda item: (item.correlation_id, item.correlation_sequence, item.timestamp.isoformat(), item.message_id),
        )

        if updated_subscriptions:
            subscription_map = {
                (item.agent_id, item.topic): item
                for item in updated_subscriptions
            }
            new_subscriptions = []
            for raw in snapshot["subscriptions"]:
                key = (raw["agent_id"], raw["topic"])
                if key in subscription_map:
                    new_subscriptions.append(self._serialize_subscription(subscription_map[key]))
                else:
                    new_subscriptions.append(raw)
            snapshot["subscriptions"] = new_subscriptions
            self._save_snapshot(snapshot)

        return AgentBusDeliveryResult(succeeded=True, messages=messages)

    def list_messages(
        self,
        *,
        correlation_id: str | None = None,
    ) -> list[AgentBusMessage]:
        snapshot = self._load_snapshot()
        messages = [self._deserialize_message(item) for item in snapshot["messages"]]
        if correlation_id is None:
            return messages
        filtered = [item for item in messages if item.correlation_id == correlation_id]
        return sorted(filtered, key=lambda item: (item.correlation_sequence, item.timestamp.isoformat(), item.message_id))

    def _pending_topic_messages(
        self,
        raw_messages: list[dict],
        subscription: AgentBusSubscription,
    ) -> list[AgentBusMessage]:
        messages = [
            self._deserialize_message(item)
            for item in raw_messages
            if item["recipient_kind"] == BusRecipientKind.TOPIC.value and item["recipient_id"] == subscription.topic
        ]
        messages.sort(key=lambda item: (item.correlation_id, item.correlation_sequence, item.timestamp.isoformat(), item.message_id))
        if subscription.last_delivered_message_id is None:
            return messages
        delivered_seen = False
        pending: list[AgentBusMessage] = []
        for message in messages:
            if delivered_seen:
                pending.append(message)
            elif message.message_id == subscription.last_delivered_message_id:
                delivered_seen = True
        return pending

    def _next_correlation_sequence(
        self,
        raw_messages: list[dict],
        correlation_id: str,
    ) -> int:
        sequences = [int(item.get("correlation_sequence", 0)) for item in raw_messages if item.get("correlation_id") == correlation_id]
        return (max(sequences) + 1) if sequences else 1

    def _find_subscription(
        self,
        subscriptions: list[dict],
        agent_id: str,
        topic: str,
    ) -> dict | None:
        for item in subscriptions:
            if item["agent_id"] == agent_id and item["topic"] == topic:
                return item
        return None

    def _load_snapshot(self) -> dict:
        path = Path(self.storage_path)
        if not path.exists():
            return {"messages": [], "subscriptions": []}
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("messages", [])
        payload.setdefault("subscriptions", [])
        return payload

    def _save_snapshot(self, snapshot: dict) -> None:
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    def _serialize_message(self, message: AgentBusMessage) -> dict:
        return {
            "message_id": message.message_id,
            "sender_id": message.sender_id,
            "recipient_kind": message.recipient_kind.value,
            "recipient_id": message.recipient_id,
            "message_type": message.message_type,
            "payload": message.payload,
            "correlation_id": message.correlation_id,
            "correlation_sequence": message.correlation_sequence,
            "timestamp": message.timestamp.isoformat(),
        }

    def _deserialize_message(self, payload: dict) -> AgentBusMessage:
        return AgentBusMessage(
            message_id=payload["message_id"],
            sender_id=payload["sender_id"],
            recipient_kind=BusRecipientKind(payload["recipient_kind"]),
            recipient_id=payload["recipient_id"],
            message_type=payload["message_type"],
            payload=dict(payload.get("payload", {})),
            correlation_id=payload.get("correlation_id", ""),
            correlation_sequence=int(payload.get("correlation_sequence", 1)),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
        )

    def _serialize_subscription(self, subscription: AgentBusSubscription) -> dict:
        return {
            "agent_id": subscription.agent_id,
            "topic": subscription.topic,
            "subscribed_at": subscription.subscribed_at.isoformat(),
            "last_delivered_message_id": subscription.last_delivered_message_id,
        }

    def _deserialize_subscription(self, payload: dict) -> AgentBusSubscription:
        return AgentBusSubscription(
            agent_id=payload["agent_id"],
            topic=payload["topic"],
            subscribed_at=datetime.fromisoformat(payload["subscribed_at"]),
            last_delivered_message_id=payload.get("last_delivered_message_id"),
        )
