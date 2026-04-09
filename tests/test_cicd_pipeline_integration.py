import json
from pathlib import Path

from desktop_automation_agent.automation import CICDPipelineIntegrationModule
from desktop_automation_agent.models import (
    CICDReportMode,
    CICDTriggerPayload,
    CICDWorkflowSpecification,
    WorkflowContext,
    WorkflowCoordinatorResult,
    WorkflowStep,
    WorkflowStepResult,
)


class FakeCoordinator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, steps, *, initial_context=None):
        self.calls.append({"steps": steps, "initial_context": initial_context})
        return self.result


class FakeAPIExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, request, *, workflow_id=None, step_name=None):
        self.calls.append({"request": request, "workflow_id": workflow_id, "step_name": step_name})
        return type("APIResult", (), {"succeeded": True, "response": None})()


def test_cicd_pipeline_integration_executes_webhook_spec_and_tags_context():
    coordinator = FakeCoordinator(
        WorkflowCoordinatorResult(
            succeeded=True,
            context=WorkflowContext(shared_data={"done": "yes"}),
            step_results=[
                WorkflowStepResult(step_id="open", application_name="chrome", succeeded=True),
                WorkflowStepResult(step_id="submit", application_name="crm", succeeded=True),
            ],
        )
    )
    module = CICDPipelineIntegrationModule(workflow_coordinator=coordinator)

    result = module.handle_webhook_trigger(
        {
            "build_id": "build-42",
            "workflow_specification": {
                "workflow_id": "wf-1",
                "workflow_name": "Deploy Validation",
                "parameters": {"env": "staging"},
                "steps": [
                    {"step_id": "open", "application_name": "chrome"},
                    {"step_id": "submit", "application_name": "crm"},
                ],
            },
            "pipeline_parameters": {"branch": "main"},
            "report_mode": "output_file",
        }
    )

    seeded_context = coordinator.calls[0]["initial_context"]

    assert result.succeeded is True
    assert result.build_id == "build-42"
    assert result.workflow_id == "wf-1"
    assert result.parameters == {"env": "staging", "branch": "main"}
    assert seeded_context.shared_data["cicd_build_id"] == "build-42"
    assert seeded_context.shared_data["env"] == "staging"
    assert seeded_context.shared_data["branch"] == "main"


def test_cicd_pipeline_integration_reports_result_via_api_callback():
    coordinator = FakeCoordinator(
        WorkflowCoordinatorResult(
            succeeded=False,
            context=WorkflowContext(),
            step_results=[
                WorkflowStepResult(step_id="step-1", application_name="chrome", succeeded=True),
                WorkflowStepResult(step_id="step-2", application_name="crm", succeeded=False, reason="validation failed"),
            ],
            reason="validation failed",
        )
    )
    api_executor = FakeAPIExecutor()
    module = CICDPipelineIntegrationModule(
        workflow_coordinator=coordinator,
        api_executor=api_executor,
    )

    result = module.handle_webhook_trigger(
        CICDTriggerPayload(
            build_id="build-9",
            specification=CICDWorkflowSpecification(
                workflow_id="wf-9",
                workflow_name="Nightly Check",
                steps=[WorkflowStep(step_id="step-1", application_name="chrome")],
            ),
            report_mode=CICDReportMode.API,
            callback_endpoint="https://ci.example/report",
            pipeline_parameters={"job": "nightly"},
        )
    )

    callback = api_executor.calls[0]["request"]

    assert result.succeeded is False
    assert result.status == "failure"
    assert result.callback_result is not None
    assert callback.endpoint == "https://ci.example/report"
    assert callback.payload["build_id"] == "build-9"
    assert callback.payload["workflow_id"] == "wf-9"
    assert callback.payload["status"] == "failure"
    assert callback.payload["step_outcomes"][1]["reason"] == "validation failed"


def test_cicd_pipeline_integration_writes_structured_output_file():
    coordinator = FakeCoordinator(
        WorkflowCoordinatorResult(
            succeeded=True,
            context=WorkflowContext(),
            step_results=[
                WorkflowStepResult(step_id="optional-step", application_name="erp", succeeded=False, reason="skipped by pipeline"),
            ],
        )
    )
    module = CICDPipelineIntegrationModule(workflow_coordinator=coordinator)
    output_path = Path("C:/Users/antho/Downloads/fi/tests_artifacts/cicd-output.json")

    result = module.handle_webhook_trigger(
        {
            "build_id": "build-output",
            "workflow_specification": {
                "workflow_id": "wf-output",
                "workflow_name": "Output Reporter",
                "steps": [{"step_id": "optional-step", "application_name": "erp", "optional": True}],
            },
            "report_mode": "output_file",
            "output_path": str(output_path),
        }
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert result.output_path == str(output_path)
    assert payload["build_id"] == "build-output"
    assert payload["workflow_id"] == "wf-output"
    assert payload["skipped_steps"] == ["optional-step"]
    assert payload["step_outcomes"][0]["status"] == "skipped"
