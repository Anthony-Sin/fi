from desktop_automation_perception.agents import IPCModule
from desktop_automation_perception.models import IPCChannelType, IPCMessage, RetryConfiguration


class FakeIPCBackend:
    def __init__(self):
        self.connections = {}
        self.sent = {}
        self.connect_attempts = {}
        self.fail_connect_once = set()
        self.fail_send_once = set()
        self.received = {}

    def connect(self, endpoint: str):
        attempts = self.connect_attempts.get(endpoint, 0) + 1
        self.connect_attempts[endpoint] = attempts
        if endpoint in self.fail_connect_once and attempts == 1:
            raise ConnectionError(f"Unable to connect to {endpoint}")
        handle = f"handle:{endpoint}"
        self.connections[endpoint] = handle
        return handle

    def send(self, handle: str, message: IPCMessage):
        if handle in self.fail_send_once:
            self.fail_send_once.remove(handle)
            raise BrokenPipeError(f"Broken channel for {handle}")
        self.sent.setdefault(handle, []).append(message)

    def receive(self, handle: str):
        return list(self.received.get(handle, []))

    def disconnect(self, handle: str):
        self.connections = {endpoint: current for endpoint, current in self.connections.items() if current != handle}


def make_module(tmp_path, *, process_id="orchestrator", channel_type=IPCChannelType.LOCAL_SOCKET):
    backend = FakeIPCBackend()
    module = IPCModule(
        storage_path=str(tmp_path / "ipc.json"),
        process_id=process_id,
        channel_type=channel_type,
        local_socket_backend=backend,
        named_pipe_backend=backend,
        retry_configuration=RetryConfiguration(max_retry_count=1, initial_delay_seconds=0.0, max_delay_seconds=0.0),
    )
    return module, backend


def test_ipc_module_connects_and_sends_message_with_protocol_fields(tmp_path):
    module, backend = make_module(tmp_path)
    connect_result = module.connect(remote_process_id="worker-1", endpoint="socket://worker-1")
    send_result = module.send_command(
        remote_process_id="worker-1",
        message_type="execute_task",
        payload={"task_id": "task-1"},
        correlation_id="corr-1",
    )

    assert connect_result.succeeded is True
    assert send_result.succeeded is True
    assert send_result.message is not None
    assert send_result.message.message_type == "execute_task"
    assert send_result.message.payload == {"task_id": "task-1"}
    assert send_result.message.correlation_id == "corr-1"
    assert backend.sent["handle:socket://worker-1"][0].recipient_id == "worker-1"


def test_ipc_module_receives_messages_from_connected_process(tmp_path):
    module, backend = make_module(tmp_path)
    module.connect(remote_process_id="worker-1", endpoint="socket://worker-1")
    backend.received["handle:socket://worker-1"] = [
        IPCMessage(
            message_id="msg-1",
            sender_id="worker-1",
            recipient_id="orchestrator",
            message_type="task_complete",
            payload={"task_id": "task-9"},
            correlation_id="corr-9",
        )
    ]

    result = module.receive_messages(remote_process_id="worker-1")

    assert result.succeeded is True
    assert len(result.messages) == 1
    assert result.messages[0].message_type == "task_complete"
    assert result.messages[0].payload == {"task_id": "task-9"}


def test_ipc_module_reconnects_after_initial_connection_failure(tmp_path):
    module, backend = make_module(tmp_path)
    backend.fail_connect_once.add("pipe://worker-2")

    result = module.connect(remote_process_id="worker-2", endpoint="pipe://worker-2")

    assert result.succeeded is True
    assert backend.connect_attempts["pipe://worker-2"] == 2
    assert result.connection is not None and result.connection.connected is True


def test_ipc_module_reconnects_when_send_hits_broken_channel(tmp_path):
    module, backend = make_module(tmp_path)
    module.connect(remote_process_id="worker-1", endpoint="socket://worker-1")
    backend.fail_send_once.add("handle:socket://worker-1")

    result = module.send_command(
        remote_process_id="worker-1",
        message_type="ping",
        payload={"sequence": 1},
        correlation_id="corr-ping",
    )

    assert result.succeeded is True
    assert backend.connect_attempts["socket://worker-1"] >= 2
    assert result.message is not None and result.message.message_type == "ping"


def test_ipc_module_broadcasts_command_to_all_connected_workers(tmp_path):
    module, backend = make_module(tmp_path, channel_type=IPCChannelType.NAMED_PIPE)
    module.connect(remote_process_id="worker-1", endpoint="pipe://worker-1")
    module.connect(remote_process_id="worker-2", endpoint="pipe://worker-2")

    result = module.broadcast_command(
        message_type="refresh_state",
        payload={"source": "orchestrator"},
        correlation_id="corr-broadcast",
    )

    assert result.succeeded is True
    assert len(result.messages) == 2
    assert {message.recipient_id for message in result.messages} == {"worker-1", "worker-2"}
    assert len(backend.sent["handle:pipe://worker-1"]) == 1
    assert len(backend.sent["handle:pipe://worker-2"]) == 1
