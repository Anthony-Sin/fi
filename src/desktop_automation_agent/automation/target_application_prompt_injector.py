from __future__ import annotations

from dataclasses import dataclass

from desktop_automation_agent.contracts import (
    AccessibilityReader,
    ClipboardWriter,
    InputRunner,
    OCRExtractor as OCRReader,
    PlatformTextInputBackend,
    WindowManager,
)
from desktop_automation_agent.models import (
    AccessibilityElement,
    InputAction,
    InputActionType,
    InputTarget,
    LineEndingStyle,
    OCRTextBlock,
    PromptFieldContext,
    PromptInjectionMethod,
    PromptInjectionResult,
    PromptInjectionTarget,
    PromptReadbackMethod,
    WindowReference,
)


@dataclass(slots=True)
class Win32PlatformTextInputBackend:
    def inject_text(self, text: str) -> None:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        inputs = []
        extra = ctypes.c_ulong(0)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

        input_type_keyboard = 1
        keyeventf_unicode = 0x0004
        keyeventf_keyup = 0x0002

        for character in text:
            scan_code = ord(character)
            key_down = INPUT(
                type=input_type_keyboard,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(
                        wVk=0,
                        wScan=scan_code,
                        dwFlags=keyeventf_unicode,
                        time=0,
                        dwExtraInfo=ctypes.pointer(extra),
                    )
                ),
            )
            key_up = INPUT(
                type=input_type_keyboard,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(
                        wVk=0,
                        wScan=scan_code,
                        dwFlags=keyeventf_unicode | keyeventf_keyup,
                        time=0,
                        dwExtraInfo=ctypes.pointer(extra),
                    )
                ),
            )
            inputs.extend([key_down, key_up])

        if not inputs:
            return

        sent = user32.SendInput(
            len(inputs),
            (INPUT * len(inputs))(*inputs),
            ctypes.sizeof(INPUT),
        )
        if sent != len(inputs):
            error_code = kernel32.GetLastError()
            raise RuntimeError(f"Platform text injection failed with error code {error_code}.")


@dataclass(slots=True)
class TargetApplicationPromptInjector:
    input_runner: InputRunner
    clipboard_manager: ClipboardWriter | None = None
    accessibility_reader: AccessibilityReader | None = None
    ocr_extractor: OCRReader | None = None
    window_manager: WindowManager | None = None
    platform_input_backend: PlatformTextInputBackend | None = None
    sensitive_data_protector: object | None = None

    def inject_prompt(
        self,
        *,
        prompt: str,
        target: PromptInjectionTarget,
        method: PromptInjectionMethod = PromptInjectionMethod.TYPE,
        line_endings: LineEndingStyle = LineEndingStyle.AUTO,
        ocr_language: str = "eng",
        minimum_ocr_confidence: float = 0.0,
    ) -> PromptInjectionResult:
        window = self._resolve_window_reference(target)
        field_context = self._locate_field(target, window)
        if field_context is None:
            return PromptInjectionResult(
                succeeded=False,
                method=method,
                target=target,
                reason="Unable to locate an active text input field in the target application.",
            )

        normalized_prompt = self._normalize_line_endings(prompt, line_endings, target)
        if self.sensitive_data_protector is not None:
            validation = self.sensitive_data_protector.validate_prompt(
                normalized_prompt,
                location="target_application_prompt_injector",
            )
            if not validation.succeeded:
                return PromptInjectionResult(
                    succeeded=False,
                    method=method,
                    target=target,
                    field_context=field_context,
                    normalized_prompt=normalized_prompt,
                    reason=validation.reason,
                )
        input_target = InputTarget(window=window, element_bounds=field_context.bounds)

        focus_result = self._focus_field(field_context, input_target)
        if getattr(focus_result, "succeeded", True) is False:
            return PromptInjectionResult(
                succeeded=False,
                method=method,
                target=target,
                field_context=field_context,
                normalized_prompt=normalized_prompt,
                reason=getattr(focus_result, "failure_reason", "Unable to focus the target input field."),
            )

        clear_result = self._clear_field(input_target)
        if getattr(clear_result, "succeeded", True) is False:
            return PromptInjectionResult(
                succeeded=False,
                method=method,
                target=target,
                field_context=field_context,
                normalized_prompt=normalized_prompt,
                reason=getattr(clear_result, "failure_reason", "Unable to clear the target input field."),
            )

        injection_failure = self._perform_injection(method, normalized_prompt, input_target)
        if injection_failure is not None:
            return PromptInjectionResult(
                succeeded=False,
                method=method,
                target=target,
                field_context=field_context,
                normalized_prompt=normalized_prompt,
                reason=injection_failure,
            )

        verification_method, actual_text = self._read_back_text(
            field_context=field_context,
            target=target,
            ocr_language=ocr_language,
            minimum_ocr_confidence=minimum_ocr_confidence,
        )
        expected_text = self._normalize_for_comparison(normalized_prompt)

        if actual_text is None:
            return PromptInjectionResult(
                succeeded=False,
                method=method,
                target=target,
                field_context=field_context,
                normalized_prompt=normalized_prompt,
                expected_text=expected_text,
                verification_method=verification_method,
                reason="Prompt was injected but the field content could not be read back for verification.",
            )

        if self._normalize_for_comparison(actual_text) != expected_text:
            return PromptInjectionResult(
                succeeded=False,
                method=method,
                target=target,
                field_context=field_context,
                normalized_prompt=normalized_prompt,
                expected_text=expected_text,
                actual_text=self._normalize_for_comparison(actual_text),
                verification_method=verification_method,
                reason="Read-back text does not match the expected injected prompt.",
            )

        return PromptInjectionResult(
            succeeded=True,
            method=method,
            target=target,
            field_context=field_context,
            normalized_prompt=normalized_prompt,
            expected_text=expected_text,
            actual_text=self._normalize_for_comparison(actual_text),
            verification_method=verification_method,
        )

    def _resolve_window_reference(self, target: PromptInjectionTarget) -> WindowReference | None:
        if target.window_title is None and target.process_name is None:
            return None

        handle = None
        resolved_title = target.window_title
        if self.window_manager is not None:
            for window in self.window_manager.list_windows():
                if target.window_title and target.window_title.casefold() not in window.title.casefold():
                    continue
                if target.process_name and (window.process_name or "").casefold() != target.process_name.casefold():
                    continue
                handle = window.handle
                resolved_title = window.title
                break

        return WindowReference(title=resolved_title, handle=handle)

    def _locate_field(
        self,
        target: PromptInjectionTarget,
        window: WindowReference | None,
    ) -> PromptFieldContext | None:
        if target.element_bounds is not None:
            return PromptFieldContext(
                bounds=target.element_bounds,
                center=self._center(target.element_bounds),
                window=window,
                used_fallback=False,
            )

        if self.accessibility_reader is not None:
            query = self.accessibility_reader.find_elements(
                name=target.element_name,
                role=target.element_role,
                value=None,
            )
            candidates = [
                element
                for element in getattr(query, "matches", [])
                if self._is_text_field_candidate(element)
            ]
            if candidates:
                best = self._rank_accessibility_candidates(candidates)[0]
                return PromptFieldContext(
                    bounds=best.bounds,
                    center=self._center(best.bounds) if best.bounds is not None else None,
                    element=best,
                    window=window,
                    used_fallback=bool(getattr(query, "used_fallback", False)),
                )

            tree = self.accessibility_reader.read_active_application_tree()
            if getattr(tree, "root", None) is not None:
                all_candidates = [
                    element
                    for element in self._walk(getattr(tree, "root"))
                    if self._is_text_field_candidate(element)
                ]
                if all_candidates:
                    best = self._rank_accessibility_candidates(all_candidates)[0]
                    return PromptFieldContext(
                        bounds=best.bounds,
                        center=self._center(best.bounds) if best.bounds is not None else None,
                        element=best,
                        window=window,
                        used_fallback=best.source == "raw_window",
                    )

        return None

    def _focus_field(self, field_context: PromptFieldContext, input_target: InputTarget):
        if field_context.center is None:
            return self.input_runner.run([])
        return self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.CLICK,
                    target=input_target,
                    position=field_context.center,
                )
            ]
        )

    def _clear_field(self, input_target: InputTarget):
        return self.input_runner.run(
            [
                InputAction(
                    action_type=InputActionType.HOTKEY,
                    target=input_target,
                    hotkey=("ctrl", "a"),
                ),
                InputAction(
                    action_type=InputActionType.KEYPRESS,
                    target=input_target,
                    key="delete",
                ),
            ]
        )

    def _perform_injection(
        self,
        method: PromptInjectionMethod,
        prompt: str,
        input_target: InputTarget,
    ) -> str | None:
        if method is PromptInjectionMethod.TYPE:
            result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.TYPE_TEXT,
                        target=input_target,
                        text=prompt,
                    )
                ]
            )
            if getattr(result, "succeeded", True) is False:
                return getattr(result, "failure_reason", "Character typing failed.")
            return None

        if method is PromptInjectionMethod.CLIPBOARD:
            if self.clipboard_manager is None:
                return "Clipboard injection requested but no clipboard manager is configured."
            clipboard_result = self.clipboard_manager.write_text(prompt)
            if getattr(clipboard_result, "succeeded", True) is False:
                return getattr(clipboard_result, "reason", "Clipboard write failed.")
            result = self.input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.HOTKEY,
                        target=input_target,
                        hotkey=("ctrl", "v"),
                    )
                ]
            )
            if getattr(result, "succeeded", True) is False:
                return getattr(result, "failure_reason", "Clipboard paste failed.")
            return None

        if method is PromptInjectionMethod.PLATFORM_API:
            if self.platform_input_backend is None:
                return "Platform API injection requested but no platform input backend is configured."
            try:
                self.platform_input_backend.inject_text(prompt)
            except Exception as exc:
                return str(exc)
            return None

        return "Unsupported prompt injection method."

    def _read_back_text(
        self,
        *,
        field_context: PromptFieldContext,
        target: PromptInjectionTarget,
        ocr_language: str,
        minimum_ocr_confidence: float,
    ) -> tuple[PromptReadbackMethod | None, str | None]:
        if self.accessibility_reader is not None and field_context.element is not None:
            text = self._accessibility_field_text(field_context.element)
            if text:
                return (PromptReadbackMethod.ACCESSIBILITY, text)

            refreshed = self.accessibility_reader.find_elements(
                name=target.element_name or field_context.element.name,
                role=target.element_role or field_context.element.role,
                value=None,
            )
            for element in getattr(refreshed, "matches", []):
                if field_context.bounds is not None and element.bounds != field_context.bounds:
                    continue
                refreshed_text = self._accessibility_field_text(element)
                if refreshed_text:
                    return (PromptReadbackMethod.ACCESSIBILITY, refreshed_text)

        if self.ocr_extractor is not None and field_context.bounds is not None:
            extraction = self.ocr_extractor.extract_text(
                region_of_interest=field_context.bounds,
                language=ocr_language,
                minimum_confidence=minimum_ocr_confidence,
            )
            combined = self._combine_ocr_blocks(getattr(extraction, "blocks", []))
            if combined:
                return (PromptReadbackMethod.OCR, combined)

        return (None, None)

    def _combine_ocr_blocks(self, blocks: list[OCRTextBlock]) -> str:
        if not blocks:
            return ""

        ordered = sorted(blocks, key=lambda block: (block.bounds[1], block.bounds[0]))
        lines: list[list[OCRTextBlock]] = []
        line_height_tolerance = 12

        for block in ordered:
            if not lines:
                lines.append([block])
                continue
            current_line = lines[-1]
            previous_top = current_line[0].bounds[1]
            if abs(block.bounds[1] - previous_top) <= line_height_tolerance:
                current_line.append(block)
            else:
                lines.append([block])

        rendered_lines = [
            " ".join(part.text for part in sorted(line, key=lambda item: item.bounds[0])).strip()
            for line in lines
        ]
        return "\n".join(line for line in rendered_lines if line)

    def _accessibility_field_text(self, element: AccessibilityElement) -> str | None:
        if element.state.text:
            return element.state.text
        if element.value:
            return element.value
        if not self._is_explicit_text_role(element.role):
            return self.accessibility_reader.get_element_text(element)
        return None

    def _rank_accessibility_candidates(
        self,
        candidates: list[AccessibilityElement],
    ) -> list[AccessibilityElement]:
        return sorted(
            candidates,
            key=lambda element: (
                0 if self._is_explicit_text_role(element.role) else 1,
                0 if self.accessibility_reader is None or self.accessibility_reader.is_element_enabled(element) is not False else 1,
                0 if element.bounds is not None else 1,
                len(element.children),
            ),
        )

    def _is_text_field_candidate(self, element: AccessibilityElement) -> bool:
        role = (element.role or "").casefold()
        if self._is_explicit_text_role(role):
            return True
        return bool(role == "" and element.bounds is not None and element.state.enabled is not False)

    def _is_explicit_text_role(self, role: str | None) -> bool:
        normalized = (role or "").casefold()
        return normalized in {"edit", "document", "text", "textbox", "richtext", "combo box", "combobox"}

    def _walk(self, element: AccessibilityElement):
        yield element
        for child in element.children:
            yield from self._walk(child)

    def _normalize_line_endings(
        self,
        prompt: str,
        style: LineEndingStyle,
        target: PromptInjectionTarget,
    ) -> str:
        normalized = prompt.replace("\r\n", "\n").replace("\r", "\n")
        if style is LineEndingStyle.LF:
            return normalized
        if style is LineEndingStyle.CRLF:
            return normalized.replace("\n", "\r\n")

        application_hint = f"{target.window_title or ''} {target.process_name or ''}".casefold()
        if any(name in application_hint for name in ("notepad", "wordpad", "outlook", "winword")):
            return normalized.replace("\n", "\r\n")
        return normalized

    def _normalize_for_comparison(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _center(self, bounds: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if bounds is None:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
