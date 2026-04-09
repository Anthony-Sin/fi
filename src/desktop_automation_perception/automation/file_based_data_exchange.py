from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

from desktop_automation_perception.models import (
    AllowlistCheckRequest,
    DataExportFileFormat,
    FileDataExchangeResult,
    ScreenVerificationCheck,
    ScreenVerificationResult,
)


@dataclass(slots=True)
class FileBasedDataExchange:
    shared_directory: str
    state_verifier: object
    import_trigger: Callable[[str], object] | None = None
    cleanup_after_transfer: bool = True
    default_encoding: str = "utf-8"
    allowlist_enforcer: object | None = None

    def transfer(
        self,
        data: str | dict[str, Any] | list[dict[str, Any]],
        *,
        file_format: DataExportFileFormat,
        verification_checks: list[ScreenVerificationCheck],
        encoding: str | None = None,
        import_trigger: Callable[[str], object] | None = None,
        file_stem: str = "exchange",
        workflow_id: str | None = None,
        step_name: str = "file_based_data_exchange",
    ) -> FileDataExchangeResult:
        exchange_path = self._build_exchange_path(file_stem=file_stem, file_format=file_format)
        allowlist_result = self._check_allowlist(
            exchange_path=exchange_path,
            workflow_id=workflow_id,
            step_name=step_name,
        )
        if allowlist_result is not None:
            return allowlist_result
        selected_encoding = encoding or self.default_encoding
        payload_preview = self.render_payload(data, file_format=file_format)
        cleaned_up = False
        result: FileDataExchangeResult | None = None

        try:
            self._write_exchange_file(exchange_path, data, file_format=file_format, encoding=selected_encoding)
            importer = import_trigger or self.import_trigger
            if importer is None:
                result = FileDataExchangeResult(
                    succeeded=False,
                    file_path=str(exchange_path),
                    file_format=file_format,
                    encoding=selected_encoding,
                    payload_preview=payload_preview,
                    reason="No import trigger was provided for the target application.",
                )
                return result

            import_result = importer(str(exchange_path))
            verification = self.state_verifier.verify(verification_checks)
            if verification.failed_checks:
                result = FileDataExchangeResult(
                    succeeded=False,
                    file_path=str(exchange_path),
                    file_format=file_format,
                    encoding=selected_encoding,
                    import_result=import_result,
                    verification=verification,
                    payload_preview=payload_preview,
                    reason="Target application did not reach the expected post-import UI state.",
                )
                return result

            result = FileDataExchangeResult(
                succeeded=True,
                file_path=str(exchange_path),
                file_format=file_format,
                encoding=selected_encoding,
                import_result=import_result,
                verification=verification,
                payload_preview=payload_preview,
            )
            return result
        except Exception as exc:
            result = FileDataExchangeResult(
                succeeded=False,
                file_path=str(exchange_path),
                file_format=file_format,
                encoding=selected_encoding,
                payload_preview=payload_preview,
                reason=str(exc),
            )
            return result
        finally:
            if self.cleanup_after_transfer and exchange_path.exists():
                exchange_path.unlink()
                cleaned_up = True
            if result is not None:
                result.cleaned_up = cleaned_up

    def _check_allowlist(
        self,
        *,
        exchange_path: Path,
        workflow_id: str | None,
        step_name: str,
    ) -> FileDataExchangeResult | None:
        if self.allowlist_enforcer is None:
            return None
        decision = self.allowlist_enforcer.evaluate(
            AllowlistCheckRequest(
                workflow_id=workflow_id,
                step_name=step_name,
                action_type="file_transfer",
                file_path=str(exchange_path),
            )
        )
        if decision.allowed:
            return None
        return FileDataExchangeResult(
            succeeded=False,
            file_path=str(exchange_path),
            reason=decision.reason,
        )

    def render_payload(
        self,
        data: str | dict[str, Any] | list[dict[str, Any]],
        *,
        file_format: DataExportFileFormat,
    ) -> str:
        if file_format is DataExportFileFormat.JSON:
            return json.dumps(data, indent=2, sort_keys=True)
        if file_format is DataExportFileFormat.CSV:
            rows = self._normalize_rows(data)
            if not rows:
                return ""
            fieldnames = self._fieldnames(rows)
            lines = [",".join(fieldnames)]
            for row in rows:
                lines.append(",".join(self._csv_string(row.get(name)) for name in fieldnames))
            return "\n".join(lines)
        if file_format is DataExportFileFormat.XML:
            return self._render_xml(data)
        raise ValueError(f"Unsupported file format: {file_format}")

    def _write_exchange_file(
        self,
        path: Path,
        data: str | dict[str, Any] | list[dict[str, Any]],
        *,
        file_format: DataExportFileFormat,
        encoding: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if file_format is DataExportFileFormat.JSON:
            path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding=encoding)
            return
        if file_format is DataExportFileFormat.XML:
            path.write_text(self._render_xml(data), encoding=encoding)
            return
        if file_format is DataExportFileFormat.CSV:
            rows = self._normalize_rows(data)
            fieldnames = self._fieldnames(rows)
            with path.open("w", encoding=encoding, newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                if fieldnames:
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({key: self._stringify_value(value) for key, value in row.items()})
            return
        raise ValueError(f"Unsupported file format: {file_format}")

    def _render_xml(self, data: str | dict[str, Any] | list[dict[str, Any]]) -> str:
        root = Element("exchange")
        if isinstance(data, str):
            payload = SubElement(root, "value")
            payload.text = data
        elif isinstance(data, dict):
            self._append_xml_mapping(root, data)
        else:
            items = SubElement(root, "records")
            for row in self._normalize_rows(data):
                item = SubElement(items, "record")
                self._append_xml_mapping(item, row)
        return tostring(root, encoding="unicode")

    def _append_xml_mapping(self, parent: Element, values: dict[str, Any]) -> None:
        for key, value in values.items():
            node = SubElement(parent, str(key))
            if isinstance(value, dict):
                self._append_xml_mapping(node, value)
            elif isinstance(value, list):
                for item in value:
                    child = SubElement(node, "item")
                    if isinstance(item, dict):
                        self._append_xml_mapping(child, item)
                    else:
                        child.text = self._stringify_value(item)
            else:
                node.text = self._stringify_value(value)

    def _normalize_rows(self, data: str | dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [dict(row) for row in data]
        if isinstance(data, dict):
            return [dict(data)]
        return [{"value": data}]

    def _fieldnames(self, rows: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.keys():
                if key in seen:
                    continue
                seen.add(key)
                names.append(str(key))
        return names

    def _stringify_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    def _csv_string(self, value: Any) -> str:
        text = self._stringify_value(value)
        if any(marker in text for marker in [",", "\"", "\n"]):
            return f"\"{text.replace('\"', '\"\"')}\""
        return text

    def _build_exchange_path(self, *, file_stem: str, file_format: DataExportFileFormat) -> Path:
        suffix = f".{file_format.value}"
        return Path(self.shared_directory) / f"{file_stem}_{uuid4().hex}{suffix}"
