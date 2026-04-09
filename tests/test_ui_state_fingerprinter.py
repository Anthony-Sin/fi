from PIL import Image

from desktop_automation_perception.change_detection_monitor import (
    PILDifferenceBackend,
    ScreenChangeDetectionMonitor,
)
from desktop_automation_perception.models import UIStateFingerprint, UILandmark, WindowContext
from desktop_automation_perception.ui_state_fingerprinter import UIStateFingerprinter


class FakeCaptureBackend:
    def __init__(self, image):
        self.image = image

    def capture(self, region_of_interest=None, monitor_id=None):
        return self.image

    def save(self, image, path: str) -> str:
        image.save(path)
        return path


class FakeWindowManager:
    def __init__(self, titles: list[str]):
        self.titles = titles

    def list_windows(self):
        return [WindowContext(handle=index + 1, title=title) for index, title in enumerate(self.titles)]


class FakeLandmarkProvider:
    def __init__(self, landmarks: list[UILandmark]):
        self.landmarks = landmarks

    def list_landmarks(self) -> list[UILandmark]:
        return list(self.landmarks)


def test_ui_state_fingerprinter_captures_compact_signature():
    fingerprinter = UIStateFingerprinter(
        capture_backend=FakeCaptureBackend(Image.new("RGB", (100, 50), color=(255, 0, 0))),
        window_manager=FakeWindowManager(["Editor", "Browser"]),
        landmark_provider=FakeLandmarkProvider(
            [
                UILandmark(name="toolbar", bounds=(0, 0, 100, 10)),
                UILandmark(name="submit", center=(80, 40)),
            ]
        ),
    )

    fingerprint = fingerprinter.capture_fingerprint()

    assert fingerprint.window_title_hash
    assert fingerprint.window_count == 2
    assert fingerprint.screen_size == (100, 50)
    assert fingerprint.landmark_positions["toolbar"] == (0.5, 0.1)
    assert fingerprint.landmark_positions["submit"] == (0.8, 0.8)
    assert len(fingerprint.pixel_histogram) == 12
    assert abs(sum(fingerprint.pixel_histogram) - 1.0) < 1e-9


def test_ui_state_fingerprinter_compare_scores_known_good_state_higher():
    good = UIStateFingerprint(
        window_title_hash="same",
        landmark_positions={"submit": (0.8, 0.8), "toolbar": (0.5, 0.1)},
        pixel_histogram=(0.0, 0.0, 0.34, 0.0, 0.33, 0.0, 0.0, 0.0, 0.33, 0.0, 0.0, 0.0),
        screen_size=(100, 50),
        window_count=2,
    )
    almost_same = UIStateFingerprint(
        window_title_hash="same",
        landmark_positions={"submit": (0.79, 0.81), "toolbar": (0.5, 0.12)},
        pixel_histogram=(0.0, 0.0, 0.32, 0.0, 0.35, 0.0, 0.0, 0.0, 0.33, 0.0, 0.0, 0.0),
        screen_size=(100, 50),
        window_count=2,
    )
    different = UIStateFingerprint(
        window_title_hash="different",
        landmark_positions={"submit": (0.2, 0.2)},
        pixel_histogram=(0.25, 0.0, 0.0, 0.08, 0.25, 0.0, 0.0, 0.08, 0.25, 0.0, 0.0, 0.09),
        screen_size=(100, 50),
        window_count=1,
    )

    similar_score = UIStateFingerprinter.compare_fingerprints(good, almost_same)
    different_score = UIStateFingerprinter.compare_fingerprints(good, different)

    assert similar_score > 0.95
    assert different_score < 0.35


def test_change_detection_monitor_can_compare_current_state_to_fingerprint():
    baseline_image = Image.new("RGB", (40, 40), color=(200, 200, 200))
    fingerprinter = UIStateFingerprinter(
        capture_backend=FakeCaptureBackend(baseline_image),
        window_manager=FakeWindowManager(["Editor"]),
        landmark_provider=FakeLandmarkProvider([UILandmark(name="primary", center=(20, 20))]),
    )
    baseline = fingerprinter.capture_fingerprint()
    monitor = ScreenChangeDetectionMonitor(
        capture_backend=FakeCaptureBackend(baseline_image),
        difference_backend=PILDifferenceBackend(),
        fingerprinter=fingerprinter,
    )

    similarity = monitor.compare_to_fingerprint(baseline)

    assert similarity == 1.0


def test_change_detection_monitor_requires_configured_fingerprinter():
    monitor = ScreenChangeDetectionMonitor(
        capture_backend=FakeCaptureBackend(Image.new("RGB", (10, 10), color=(0, 0, 0))),
        difference_backend=PILDifferenceBackend(),
    )

    try:
        monitor.compare_to_fingerprint(UIStateFingerprint(window_title_hash="x"))
    except ValueError as exc:
        assert "fingerprinter" in str(exc)
    else:
        raise AssertionError("Expected compare_to_fingerprint to reject missing fingerprinter.")
