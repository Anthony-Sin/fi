from desktop_automation_agent.animation_wait_module import AnimationTransitionWaitModule
from desktop_automation_agent.models import AnimationCompletionSignal, AnimationWaitRequest


class FakeCaptureBackend:
    def __init__(self, frames):
        self.frames = list(frames)
        self.saved = []

    def capture(self, region_of_interest=None, monitor_id=None):
        if len(self.frames) == 1:
            return self.frames[0]
        return self.frames.pop(0)

    def save(self, image, path):
        self.saved.append((image, path))
        return path


class FakeDifferenceBackend:
    def __init__(self, values):
        self.values = list(values)

    def compute_difference(self, previous_image, current_image):
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


class FakeTemplateMatcher:
    def __init__(self, matches):
        self.matches = list(matches)

    def search(self, screenshot_path, requests):
        current = self.matches.pop(0)
        return [type("TemplateResult", (), {"matches": [object()] * current})()]


class FakeOCRExtractor:
    def __init__(self, results):
        self.results = list(results)

    def find_text(self, *, target, screenshot_path=None, region_of_interest=None):
        succeeded = self.results.pop(0)
        return type("OCRResult", (), {"succeeded": succeeded, "matched_text": target if succeeded else None})()


class FakeAccessibilityReader:
    def __init__(self, match_counts):
        self.match_counts = list(match_counts)

    def find_elements(self, *, name=None, role=None, value=None):
        count = self.match_counts.pop(0)
        return type("AccessibilityQuery", (), {"matches": [object()] * count})()


def test_wait_for_animation_to_settle_detects_stable_frames_and_logs_elapsed_time():
    clock = iter([0.0, 0.0, 0.1, 0.2, 0.3]).__next__
    sleeps = []
    module = AnimationTransitionWaitModule(
        capture_backend=FakeCaptureBackend(["frame-0", "frame-1", "frame-2", "frame-3"]),
        difference_backend=FakeDifferenceBackend([0.2, 0.005, 0.003]),
        sleep_fn=sleeps.append,
        monotonic_fn=clock,
    )

    result = module.wait_for_animation_to_settle(
        AnimationWaitRequest(
            wait_id="settle",
            timeout_seconds=1.0,
            sampling_interval_seconds=0.1,
            settle_threshold=0.01,
            consecutive_stable_frames=2,
        )
    )

    assert result.succeeded is True
    assert result.completed_signals == (AnimationCompletionSignal.SETTLED,)
    assert result.elapsed_seconds == 0.3
    assert sleeps == [0.1, 0.1]
    assert module.wait_logs[-1].elapsed_seconds == 0.3


def test_wait_for_spinner_to_disappear_uses_template_signal():
    clock = iter([0.0, 0.0, 0.1, 0.2]).__next__
    module = AnimationTransitionWaitModule(
        capture_backend=FakeCaptureBackend(["frame-0", "frame-1", "frame-2"]),
        difference_backend=FakeDifferenceBackend([0.3, 0.02]),
        template_matcher=FakeTemplateMatcher([1, 0]),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = module.wait_for_spinner_to_disappear(
        AnimationWaitRequest(
            wait_id="spinner",
            timeout_seconds=1.0,
            sampling_interval_seconds=0.1,
            spinner_template_name="spinner",
            spinner_template_path="spinner.png",
        )
    )

    assert result.succeeded is True
    assert AnimationCompletionSignal.SPINNER_GONE in result.completed_signals
    assert "spinner" in (result.detail or "").lower()


def test_wait_for_progress_completion_uses_accessibility_signal():
    clock = iter([0.0, 0.0, 0.1, 0.2]).__next__
    module = AnimationTransitionWaitModule(
        capture_backend=FakeCaptureBackend(["frame-0", "frame-1", "frame-2"]),
        difference_backend=FakeDifferenceBackend([0.2, 0.01]),
        accessibility_reader=FakeAccessibilityReader([0, 1]),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = module.wait_for_progress_completion(
        AnimationWaitRequest(
            wait_id="progress",
            timeout_seconds=1.0,
            sampling_interval_seconds=0.1,
            progress_element_role="ProgressBar",
            progress_expected_value="100",
        )
    )

    assert result.succeeded is True
    assert AnimationCompletionSignal.PROGRESS_COMPLETE in result.completed_signals


def test_wait_for_completion_can_require_settle_and_progress_signals_together():
    clock = iter([0.0, 0.0, 0.1, 0.2, 0.3]).__next__
    module = AnimationTransitionWaitModule(
        capture_backend=FakeCaptureBackend(["frame-0", "frame-1", "frame-2", "frame-3"]),
        difference_backend=FakeDifferenceBackend([0.2, 0.005, 0.004]),
        ocr_extractor=FakeOCRExtractor([False, True]),
        sleep_fn=lambda _: None,
        monotonic_fn=clock,
    )

    result = module.wait_for_completion(
        AnimationWaitRequest(
            wait_id="combined",
            timeout_seconds=1.0,
            sampling_interval_seconds=0.1,
            settle_threshold=0.01,
            consecutive_stable_frames=2,
            progress_complete_text="100%",
            required_signals=(
                AnimationCompletionSignal.SETTLED,
                AnimationCompletionSignal.PROGRESS_COMPLETE,
            ),
            screenshot_path="transition.png",
        )
    )

    assert result.succeeded is True
    assert result.screenshot_path == "transition.png"
    assert result.completed_signals == (
        AnimationCompletionSignal.SETTLED,
        AnimationCompletionSignal.PROGRESS_COMPLETE,
    )
