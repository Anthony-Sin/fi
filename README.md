# Desktop Automation Agent

Desktop Automation Agent is a comprehensive autonomous system for controlling your entire computer environment. It goes beyond simple toolkits by providing an integrated "brain" that can switch accounts, paste prompts, navigate applications, and perform complex multi-step workflows across your desktop.

By combining UI perception, action execution, workflow coordination, and safety controls into a single agent-centric interface, it allows you to automate high-level tasks through simple instructions.

## Key Capabilities

- **Autonomous Orchestration**: Decomposes complex human instructions into executable subtasks.
- **Account & Session Management**: Seamlessly switches between accounts and manages credentials.
- **AI Interface Navigation**: Interacts with LLM interfaces, pasting prompts and extracting responses.
- **Cross-App Workflows**: Coordinates data and actions across multiple desktop applications.
- **Always-on Dashboard**: A priority overlay for real-time monitoring and interaction (text or voice).
- **Safe & Observability**: Built-in allowlists, approval gates, and detailed audit logging.

## Core Agent Interface

The primary way to interact with the system is through the `DesktopAutomationAgent`.

### Quick Start

```python
from desktop_automation_perception import DesktopAutomationAgent

# Initialize the agent
agent = DesktopAutomationAgent()

# Run a complex workflow with a single instruction
result = agent.execute(
    "Switch to my personal account, open Chrome, and ask 'What is the weather in London?'"
)

if result.succeeded:
    print("Task completed successfully!")
```

### Interactive Mode

Launch the priority overlay to talk to the agent directly:

```python
from desktop_automation_perception import DesktopAutomationAgent

agent = DesktopAutomationAgent()
agent.run_interactive()  # Opens the always-on-top dashboard
```

## Internal Architecture

While the `DesktopAutomationAgent` is the unified face, it leverages specialized modules:

- `perception`: OCR, Template Matching, and Accessibility tree analysis.
- `automation`: Application launching, navigation, and form filling.
- `accounts`: Registry, credential vault, and profile switching.
- `resilience`: Retries, rate limits, and self-healing.
- `observability`: Audit logs and failure reporting.

## Installation

```powershell
python -m pip install -e .
```

Requirements: Python 3.11+

## Development

Guidelines for contributors:

- Keep new logic agent-centric.
- Add tests for orchestrated workflows in `tests/`.
- Ensure optional dependencies (like `pywinauto` or `SpeechRecognition`) are handled gracefully.

## Validation

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```
