from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from desktop_automation_agent.models import (
    COMAutomationResult,
    COMAutomationSession,
    FormFieldValue,
    RetryConfiguration,
    RetryExceptionRule,
    RetryFailureResult,
    RetryDisposition,
)
from desktop_automation_agent.resilience.retry_engine import (
    ExponentialBackoffRetryEngine,
    RetryExhaustedError,
)


class PyWin32COMBackend:
    def initialize(self) -> None:
        import pythoncom

        pythoncom.CoInitialize()

    def uninitialize(self) -> None:
        import pythoncom

        pythoncom.CoUninitialize()

    def dispatch(self, programmatic_identifier: str):
        import win32com.client

        return win32com.client.Dispatch(programmatic_identifier)

    def release(self, com_object: object) -> None:
        try:
            import pythoncom

            if hasattr(com_object, "_oleobj_"):
                pythoncom.CoUninitialize()
                pythoncom.CoInitialize()
        except Exception:
            pass


@dataclass(slots=True)
class COMAutomationConnector:
    application_name: str
    programmatic_identifier: str
    backend: object | None = None
    retry_engine: ExponentialBackoffRetryEngine[object] | None = None
    retry_configuration: RetryConfiguration | None = None
    visible: bool = False
    _application: object | None = field(default=None, init=False, repr=False)
    _released_objects: int = field(default=0, init=False, repr=False)
    _tracked_objects: list[object] = field(default_factory=list, init=False, repr=False)

    def connect(self) -> COMAutomationResult:
        backend = self._backend()
        try:
            backend.initialize()
            application = self._run_with_retry(lambda: backend.dispatch(self.programmatic_identifier))
            self._application = application
            self._track_object(application)
            if hasattr(application, "Visible"):
                application.Visible = self.visible
            return COMAutomationResult(
                succeeded=True,
                session=self._session(connected=True),
            )
        except RetryExhaustedError as exc:
            return COMAutomationResult(
                succeeded=False,
                session=self._session(connected=False),
                retry_failure=exc.failure,
                released_objects=self._released_objects,
                reason=exc.failure.reason,
            )
        except Exception as exc:
            return COMAutomationResult(
                succeeded=False,
                session=self._session(connected=False),
                released_objects=self._released_objects,
                reason=str(exc),
            )

    def open_document(self, path: str, *, read_only: bool = False) -> COMAutomationResult:
        if self._application is None:
            return COMAutomationResult(succeeded=False, session=self._session(connected=False), reason="COM session is not connected.")
        try:
            opened = self._run_with_retry(lambda: self._application.Documents.Open(path, ReadOnly=read_only))
            self._track_object(opened)
            return COMAutomationResult(succeeded=True, session=self._session(), value=opened)
        except RetryExhaustedError as exc:
            return COMAutomationResult(
                succeeded=False,
                session=self._session(),
                retry_failure=exc.failure,
                released_objects=self._released_objects,
                reason=exc.failure.reason,
            )
        except Exception as exc:
            return COMAutomationResult(succeeded=False, session=self._session(), released_objects=self._released_objects, reason=str(exc))

    def read_cell_value(
        self,
        workbook_path: str,
        *,
        sheet_name: str,
        cell_reference: str,
    ) -> COMAutomationResult:
        if self._application is None:
            return COMAutomationResult(succeeded=False, session=self._session(connected=False), reason="COM session is not connected.")
        workbook = None
        try:
            workbook = self._run_with_retry(lambda: self._application.Workbooks.Open(workbook_path))
            self._track_object(workbook)
            worksheet = workbook.Worksheets(sheet_name)
            self._track_object(worksheet)
            value = self._run_with_retry(lambda: worksheet.Range(cell_reference).Value)
            return COMAutomationResult(succeeded=True, session=self._session(), value=value)
        except RetryExhaustedError as exc:
            return COMAutomationResult(
                succeeded=False,
                session=self._session(),
                retry_failure=exc.failure,
                released_objects=self._released_objects,
                reason=exc.failure.reason,
            )
        except Exception as exc:
            return COMAutomationResult(succeeded=False, session=self._session(), released_objects=self._released_objects, reason=str(exc))
        finally:
            self._close_if_possible(workbook, save_changes=False)

    def fill_form_fields(self, fields: list[FormFieldValue]) -> COMAutomationResult:
        if self._application is None:
            return COMAutomationResult(succeeded=False, session=self._session(connected=False), reason="COM session is not connected.")
        try:
            values_written: dict[str, Any] = {}
            for field in fields:
                control_name = field.accessibility_name or field.label
                control = self._run_with_retry(lambda name=control_name: self._application.Controls(name))
                self._track_object(control)
                self._run_with_retry(lambda value=field.value, target=control: self._set_control_value(target, value))
                values_written[field.label] = field.value
            return COMAutomationResult(succeeded=True, session=self._session(), value=values_written)
        except RetryExhaustedError as exc:
            return COMAutomationResult(
                succeeded=False,
                session=self._session(),
                retry_failure=exc.failure,
                released_objects=self._released_objects,
                reason=exc.failure.reason,
            )
        except Exception as exc:
            return COMAutomationResult(succeeded=False, session=self._session(), released_objects=self._released_objects, reason=str(exc))

    def save_file(self, path: str | None = None) -> COMAutomationResult:
        if self._application is None:
            return COMAutomationResult(succeeded=False, session=self._session(connected=False), reason="COM session is not connected.")
        try:
            if path:
                self._run_with_retry(lambda: self._application.ActiveDocument.SaveAs(path))
                return COMAutomationResult(succeeded=True, session=self._session(), value=path)
            self._run_with_retry(lambda: self._application.ActiveDocument.Save())
            return COMAutomationResult(succeeded=True, session=self._session(), value=True)
        except RetryExhaustedError as exc:
            return COMAutomationResult(
                succeeded=False,
                session=self._session(),
                retry_failure=exc.failure,
                released_objects=self._released_objects,
                reason=exc.failure.reason,
            )
        except Exception as exc:
            return COMAutomationResult(succeeded=False, session=self._session(), released_objects=self._released_objects, reason=str(exc))

    def release(self) -> COMAutomationResult:
        released_count = 0
        while self._tracked_objects:
            com_object = self._tracked_objects.pop()
            try:
                self._backend().release(com_object)
            except Exception:
                pass
            released_count += 1
        self._released_objects += released_count
        self._application = None
        try:
            self._backend().uninitialize()
        except Exception:
            pass
        return COMAutomationResult(
            succeeded=True,
            session=self._session(connected=False),
            released_objects=self._released_objects,
        )

    def _run_with_retry(self, action: Callable[[], object]) -> object:
        engine = self.retry_engine or ExponentialBackoffRetryEngine[object]()
        configuration = self.retry_configuration or RetryConfiguration(
            max_retry_count=2,
            initial_delay_seconds=0.25,
            backoff_multiplier=2.0,
            max_delay_seconds=2.0,
            exception_rules=[
                RetryExceptionRule(exception_type_name="com_error", disposition=RetryDisposition.RETRY),
                RetryExceptionRule(exception_type_name="RuntimeError", disposition=RetryDisposition.RETRY),
            ],
        )
        return engine.run(action, configuration=configuration)

    def _backend(self):
        return self.backend or PyWin32COMBackend()

    def _track_object(self, com_object: object | None) -> None:
        if com_object is not None:
            self._tracked_objects.append(com_object)

    def _set_control_value(self, control: object, value: Any) -> object:
        if hasattr(control, "Value"):
            control.Value = value
            return value
        if hasattr(control, "Text"):
            control.Text = str(value)
            return value
        raise RuntimeError("COM control does not support a writable Value or Text property.")

    def _close_if_possible(self, com_object: object | None, *, save_changes: bool) -> None:
        if com_object is None:
            return
        close = getattr(com_object, "Close", None)
        if callable(close):
            try:
                close(SaveChanges=save_changes)
            except TypeError:
                close()

    def _session(self, *, connected: bool | None = None) -> COMAutomationSession:
        return COMAutomationSession(
            application_name=self.application_name,
            programmatic_identifier=self.programmatic_identifier,
            visible=self.visible,
            connected=self._application is not None if connected is None else connected,
        )
