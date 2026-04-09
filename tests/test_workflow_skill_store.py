from pathlib import Path

from desktop_automation_agent.models import WorkflowSkillStep
from desktop_automation_agent.workflow_skill_store import WorkflowSkillStore


def test_workflow_skill_store_records_successful_workflow(tmp_path):
    store = WorkflowSkillStore(storage_path=str(Path(tmp_path) / "skills.json"))

    result = store.record_successful_workflow(
        workflow_name="Invoice Workflow",
        description="Process incoming invoices",
        steps=[
            WorkflowSkillStep("open-mail", {"folder": "Inbox"}),
            WorkflowSkillStep("extract-amount", {"model": "ocr"}),
        ],
        execution_time_seconds=12.5,
        contextual_notes="Used the finance mailbox.",
        task_description="Process a new invoice from email and extract payment details.",
    )

    assert result.succeeded is True
    assert result.skill is not None
    assert result.skill.current_version == 1
    assert result.skill.versions[0].steps[0].step_name == "open-mail"


def test_workflow_skill_store_versions_existing_skill(tmp_path):
    store = WorkflowSkillStore(storage_path=str(Path(tmp_path) / "skills.json"))
    store.record_successful_workflow(
        workflow_name="Invoice Workflow",
        description="v1",
        steps=[{"step_name": "open-mail", "parameters": {"folder": "Inbox"}}],
        execution_time_seconds=10.0,
    )

    result = store.record_successful_workflow(
        workflow_name="Invoice Workflow",
        description="v2",
        steps=[{"step_name": "open-mail", "parameters": {"folder": "Priority"}}],
        execution_time_seconds=9.0,
    )

    assert result.skill is not None
    assert result.skill.current_version == 2
    assert len(result.skill.versions) == 2
    assert result.skill.versions[-1].description == "v2"


def test_workflow_skill_store_semantic_search_returns_relevant_skills(tmp_path):
    store = WorkflowSkillStore(storage_path=str(Path(tmp_path) / "skills.json"))
    store.record_successful_workflow(
        workflow_name="Invoice Workflow",
        description="Process invoices from email",
        steps=[WorkflowSkillStep("extract-invoice", {"source": "mail"})],
        execution_time_seconds=8.0,
        task_description="Read invoice emails and capture billing details.",
    )
    store.record_successful_workflow(
        workflow_name="Calendar Workflow",
        description="Create a meeting invite",
        steps=[WorkflowSkillStep("open-calendar", {"app": "outlook"})],
        execution_time_seconds=4.0,
        task_description="Schedule a meeting with attendees.",
    )

    result = store.search_skills("Handle a vendor invoice from the inbox and extract amounts.")

    assert result.succeeded is True
    assert result.matches
    assert result.matches[0].workflow_name == "Invoice Workflow"


def test_workflow_skill_store_supports_deprecation(tmp_path):
    store = WorkflowSkillStore(storage_path=str(Path(tmp_path) / "skills.json"))
    store.record_successful_workflow(
        workflow_name="Legacy Portal Login",
        description="Old login sequence",
        steps=[WorkflowSkillStep("login", {"portal": "legacy"})],
        execution_time_seconds=6.0,
    )

    deprecated = store.deprecate_skill("Legacy Portal Login", reason="Portal retired")
    visible = store.search_skills("Use the legacy portal login")

    assert deprecated.succeeded is True
    assert deprecated.skill is not None
    assert deprecated.skill.deprecated is True
    assert visible.matches == []


def test_workflow_skill_store_returns_specific_version(tmp_path):
    store = WorkflowSkillStore(storage_path=str(Path(tmp_path) / "skills.json"))
    store.record_successful_workflow(
        workflow_name="Report Workflow",
        description="Initial report flow",
        steps=[WorkflowSkillStep("open-report", {"kind": "weekly"})],
        execution_time_seconds=5.0,
    )
    store.record_successful_workflow(
        workflow_name="Report Workflow",
        description="Updated report flow",
        steps=[WorkflowSkillStep("open-report", {"kind": "monthly"})],
        execution_time_seconds=5.5,
    )

    result = store.get_skill_version("Report Workflow", 1)

    assert result.succeeded is True
    assert result.skill is not None
    assert result.skill.current_version == 1
    assert result.skill.versions[0].steps[0].parameters["kind"] == "weekly"
