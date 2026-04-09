# Desktop Automation Perception

Desktop Automation Perception is a Python toolkit for building desktop automation pipelines. It combines UI perception, action execution, workflow coordination, safety controls, and observability in one package.

This repository is a library, not a single long-running service. Most components are small, composable building blocks that you wire together for your own automation workflows.

## What The Package Provides

- Desktop perception primitives for accessibility trees, OCR, template matching, AI-assisted analysis, waits, and screen-change detection
- Action and navigation utilities for input simulation, application launching, form automation, workflow sequencing, and cross-application coordination
- Safety and recovery modules for allowlists, approvals, anti-loop protection, checkpointing, idempotency, retrying, rate limiting, and self-healing
- Observability tooling for audit logs, failure captures, metrics, dashboards, reports, watchdogs, and database-backed logging
- Account, credential, and session helpers for multi-account workflows
- Agent and orchestration helpers for message buses, task decomposition, worker pools, and shared state
- AI workflow helpers for prompt templates, prompt pipelines, response extraction, and LangGraph/MCP integration

## Package Layout

The public API is available through both grouped domain packages and backward-compatible root imports.

- `desktop_automation_perception.desktop`
  Desktop perception, input, window, display, clipboard, waits, and matching backends
- `desktop_automation_perception.automation`
  Workflow execution, navigation, data extraction/export, plugins, hybrid automation, LangGraph, and MCP adapters
- `desktop_automation_perception.resilience`
  Allowlists, approvals, retrying, checkpointing, anti-loop detection, rate limiting, and recovery
- `desktop_automation_perception.observability`
  Audit logging, reports, metrics, dashboard data, watchdogs, and failure recorders
- `desktop_automation_perception.accounts`
  Registries, credential vaults, browser profiles, load balancing, and session tracking
- `desktop_automation_perception.ai`
  Prompt templates, prompt pipelines, response extraction, and AI-interface navigation
- `desktop_automation_perception.agents`
  Worker pools, agent routing, message buses, handoffs, IPC, and shared state
- `desktop_automation_perception.knowledge`
  Memory, history analysis, feedback collection, and workflow skill storage
- `desktop_automation_perception.models`
  Shared dataclasses and enums used across the library
- `desktop_automation_perception.contracts`
  Common runtime contracts used to plug in concrete implementations cleanly

## Installation

Requirements:

- Python 3.11 or newer

Install the package in editable mode:

```powershell
python -m pip install -e .
```

Run the test suite:

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```

Verify the package compiles cleanly:

```powershell
python -m compileall src
```

## Optional Runtime Backends

The base package has no hard runtime dependencies in `pyproject.toml`, but several adapters import third-party tools lazily when you use them.

Common examples:

- `requests` for REST and external credential fetching
- `pyautogui` for screen capture and input automation
- `Pillow` for image and screenshot handling
- `opencv-python` for template matching
- Tesseract plus a Python OCR wrapper for OCR extraction
- `pywin32` for COM and some Windows-specific automation backends

Install only the backends your workflow needs.

## Typical Workflow

Most production workflows follow the same shape:

1. Capture or inspect desktop/application state.
2. Locate elements or verify conditions.
3. Execute an action or a navigation step.
4. Guard execution with allowlists, checkpoints, retries, and rate limits.
5. Record telemetry, audit trails, and failure artifacts.

The library is intentionally modular, so you can adopt only the pieces you need.

## Quick Examples

### Wait For Text With `SmartWaitEngine`

```python
from desktop_automation_perception import SmartWaitEngine, SmartWaitRequest, WaitType

engine = SmartWaitEngine(ocr_extractor=my_ocr_extractor)

result = engine.wait_until_text_visible(
    SmartWaitRequest(
        wait_id="ready-banner",
        wait_type=WaitType.TEXT_VISIBLE,
        target_text="Ready",
        timeout_seconds=5.0,
        polling_interval_seconds=0.25,
    )
)

if not result.succeeded:
    raise RuntimeError(result.detail)
```

### Enforce Action Throughput With `RateLimiter`

```python
from desktop_automation_perception import (
    RateLimiter,
    RateLimitRequest,
    RateLimitRule,
    RateLimitScope,
    RateLimitWindow,
)

limiter = RateLimiter(
    storage_path="data/rate_limiter.json",
    rules=[
        RateLimitRule(
            scope=RateLimitScope.ACTION_TYPE,
            key="submit",
            limit=5,
            window=RateLimitWindow.MINUTE,
        )
    ],
)

decision = limiter.submit_request(
    RateLimitRequest(
        request_id="request-1",
        action_type="submit",
        payload={"record_id": "abc-123"},
    )
)

print(decision.allowed, decision.queued)
```

### Load And Execute Automation Plugins

```python
from desktop_automation_perception import AutomationPluginLoader

loader = AutomationPluginLoader(plugin_directory="plugins")

discovered = loader.discover_plugins()
if not discovered.succeeded:
    raise RuntimeError(discovered.reason)

loader.initialize_plugins(context={"run_mode": "production"})
result = loader.execute_action("sample_action", payload={"value": "hello"})

print(result.succeeded, result.reason)
```

### Wire A LangGraph-Friendly State Adapter

```python
from desktop_automation_perception import LangGraphStateConnector, WorkflowContext

connector = LangGraphStateConnector(
    perception_engine=my_perception_engine,
    element_locator=my_element_locator,
    input_runner=my_input_runner,
    workflow_id="wf-demo",
)

state = connector.create_initial_state(
    workflow_context=WorkflowContext(current_application="Editor"),
    extra_state={
        "automation_inputs": {
            "find_element": {"text": "Continue"},
            "type": {"text": "hello world", "application_name": "Editor"},
        }
    },
)

nodes = connector.build_nodes()
state = nodes["find_element"](state)
state = nodes["type"](state)
```

## Development Notes

Repository layout:

- `src/desktop_automation_perception/`
  Package source
- `tests/`
  Canonical pytest suite
- `tests_artifacts/`
  Stable test output fixtures used by a subset of tests

Guidelines for contributors:

- Keep implementation code under `src/desktop_automation_perception`
- Add or update tests under `tests`
- Prefer the grouped subpackages for new imports
- Preserve backward-compatible root imports unless a deliberate breaking change is planned
- Use persistent storage paths deliberately; many modules write JSON or SQLite state to disk

## Validation Checklist

Before shipping changes, run at least:

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
python -m compileall src
```

For behavior changes, also validate the specific end-to-end path you modified:

- Perception path: capture, locate, and verify
- Action path: input execution, navigation, or export
- Safety path: allowlist, retry, checkpoint, idempotency, and rate limit behavior
- Observability path: logs, metrics, failure capture, and report generation

## Production Readiness Notes

This repository is production-ready as a library when it is embedded into a workflow with the right concrete backends and storage choices.

Recommended practices:

- Use explicit storage paths for every stateful module
- Turn on allowlists, checkpoints, idempotency, and watchdogs for destructive workflows
- Use real audit logging and failure recording for long-running automations
- Install only the runtime backends your deployment requires
- Treat credentials and sensitive prompts as secure data and route them through the vault/protection modules
- Re-run the full test suite after dependency, persistence, or workflow changes

## Backward Compatibility

Legacy root-level imports are still supported. For example, both of these styles work:

```python
from desktop_automation_perception import RateLimiter
from desktop_automation_perception.resilience import RateLimiter
```

New code should prefer grouped imports because they make module ownership and dependencies easier to understand.
