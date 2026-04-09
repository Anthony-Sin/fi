from pathlib import Path

from desktop_automation_agent.error_classifier import ErrorClassifier
from desktop_automation_agent.models import ErrorCategory, RecoveryStrategy


class FakeScreenshotBackend:
    def __init__(self):
        self.count = 0

    def capture_screenshot_to_path(self, path=None):
        self.count += 1
        return f"screen-{self.count}.png"


class FakeFailureEvent:
    def __init__(self, message, screenshot_path=None):
        self.event_type = "AutomationFailureEvent"
        self.error_message = message
        self.screenshot_path = screenshot_path


def test_error_classifier_categorizes_ui_element_not_found(tmp_path):
    classifier = ErrorClassifier(
        storage_path=str(Path(tmp_path) / "errors.json"),
        screenshot_backend=FakeScreenshotBackend(),
    )

    result = classifier.classify(RuntimeError("Unable to locate send button element"))

    assert result.category is ErrorCategory.UI_ELEMENT_NOT_FOUND
    assert result.recovery_strategy is RecoveryStrategy.RETRY
    assert result.record.screenshot_path == "screen-1.png"


def test_error_classifier_categorizes_application_not_responding(tmp_path):
    classifier = ErrorClassifier(storage_path=str(Path(tmp_path) / "errors.json"))

    result = classifier.classify(RuntimeError("Application not responding"))

    assert result.category is ErrorCategory.APPLICATION_NOT_RESPONDING
    assert result.recovery_strategy is RecoveryStrategy.REFRESH


def test_error_classifier_categorizes_session_expired(tmp_path):
    classifier = ErrorClassifier(storage_path=str(Path(tmp_path) / "errors.json"))

    result = classifier.classify(RuntimeError("Session expired, please sign in"))

    assert result.category is ErrorCategory.SESSION_EXPIRED
    assert result.recovery_strategy is RecoveryStrategy.REAUTHENTICATE


def test_error_classifier_categorizes_network_timeout(tmp_path):
    classifier = ErrorClassifier(storage_path=str(Path(tmp_path) / "errors.json"))

    result = classifier.classify(TimeoutError("Network timeout while fetching"))

    assert result.category is ErrorCategory.NETWORK_TIMEOUT
    assert result.recovery_strategy is RecoveryStrategy.RETRY


def test_error_classifier_categorizes_unexpected_dialog_and_persists_log(tmp_path):
    storage = Path(tmp_path) / "errors.json"
    classifier = ErrorClassifier(storage_path=str(storage))

    result = classifier.classify(RuntimeError("Unexpected dialog appeared"))
    records = classifier.list_records()

    assert result.category is ErrorCategory.UNEXPECTED_DIALOG_APPEARED
    assert result.recovery_strategy is RecoveryStrategy.DISMISS_DIALOG
    assert len(records) == 1
    assert records[0].category is ErrorCategory.UNEXPECTED_DIALOG_APPEARED


def test_error_classifier_categorizes_screen_state_mismatch(tmp_path):
    classifier = ErrorClassifier(storage_path=str(Path(tmp_path) / "errors.json"))

    result = classifier.classify(RuntimeError("Post-condition verification failed"))

    assert result.category is ErrorCategory.SCREEN_STATE_MISMATCH
    assert result.recovery_strategy is RecoveryStrategy.ESCALATE


def test_error_classifier_falls_back_to_unrecognized_error(tmp_path):
    classifier = ErrorClassifier(storage_path=str(Path(tmp_path) / "errors.json"))

    result = classifier.classify(RuntimeError("something novel"))

    assert result.category is ErrorCategory.UNRECOGNIZED_ERROR
    assert result.recovery_strategy is RecoveryStrategy.ABORT


def test_error_classifier_accepts_failure_event_objects(tmp_path):
    classifier = ErrorClassifier(
        storage_path=str(Path(tmp_path) / "errors.json"),
        screenshot_backend=FakeScreenshotBackend(),
    )

    result = classifier.classify(
        FakeFailureEvent(
            "Session expired during workflow handoff",
            screenshot_path="captured-before-classify.png",
        )
    )

    assert result.category is ErrorCategory.SESSION_EXPIRED
    assert result.recovery_strategy is RecoveryStrategy.REAUTHENTICATE
    assert result.record.exception_type == "AutomationFailureEvent"
    assert result.record.screenshot_path == "captured-before-classify.png"
