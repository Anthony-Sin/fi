import json
from pathlib import Path

from desktop_automation_agent.data_export_pipeline import DataExportPipeline
from desktop_automation_agent.models import (
    DataExportDestination,
    DataExportDestinationType,
    DataExportFileFormat,
    RetryConfiguration,
    RetryDisposition,
    RetryExceptionRule,
    StructuredDataFieldSchema,
    StructuredDataFieldType,
    StructuredDataRecord,
    StructuredDataSchema,
)
from desktop_automation_agent.retry_engine import ExponentialBackoffRetryEngine


class FakeDatabaseExporter:
    def __init__(self):
        self.calls = []

    def insert_records(self, *, table, records):
        self.calls.append((table, records))
        return len(records)


class FakeAPIExporter:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def push_records(self, *, endpoint, records):
        self.calls.append((endpoint, records))
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("temporary api failure")
        return len(records)


class FakeAuditLogger:
    def __init__(self):
        self.calls = []

    def log_action(self, **kwargs):
        self.calls.append(kwargs)
        return type("AuditResult", (), {"succeeded": True})()


def make_schema():
    return StructuredDataSchema(
        schema_name="people",
        fields=[
            StructuredDataFieldSchema(field_name="name", field_type=StructuredDataFieldType.STRING, required=True),
            StructuredDataFieldSchema(field_name="age", field_type=StructuredDataFieldType.INTEGER, required=True),
        ],
    )


def test_data_export_pipeline_deduplicates_validates_and_exports_to_json_csv_db_and_api(tmp_path):
    db = FakeDatabaseExporter()
    api = FakeAPIExporter()
    audit = FakeAuditLogger()
    pipeline = DataExportPipeline(
        schema=make_schema(),
        destinations=[
            DataExportDestination(
                destination_type=DataExportDestinationType.FILE,
                destination_name="json-out",
                file_path=str(Path(tmp_path) / "records.json"),
                file_format=DataExportFileFormat.JSON,
            ),
            DataExportDestination(
                destination_type=DataExportDestinationType.FILE,
                destination_name="csv-out",
                file_path=str(Path(tmp_path) / "records.csv"),
                file_format=DataExportFileFormat.CSV,
            ),
            DataExportDestination(
                destination_type=DataExportDestinationType.DATABASE,
                destination_name="db-out",
                database_table="people",
            ),
            DataExportDestination(
                destination_type=DataExportDestinationType.API,
                destination_name="api-out",
                api_endpoint="https://example.invalid/people",
            ),
        ],
        database_exporter=db,
        api_exporter=api,
        audit_logger=audit,
        log_storage_path=str(Path(tmp_path) / "export_log.jsonl"),
    )

    pipeline.buffer_records(
        [
            StructuredDataRecord(values={"name": "Alice", "age": 31}),
            StructuredDataRecord(values={"name": "Alice", "age": 31}),
            StructuredDataRecord(values={"name": "Bob", "age": 27}),
        ]
    )
    result = pipeline.flush(workflow_id="wf-1")

    assert result.succeeded is True
    assert result.buffered_count == 3
    assert result.deduplicated_count == 2
    assert result.exported_count == 2
    assert result.validation_failures == []
    assert len(db.calls) == 1
    assert len(api.calls) == 1
    assert len(audit.calls) == 4
    json_payload = json.loads(Path(tmp_path, "records.json").read_text(encoding="utf-8"))
    assert len(json_payload["records"]) == 2
    assert "Alice" in Path(tmp_path, "records.csv").read_text(encoding="utf-8")
    assert len(Path(tmp_path, "export_log.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 4


def test_data_export_pipeline_reports_validation_failures_and_skips_invalid_records(tmp_path):
    pipeline = DataExportPipeline(
        schema=make_schema(),
        destinations=[
            DataExportDestination(
                destination_type=DataExportDestinationType.FILE,
                destination_name="json-out",
                file_path=str(Path(tmp_path) / "records.json"),
                file_format=DataExportFileFormat.JSON,
            )
        ],
    )
    pipeline.buffer_records(
        [
            StructuredDataRecord(values={"name": "Alice", "age": 31}),
            StructuredDataRecord(values={"name": "Broken", "age": "old"}),
            StructuredDataRecord(values={"age": 19}),
        ]
    )

    result = pipeline.flush(workflow_id="wf-2")

    assert result.succeeded is True
    assert len(result.validation_failures) == 2
    payload = json.loads(Path(tmp_path, "records.json").read_text(encoding="utf-8"))
    assert len(payload["records"]) == 1
    assert payload["records"][0]["name"] == "Alice"


def test_data_export_pipeline_retries_failed_export_and_succeeds(tmp_path):
    api = FakeAPIExporter(fail_first=True)
    pipeline = DataExportPipeline(
        schema=make_schema(),
        destinations=[
            DataExportDestination(
                destination_type=DataExportDestinationType.API,
                destination_name="api-out",
                api_endpoint="https://example.invalid/retry",
            )
        ],
        api_exporter=api,
        retry_engine=ExponentialBackoffRetryEngine(sleep_fn=lambda _: None),
        retry_configuration=RetryConfiguration(
            max_retry_count=1,
            initial_delay_seconds=0.0,
            exception_rules=[
                RetryExceptionRule(
                    exception_type_name="RuntimeError",
                    disposition=RetryDisposition.RETRY,
                )
            ],
        ),
    )
    pipeline.buffer_records([StructuredDataRecord(values={"name": "Alice", "age": 31})])

    result = pipeline.flush(workflow_id="wf-3")

    assert result.succeeded is True
    assert len(api.calls) == 2


def test_data_export_pipeline_reports_retry_failure_when_export_keeps_failing(tmp_path):
    class AlwaysFailAPI:
        def push_records(self, *, endpoint, records):
            raise RuntimeError("still failing")

    pipeline = DataExportPipeline(
        schema=make_schema(),
        destinations=[
            DataExportDestination(
                destination_type=DataExportDestinationType.API,
                destination_name="api-out",
                api_endpoint="https://example.invalid/fail",
            )
        ],
        api_exporter=AlwaysFailAPI(),
        retry_engine=ExponentialBackoffRetryEngine(sleep_fn=lambda _: None),
        retry_configuration=RetryConfiguration(
            max_retry_count=1,
            initial_delay_seconds=0.0,
            exception_rules=[
                RetryExceptionRule(
                    exception_type_name="RuntimeError",
                    disposition=RetryDisposition.RETRY,
                )
            ],
        ),
    )
    pipeline.buffer_records([StructuredDataRecord(values={"name": "Alice", "age": 31})])

    result = pipeline.flush(workflow_id="wf-4")

    assert result.succeeded is False
    assert result.retry_failure is not None
    assert result.destination_results[0].success is False
