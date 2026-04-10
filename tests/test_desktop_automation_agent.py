import pytest
from unittest.mock import MagicMock
from desktop_automation_agent.agents.desktop_automation_agent import DesktopAutomationAgent
from desktop_automation_agent.models import OrchestratorSubtaskStatus


def test_desktop_automation_agent_workflow_orchestration():
    """Verifies that the main agent can correctly decompose a complex natural language task
    and orchestrate the execution across multiple registered specialist modules (Launcher, Accounts, AI Navigator)."""
    # Setup agent
    agent = DesktopAutomationAgent()

    # Mock specialists
    launcher = MagicMock()
    launcher.launch.return_value = type("Result", (), {"succeeded": True})()

    account_orchestrator = MagicMock()
    account_orchestrator.execute.return_value = type("Result", (), {"succeeded": True})()

    ai_navigator = MagicMock()
    ai_navigator.navigate.return_value = type("Result", (), {
        "succeeded": True,
        "response_text": "The weather is sunny."
    })()

    # Register specialists
    agent.register_specialist("application_launcher", launcher)
    agent.register_specialist("account_rotation_orchestrator", account_orchestrator)
    agent.register_specialist("ai_interface_navigator", ai_navigator)

    # Define a complex task
    task = "Login to my personal account, then launch Chrome, and ask 'What is the weather?'"

    # Execute
    result = agent.execute(task)

    # Verify results
    assert result.succeeded is True
    assert len(result.subtask_results) == 3

    # Verify subtask results (using ids from hierarchical decomposer)
    # The IDs will be decomp-subtask-1, decomp-subtask-2, etc.
    results_by_module = {r.responsible_module: r for r in result.subtask_results}

    # Verify subtask 1: Account
    assert "account_rotation_orchestrator" in results_by_module
    assert results_by_module["account_rotation_orchestrator"].status == OrchestratorSubtaskStatus.COMPLETED
    account_orchestrator.execute.assert_called_once()

    # Verify subtask 2: Launcher
    assert "application_launcher" in results_by_module
    launcher.launch.assert_called_once()

    # Verify subtask 3: AI Navigator
    assert "ai_interface_navigator" in results_by_module
    ai_navigator.navigate.assert_called_once()


def test_desktop_automation_agent_fallback_to_simulation():
    """Verifies that the agent can fall back to an AI-driven vision simulation (via Gemini)
    when a task requires a specialist module that is not currently registered or available."""
    agent = DesktopAutomationAgent()
    agent.api_key = "mock-key"

    # Register only one specialist
    launcher = MagicMock()
    launcher.launch.return_value = type("Result", (), {"succeeded": True})()
    agent.register_specialist("application_launcher", launcher)

    # Mock AI provider
    from unittest.mock import patch
    with patch("desktop_automation_agent.ai.gemini_provider.GeminiProvider") as mock_gen:
        instance = mock_gen.return_value
        instance.analyze_image.return_value = '{"succeeded": true, "summary": "AI simulated execution"}'
        instance.get_token_count.return_value = 10

        # Task with a module that isn't registered
        task = "Launch Notepad and then write 'Hello World'"

        result = agent.execute(task)

        assert result.succeeded is True
        assert len(result.subtask_results) == 2

        # First subtask should use registered launcher
        assert result.subtask_results[0].responsible_module == "application_launcher"
        launcher.launch.assert_called_once()

        # Second subtask (write) maps to navigation/desktop_automation and should use AI fallback
        assert result.subtask_results[1].responsible_module == "ai_vision_fallback"
        assert "AI simulated execution" in result.subtask_results[1].produced_outputs["decomp-subtask-2_result"]
