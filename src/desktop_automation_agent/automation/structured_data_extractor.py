from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desktop_automation_agent.contracts import AccessibilityReader, InputRunner, OCRExtractor
from desktop_automation_agent.models import (
    AIInterfaceElementMatch,
    AIInterfaceElementSelector,
    AccessibilityElement,
    InputAction,
    InputActionType,
    InputTarget,
    OCRTextBlock,
    PaginationAdvanceMode,
    SelectorStrategy,
    StructuredDataExtractionConfiguration,
    StructuredDataExtractionMode,
    StructuredDataExtractionResult,
    StructuredDataFieldSchema,
    StructuredDataFieldType,
    StructuredDataPageResult,
    StructuredDataRecord,
)


@dataclass(slots=True)
class StructuredDataExtractor:
    accessibility_reader: AccessibilityReader | None = None
    ocr_extractor: OCRExtractor | None = None
    input_runner: InputRunner | None = None

    def extract(
        self,
        configuration: StructuredDataExtractionConfiguration,
    ) -> StructuredDataExtractionResult:
        records: list[StructuredDataRecord] = []
        pages: list[StructuredDataPageResult] = []
        max_pages = configuration.pagination.max_pages if configuration.pagination is not None else 1

        for page_number in range(1, max_pages + 1):
            page_result = self._extract_page(configuration, page_number)
            pages.append(page_result)
            records.extend(page_result.records)
            if page_result.reason and not page_result.records:
                return StructuredDataExtractionResult(
                    succeeded=False,
                    records=records,
                    page_results=pages,
                    reason=page_result.reason,
                )
            if configuration.pagination is None or page_number >= max_pages:
                break
            advanced = self._advance_to_next_page(configuration)
            page_result.advanced_to_next_page = advanced
            if not advanced:
                break

        succeeded = bool(records) or all(page.reason is None for page in pages)
        return StructuredDataExtractionResult(
            succeeded=succeeded,
            records=records,
            page_results=pages,
            reason=None if succeeded else "No structured data could be extracted.",
        )

    def _extract_page(
        self,
        configuration: StructuredDataExtractionConfiguration,
        page_number: int,
    ) -> StructuredDataPageResult:
        if configuration.mode is StructuredDataExtractionMode.TABLE:
            page_records, raw_text, reason = self._extract_table(configuration, page_number)
        elif configuration.mode is StructuredDataExtractionMode.FORM:
            page_records, raw_text, reason = self._extract_form(configuration, page_number)
        else:
            page_records, raw_text, reason = self._extract_text_block(configuration, page_number)
        return StructuredDataPageResult(
            page_number=page_number,
            records=page_records,
            raw_text=raw_text,
            reason=reason,
        )

    def _extract_table(
        self,
        configuration: StructuredDataExtractionConfiguration,
        page_number: int,
    ) -> tuple[list[StructuredDataRecord], str | None, str | None]:
        rows = self._extract_table_rows_via_accessibility(configuration)
        if not rows:
            rows = self._extract_table_rows_via_ocr(configuration)
        if not rows:
            return ([], None, "Table data could not be detected.")

        header_row: list[str] | None = rows[0] if configuration.has_header_row and rows else None
        data_rows = rows[1:] if configuration.has_header_row and len(rows) > 1 else rows
        if configuration.max_rows_per_page is not None:
            data_rows = data_rows[: configuration.max_rows_per_page]

        records: list[StructuredDataRecord] = []
        for row in data_rows:
            values: dict[str, Any] = {}
            validation_errors: list[str] = []
            for index, field_schema in enumerate(configuration.schema.fields):
                value = self._map_table_cell(field_schema, row, header_row, index)
                coerced, error = self._coerce_value(value, field_schema)
                values[field_schema.field_name] = coerced
                if error is not None:
                    validation_errors.append(error)
            records.append(
                StructuredDataRecord(
                    values=values,
                    page_number=page_number,
                    source_mode=StructuredDataExtractionMode.TABLE,
                    validation_errors=validation_errors,
                )
            )

        raw_text = "\n".join(" | ".join(row) for row in rows)
        return (records, raw_text, None)

    def _extract_form(
        self,
        configuration: StructuredDataExtractionConfiguration,
        page_number: int,
    ) -> tuple[list[StructuredDataRecord], str | None, str | None]:
        values: dict[str, Any] = {}
        validation_errors: list[str] = []
        fragments: list[str] = []

        for field_schema in configuration.schema.fields:
            raw_value = self._read_form_field_value(field_schema, configuration)
            coerced, error = self._coerce_value(raw_value, field_schema)
            values[field_schema.field_name] = coerced
            if raw_value is not None:
                fragments.append(f"{field_schema.field_name}: {raw_value}")
            if error is not None:
                validation_errors.append(error)

        if not values:
            return ([], None, "Form field values could not be extracted.")

        return (
            [
                StructuredDataRecord(
                    values=values,
                    page_number=page_number,
                    source_mode=StructuredDataExtractionMode.FORM,
                    validation_errors=validation_errors,
                )
            ],
            "\n".join(fragments),
            None,
        )

    def _extract_text_block(
        self,
        configuration: StructuredDataExtractionConfiguration,
        page_number: int,
    ) -> tuple[list[StructuredDataRecord], str | None, str | None]:
        raw_text = self._read_text_block(configuration)
        if raw_text is None:
            return ([], None, "Text block could not be extracted.")

        line_map = self._parse_label_value_lines(raw_text)
        values: dict[str, Any] = {}
        validation_errors: list[str] = []

        for field_schema in configuration.schema.fields:
            raw_value = line_map.get(field_schema.field_name.casefold())
            if raw_value is None:
                for alias in field_schema.aliases:
                    raw_value = line_map.get(alias.casefold())
                    if raw_value is not None:
                        break
            if raw_value is None and len(configuration.schema.fields) == 1:
                raw_value = raw_text
            coerced, error = self._coerce_value(raw_value, field_schema)
            values[field_schema.field_name] = coerced
            if error is not None:
                validation_errors.append(error)

        return (
            [
                StructuredDataRecord(
                    values=values,
                    page_number=page_number,
                    source_mode=StructuredDataExtractionMode.TEXT_BLOCK,
                    validation_errors=validation_errors,
                )
            ],
            raw_text,
            None,
        )

    def _extract_table_rows_via_accessibility(
        self,
        configuration: StructuredDataExtractionConfiguration,
    ) -> list[list[str]]:
        if self.accessibility_reader is None:
            return []
        tree = self.accessibility_reader.read_active_application_tree()
        root = getattr(tree, "root", None)
        if root is None:
            return []
        table_root = self._resolve_selector(configuration.table_selector) if configuration.table_selector else None
        search_root = table_root.element if table_root is not None and table_root.element is not None else root
        rows: list[list[str]] = []
        for element in self._walk(search_root):
            role = (element.role or "").casefold()
            if role not in {"row", "data item", "list item"}:
                continue
            cells = [self._normalize_text(self.accessibility_reader.get_element_text(child) or "") for child in element.children]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(cells)
        return rows

    def _extract_table_rows_via_ocr(
        self,
        configuration: StructuredDataExtractionConfiguration,
    ) -> list[list[str]]:
        if self.ocr_extractor is None:
            return []
        region = configuration.table_selector.bounds if configuration.table_selector and configuration.table_selector.bounds else None
        extraction = self.ocr_extractor.extract_text(
            region_of_interest=region,
            language=configuration.ocr_language,
            minimum_confidence=configuration.minimum_ocr_confidence,
        )
        blocks = sorted(
            getattr(extraction, "blocks", []),
            key=lambda block: (block.bounds[1], block.bounds[0]),
        )
        grouped_rows: list[list[OCRTextBlock]] = []
        for block in blocks:
            if not grouped_rows:
                grouped_rows.append([block])
                continue
            current_top = grouped_rows[-1][0].bounds[1]
            if abs(block.bounds[1] - current_top) <= configuration.row_merge_tolerance:
                grouped_rows[-1].append(block)
            else:
                grouped_rows.append([block])

        rows: list[list[str]] = []
        for block_row in grouped_rows:
            ordered = sorted(block_row, key=lambda block: block.bounds[0])
            texts = [self._normalize_text(block.text) for block in ordered if self._normalize_text(block.text)]
            if texts:
                rows.append(texts)
        return rows

    def _read_form_field_value(
        self,
        field_schema: StructuredDataFieldSchema,
        configuration: StructuredDataExtractionConfiguration,
    ) -> str | None:
        match = self._resolve_selector(field_schema.selector) if field_schema.selector is not None else None
        if match is not None and match.text:
            return match.text
        if match is not None and match.element is not None and self.accessibility_reader is not None:
            text = self.accessibility_reader.get_element_text(match.element)
            if text:
                return self._normalize_text(text)

        if self.accessibility_reader is not None:
            candidates = [field_schema.source_name, field_schema.field_name, *field_schema.aliases]
            for candidate in candidates:
                if not candidate:
                    continue
                query = self.accessibility_reader.find_elements(name=candidate, role=None, value=None)
                for element in getattr(query, "matches", []):
                    text = self.accessibility_reader.get_element_text(element)
                    if text:
                        return self._normalize_text(text)

        if self.ocr_extractor is not None and match is not None and match.bounds is not None:
            extraction = self.ocr_extractor.extract_text(
                region_of_interest=match.bounds,
                language=configuration.ocr_language,
                minimum_confidence=configuration.minimum_ocr_confidence,
            )
            return self._combine_ocr_blocks(getattr(extraction, "blocks", [])) or None
        return None

    def _read_text_block(
        self,
        configuration: StructuredDataExtractionConfiguration,
    ) -> str | None:
        match = self._resolve_selector(configuration.text_block_selector) if configuration.text_block_selector else None
        if match is not None and match.text:
            return match.text
        if match is not None and match.element is not None and self.accessibility_reader is not None:
            text = self.accessibility_reader.get_element_text(match.element)
            if text:
                return self._normalize_text(text)
        if self.ocr_extractor is not None:
            region = match.bounds if match is not None else None
            extraction = self.ocr_extractor.extract_text(
                region_of_interest=region,
                language=configuration.ocr_language,
                minimum_confidence=configuration.minimum_ocr_confidence,
            )
            text = self._combine_ocr_blocks(getattr(extraction, "blocks", []))
            return text or None
        return None

    def _advance_to_next_page(
        self,
        configuration: StructuredDataExtractionConfiguration,
    ) -> bool:
        if configuration.pagination is None:
            return False
        if configuration.pagination.disabled_selector is not None:
            disabled = self._resolve_selector(configuration.pagination.disabled_selector)
            if disabled is not None:
                return False
        next_match = self._resolve_selector(configuration.pagination.next_page_selector)
        if next_match is None:
            return False
        if configuration.pagination.advance_mode is PaginationAdvanceMode.HOTKEY:
            if self.input_runner is None:
                return False
            result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.HOTKEY,
                        target=InputTarget(element_bounds=next_match.bounds),
                        hotkey=configuration.pagination.advance_hotkey,
                    )
                ]
            )
            return getattr(result, "succeeded", True)
        if self.input_runner is None or next_match.center is None:
            return False
        result = self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.CLICK,
                    target=InputTarget(element_bounds=next_match.bounds),
                    position=next_match.center,
                )
            ]
        )
        return getattr(result, "succeeded", True)

    def _map_table_cell(
        self,
        field_schema: StructuredDataFieldSchema,
        row: list[str],
        header_row: list[str] | None,
        default_index: int,
    ) -> str | None:
        if field_schema.column_index is not None and field_schema.column_index < len(row):
            return row[field_schema.column_index]
        if header_row is not None:
            targets = {
                value.casefold()
                for value in [field_schema.field_name, field_schema.source_name, *field_schema.aliases]
                if value
            }
            for index, header in enumerate(header_row):
                if header.casefold() in targets and index < len(row):
                    return row[index]
        if default_index < len(row):
            return row[default_index]
        return None

    def _coerce_value(
        self,
        value: str | None,
        field_schema: StructuredDataFieldSchema,
    ) -> tuple[Any, str | None]:
        if value is None or value == "":
            if field_schema.required:
                return (None, f"Field {field_schema.field_name!r} is required but missing.")
            return (None, None)
        normalized = self._normalize_text(value)
        if field_schema.field_type is StructuredDataFieldType.STRING:
            return (normalized, None)
        if field_schema.field_type is StructuredDataFieldType.INTEGER:
            try:
                return (int(normalized.replace(",", "")), None)
            except ValueError:
                return (normalized, f"Field {field_schema.field_name!r} is not a valid integer.")
        if field_schema.field_type is StructuredDataFieldType.NUMBER:
            try:
                return (float(normalized.replace(",", "")), None)
            except ValueError:
                return (normalized, f"Field {field_schema.field_name!r} is not a valid number.")
        if field_schema.field_type is StructuredDataFieldType.BOOLEAN:
            lowered = normalized.casefold()
            if lowered in {"true", "yes", "checked", "on"}:
                return (True, None)
            if lowered in {"false", "no", "unchecked", "off"}:
                return (False, None)
            return (normalized, f"Field {field_schema.field_name!r} is not a valid boolean.")
        return (normalized, None)

    def _resolve_selector(
        self,
        selector: AIInterfaceElementSelector | None,
    ) -> AIInterfaceElementMatch | None:
        if selector is None:
            return None
        if selector.bounds is not None:
            return AIInterfaceElementMatch(
                selector=selector,
                strategy=SelectorStrategy.DIRECT_BOUNDS,
                bounds=selector.bounds,
                center=self._center(selector.bounds),
                confidence=1.0,
            )
        for strategy in selector.strategies:
            if strategy is SelectorStrategy.ACCESSIBILITY:
                match = self._resolve_accessibility(selector)
            elif strategy is SelectorStrategy.OCR:
                match = self._resolve_ocr(selector)
            else:
                match = None
            if match is not None:
                return match
        return None

    def _resolve_accessibility(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.accessibility_reader is None:
            return None
        query = self.accessibility_reader.find_elements(
            name=selector.name,
            role=selector.role,
            value=selector.value,
        )
        matches = getattr(query, "matches", [])
        if not matches:
            return None
        element = matches[0]
        text = self.accessibility_reader.get_element_text(element)
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.ACCESSIBILITY,
            bounds=element.bounds,
            center=self._center(element.bounds),
            text=self._normalize_text(text) if text else None,
            confidence=1.0,
            element=element,
        )

    def _resolve_ocr(self, selector: AIInterfaceElementSelector) -> AIInterfaceElementMatch | None:
        if self.ocr_extractor is None or not selector.target_text:
            return None
        result = self.ocr_extractor.find_text(
            target=selector.target_text,
            region_of_interest=selector.region_of_interest,
        )
        if not getattr(result, "succeeded", False):
            return None
        bounds = getattr(result, "bounds", None)
        return AIInterfaceElementMatch(
            selector=selector,
            strategy=SelectorStrategy.OCR,
            bounds=bounds,
            center=self._center(bounds),
            text=getattr(result, "matched_text", None),
            confidence=float(getattr(result, "confidence", 0.0)),
        )

    def _parse_label_value_lines(self, text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in self._normalize_text(text).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip().casefold()] = value.strip()
        return values

    def _combine_ocr_blocks(self, blocks: list[OCRTextBlock]) -> str:
        if not blocks:
            return ""
        ordered = sorted(blocks, key=lambda block: (block.bounds[1], block.bounds[0]))
        return "\n".join(block.text.strip() for block in ordered if block.text.strip())

    def _normalize_text(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def _walk(self, element: AccessibilityElement):
        yield element
        for child in element.children:
            yield from self._walk(child)

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
