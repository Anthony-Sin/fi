from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from desktop_automation_agent.contracts import InputRunner, TextReader, WindowManager
from desktop_automation_agent.models import (
    ClipboardBridgeFormat,
    ClipboardBridgeResult,
    ClipboardContentType,
    ClipboardOperationResult,
    ClipboardPasteMode,
    ClipboardVerificationResult,
    InputTarget,
)


@dataclass(slots=True)
class ClipboardDataBridge:
    clipboard_manager: object
    window_manager: WindowManager
    input_runner: InputRunner
    max_retry_count: int = 3
    conflict_retry_callback: Callable[[int], None] | None = None

    def transfer(
        self,
        data: str | dict[str, Any],
        *,
        target_window_title: str | None = None,
        target_process_name: str | None = None,
        target: InputTarget | None = None,
        readback: TextReader | Callable[[], str],
        data_format: ClipboardBridgeFormat = ClipboardBridgeFormat.TEXT,
        paste_mode: ClipboardPasteMode = ClipboardPasteMode.PASTE,
        encoding: str = "utf-8",
    ) -> ClipboardBridgeResult:
        rendered_text = self.render_data(data, data_format=data_format)
        retries_used = 0
        clipboard_written: ClipboardOperationResult | None = None
        window_result = None
        verification: ClipboardVerificationResult | None = None

        for attempt in range(self.max_retry_count + 1):
            clipboard_written = self.clipboard_manager.write_text(rendered_text, encoding=encoding)
            if getattr(clipboard_written, "succeeded", True) is False:
                return ClipboardBridgeResult(
                    succeeded=False,
                    clipboard_written=clipboard_written,
                    rendered_text=rendered_text,
                    retry_count=attempt,
                    reason=getattr(clipboard_written, "reason", "Failed to write clipboard content."),
                )

            if self._clipboard_conflicted(rendered_text):
                retries_used = attempt + 1
                if attempt >= self.max_retry_count:
                    return ClipboardBridgeResult(
                        succeeded=False,
                        clipboard_written=clipboard_written,
                        rendered_text=rendered_text,
                        retry_count=retries_used,
                        conflict_detected=True,
                        reason="Clipboard content changed unexpectedly before paste could occur.",
                    )
                self._notify_conflict_retry(retries_used)
                continue

            window_result = self.window_manager.focus_window(
                title=target_window_title,
                process_name=target_process_name,
            )
            if getattr(window_result, "succeeded", False) is False:
                return ClipboardBridgeResult(
                    succeeded=False,
                    clipboard_written=clipboard_written,
                    window_result=window_result,
                    rendered_text=rendered_text,
                    retry_count=attempt,
                    reason=getattr(window_result, "reason", "Failed to focus the target application."),
                )

            verification = self.clipboard_manager.paste_and_verify(
                text=rendered_text,
                input_runner=self.input_runner,
                target=target,
                readback=readback,
                mode=paste_mode,
                encoding=encoding,
            )
            if verification.succeeded:
                return ClipboardBridgeResult(
                    succeeded=True,
                    clipboard_written=clipboard_written,
                    window_result=window_result,
                    verification=verification,
                    rendered_text=rendered_text,
                    retry_count=attempt,
                    conflict_detected=retries_used > 0,
                )

            current_clipboard = self.clipboard_manager.read_clipboard()
            if self._clipboard_read_matches(current_clipboard, rendered_text):
                return ClipboardBridgeResult(
                    succeeded=False,
                    clipboard_written=clipboard_written,
                    window_result=window_result,
                    verification=verification,
                    rendered_text=rendered_text,
                    retry_count=attempt,
                    conflict_detected=retries_used > 0,
                    reason=verification.reason,
                )

            retries_used = attempt + 1
            if attempt >= self.max_retry_count:
                return ClipboardBridgeResult(
                    succeeded=False,
                    clipboard_written=clipboard_written,
                    window_result=window_result,
                    verification=verification,
                    rendered_text=rendered_text,
                    retry_count=retries_used,
                    conflict_detected=True,
                    reason="Clipboard content changed unexpectedly during paste verification.",
                )
            self._notify_conflict_retry(retries_used)

        return ClipboardBridgeResult(
            succeeded=False,
            clipboard_written=clipboard_written,
            window_result=window_result,
            verification=verification,
            rendered_text=rendered_text,
            retry_count=retries_used,
            conflict_detected=retries_used > 0,
            reason="Clipboard transfer failed after exhausting retries.",
        )

    def render_data(
        self,
        data: str | dict[str, Any],
        *,
        data_format: ClipboardBridgeFormat = ClipboardBridgeFormat.TEXT,
    ) -> str:
        if data_format is ClipboardBridgeFormat.TEXT:
            return data if isinstance(data, str) else str(data)
        if data_format is ClipboardBridgeFormat.JSON:
            return json.dumps(data, indent=2, sort_keys=True)
        if data_format is ClipboardBridgeFormat.FORMATTED_VALUES:
            if isinstance(data, str):
                return data
            return "\n".join(f"{key}: {self._format_value(value)}" for key, value in data.items())
        raise ValueError(f"Unsupported clipboard bridge format: {data_format}")

    def _format_value(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    def _clipboard_conflicted(self, expected_text: str) -> bool:
        current = self.clipboard_manager.read_clipboard()
        return not self._clipboard_read_matches(current, expected_text)

    def _clipboard_read_matches(self, clipboard_result: ClipboardOperationResult, expected_text: str) -> bool:
        if getattr(clipboard_result, "succeeded", False) is False:
            return False
        content = getattr(clipboard_result, "content", None)
        if content is None or getattr(content, "content_type", None) is not ClipboardContentType.TEXT:
            return False
        actual_text = self._normalize(getattr(content, "text", "") or "")
        return actual_text == self._normalize(expected_text)

    def _normalize(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _notify_conflict_retry(self, attempt_number: int) -> None:
        if self.conflict_retry_callback is not None:
            self.conflict_retry_callback(attempt_number)
