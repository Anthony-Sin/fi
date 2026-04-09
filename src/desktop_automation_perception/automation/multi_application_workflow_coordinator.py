from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from desktop_automation_perception.contracts import ApplicationLauncher, ClipboardManager, WindowManager
from desktop_automation_perception.models import (
    AllowlistCheckRequest,
    WorkflowContext,
    WorkflowCoordinatorResult,
    WorkflowDataExchangeMode,
    WorkflowExchangeRequest,
    WorkflowStep,
    WorkflowStepResult,
)


@dataclass(slots=True)
class MultiApplicationWorkflowCoordinator:
    launcher: ApplicationLauncher
    window_manager: WindowManager
    clipboard_manager: ClipboardManager | None = None
    dry_run: bool = False
    abort_checker: Callable[[], bool] | None = None
    allowlist_enforcer: object | None = None
    workflow_id: str | None = None
    anti_loop_detector: object | None = None

    def run(
        self,
        steps: list[WorkflowStep],
        *,
        initial_context: WorkflowContext | None = None,
    ) -> WorkflowCoordinatorResult:
        context = deepcopy(initial_context) if initial_context is not None else WorkflowContext()
        results: list[WorkflowStepResult] = []

        for index, step in enumerate(steps, start=1):
            if self._abort_requested():
                return WorkflowCoordinatorResult(
                    succeeded=False,
                    context=context,
                    step_results=results,
                    reason="Execution aborted by fail-safe controller.",
                )
            if self.anti_loop_detector is not None:
                loop_result = self.anti_loop_detector.before_step(
                    step.step_id,
                    metadata={
                        "application_name": step.application_name,
                        "focus_required": step.focus_required,
                    },
                )
                if getattr(loop_result, "triggered", False):
                    return WorkflowCoordinatorResult(
                        succeeded=False,
                        context=context,
                        step_results=results,
                        reason=getattr(loop_result, "reason", "Anti-loop detector halted execution."),
                    )
            context.step_number = index
            prior_application = context.current_application

            if not self.dry_run and prior_application is not None and self._application_closed(prior_application, context):
                result = WorkflowStepResult(
                    step_id=step.step_id,
                    application_name=step.application_name,
                    succeeded=False,
                    dry_run=self.dry_run,
                    context_snapshot=self._snapshot_context(context),
                    reason=f"Prior application {prior_application!r} closed unexpectedly.",
                )
                results.append(result)
                return WorkflowCoordinatorResult(
                    succeeded=False,
                    context=context,
                    step_results=results,
                    reason=result.reason,
                )

            step_result = self._run_step(step, context)
            results.append(step_result)
            if (
                not self.dry_run
                and
                step_result.succeeded
                and prior_application is not None
                and self._application_closed(prior_application, context)
            ):
                results[-1] = WorkflowStepResult(
                    step_id=step_result.step_id,
                    application_name=step_result.application_name,
                    succeeded=False,
                    dry_run=step_result.dry_run,
                    context_snapshot=step_result.context_snapshot,
                    reason=f"Prior application {prior_application!r} closed unexpectedly.",
                )
                return WorkflowCoordinatorResult(
                    succeeded=False,
                    context=context,
                    step_results=results,
                    reason=results[-1].reason,
                )
            if not step_result.succeeded and not step.optional:
                return WorkflowCoordinatorResult(
                    succeeded=False,
                    context=context,
                    step_results=results,
                    reason=step_result.reason,
                )

        return WorkflowCoordinatorResult(
            succeeded=True,
            context=context,
            step_results=results,
        )

    def _run_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext,
    ) -> WorkflowStepResult:
        if self._abort_requested():
            return WorkflowStepResult(
                step_id=step.step_id,
                application_name=step.application_name,
                succeeded=False,
                dry_run=self.dry_run,
                context_snapshot=self._snapshot_context(context),
                reason="Execution aborted by fail-safe controller.",
            )
        if self.dry_run:
            context.current_application = step.application_name
            if step.application_name not in context.active_applications:
                context.active_applications.append(step.application_name)
            self._apply_exchange(step.incoming_exchange, context, read_only=True)
            self._apply_exchange(step.outgoing_exchange, context, read_only=True)
            return WorkflowStepResult(
                step_id=step.step_id,
                application_name=step.application_name,
                succeeded=True,
                dry_run=True,
                context_snapshot=self._snapshot_context(context),
            )

        if step.launch_request is not None:
            try:
                launch_result = self.launcher.launch(
                    step.launch_request,
                    workflow_id=self.workflow_id,
                    step_name=step.step_id,
                )
            except TypeError:
                launch_result = self.launcher.launch(step.launch_request)
            if not getattr(launch_result, "succeeded", False):
                return WorkflowStepResult(
                    step_id=step.step_id,
                    application_name=step.application_name,
                    succeeded=False,
                    dry_run=False,
                    context_snapshot=self._snapshot_context(context),
                    reason=getattr(launch_result, "reason", "Failed to launch application."),
                )

        if step.focus_required:
            focus_result = self.window_manager.focus_window(
                title=step.required_window_title,
                process_name=step.required_process_name,
            )
            if not getattr(focus_result, "succeeded", False):
                return WorkflowStepResult(
                    step_id=step.step_id,
                    application_name=step.application_name,
                    succeeded=False,
                    dry_run=False,
                    context_snapshot=self._snapshot_context(context),
                    reason=getattr(focus_result, "reason", "Failed to focus application."),
                )

        context.current_application = step.application_name
        if step.application_name not in context.active_applications:
            context.active_applications.append(step.application_name)
        signature = step.required_process_name or step.required_window_title or step.application_name
        context.application_signatures[step.application_name] = signature

        exchange_error = self._apply_exchange(step.incoming_exchange, context, read_only=False)
        if exchange_error is not None:
            return WorkflowStepResult(
                step_id=step.step_id,
                application_name=step.application_name,
                succeeded=False,
                dry_run=False,
                context_snapshot=self._snapshot_context(context),
                reason=exchange_error,
            )

        exchange_error = self._apply_exchange(step.outgoing_exchange, context, read_only=False)
        if exchange_error is not None:
            return WorkflowStepResult(
                step_id=step.step_id,
                application_name=step.application_name,
                succeeded=False,
                dry_run=False,
                context_snapshot=self._snapshot_context(context),
                reason=exchange_error,
            )

        return WorkflowStepResult(
            step_id=step.step_id,
            application_name=step.application_name,
            succeeded=True,
            dry_run=False,
            context_snapshot=self._snapshot_context(context),
        )

    def _apply_exchange(
        self,
        exchange: WorkflowExchangeRequest | None,
        context: WorkflowContext,
        *,
        read_only: bool,
    ) -> str | None:
        if exchange is None:
            return None

        if exchange.mode is WorkflowDataExchangeMode.CLIPBOARD:
            if self.clipboard_manager is None:
                return "Clipboard exchange requested but no clipboard manager is configured."
            if read_only:
                if exchange.value is not None:
                    context.shared_data[exchange.data_key] = exchange.value
                return None
            if exchange.value is not None:
                context.shared_data[exchange.data_key] = exchange.value
                result = self.clipboard_manager.write_text(exchange.value)
                if getattr(result, "succeeded", True) is False:
                    return getattr(result, "reason", "Clipboard write failed.")
                return None

            clipboard = self.clipboard_manager.read_clipboard()
            content = getattr(clipboard, "content", None)
            if getattr(clipboard, "succeeded", False) and content is not None and getattr(content, "text", None) is not None:
                context.shared_data[exchange.data_key] = content.text
                return None
            return getattr(clipboard, "reason", "Clipboard read failed.")

        if exchange.mode is WorkflowDataExchangeMode.FILE:
            if exchange.file_path is None:
                return "File exchange requested without a file path."
            path = Path(exchange.file_path)
            if self.allowlist_enforcer is not None:
                decision = self.allowlist_enforcer.evaluate(
                    AllowlistCheckRequest(
                        workflow_id=self.workflow_id,
                        step_name=context.current_application or "workflow_file_exchange",
                        action_type="file_exchange",
                        application_name=context.current_application,
                        file_path=str(path),
                    )
                )
                if not decision.allowed:
                    return decision.reason
            if read_only:
                if exchange.value is not None:
                    context.shared_data[exchange.data_key] = exchange.value
                return None
            if exchange.value is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(exchange.value, encoding="utf-8")
                context.shared_data[exchange.data_key] = exchange.value
                return None
            if not path.exists():
                return f"Exchange file does not exist: {exchange.file_path}"
            context.shared_data[exchange.data_key] = path.read_text(encoding="utf-8")
            return None

        return "Unsupported workflow data exchange mode."

    def _list_application_names(self) -> list[str]:
        names: list[str] = []
        for window in self.window_manager.list_windows():
            title = getattr(window, "title", None)
            process_name = getattr(window, "process_name", None)
            if title:
                names.append(title)
            if process_name:
                names.append(process_name)
        return names

    def _application_closed(
        self,
        application_name: str,
        context: WorkflowContext,
    ) -> bool:
        signature = context.application_signatures.get(application_name, application_name)
        normalized = signature.casefold()
        for name in self._list_application_names():
            if normalized in name.casefold():
                return False
        return True

    def _snapshot_context(self, context: WorkflowContext) -> WorkflowContext:
        return WorkflowContext(
            current_application=context.current_application,
            step_number=context.step_number,
            shared_data=dict(context.shared_data),
            active_applications=list(context.active_applications),
            application_signatures=dict(context.application_signatures),
        )

    def _abort_requested(self) -> bool:
        return bool(self.abort_checker and self.abort_checker())
