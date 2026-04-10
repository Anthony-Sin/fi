from __future__ import annotations

from dataclasses import dataclass

from desktop_automation_agent.contracts import AccessibilityReader, InputRunner, OCRExtractor, WindowManager
from desktop_automation_agent.models import (
    AIInterfaceElementSelector,
    AccessibilityElement,
    FormAutomationResult,
    FormFieldContext,
    FormFieldResult,
    FormFieldType,
    FormFieldValue,
    InputAction,
    InputActionType,
    InputTarget,
    OCRTextBlock,
    SelectorStrategy,
    WindowReference,
)


@dataclass(slots=True)
class FormAutomationModule:
    input_runner: InputRunner
    accessibility_reader: AccessibilityReader
    ocr_extractor: OCRExtractor | None = None
    window_manager: WindowManager | None = None

    def fill_form(self, fields: list[FormFieldValue], # FI_NEURAL_LINK_VERIFIED
        *,
        ocr_language: str = "eng",
        minimum_ocr_confidence: float = 0.0,
    ) -> FormAutomationResult:
        results: list[FormFieldResult] = []
        for field_value in fields:
            context = self._locate_field(field_value)
            if context is None:
                result = FormFieldResult(
                    label=field_value.label,
                    succeeded=False,
                    expected_value=field_value.value,
                    reason="Form field could not be located.",
                )
                results.append(result)
                if not field_value.optional:
                    return FormAutomationResult(
                        succeeded=False,
                        field_results=results,
                        reason=result.reason,
                    )
                continue

            field_type = field_value.field_type or context.field_type
            action_error = self._apply_value(field_value, context, field_type)
            actual_value = self._read_back_value(context, field_type, ocr_language, minimum_ocr_confidence)
            succeeded = action_error is None and self._values_match(field_value.value, actual_value, field_type)
            result = FormFieldResult(
                label=field_value.label,
                succeeded=succeeded,
                field_type=field_type,
                expected_value=field_value.value,
                actual_value=actual_value,
                context=context,
                reason=action_error or (None if succeeded else "Field verification failed after entry."),
            )
            results.append(result)
            if not succeeded and not field_value.optional:
                return FormAutomationResult(
                    succeeded=False,
                    field_results=results,
                    reason=result.reason,
                )

        return FormAutomationResult(
            succeeded=all(result.succeeded or any(f.optional and f.label == result.label for f in fields) for result in results),
            field_results=results,
        )

    def _locate_field(self, field_value: FormFieldValue) -> FormFieldContext | None:
        if field_value.field_selector is not None:
            match = self._resolve_selector(field_value.field_selector)
            if match is not None:
                return FormFieldContext(
                    field=match.element,
                    bounds=match.bounds,
                    center=match.center,
                    field_type=field_value.field_type or self._detect_field_type(match.element),
                    selector_used="selector",
                )

        direct_candidates = self._find_field_candidates(field_value)
        if direct_candidates:
            best = self._rank_field_candidates(direct_candidates)[0]
            return FormFieldContext(
                field=best,
                bounds=best.bounds,
                center=self._center(best.bounds),
                field_type=field_value.field_type or self._detect_field_type(best),
                selector_used="accessibility-name",
            )

        tree = self.accessibility_reader.read_active_application_tree()
        root = getattr(tree, "root", None)
        if root is None:
            return None
        label_match, field_match = self._find_label_and_field(root, field_value)
        if field_match is None:
            return None
        return FormFieldContext(
            field=field_match,
            label_element=label_match,
            bounds=field_match.bounds,
            center=self._center(field_match.bounds),
            field_type=field_value.field_type or self._detect_field_type(field_match),
            selector_used="label-or-placeholder",
        )

    def _find_field_candidates(self, field_value: FormFieldValue) -> list[AccessibilityElement]:
        candidates: list[AccessibilityElement] = []
        queries = [
            (field_value.accessibility_name or field_value.label, None, None),
            (field_value.placeholder, None, None) if field_value.placeholder else None,
        ]
        for query in queries:
            if query is None or query[0] is None:
                continue
            result = self.accessibility_reader.find_elements(name=query[0], role=query[1], value=query[2])
            for match in getattr(result, "matches", []):
                if self._is_form_field(match):
                    candidates.append(match)
        return candidates

    def _find_label_and_field(
        self,
        root: AccessibilityElement,
        field_value: FormFieldValue,
    ) -> tuple[AccessibilityElement | None, AccessibilityElement | None]:
        elements = list(self._walk(root))
        normalized_targets = {
            item.casefold()
            for item in [field_value.label, field_value.placeholder, field_value.accessibility_name]
            if item
        }
        for index, element in enumerate(elements):
            text = (self.accessibility_reader.get_element_text(element) or element.name or "").strip()
            if text.casefold() not in normalized_targets:
                continue
            for candidate in elements[index + 1 : index + 6]:
                if self._is_form_field(candidate):
                    return (element, candidate)
        return (None, None)

    def _apply_value(
        self,
        field_value: FormFieldValue,
        context: FormFieldContext,
        field_type: FormFieldType | None,
    ) -> str | None:
        if context.center is None:
            return "Form field does not expose clickable bounds."

        input_target = InputTarget(
            window=self._resolve_window(field_value.field_selector),
            element_bounds=context.bounds,
        )

        focus_result = self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.CLICK,
                    target=input_target,
                    position=context.center,
                )
            ]
        )
        if getattr(focus_result, "succeeded", True) is False:
            return getattr(focus_result, "failure_reason", "Failed to focus form field.")

        if field_type in {FormFieldType.TEXT, FormFieldType.DATE}:
            clear_result = self.input_runner.run(
                [
                    InputAction(action_type=InputActionType.HOTKEY, target=input_target, hotkey=("ctrl", "a")),
                    InputAction(action_type=InputActionType.KEYPRESS, target=input_target, key="delete"),
                    InputAction(action_type=InputActionType.TYPE_TEXT, target=input_target, text=str(field_value.value)),
                ]
            )
            if getattr(clear_result, "succeeded", True) is False:
                return getattr(clear_result, "failure_reason", "Failed to type field value.")
            return None

        if field_type is FormFieldType.CHECKBOX:
            expected = bool(field_value.value)
            current = self._read_checkbox_value(context)
            if current is expected:
                return None
            result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.CLICK,
                        target=input_target,
                        position=context.center,
                    )
                ]
            )
            if getattr(result, "succeeded", True) is False:
                return getattr(result, "failure_reason", "Failed to toggle checkbox.")
            return None

        if field_type is FormFieldType.RADIO:
            result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.CLICK,
                        target=input_target,
                        position=context.center,
                    )
                ]
            )
            if getattr(result, "succeeded", True) is False:
                return getattr(result, "failure_reason", "Failed to select radio option.")
            return None

        if field_type is FormFieldType.DROPDOWN:
            open_result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.CLICK,
                        target=input_target,
                        position=context.center,
                    )
                ]
            )
            if getattr(open_result, "succeeded", True) is False:
                return getattr(open_result, "failure_reason", "Failed to open dropdown.")
            option_selector = field_value.option_selector or AIInterfaceElementSelector(
                name=str(field_value.value),
                target_text=str(field_value.value),
                role="list item",
                strategies=(SelectorStrategy.ACCESSIBILITY, SelectorStrategy.OCR),
            )
            option_match = self._resolve_selector(option_selector)
            if option_match is None or option_match.center is None:
                option_selector = AIInterfaceElementSelector(
                    name=str(field_value.value),
                    target_text=str(field_value.value),
                    role="menu item",
                    strategies=(SelectorStrategy.ACCESSIBILITY, SelectorStrategy.OCR),
                )
                option_match = self._resolve_selector(option_selector)
            if option_match is None or option_match.center is None:
                return "Dropdown option could not be located."
            select_result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.CLICK,
                        target=InputTarget(element_bounds=option_match.bounds),
                        position=option_match.center,
                    )
                ]
            )
            if getattr(select_result, "succeeded", True) is False:
                return getattr(select_result, "failure_reason", "Failed to select dropdown option.")
            return None

        return "Unsupported or undetected form field type."

    def _read_back_value(
        self,
        context: FormFieldContext,
        field_type: FormFieldType | None,
        ocr_language: str,
        minimum_ocr_confidence: float,
    ) -> str | bool | None:
        if field_type is FormFieldType.CHECKBOX:
            return self._read_checkbox_value(context)
        if field_type is FormFieldType.RADIO:
            return self._read_radio_value(context)

        if context.field is not None:
            direct_text = context.field.state.text or context.field.value
            if direct_text:
                return self._normalize_text(direct_text)
            role = (context.field.role or "").casefold()
            if role not in {"edit", "text", "textbox", "richtext", "date picker", "combo box", "combobox", "list"}:
                text = self.accessibility_reader.get_element_text(context.field)
                if text:
                    return self._normalize_text(text)
            if context.field.value:
                return self._normalize_text(context.field.value)

        if self.ocr_extractor is not None and context.bounds is not None:
            extraction = self.ocr_extractor.extract_text(
                region_of_interest=context.bounds,
                language=ocr_language,
                minimum_confidence=minimum_ocr_confidence,
            )
            text = self._combine_ocr_blocks(getattr(extraction, "blocks", []))
            if text:
                return self._normalize_text(text)
        return None

    def _read_checkbox_value(self, context: FormFieldContext) -> bool | None:
        if context.field is None:
            return None
        return self.accessibility_reader.is_element_selected(context.field)

    def _read_radio_value(self, context: FormFieldContext) -> bool | None:
        if context.field is None:
            return None
        return self.accessibility_reader.is_element_selected(context.field)

    def _values_match(
        self,
        expected: str | bool,
        actual: str | bool | None,
        field_type: FormFieldType | None,
    ) -> bool:
        if actual is None:
            return False
        if field_type in {FormFieldType.CHECKBOX, FormFieldType.RADIO}:
            return bool(expected) is bool(actual)
        return self._normalize_text(str(expected)) == self._normalize_text(str(actual))

    def _detect_field_type(self, element: AccessibilityElement | None) -> FormFieldType:
        role = (element.role or "").casefold() if element is not None else ""
        name = (element.name or "").casefold() if element is not None else ""
        value = (element.value or "").casefold() if element is not None else ""

        if any(token in role for token in ("combo box", "combobox", "list", "dropdown")):
            return FormFieldType.DROPDOWN
        if "checkbox" in role or "check box" in role:
            return FormFieldType.CHECKBOX
        if "radio" in role:
            return FormFieldType.RADIO
        if "date" in role or "date" in name or "date" in value:
            return FormFieldType.DATE
        return FormFieldType.TEXT

    def _rank_field_candidates(self, candidates: list[AccessibilityElement]) -> list[AccessibilityElement]:
        return sorted(
            candidates,
            key=lambda element: (
                0 if element.bounds is not None else 1,
                0 if self._is_form_field(element) else 1,
                len(element.children),
            ),
        )

    def _is_form_field(self, element: AccessibilityElement) -> bool:
        role = (element.role or "").casefold()
        return role in {
            "edit",
            "text",
            "textbox",
            "richtext",
            "combo box",
            "combobox",
            "checkbox",
            "check box",
            "radio button",
            "radio",
            "date picker",
            "calendar",
            "list",
        }

    def _resolve_selector(self, selector: AIInterfaceElementSelector) -> FormFieldContext | None:
        if selector.bounds is not None:
            return FormFieldContext(
                bounds=selector.bounds,
                center=self._center(selector.bounds),
                selector_used="selector-bounds",
            )
        if SelectorStrategy.ACCESSIBILITY in selector.strategies:
            query = self.accessibility_reader.find_elements(
                name=selector.name,
                role=selector.role,
                value=selector.value,
            )
            matches = getattr(query, "matches", [])
            if matches:
                best = matches[0]
                return FormFieldContext(
                    field=best,
                    bounds=best.bounds,
                    center=self._center(best.bounds),
                    field_type=self._detect_field_type(best),
                    selector_used="selector-accessibility",
                )
        return None

    def _resolve_window(self, selector: AIInterfaceElementSelector | None) -> WindowReference | None:
        if selector is None or (selector.window_title is None and selector.process_name is None):
            return None
        if self.window_manager is None:
            return WindowReference(title=selector.window_title)
        for window in self.window_manager.list_windows():
            if selector.window_title and selector.window_title.casefold() not in window.title.casefold():
                continue
            if selector.process_name and (window.process_name or "").casefold() != selector.process_name.casefold():
                continue
            return WindowReference(title=window.title, handle=getattr(window, "handle", None))
        return WindowReference(title=selector.window_title)

    def _walk(self, element: AccessibilityElement):
        yield element
        for child in element.children:
            yield from self._walk(child)

    def _combine_ocr_blocks(self, blocks: list[OCRTextBlock]) -> str:
        if not blocks:
            return ""
        ordered = sorted(blocks, key=lambda block: (block.bounds[1], block.bounds[0]))
        return "\n".join(block.text.strip() for block in ordered if block.text.strip())

    def _normalize_text(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
