from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop_automation_agent.models import (
    APIAuthType,
    CICDReportMode,
    CICDRunResult,
    CICDStepOutcome,
    CICDTriggerPayload,
    CICDWorkflowSpecification,
    RESTAPIMethod,
    RESTAPIRequest,
    WorkflowContext,
    WorkflowStep,
)


@dataclass(slots=True)
class CICDPipelineIntegrationModule:
    workflow_coordinator: object
    api_executor: object | None = None
    report_generator: object | None = None

    def handle_webhook_trigger(self, payload: dict[str, Any] | CICDTriggerPayload) -> CICDRunResult:
        trigger = payload if isinstance(payload, CICDTriggerPayload) else self._deserialize_trigger_payload(payload)
        context = WorkflowContext(
            shared_data={
                **trigger.specification.parameters,
                **trigger.pipeline_parameters,
                "cicd_build_id": trigger.build_id,
            }
        )
        result = self.workflow_coordinator.run(trigger.specification.steps, initial_context=context)
        step_outcomes = self._build_step_outcomes(result.step_results)
        skipped_steps = [item.step_id for item in step_outcomes if item.status == "skipped"]
        run_status = self._status_from_result(result, step_outcomes)
        report_payload = self._build_report_payload(trigger=trigger, result=result, step_outcomes=step_outcomes, status=run_status)

        callback_result = None
        output_path = None
        if trigger.report_mode in (CICDReportMode.API, CICDReportMode.BOTH) and trigger.callback_endpoint and self.api_executor is not None:
            callback_result = self.api_executor.execute(
                RESTAPIRequest(
                    endpoint=trigger.callback_endpoint,
                    method=RESTAPIMethod.POST,
                    headers={"Content-Type": "application/json"},
                    payload=report_payload,
                    auth_type=APIAuthType.NONE,
                ),
                workflow_id=trigger.specification.workflow_id,
                step_name="cicd_report_callback",
            )

        if trigger.report_mode in (CICDReportMode.OUTPUT_FILE, CICDReportMode.BOTH) and trigger.output_path:
            output_path = self._write_output_file(trigger.output_path, report_payload)

        return CICDRunResult(
            succeeded=run_status == "success",
            build_id=trigger.build_id,
            workflow_id=trigger.specification.workflow_id,
            workflow_name=trigger.specification.workflow_name,
            status=run_status,
            skipped_steps=skipped_steps,
            step_outcomes=step_outcomes,
            parameters={**trigger.specification.parameters, **trigger.pipeline_parameters},
            report_payload=report_payload,
            callback_result=callback_result,
            output_path=output_path,
            reason=getattr(result, "reason", None),
        )

    def _deserialize_trigger_payload(self, payload: dict[str, Any]) -> CICDTriggerPayload:
        specification = payload.get("workflow_specification", payload.get("specification", {}))
        steps = [self._deserialize_step(item) for item in specification.get("steps", [])]
        return CICDTriggerPayload(
            build_id=payload["build_id"],
            specification=CICDWorkflowSpecification(
                workflow_id=specification["workflow_id"],
                workflow_name=specification.get("workflow_name", specification["workflow_id"]),
                steps=steps,
                parameters={key: str(value) for key, value in specification.get("parameters", {}).items()},
            ),
            report_mode=CICDReportMode(payload.get("report_mode", CICDReportMode.API.value)),
            callback_endpoint=payload.get("callback_endpoint"),
            output_path=payload.get("output_path"),
            pipeline_parameters={key: str(value) for key, value in payload.get("pipeline_parameters", {}).items()},
        )

    def _deserialize_step(self, payload: dict[str, Any]) -> WorkflowStep:
        return WorkflowStep(
            step_id=payload["step_id"],
            application_name=payload["application_name"],
            required_window_title=payload.get("required_window_title"),
            required_process_name=payload.get("required_process_name"),
            focus_required=bool(payload.get("focus_required", True)),
            optional=bool(payload.get("optional", False)),
        )

    def _build_step_outcomes(self, step_results: list[object]) -> list[CICDStepOutcome]:
        outcomes: list[CICDStepOutcome] = []
        for item in step_results:
            succeeded = bool(getattr(item, "succeeded", False))
            skipped = bool(getattr(item, "reason", None)) and "skip" in str(getattr(item, "reason", "")).casefold()
            outcomes.append(
                CICDStepOutcome(
                    step_id=getattr(item, "step_id", ""),
                    application_name=getattr(item, "application_name", ""),
                    status="skipped" if skipped else ("success" if succeeded else "failure"),
                    reason=getattr(item, "reason", None),
                )
            )
        return outcomes

    def _status_from_result(self, result: object, step_outcomes: list[CICDStepOutcome]) -> str:
        if getattr(result, "succeeded", False):
            return "success"
        if step_outcomes and all(item.status == "skipped" for item in step_outcomes):
            return "skipped"
        return "failure"

    def _build_report_payload(
        self,
        *,
        trigger: CICDTriggerPayload,
        result: object,
        step_outcomes: list[CICDStepOutcome],
        status: str,
    ) -> dict[str, Any]:
        payload = {
            "build_id": trigger.build_id,
            "workflow_id": trigger.specification.workflow_id,
            "workflow_name": trigger.specification.workflow_name,
            "status": status,
            "parameters": {**trigger.specification.parameters, **trigger.pipeline_parameters},
            "skipped_steps": [item.step_id for item in step_outcomes if item.status == "skipped"],
            "step_outcomes": [
                {
                    "step_id": item.step_id,
                    "application_name": item.application_name,
                    "status": item.status,
                    "reason": item.reason,
                }
                for item in step_outcomes
            ],
            "reason": getattr(result, "reason", None),
        }
        if self.report_generator is not None:
            report = self.report_generator.generate_report(
                workflow_id=trigger.specification.workflow_id,
                workflow_name=trigger.specification.workflow_name,
                audit_entries=[],
            )
            if getattr(report, "succeeded", False) and getattr(report, "report", None) is not None:
                payload["report_body"] = getattr(report.report, "body_text", None)
        return payload

    def _write_output_file(self, output_path: str, payload: dict[str, Any]) -> str:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return str(path)
