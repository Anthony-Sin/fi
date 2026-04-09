from datetime import datetime, timezone

from desktop_automation_agent.graph_workflow_planner import GraphBasedWorkflowPlanner
from desktop_automation_agent.models import (
    WorkflowGraphDefinition,
    WorkflowGraphEdge,
    WorkflowGraphNode,
    WorkflowTemplateCompositionComponent,
    WorkflowTemplateParameter,
)
from desktop_automation_agent.workflow_template_library import WorkflowTemplateLibrary


def _template_graph(workflow_id, node_suffix, label):
    return WorkflowGraphDefinition(
        workflow_id=workflow_id,
        entry_node_ids=("start",),
        nodes=[
            WorkflowGraphNode(
                node_id="start",
                step_name=f"{label} {{{{target}}}}",
                step_payload={"screen": "{{target}}"},
            ),
            WorkflowGraphNode(node_id=f"end-{node_suffix}", step_name=f"Finish {label}"),
        ],
        edges=[
            WorkflowGraphEdge(
                edge_id=f"edge-{node_suffix}",
                source_node_id="start",
                target_node_id=f"end-{node_suffix}",
            )
        ],
    )


def test_workflow_template_library_versions_templates_independently(tmp_path):
    library = WorkflowTemplateLibrary(
        storage_path=str(tmp_path / "templates.json"),
        graph_planner=GraphBasedWorkflowPlanner(),
    )

    created = library.create_template(
        template_id="login",
        name="Login",
        description="Login to an application",
        author="alice",
        application="CRM",
        task_type="login",
        keywords=["login", "auth"],
        workflow_graph=_template_graph("tpl-login", "a", "Open"),
        parameters=[WorkflowTemplateParameter(name="target", description="Target application", required=True)],
        timestamp=datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
    )
    updated = library.create_template_version(
        template_id="login",
        author="bob",
        change_description="Adjust login end node",
        workflow_graph=_template_graph("tpl-login", "b", "Open"),
        timestamp=datetime(2026, 4, 9, 11, 0, tzinfo=timezone.utc),
    )

    assert created.succeeded is True
    assert updated.succeeded is True
    assert updated.template is not None
    assert updated.template.current_version_number == 2
    assert len(updated.template.versions) == 2


def test_workflow_template_library_searches_by_keyword_application_and_task_type(tmp_path):
    library = WorkflowTemplateLibrary(
        storage_path=str(tmp_path / "templates.json"),
        graph_planner=GraphBasedWorkflowPlanner(),
    )
    library.create_template(
        template_id="login",
        name="Login",
        description="Login to CRM",
        author="alice",
        application="CRM",
        task_type="login",
        keywords=["authenticate", "sign-in"],
        workflow_graph=_template_graph("tpl-login", "a", "Open"),
    )
    library.create_template(
        template_id="extract-table",
        name="Extract Table",
        description="Extract a data table from reports",
        author="alice",
        application="CRM",
        task_type="extract",
        keywords=["table", "report"],
        workflow_graph=_template_graph("tpl-extract", "a", "Extract"),
    )

    keyword_result = library.search_templates(keyword="table")
    filtered_result = library.search_templates(application="CRM", task_type="login")

    assert keyword_result.succeeded is True
    assert keyword_result.matches[0].template_id == "extract-table"
    assert filtered_result.succeeded is True
    assert [match.template_id for match in filtered_result.matches] == ["login"]


def test_workflow_template_library_composes_full_workflow_from_templates(tmp_path):
    library = WorkflowTemplateLibrary(
        storage_path=str(tmp_path / "templates.json"),
        graph_planner=GraphBasedWorkflowPlanner(),
    )
    library.create_template(
        template_id="login",
        name="Login",
        description="Login to app",
        author="alice",
        application="CRM",
        task_type="login",
        workflow_graph=_template_graph("tpl-login", "a", "Open"),
        parameters=[WorkflowTemplateParameter(name="target", description="Target app", default_value="CRM")],
    )
    library.create_template(
        template_id="extract",
        name="Extract Table",
        description="Extract a data table",
        author="alice",
        application="CRM",
        task_type="extract",
        workflow_graph=_template_graph("tpl-extract", "b", "Extract"),
        parameters=[WorkflowTemplateParameter(name="target", description="Screen", default_value="Reports")],
    )

    composed = library.compose_workflow(
        workflow_id="wf-composed",
        components=[
            WorkflowTemplateCompositionComponent(
                template_id="login",
                parameter_values={"target": "CRM"},
                node_prefix="login",
            ),
            WorkflowTemplateCompositionComponent(
                template_id="extract",
                parameter_values={"target": "Reports"},
                node_prefix="extract",
            ),
        ],
    )

    assert composed.succeeded is True
    assert composed.workflow_graph is not None
    assert len(composed.workflow_graph.nodes) == 4
    assert any(node.step_name == "Open CRM" for node in composed.workflow_graph.nodes)
    assert any(node.step_name == "Extract Reports" for node in composed.workflow_graph.nodes)
    assert any(edge.edge_id.startswith("compose__") for edge in composed.workflow_graph.edges)
