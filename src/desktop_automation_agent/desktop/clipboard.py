from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import sleep
from typing import Callable

from desktop_automation_agent.contracts import ClipboardBackend, InputRunner, TextReader
from desktop_automation_agent.models import (
    ClipboardContent,
    ClipboardContentType,
    ClipboardEvent,
    ClipboardOperationResult,
    ClipboardPasteMode,
    ClipboardVerificationResult,
    InputAction,
    InputActionType,
    InputTarget,
)


CF_UNICODETEXT = 13
CF_DIB = 8
GHND = 0x0042


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Win32ClipboardBackend:
    def read(self) -> ClipboardContent:
        """Read content from the Win32 clipboard."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if not user32.OpenClipboard(None):
            return ClipboardContent(content_type=ClipboardContentType.EMPTY)

        try:
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ClipboardContent(content_type=ClipboardContentType.EMPTY)

                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    return ClipboardContent(content_type=ClipboardContentType.EMPTY)

                try:
                    text = ctypes.wstring_at(pointer)
                finally:
                    kernel32.GlobalUnlock(handle)

                return ClipboardContent(
                    content_type=ClipboardContentType.TEXT,
                    text=text,
                    encoding="utf-16-le",
                )

            if user32.IsClipboardFormatAvailable(CF_DIB):
                handle = user32.GetClipboardData(CF_DIB)
                if not handle:
                    return ClipboardContent(content_type=ClipboardContentType.EMPTY)

                size = kernel32.GlobalSize(handle)
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    return ClipboardContent(content_type=ClipboardContentType.EMPTY)

                try:
                    image_bytes = ctypes.string_at(pointer, size)
                finally:
                    kernel32.GlobalUnlock(handle)

                return ClipboardContent(
                    content_type=ClipboardContentType.IMAGE,
                    image_bytes=image_bytes,
                )

            return ClipboardContent(content_type=ClipboardContentType.EMPTY)
        finally:
            user32.CloseClipboard()

    def write_text(self, text: str, encoding: str = "utf-8") -> bool:
        """Write text to the Win32 clipboard."""
        try:
            normalized = self._normalize_text(text, encoding)
            encoded = normalized.encode("utf-16-le") + b"\x00\x00"
            if not self._open_and_clear():
                return False

            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            handle = kernel32.GlobalAlloc(GHND, len(encoded))
            if not handle:
                user32.CloseClipboard()
                logger.warning("Failed to allocate clipboard memory.")
                return False

            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                user32.CloseClipboard()
                logger.warning("Failed to lock clipboard memory.")
                return False

            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                kernel32.GlobalUnlock(handle)

            user32.SetClipboardData(CF_UNICODETEXT, handle)
            user32.CloseClipboard()
            return True
        except Exception as e:
            logger.warning(f"Clipboard write_text failed: {e}")
            return False

    def write_image(self, image_bytes: bytes) -> bool:
        """Write image DIB data to the Win32 clipboard."""
        try:
            if not self._open_and_clear():
                return False
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            handle = kernel32.GlobalAlloc(GHND, len(image_bytes))
            if not handle:
                user32.CloseClipboard()
                logger.warning("Failed to allocate clipboard memory for image data.")
                return False

            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                user32.CloseClipboard()
                logger.warning("Failed to lock clipboard image memory.")
                return False

            try:
                ctypes.memmove(pointer, image_bytes, len(image_bytes))
            finally:
                kernel32.GlobalUnlock(handle)

            user32.SetClipboardData(CF_DIB, handle)
            user32.CloseClipboard()
            return True
        except Exception as e:
            logger.warning(f"Clipboard write_image failed: {e}")
            return False

    def _open_and_clear(self) -> bool:
        try:
            user32 = ctypes.windll.user32
            if not user32.OpenClipboard(None):
                logger.warning("Unable to open clipboard.")
                return False
            user32.EmptyClipboard()
            return True
        except Exception as e:
            logger.warning(f"Error opening/clearing clipboard: {e}")
            return False

    def _normalize_text(self, text: str, encoding: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = normalized.replace("\n", "\r\n")
        return normalized.encode(encoding, errors="replace").decode(encoding, errors="replace")


@dataclass(slots=True)
class ClipboardManager:
    backend: ClipboardBackend
    stabilization_wait_seconds: float = 0.1
    sleep_fn: Callable[[float], None] = sleep
    _events: list[ClipboardEvent] = field(default_factory=list)

    def read_clipboard(self) -> ClipboardOperationResult:
        self.sleep_fn(self.stabilization_wait_seconds)
        content = self.backend.read()
        self._log("read", content.content_type, "Clipboard content read.")
        return ClipboardOperationResult(succeeded=True, content=content)

    def write_text(
        self,
        text: str,
        *,
        delay_seconds: float = 0.0,
        encoding: str = "utf-8",
    ) -> ClipboardOperationResult:
        if delay_seconds > 0:
            self.sleep_fn(delay_seconds)
        success = self.backend.write_text(text, encoding=encoding)
        self.sleep_fn(self.stabilization_wait_seconds)
        if not success:
            return ClipboardOperationResult(succeeded=False, reason="Backend write_text failed.")
        content = ClipboardContent(
            content_type=ClipboardContentType.TEXT,
            text=self._normalize_for_comparison(text),
            encoding=encoding,
        )
        self._log("write_text", content.content_type, f"Text written using {encoding}.")
        return ClipboardOperationResult(succeeded=True, content=content)

    def write_image(
        self,
        image_bytes: bytes,
        *,
        delay_seconds: float = 0.0,
    ) -> ClipboardOperationResult:
        if delay_seconds > 0:
            self.sleep_fn(delay_seconds)
        success = self.backend.write_image(image_bytes)
        self.sleep_fn(self.stabilization_wait_seconds)
        if not success:
            return ClipboardOperationResult(succeeded=False, reason="Backend write_image failed.")
        content = ClipboardContent(content_type=ClipboardContentType.IMAGE, image_bytes=image_bytes)
        self._log("write_image", content.content_type, "Image written to clipboard.")
        return ClipboardOperationResult(succeeded=True, content=content)

    def monitor_changes(
        self,
        timeout_seconds: float = 3.0,
        retry_count: int = 10,
    ) -> ClipboardOperationResult:
        baseline = self.backend.read()
        attempts = retry_count if retry_count > 0 else 1
        interval = timeout_seconds / retry_count if retry_count > 0 else timeout_seconds

        for attempt in range(attempts):
            self.sleep_fn(interval if attempt > 0 else self.stabilization_wait_seconds)
            current = self.backend.read()
            if self._has_changed(baseline, current):
                self._log("monitor_change", current.content_type, "Clipboard change detected.")
                return ClipboardOperationResult(succeeded=True, content=current)

        self._log("monitor_timeout", baseline.content_type, "Clipboard did not change before timeout.")
        return ClipboardOperationResult(
            succeeded=False,
            content=baseline,
            reason="Clipboard did not change within the configured timeout.",
        )

    def get_event_log(self) -> list[ClipboardEvent]:
        return list(self._events)

    def paste_and_verify(
        self,
        *,
        text: str,
        input_runner: InputRunner,
        target: InputTarget | None = None,
        readback: TextReader | Callable[[], str],
        mode: ClipboardPasteMode = ClipboardPasteMode.PASTE,
        encoding: str = "utf-8",
    ) -> ClipboardVerificationResult:
        expected = self._normalize_for_comparison(text)

        if mode is ClipboardPasteMode.PASTE:
            self.write_text(text, encoding=encoding)
            runner_result = input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.HOTKEY,
                        target=target,
                        hotkey=("ctrl", "v"),
                    )
                ]
            )
            if getattr(runner_result, "succeeded", True) is False:
                return ClipboardVerificationResult(
                    succeeded=False,
                    expected=expected,
                    actual=None,
                    mode=mode,
                    reason=getattr(runner_result, "failure_reason", "Paste action failed before verification."),
                )
            self._log("paste", ClipboardContentType.TEXT, "Triggered paste hotkey.")
        else:
            runner_result = input_runner.run(
                [
                    InputAction(
                        action_type=InputActionType.TYPE_TEXT,
                        target=target,
                        text=text,
                    )
                ]
            )
            if getattr(runner_result, "succeeded", True) is False:
                return ClipboardVerificationResult(
                    succeeded=False,
                    expected=expected,
                    actual=None,
                    mode=mode,
                    reason=getattr(runner_result, "failure_reason", "Type action failed before verification."),
                )
            self._log("type", ClipboardContentType.TEXT, "Typed text directly into target.")

        self.sleep_fn(self.stabilization_wait_seconds)
        actual = self._normalize_for_comparison(self._read_back_text(readback))
        if actual == expected:
            self._log("verify", ClipboardContentType.TEXT, "Paste verification succeeded.")
            return ClipboardVerificationResult(
                succeeded=True,
                expected=expected,
                actual=actual,
                mode=mode,
            )

        self._log("verify_failed", ClipboardContentType.TEXT, "Paste verification failed.")
        return ClipboardVerificationResult(
            succeeded=False,
            expected=expected,
            actual=actual,
            mode=mode,
            reason="Read-back text does not match the expected clipboard input.",
        )

    def _has_changed(self, before: ClipboardContent, after: ClipboardContent) -> bool:
        if before.content_type != after.content_type:
            return True
        if before.content_type is ClipboardContentType.TEXT:
            return self._normalize_for_comparison(before.text or "") != self._normalize_for_comparison(after.text or "")
        if before.content_type is ClipboardContentType.IMAGE:
            return (before.image_bytes or b"") != (after.image_bytes or b"")
        return False

    def _normalize_for_comparison(self, text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _read_back_text(self, readback: TextReader | Callable[[], str]) -> str:
        if callable(readback):
            return readback()
        return readback.read_text()

    def _log(self, operation: str, content_type: ClipboardContentType, detail: str) -> None:
        self._events.append(
            ClipboardEvent(
                operation=operation,
                content_type=content_type,
                timestamp=datetime.now(timezone.utc),
                detail=detail,
            )
        )
