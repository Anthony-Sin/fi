from __future__ import annotations

import os
import time
from pathlib import Path

from desktop_automation_perception.automation import AutomationPluginLoader, PluginActionRouter


def _write_plugin(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    os.utime(path, None)


def test_plugin_loader_discovers_initializes_and_routes_actions(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    log_path = tmp_path / "plugin.log"
    _write_plugin(
        plugin_dir / "sample_plugin.py",
        """
PLUGIN_DECLARATION = {
    "name": "sample-plugin",
    "supported_action_types": ["sample_action"],
    "required_permissions": ["screen_capture"],
}

def initialize(context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write("init\\n")

def execute(action_type, payload, context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write(f"execute:{action_type}:{payload['value']}\\n")
    return {"handled": True, "echo": payload["value"]}

def teardown(context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write("teardown\\n")
""".strip(),
    )

    loader = AutomationPluginLoader(
        plugin_directory=str(plugin_dir),
        action_router=PluginActionRouter(),
    )

    discovered = loader.discover_plugins()
    initialized = loader.initialize_plugins(context={"log_path": str(log_path)})
    executed = loader.execute_action(
        "sample_action",
        payload={"value": "hello"},
        context={"log_path": str(log_path)},
    )

    assert discovered.succeeded is True
    assert len(discovered.plugins) == 1
    assert discovered.plugins[0].declaration.name == "sample-plugin"
    assert initialized.succeeded is True
    assert initialized.routes[0].action_type == "sample_action"
    assert executed.succeeded is True
    assert executed.execution_result is not None
    assert executed.execution_result.result_payload["handled"] is True
    assert executed.execution_result.result_payload["echo"] == "hello"
    assert log_path.read_text(encoding="utf-8").splitlines() == ["init", "execute:sample_action:hello"]


def test_plugin_loader_rejects_conflicting_action_types(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(
        plugin_dir / "plugin_a.py",
        """
PLUGIN_DECLARATION = {
    "name": "plugin-a",
    "supported_action_types": ["shared_action"],
    "required_permissions": [],
}

def execute(action_type, payload, context):
    return {"plugin": "a"}
""".strip(),
    )
    _write_plugin(
        plugin_dir / "plugin_b.py",
        """
PLUGIN_DECLARATION = {
    "name": "plugin-b",
    "supported_action_types": ["shared_action"],
    "required_permissions": [],
}

def execute(action_type, payload, context):
    return {"plugin": "b"}
""".strip(),
    )

    loader = AutomationPluginLoader(plugin_directory=str(plugin_dir))

    result = loader.discover_plugins()

    assert result.succeeded is False
    assert "conflicts with plugin" in (result.reason or "")


def test_plugin_loader_hot_reloads_modified_plugins_without_stopping_pipeline(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    log_path = tmp_path / "reload.log"
    plugin_path = plugin_dir / "reloadable.py"
    _write_plugin(
        plugin_path,
        """
PLUGIN_DECLARATION = {
    "name": "reloadable-plugin",
    "supported_action_types": ["reload_action"],
    "required_permissions": ["ui_control"],
}

def initialize(context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write("init:v1\\n")

def execute(action_type, payload, context):
    return {"version": "v1"}

def teardown(context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write("teardown:v1\\n")
""".strip(),
    )

    loader = AutomationPluginLoader(plugin_directory=str(plugin_dir))
    loader.discover_plugins()
    loader.initialize_plugins(context={"log_path": str(log_path)})
    first = loader.execute_action("reload_action")
    assert first.execution_result is not None
    assert first.execution_result.result_payload["version"] == "v1"

    time.sleep(1.1)
    _write_plugin(
        plugin_path,
        """
PLUGIN_DECLARATION = {
    "name": "reloadable-plugin",
    "supported_action_types": ["reload_action", "reload_action_v2"],
    "required_permissions": ["ui_control"],
}

def initialize(context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write("init:v2\\n")

def execute(action_type, payload, context):
    return {"version": "v2", "action_type": action_type}

def teardown(context):
    with open(context["log_path"], "a", encoding="utf-8") as handle:
        handle.write("teardown:v2\\n")
""".strip(),
    )

    reloaded = loader.reload_plugins(context={"log_path": str(log_path)})
    second = loader.execute_action("reload_action_v2")

    assert reloaded.succeeded is True
    assert reloaded.reloaded_plugin_names == ["reloadable-plugin"]
    assert second.execution_result is not None
    assert second.execution_result.result_payload["version"] == "v2"
    assert second.execution_result.result_payload["action_type"] == "reload_action_v2"
    assert "reload_action_v2" in {route.action_type for route in loader.list_plugins().routes}
    assert log_path.read_text(encoding="utf-8").splitlines() == ["init:v1", "teardown:v1", "init:v2"]
