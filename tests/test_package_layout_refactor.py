from pathlib import Path


def test_grouped_package_entrypoints_expose_expected_modules():
    from desktop_automation_agent.accounts import AccountRegistry, CredentialVault
    from desktop_automation_agent.agents import AgentMessageBus, OrchestratorAgentCore
    from desktop_automation_agent.ai import AIInterfaceNavigator, PromptPipelineRunner
    from desktop_automation_agent.automation import DataExportPipeline, StructuredDataExtractor
    from desktop_automation_agent.desktop import AccessibilityTreeReader, ScreenStateVerifier
    from desktop_automation_agent.knowledge import ActionHistoryAnalyzer, WorkflowSkillStore
    from desktop_automation_agent.observability import PerformanceMetricsCollector, WorkflowAuditLogger
    from desktop_automation_agent.resilience import ExponentialBackoffRetryEngine, SelfHealingRecoveryModule

    assert AccountRegistry is not None
    assert CredentialVault is not None
    assert AgentMessageBus is not None
    assert OrchestratorAgentCore is not None
    assert AIInterfaceNavigator is not None
    assert PromptPipelineRunner is not None
    assert DataExportPipeline is not None
    assert StructuredDataExtractor is not None
    assert AccessibilityTreeReader is not None
    assert ScreenStateVerifier is not None
    assert ActionHistoryAnalyzer is not None
    assert WorkflowSkillStore is not None
    assert PerformanceMetricsCollector is not None
    assert WorkflowAuditLogger is not None
    assert ExponentialBackoffRetryEngine is not None
    assert SelfHealingRecoveryModule is not None


def test_source_files_do_not_contain_standalone_ellipsis_placeholders():
    source_root = Path(__file__).resolve().parents[1] / "src" / "desktop_automation_agent"
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip() == "...":
                offenders.append(f"{path.name}:{line_number}")
    assert offenders == []
