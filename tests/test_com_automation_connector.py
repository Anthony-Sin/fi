from desktop_automation_agent.automation import COMAutomationConnector
from desktop_automation_agent.models import FormFieldValue, RetryConfiguration


class com_error(Exception):
    pass


class FakeRange:
    def __init__(self, value):
        self.Value = value


class FakeWorksheet:
    def __init__(self, values):
        self.values = values

    def Range(self, reference):
        return FakeRange(self.values[reference])


class FakeWorkbook:
    def __init__(self, sheets):
        self.sheets = sheets
        self.closed = False

    def Worksheets(self, name):
        return self.sheets[name]

    def Close(self, SaveChanges=False):
        self.closed = True


class FakeDocuments:
    def __init__(self):
        self.opened = []

    def Open(self, path, ReadOnly=False):
        document = type("FakeDocument", (), {"path": path, "read_only": ReadOnly})()
        self.opened.append(document)
        return document


class FakeWorkbooks:
    def __init__(self, workbook):
        self.workbook = workbook
        self.opened_paths = []

    def Open(self, path):
        self.opened_paths.append(path)
        return self.workbook


class FakeControl:
    def __init__(self):
        self.Value = None


class FakeApplication:
    def __init__(self, workbook):
        self.Visible = False
        self.Documents = FakeDocuments()
        self.Workbooks = FakeWorkbooks(workbook)
        self._controls = {"First Name": FakeControl(), "Email": FakeControl()}
        self.ActiveDocument = type(
            "FakeActiveDocument",
            (),
            {
                "saved": False,
                "saved_as": None,
                "Save": lambda self: setattr(self, "saved", True),
                "SaveAs": lambda self, path: setattr(self, "saved_as", path),
            },
        )()

    def Controls(self, name):
        return self._controls[name]


class FakeBackend:
    def __init__(self, application, *, fail_dispatch_once=False):
        self.application = application
        self.fail_dispatch_once = fail_dispatch_once
        self.dispatch_calls = 0
        self.initialized = 0
        self.uninitialized = 0
        self.released = []

    def initialize(self):
        self.initialized += 1

    def uninitialize(self):
        self.uninitialized += 1

    def dispatch(self, programmatic_identifier):
        self.dispatch_calls += 1
        if self.fail_dispatch_once and self.dispatch_calls == 1:
            raise com_error("RPC server unavailable")
        return self.application

    def release(self, com_object):
        self.released.append(com_object)


def make_connector(*, fail_dispatch_once=False):
    workbook = FakeWorkbook({"Sheet1": FakeWorksheet({"B2": "approved"})})
    application = FakeApplication(workbook)
    backend = FakeBackend(application, fail_dispatch_once=fail_dispatch_once)
    connector = COMAutomationConnector(
        application_name="Excel",
        programmatic_identifier="Excel.Application",
        backend=backend,
        visible=True,
        retry_configuration=RetryConfiguration(max_retry_count=2, initial_delay_seconds=0.0, max_delay_seconds=0.0),
    )
    return connector, backend, application, workbook


def test_com_automation_connector_retries_connect_and_sets_visibility():
    connector, backend, application, _ = make_connector(fail_dispatch_once=True)

    result = connector.connect()

    assert result.succeeded is True
    assert result.session is not None and result.session.connected is True
    assert backend.dispatch_calls == 2
    assert application.Visible is True


def test_com_automation_connector_opens_documents_reads_cells_and_fills_forms():
    connector, _, application, workbook = make_connector()
    connector.connect()

    document_result = connector.open_document("C:/docs/report.docx", read_only=True)
    cell_result = connector.read_cell_value("C:/docs/sheet.xlsx", sheet_name="Sheet1", cell_reference="B2")
    form_result = connector.fill_form_fields(
        [
            FormFieldValue(label="First Name", value="Ana", accessibility_name="First Name"),
            FormFieldValue(label="Email", value="ana@example.com", accessibility_name="Email"),
        ]
    )

    assert document_result.succeeded is True
    assert application.Documents.opened[0].path == "C:/docs/report.docx"
    assert application.Documents.opened[0].read_only is True
    assert cell_result.succeeded is True
    assert cell_result.value == "approved"
    assert workbook.closed is True
    assert form_result.succeeded is True
    assert application._controls["First Name"].Value == "Ana"
    assert application._controls["Email"].Value == "ana@example.com"


def test_com_automation_connector_saves_files_and_releases_objects():
    connector, backend, application, _ = make_connector()
    connector.connect()
    connector.open_document("C:/docs/report.docx")

    save_result = connector.save_file("C:/docs/report-saved.docx")
    release_result = connector.release()

    assert save_result.succeeded is True
    assert save_result.value == "C:/docs/report-saved.docx"
    assert application.ActiveDocument.saved_as == "C:/docs/report-saved.docx"
    assert release_result.succeeded is True
    assert release_result.released_objects >= 2
    assert backend.uninitialized == 1
    assert len(backend.released) >= 2


def test_com_automation_connector_reports_retry_failure_for_nonrecoverable_connect_issue():
    connector, backend, _, _ = make_connector()

    def always_fail(_programmatic_identifier):
        raise RuntimeError("Access denied")

    backend.dispatch = always_fail
    result = connector.connect()

    assert result.succeeded is False
    assert result.retry_failure is not None
    assert result.reason == "Retry attempts exhausted."
