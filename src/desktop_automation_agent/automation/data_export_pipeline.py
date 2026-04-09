from __future__ import annotations

from desktop_automation_agent._time import utc_now

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from desktop_automation_agent.contracts import APIExporter, AuditLogger, DatabaseExporter
from desktop_automation_agent.models import (
    DataExportDestination,
    DataExportDestinationType,
    DataExportFileFormat,
    DataExportLogEntry,
    DataExportResult,
    DataExportValidationFailure,
    RetryConfiguration,
    RetryFailureResult,
    StructuredDataFieldType,
    StructuredDataRecord,
    StructuredDataSchema,
)
from desktop_automation_agent.resilience.retry_engine import (
    ExponentialBackoffRetryEngine,
    RetryExhaustedError,
)


@dataclass(slots=True)
class DataExportPipeline:
    schema: StructuredDataSchema
    destinations: list[DataExportDestination]
    retry_engine: ExponentialBackoffRetryEngine[int] | None = None
    retry_configuration: RetryConfiguration | None = None
    database_exporter: DatabaseExporter | None = None
    api_exporter: APIExporter | None = None
    audit_logger: AuditLogger | None = None
    log_storage_path: str | None = None
    buffered_records: list[StructuredDataRecord] = field(default_factory=list)

    def buffer_records(
        self,
        records: list[StructuredDataRecord],
    ) -> DataExportResult:
        self.buffered_records.extend(self._copy_record(record) for record in records)
        return DataExportResult(
            succeeded=True,
            buffered_count=len(self.buffered_records),
        )

    def flush(
        self,
        *,
        workflow_id: str,
        step_name: str = "data_export",
    ) -> DataExportResult:
        buffered_count = len(self.buffered_records)
        deduped_records = self._deduplicate_records(self.buffered_records)
        validation_failures = self._validate_records(deduped_records)
        valid_records = [
            record for index, record in enumerate(deduped_records)
            if not any(failure.record_index == index for failure in validation_failures)
        ]

        destination_results: list[DataExportLogEntry] = []
        exported_count = 0
        retry_failure: RetryFailureResult | None = None

        for destination in self.destinations:
            started_at = utc_now()
            try:
                count = self._run_export(destination, workflow_id, valid_records)
                detail = f"Exported {count} record(s)."
                log_entry = DataExportLogEntry(
                    timestamp=started_at,
                    destination_name=destination.destination_name,
                    destination_type=destination.destination_type,
                    exported_count=count,
                    validation_failure_count=len(validation_failures),
                    success=True,
                    detail=detail,
                )
                exported_count = max(exported_count, count)
            except RetryExhaustedError as exc:
                retry_failure = exc.failure
                log_entry = DataExportLogEntry(
                    timestamp=started_at,
                    destination_name=destination.destination_name,
                    destination_type=destination.destination_type,
                    exported_count=0,
                    validation_failure_count=len(validation_failures),
                    success=False,
                    detail=exc.failure.reason,
                )
            except Exception as exc:
                log_entry = DataExportLogEntry(
                    timestamp=started_at,
                    destination_name=destination.destination_name,
                    destination_type=destination.destination_type,
                    exported_count=0,
                    validation_failure_count=len(validation_failures),
                    success=False,
                    detail=str(exc),
                )
            destination_results.append(log_entry)
            self._log_export_operation(
                workflow_id=workflow_id,
                step_name=step_name,
                destination=destination,
                log_entry=log_entry,
                buffered_count=buffered_count,
                deduplicated_count=len(deduped_records),
            )

        all_succeeded = bool(destination_results) and all(item.success for item in destination_results)
        if all_succeeded:
            self.buffered_records.clear()
        return DataExportResult(
            succeeded=all_succeeded,
            buffered_count=buffered_count,
            deduplicated_count=len(deduped_records),
            exported_count=exported_count,
            validation_failures=validation_failures,
            destination_results=destination_results,
            retry_failure=retry_failure,
            reason=None if all_succeeded else "One or more export destinations failed.",
        )

    def list_export_logs(self) -> list[DataExportLogEntry]:
        if self.log_storage_path is None:
            return []
        path = Path(self.log_storage_path)
        if not path.exists():
            return []
        entries: list[DataExportLogEntry] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                normalized = line.strip()
                if not normalized:
                    continue
                payload = json.loads(normalized)
                entries.append(
                    DataExportLogEntry(
                        timestamp=datetime.fromisoformat(payload["timestamp"]),
                        destination_name=payload["destination_name"],
                        destination_type=DataExportDestinationType(payload["destination_type"]),
                        exported_count=int(payload.get("exported_count", 0)),
                        validation_failure_count=int(payload.get("validation_failure_count", 0)),
                        success=bool(payload.get("success", False)),
                        detail=payload.get("detail"),
                    )
                )
        return entries

    def _run_export(
        self,
        destination: DataExportDestination,
        workflow_id: str,
        records: list[StructuredDataRecord],
    ) -> int:
        prepared = [self._serialize_record(record, workflow_id, destination.include_workflow_id) for record in records]

        def action() -> int:
            if destination.destination_type is DataExportDestinationType.FILE:
                return self._export_file(destination, prepared)
            if destination.destination_type is DataExportDestinationType.DATABASE:
                return self._export_database(destination, prepared)
            if destination.destination_type is DataExportDestinationType.API:
                return self._export_api(destination, prepared)
            raise ValueError("Unsupported export destination.")

        if self.retry_engine is None:
            return action()
        return self.retry_engine.run(action, configuration=self.retry_configuration)

    def _export_file(
        self,
        destination: DataExportDestination,
        records: list[dict[str, Any]],
    ) -> int:
        if destination.file_path is None or destination.file_format is None:
            raise ValueError("File export requires a path and file format.")
        path = Path(destination.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if destination.file_format is DataExportFileFormat.JSON:
            existing: list[dict[str, Any]] = []
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                existing = list(payload.get("records", []))
            existing.extend(records)
            path.write_text(json.dumps({"records": existing}, indent=2), encoding="utf-8")
            return len(records)
        if destination.file_format is DataExportFileFormat.CSV:
            fieldnames = sorted({key for record in records for key in record.keys()})
            write_header = not path.exists() or path.stat().st_size == 0
            with path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                for record in records:
                    writer.writerow(record)
            return len(records)
        if destination.file_format is DataExportFileFormat.XML:
            root = Element("records")
            for record in records:
                record_element = SubElement(root, "record")
                for key, value in record.items():
                    field_element = SubElement(record_element, str(key))
                    field_element.text = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            path.write_text(tostring(root, encoding="unicode"), encoding="utf-8")
            return len(records)
        raise ValueError("Unsupported file format.")

    def _export_database(
        self,
        destination: DataExportDestination,
        records: list[dict[str, Any]],
    ) -> int:
        if self.database_exporter is None or destination.database_table is None:
            raise ValueError("Database export requires a database exporter and table name.")
        return int(self.database_exporter.insert_records(table=destination.database_table, records=records))

    def _export_api(
        self,
        destination: DataExportDestination,
        records: list[dict[str, Any]],
    ) -> int:
        if self.api_exporter is None or destination.api_endpoint is None:
            raise ValueError("API export requires an API exporter and endpoint.")
        return int(self.api_exporter.push_records(endpoint=destination.api_endpoint, records=records))

    def _deduplicate_records(
        self,
        records: list[StructuredDataRecord],
    ) -> list[StructuredDataRecord]:
        deduped: list[StructuredDataRecord] = []
        seen: set[str] = set()
        for record in records:
            key = json.dumps(record.values, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(self._copy_record(record))
        return deduped

    def _validate_records(
        self,
        records: list[StructuredDataRecord],
    ) -> list[DataExportValidationFailure]:
        failures: list[DataExportValidationFailure] = []
        field_map = {field.field_name: field for field in self.schema.fields}
        for index, record in enumerate(records):
            for message in record.validation_errors:
                failures.append(
                    DataExportValidationFailure(
                        record_index=index,
                        reason=message,
                        record_values=dict(record.values),
                    )
                )
            for field_name, field_schema in field_map.items():
                value = record.values.get(field_name)
                if value is None:
                    if field_schema.required:
                        failures.append(
                            DataExportValidationFailure(
                                record_index=index,
                                field_name=field_name,
                                reason="Required field is missing.",
                                record_values=dict(record.values),
                            )
                        )
                    continue
                if not self._value_matches_type(value, field_schema.field_type):
                    failures.append(
                        DataExportValidationFailure(
                            record_index=index,
                            field_name=field_name,
                            reason=f"Value does not match expected type {field_schema.field_type.value}.",
                            record_values=dict(record.values),
                        )
                    )
        return failures

    def _value_matches_type(
        self,
        value: Any,
        field_type: StructuredDataFieldType,
    ) -> bool:
        if field_type is StructuredDataFieldType.STRING:
            return isinstance(value, str)
        if field_type is StructuredDataFieldType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        if field_type is StructuredDataFieldType.NUMBER:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if field_type is StructuredDataFieldType.BOOLEAN:
            return isinstance(value, bool)
        if field_type is StructuredDataFieldType.DATE:
            return isinstance(value, str)
        return True

    def _serialize_record(
        self,
        record: StructuredDataRecord,
        workflow_id: str,
        include_workflow_id: bool,
    ) -> dict[str, Any]:
        payload = dict(record.values)
        payload["_page_number"] = record.page_number
        payload["_source_mode"] = record.source_mode.value
        if include_workflow_id:
            payload["_workflow_id"] = workflow_id
        return payload

    def _copy_record(
        self,
        record: StructuredDataRecord,
    ) -> StructuredDataRecord:
        return StructuredDataRecord(
            values=dict(record.values),
            page_number=record.page_number,
            source_mode=record.source_mode,
            validation_errors=list(record.validation_errors),
        )

    def _log_export_operation(
        self,
        *,
        workflow_id: str,
        step_name: str,
        destination: DataExportDestination,
        log_entry: DataExportLogEntry,
        buffered_count: int,
        deduplicated_count: int,
    ) -> None:
        if self.audit_logger is not None:
            self.audit_logger.log_action(
                workflow_id=workflow_id,
                step_name=step_name,
                action_type="data_export",
                target_element=destination.destination_name,
                input_data={
                    "destination_type": destination.destination_type.value,
                    "buffered_count": buffered_count,
                    "deduplicated_count": deduplicated_count,
                },
                output_data={
                    "exported_count": log_entry.exported_count,
                    "validation_failure_count": log_entry.validation_failure_count,
                    "detail": log_entry.detail,
                },
                success=log_entry.success,
                timestamp=log_entry.timestamp,
            )
        if self.log_storage_path is not None:
            path = Path(self.log_storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": log_entry.timestamp.isoformat(),
                "destination_name": log_entry.destination_name,
                "destination_type": log_entry.destination_type.value,
                "exported_count": log_entry.exported_count,
                "validation_failure_count": log_entry.validation_failure_count,
                "success": log_entry.success,
                "detail": log_entry.detail,
                "workflow_id": workflow_id,
                "step_name": step_name,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True))
                handle.write("\n")


