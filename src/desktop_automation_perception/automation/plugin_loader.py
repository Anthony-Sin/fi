from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from desktop_automation_perception.models import (
    AutomationPluginDeclaration,
    AutomationPluginExecutionResult,
    AutomationPluginLoaderResult,
    AutomationPluginRecord,
    PluginActionRoute,
)


@dataclass(slots=True)
class PluginActionRouter:
    _routes: dict[str, PluginActionRoute] = field(default_factory=dict, init=False, repr=False)

    def register_plugin(self, plugin: AutomationPluginRecord) -> list[PluginActionRoute]:
        routes: list[PluginActionRoute] = []
        for action_type in plugin.declaration.supported_action_types:
            route = PluginActionRoute(
                action_type=action_type,
                plugin_name=plugin.declaration.name,
                module_path=plugin.module_path,
            )
            self._routes[action_type] = route
            routes.append(route)
        return routes

    def unregister_plugin(self, plugin_name: str) -> list[str]:
        removed = [action_type for action_type, route in self._routes.items() if route.plugin_name == plugin_name]
        for action_type in removed:
            self._routes.pop(action_type, None)
        return removed

    def route_for_action(self, action_type: str) -> PluginActionRoute | None:
        return self._routes.get(action_type)

    def list_routes(self) -> list[PluginActionRoute]:
        return sorted(self._routes.values(), key=lambda item: item.action_type)


@dataclass(slots=True)
class AutomationPluginLoader:
    plugin_directory: str
    action_router: PluginActionRouter | None = None
    startup_context: dict[str, Any] = field(default_factory=dict)
    _plugins: dict[str, AutomationPluginRecord] = field(default_factory=dict, init=False, repr=False)
    _modules: dict[str, ModuleType] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.action_router = self.action_router or PluginActionRouter()

    def discover_plugins(self) -> AutomationPluginLoaderResult:
        self._plugins.clear()
        self._modules.clear()
        discovered: list[AutomationPluginRecord] = []
        for path in self._iter_plugin_paths():
            plugin_name = path.stem
            module = self._load_module(path, plugin_name)
            declaration = self._read_declaration(module)
            validation_error = self._validate_declaration(declaration)
            if validation_error is not None:
                return AutomationPluginLoaderResult(succeeded=False, reason=validation_error)
            record = AutomationPluginRecord(
                declaration=declaration,
                module_path=str(path),
                module_name=module.__name__,
                loaded_at=datetime.now(timezone.utc),
                initialized=False,
                file_mtime=path.stat().st_mtime,
            )
            self._plugins[declaration.name] = record
            self._modules[declaration.name] = module
            discovered.append(record)
        discovered.sort(key=lambda item: item.declaration.name)
        return AutomationPluginLoaderResult(succeeded=True, plugins=discovered, routes=self.action_router.list_routes())

    def initialize_plugins(self, *, context: dict[str, Any] | None = None) -> AutomationPluginLoaderResult:
        context_payload = dict(self.startup_context)
        context_payload.update(context or {})
        initialized: list[AutomationPluginRecord] = []
        for name, plugin in sorted(self._plugins.items()):
            module = self._modules[name]
            initialize_fn = getattr(module, plugin.declaration.initialize_entry_point, None)
            if callable(initialize_fn):
                initialize_fn(context_payload)
            plugin.initialized = True
            plugin.loaded_at = datetime.now(timezone.utc)
            self.action_router.register_plugin(plugin)
            initialized.append(plugin)
        return AutomationPluginLoaderResult(
            succeeded=True,
            plugins=initialized,
            routes=self.action_router.list_routes(),
        )

    def execute_action(
        self,
        action_type: str,
        *,
        payload: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AutomationPluginLoaderResult:
        route = self.action_router.route_for_action(action_type)
        if route is None:
            return AutomationPluginLoaderResult(
                succeeded=False,
                reason=f"No plugin is registered for action type '{action_type}'.",
            )
        plugin = self._plugins.get(route.plugin_name)
        module = self._modules.get(route.plugin_name)
        if plugin is None or module is None:
            return AutomationPluginLoaderResult(
                succeeded=False,
                reason=f"Plugin '{route.plugin_name}' is not loaded.",
            )
        execute_fn = getattr(module, plugin.declaration.execute_entry_point, None)
        if not callable(execute_fn):
            return AutomationPluginLoaderResult(
                succeeded=False,
                route=route,
                reason=f"Plugin '{plugin.declaration.name}' is missing execute entry point '{plugin.declaration.execute_entry_point}'.",
            )
        raw_result = execute_fn(action_type, dict(payload or {}), dict(context or {}))
        normalized = self._normalize_execution_result(
            raw_result,
            plugin_name=plugin.declaration.name,
            action_type=action_type,
        )
        return AutomationPluginLoaderResult(
            succeeded=normalized.succeeded,
            plugin=plugin,
            route=route,
            execution_result=normalized,
            reason=normalized.reason,
        )

    def reload_plugins(self, *, context: dict[str, Any] | None = None) -> AutomationPluginLoaderResult:
        context_payload = dict(self.startup_context)
        context_payload.update(context or {})
        current_paths = {path.resolve(): path for path in self._iter_plugin_paths()}
        reloaded_plugin_names: list[str] = []
        removed_plugin_names: list[str] = []

        active_paths = {Path(record.module_path).resolve(): name for name, record in self._plugins.items()}
        for resolved_path, plugin_name in list(active_paths.items()):
            if resolved_path not in current_paths:
                self._teardown_plugin(plugin_name, context_payload)
                removed_plugin_names.append(plugin_name)

        for path in current_paths.values():
            matched_name = next((name for name, record in self._plugins.items() if Path(record.module_path).resolve() == path.resolve()), None)
            if matched_name is None:
                load_result = self._load_single_plugin(path, context_payload)
                if not load_result.succeeded:
                    return load_result
                reloaded_plugin_names.append(load_result.plugin.declaration.name if load_result.plugin else path.stem)
                continue

            record = self._plugins[matched_name]
            current_mtime = path.stat().st_mtime
            if record.file_mtime is None or current_mtime > record.file_mtime:
                self._teardown_plugin(matched_name, context_payload)
                load_result = self._load_single_plugin(path, context_payload, existing_name=matched_name)
                if not load_result.succeeded:
                    return load_result
                reloaded_plugin_names.append(load_result.plugin.declaration.name if load_result.plugin else matched_name)

        return AutomationPluginLoaderResult(
            succeeded=True,
            plugins=self.list_plugins().plugins,
            routes=self.action_router.list_routes(),
            reloaded_plugin_names=reloaded_plugin_names,
            removed_plugin_names=removed_plugin_names,
        )

    def list_plugins(self) -> AutomationPluginLoaderResult:
        plugins = sorted(self._plugins.values(), key=lambda item: item.declaration.name)
        return AutomationPluginLoaderResult(succeeded=True, plugins=plugins, routes=self.action_router.list_routes())

    def _load_single_plugin(
        self,
        path: Path,
        context_payload: dict[str, Any],
        *,
        existing_name: str | None = None,
    ) -> AutomationPluginLoaderResult:
        module = self._load_module(path, path.stem)
        declaration = self._read_declaration(module)
        validation_error = self._validate_declaration(declaration)
        if validation_error is not None:
            return AutomationPluginLoaderResult(succeeded=False, reason=validation_error)
        old_name = existing_name or declaration.name
        if existing_name is not None and existing_name != declaration.name:
            self._plugins.pop(existing_name, None)
            self._modules.pop(existing_name, None)
            self.action_router.unregister_plugin(existing_name)
        record = AutomationPluginRecord(
            declaration=declaration,
            module_path=str(path),
            module_name=module.__name__,
            loaded_at=datetime.now(timezone.utc),
            initialized=False,
            last_reloaded_at=datetime.now(timezone.utc),
            file_mtime=path.stat().st_mtime,
        )
        self._plugins[declaration.name] = record
        self._modules[declaration.name] = module
        initialize_fn = getattr(module, declaration.initialize_entry_point, None)
        if callable(initialize_fn):
            initialize_fn(context_payload)
        record.initialized = True
        self.action_router.register_plugin(record)
        return AutomationPluginLoaderResult(
            succeeded=True,
            plugin=record,
            plugins=self.list_plugins().plugins,
            routes=self.action_router.list_routes(),
        )

    def _teardown_plugin(self, plugin_name: str, context_payload: dict[str, Any]) -> None:
        record = self._plugins.get(plugin_name)
        module = self._modules.get(plugin_name)
        if record is not None and module is not None:
            teardown_fn = getattr(module, record.declaration.teardown_entry_point, None)
            if callable(teardown_fn):
                teardown_fn(context_payload)
        self.action_router.unregister_plugin(plugin_name)
        record = self._plugins.pop(plugin_name, None)
        if record is not None:
            sys.modules.pop(record.module_name, None)
        self._modules.pop(plugin_name, None)

    def _iter_plugin_paths(self) -> list[Path]:
        plugin_dir = Path(self.plugin_directory)
        if not plugin_dir.exists():
            return []
        return sorted(
            [
                path
                for path in plugin_dir.glob("*.py")
                if path.name != "__init__.py" and not path.name.startswith("_")
            ],
            key=lambda item: item.name,
        )

    def _load_module(self, path: Path, plugin_hint: str) -> ModuleType:
        unique_name = f"desktop_automation_perception.dynamic_plugins.{plugin_hint}_{abs(hash((str(path), path.stat().st_mtime_ns)))}"
        spec = importlib.util.spec_from_file_location(unique_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Unable to create module spec for plugin: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        spec.loader.exec_module(module)
        return module

    def _read_declaration(self, module: ModuleType) -> AutomationPluginDeclaration:
        declaration = getattr(module, "PLUGIN_DECLARATION", None)
        if isinstance(declaration, AutomationPluginDeclaration):
            return declaration
        if isinstance(declaration, dict):
            return AutomationPluginDeclaration(
                name=str(declaration["name"]),
                supported_action_types=[str(item) for item in declaration.get("supported_action_types", [])],
                required_permissions=[str(item) for item in declaration.get("required_permissions", [])],
                initialize_entry_point=str(declaration.get("initialize_entry_point", "initialize")),
                execute_entry_point=str(declaration.get("execute_entry_point", "execute")),
                teardown_entry_point=str(declaration.get("teardown_entry_point", "teardown")),
                version=str(declaration["version"]) if declaration.get("version") is not None else None,
                description=str(declaration["description"]) if declaration.get("description") is not None else None,
            )
        raise ValueError(f"Plugin module '{module.__name__}' is missing a valid PLUGIN_DECLARATION.")

    def _validate_declaration(self, declaration: AutomationPluginDeclaration) -> str | None:
        if not declaration.name.strip():
            return "Plugin declaration is missing a non-empty name."
        if not declaration.supported_action_types:
            return f"Plugin '{declaration.name}' must declare at least one supported action type."
        if len(set(declaration.supported_action_types)) != len(declaration.supported_action_types):
            return f"Plugin '{declaration.name}' declares duplicate action types."
        for plugin in self._plugins.values():
            if plugin.declaration.name == declaration.name:
                continue
            overlap = set(plugin.declaration.supported_action_types).intersection(declaration.supported_action_types)
            if overlap:
                return (
                    f"Plugin '{declaration.name}' conflicts with plugin '{plugin.declaration.name}' "
                    f"for action types: {', '.join(sorted(overlap))}."
                )
        for action_type in declaration.supported_action_types:
            route = self.action_router.route_for_action(action_type)
            if route is not None and route.plugin_name != declaration.name:
                return (
                    f"Action type '{action_type}' is already registered by plugin '{route.plugin_name}'."
                )
        return None

    def _normalize_execution_result(
        self,
        raw_result: Any,
        *,
        plugin_name: str,
        action_type: str,
    ) -> AutomationPluginExecutionResult:
        if isinstance(raw_result, AutomationPluginExecutionResult):
            return raw_result
        if isinstance(raw_result, dict):
            succeeded = bool(raw_result.get("succeeded", True))
            reason = raw_result.get("reason")
            payload = {str(key): value for key, value in raw_result.items() if key not in {"succeeded", "reason"}}
            return AutomationPluginExecutionResult(
                succeeded=succeeded,
                plugin_name=plugin_name,
                action_type=action_type,
                result_payload=payload,
                reason=reason,
            )
        return AutomationPluginExecutionResult(
            succeeded=raw_result is not False,
            plugin_name=plugin_name,
            action_type=action_type,
            result_payload={"result": raw_result} if raw_result is not None and raw_result is not True and raw_result is not False else {},
            reason=None,
        )
