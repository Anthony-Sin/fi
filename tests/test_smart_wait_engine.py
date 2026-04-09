from desktop_automation_perception.models import SmartWaitRequest, WaitType
from desktop_automation_perception.smart_wait_engine import SmartWaitEngine


class FakeOCRExtractor:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def find_text(self, *, target, screenshot_path=None, region_of_interest=None):
        succeeded = self.outcomes.pop(0)
        return type(
            "OCRMatch",
            (),
            {
                "succeeded": succeeded,
                "matched_text": target if succeeded else None,
                "reason": None if succeeded else "not found",
            },
        )()


class FakeTemplateMatcher:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def search(self, screenshot_path, requests):
        present = self.outcomes.pop(0)
        matches = [object()] if present else []
        return [type("TemplateResult", (), {"matches": matches})()]


class FakeAccessibilityReader:
    def __init__(self, counts):
        self.counts = list(counts)

    def find_elements(self, *, name=None, role=None, value=None):
        count = self.counts.pop(0)
        return type("Query", (), {"matches": [object()] * count})()


class FakeChangeMonitor:
    def __init__(self, changed_values):
        self.changed_values = list(changed_values)

    def wait_for_change(
        self,
        *,
        region_of_interest=None,
        change_threshold=0.1,
        timeout_seconds=0.0,
        polling_interval_seconds=0.25,
        screenshot_path=None,
        monitor_id=None,
    ):
        changed = self.changed_values.pop(0)
        if changed:
            return type(
                "ChangeResult",
                (),
                {
                    "changed": True,
                    "event": type("Event", (), {"screenshot_path": screenshot_path, "difference_metric": 0.2})(),
                    "reason": None,
                },
            )()
        return type("ChangeResult", (), {"changed": False, "event": None, "reason": "No meaningful change detected."})()


def test_wait_until_element_appears_logs_actual_wait_time():
    clock = iter([0.0, 0.0, 0.2, 0.2, 0.4]).__next__
    sleeps = []
    engine = SmartWaitEngine(
        accessibility_reader=FakeAccessibilityReader([0, 1]),
        sleep_fn=sleeps.append,
        monotonic_fn=clock,
    )

    result = engine.wait_until_element_appears(
        SmartWaitRequest(
            wait_id="appear",
            wait_type=WaitType.ELEMENT_APPEARS,
            timeout_seconds=2.0,
            polling_interval_seconds=0.2,
            element_name="Send",
            element_role="Button",
        )
    )

    assert result.succeeded is True
    assert result.attempts == 2
    assert result.elapsed_seconds == 0.2
    assert sleeps == [0.2]
    assert engine.wait_logs[-1].elapsed_seconds == 0.2


def test_wait_until_element_disappears_uses_template_matching():
    clock = iter([0.0, 0.0, 0.1, 0.1, 0.3]).__next__
    engine = SmartWaitEngine(
        template_matcher=FakeTemplateMatcher([True, False]),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = engine.wait_until_element_disappears(
        SmartWaitRequest(
            wait_id="disappear",
            wait_type=WaitType.ELEMENT_DISAPPEARS,
            timeout_seconds=1.0,
            polling_interval_seconds=0.1,
            template_name="spinner",
            template_path="spinner.png",
        )
    )

    assert result.succeeded is True
    assert result.attempts == 2
    assert "disappeared" in (result.detail or "")


def test_wait_until_text_visible_times_out_when_text_never_appears():
    clock = iter([0.0, 0.0, 0.3, 0.3, 0.6, 0.6]).__next__
    sleeps = []
    engine = SmartWaitEngine(
        ocr_extractor=FakeOCRExtractor([False, False]),
        sleep_fn=sleeps.append,
        monotonic_fn=clock,
    )

    result = engine.wait_until_text_visible(
        SmartWaitRequest(
            wait_id="text",
            wait_type=WaitType.TEXT_VISIBLE,
            timeout_seconds=0.5,
            polling_interval_seconds=0.3,
            target_text="Ready",
        )
    )

    assert result.succeeded is False
    assert result.attempts == 2
    assert "Timeout expired" in (result.detail or "")
    assert sleeps == [0.3]


def test_wait_until_screen_changes_returns_change_screenshot_path():
    clock = iter([0.0, 0.0, 0.25]).__next__
    engine = SmartWaitEngine(
        change_monitor=FakeChangeMonitor([True]),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = engine.wait_until_screen_changes(
        SmartWaitRequest(
            wait_id="change",
            wait_type=WaitType.SCREEN_CHANGES,
            timeout_seconds=1.0,
            polling_interval_seconds=0.25,
            screenshot_path="changed.png",
        )
    )

    assert result.succeeded is True
    assert result.screenshot_path == "changed.png"


def test_wait_until_network_idle_uses_text_indicator_and_logs_duration():
    clock = iter([0.0, 0.0, 0.1, 0.1, 0.2, 0.2, 0.4]).__next__
    engine = SmartWaitEngine(
        ocr_extractor=FakeOCRExtractor([True, False]),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = engine.wait_until_network_idle(
        SmartWaitRequest(
            wait_id="network",
            wait_type=WaitType.NETWORK_IDLE,
            timeout_seconds=1.0,
            polling_interval_seconds=0.1,
            network_indicator_text="Loading",
        )
    )

    assert result.succeeded is True
    assert result.attempts == 2
    assert engine.wait_logs[-1].wait_type is WaitType.NETWORK_IDLE
