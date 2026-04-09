from datetime import datetime, timezone

from desktop_automation_perception.desktop import ClipboardDataBridge
from desktop_automation_perception.models import (
    ClipboardBridgeFormat,
    ClipboardContent,
    ClipboardContentType,
    ClipboardOperationResult,
    ClipboardPasteMode,
    ClipboardVerificationResult,
    WindowContext,
    WindowOperationResult,
)


class FakeClipboardManager:
    def __init__(self):
        self.current_text = ""
        self.write_calls = []
        self.read_results = []
        self.verification_results = []
        self.paste_calls = []

    def write_text(self, text: str, *, delay_seconds: float = 0.0, encoding: str = "utf-8"):
        self.current_text = text
        self.write_calls.append((text, delay_seconds, encoding))
        return ClipboardOperationResult(
            succeeded=True,
            content=ClipboardContent(content_type=ClipboardContentType.TEXT, text=text, encoding=encoding),
        )

    def read_clipboard(self):
        if self.read_results:
            result = self.read_results.pop(0)
            if isinstance(result, ClipboardOperationResult):
                return result
            self.current_text = result
        return ClipboardOperationResult(
            succeeded=True,
            content=ClipboardContent(content_type=ClipboardContentType.TEXT, text=self.current_text),
        )

    def paste_and_verify(self, *, text, input_runner, target=None, readback, mode=ClipboardPasteMode.PASTE, encoding="utf-8"):
        self.paste_calls.append((text, mode, encoding))
        if self.verification_results:
            return self.verification_results.pop(0)
        actual = readback() if callable(readback) else readback.read_text()
        return ClipboardVerificationResult(
            succeeded=actual == text,
            expected=text,
            actual=actual,
            mode=mode,
            reason=None if actual == text else "Read-back text does not match the expected clipboard input.",
        )


class FakeWindowManager:
    def __init__(self, *, succeed: bool = True):
        self.succeed = succeed
        self.focus_calls = []

    def focus_window(self, title=None, process_name=None):
        self.focus_calls.append((title, process_name))
        if not self.succeed:
            return WindowOperationResult(succeeded=False, reason="Failed to focus the target window.")
        return WindowOperationResult(
            succeeded=True,
            window=WindowContext(
                handle=101,
                title=title or "Target App",
                process_name=process_name,
                focused=True,
            ),
        )


class FakeInputRunner:
    def run(self, actions):
        return type("Result", (), {"succeeded": True, "logs": list(actions), "failure_reason": None})()


def test_clipboard_data_bridge_transfers_json_and_verifies_paste():
    clipboard = FakeClipboardManager()
    bridge = ClipboardDataBridge(
        clipboard_manager=clipboard,
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
    )

    result = bridge.transfer(
        {"account": "acct-a", "priority": "high"},
        target_window_title="CRM",
        readback=lambda: '{\n  "account": "acct-a",\n  "priority": "high"\n}',
        data_format=ClipboardBridgeFormat.JSON,
    )

    assert result.succeeded is True
    assert result.rendered_text == '{\n  "account": "acct-a",\n  "priority": "high"\n}'
    assert result.verification is not None and result.verification.succeeded is True
    assert clipboard.paste_calls[0][0] == result.rendered_text


def test_clipboard_data_bridge_formats_key_value_payloads_for_transfer():
    bridge = ClipboardDataBridge(
        clipboard_manager=FakeClipboardManager(),
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
    )

    rendered = bridge.render_data(
        {"account": "acct-a", "amount": 25, "tags": ["new", "priority"]},
        data_format=ClipboardBridgeFormat.FORMATTED_VALUES,
    )

    assert rendered == 'account: acct-a\namount: 25\ntags: ["new", "priority"]'


def test_clipboard_data_bridge_retries_when_clipboard_changes_unexpectedly():
    clipboard = FakeClipboardManager()
    clipboard.read_results = [
        "foreign text",
        "important payload",
    ]
    retry_attempts = []
    bridge = ClipboardDataBridge(
        clipboard_manager=clipboard,
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
        max_retry_count=2,
        conflict_retry_callback=retry_attempts.append,
    )

    result = bridge.transfer(
        "important payload",
        target_window_title="Target App",
        readback=lambda: "important payload",
    )

    assert result.succeeded is True
    assert result.conflict_detected is True
    assert result.retry_count == 1
    assert retry_attempts == [1]
    assert len(clipboard.write_calls) == 2


def test_clipboard_data_bridge_fails_after_exhausting_conflict_retries():
    clipboard = FakeClipboardManager()
    clipboard.read_results = [
        "changed-1",
        "changed-2",
    ]
    bridge = ClipboardDataBridge(
        clipboard_manager=clipboard,
        window_manager=FakeWindowManager(),
        input_runner=FakeInputRunner(),
        max_retry_count=1,
    )

    result = bridge.transfer(
        "stable payload",
        target_window_title="Target App",
        readback=lambda: "stable payload",
    )

    assert result.succeeded is False
    assert result.conflict_detected is True
    assert result.retry_count == 2
    assert result.reason == "Clipboard content changed unexpectedly before paste could occur."
