from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from desktop_automation_agent.contracts import ScreenCaptureBackend, UILandmarkProvider, WindowManager
from desktop_automation_agent.models import UIStateFingerprint, UILandmark


@dataclass(slots=True)
class UIStateFingerprinter:
    capture_backend: ScreenCaptureBackend
    window_manager: WindowManager | None = None
    landmark_provider: UILandmarkProvider | None = None
    histogram_bins_per_channel: int = 4
    histogram_sample_size: tuple[int, int] = (64, 64)

    def capture_fingerprint(
        self,
        *,
        region_of_interest: tuple[int, int, int, int] | None = None,
    ) -> UIStateFingerprint:
        image = self.capture_backend.capture(region_of_interest)
        width, height = self._get_image_size(image)
        titles = self._extract_window_titles()
        landmarks = self._extract_landmark_positions(width=width, height=height)
        histogram = self._build_histogram(image)

        return UIStateFingerprint(
            window_title_hash=self._hash_window_titles(titles),
            landmark_positions=landmarks,
            pixel_histogram=histogram,
            screen_size=(width, height),
            window_count=len(titles),
        )

    def compare(
        self,
        first: UIStateFingerprint,
        second: UIStateFingerprint,
    ) -> float:
        return self.compare_fingerprints(first, second)

    def compare_to_current(
        self,
        baseline: UIStateFingerprint,
        *,
        region_of_interest: tuple[int, int, int, int] | None = None,
    ) -> float:
        current = self.capture_fingerprint(region_of_interest=region_of_interest)
        return self.compare_fingerprints(baseline, current)

    @staticmethod
    def compare_fingerprints(first: UIStateFingerprint, second: UIStateFingerprint) -> float:
        title_similarity = UIStateFingerprinter._compare_titles(first, second)
        landmark_similarity = UIStateFingerprinter._compare_landmarks(first, second)
        histogram_similarity = UIStateFingerprinter._compare_histograms(first, second)
        score = (0.4 * title_similarity) + (0.3 * landmark_similarity) + (0.3 * histogram_similarity)
        return max(0.0, min(1.0, score))

    def _extract_window_titles(self) -> tuple[str, ...]:
        if self.window_manager is None:
            return ()

        titles = []
        for window in self.window_manager.list_windows():
            title = getattr(window, "title", None)
            if not title:
                continue
            normalized = str(title).strip()
            if normalized:
                titles.append(normalized)
        return tuple(sorted(titles))

    def _extract_landmark_positions(self, *, width: int, height: int) -> dict[str, tuple[float, float]]:
        if self.landmark_provider is None or width <= 0 or height <= 0:
            return {}

        positions: dict[str, tuple[float, float]] = {}
        for landmark in self.landmark_provider.list_landmarks():
            normalized = self._normalize_landmark_position(landmark, width=width, height=height)
            if normalized is not None:
                positions[landmark.name] = normalized
        return positions

    def _build_histogram(self, image) -> tuple[float, ...]:
        rgb_image = image.convert("RGB")
        if self.histogram_sample_size[0] > 0 and self.histogram_sample_size[1] > 0:
            rgb_image = rgb_image.resize(self.histogram_sample_size)

        bins_per_channel = max(1, self.histogram_bins_per_channel)
        histogram = [0.0] * (bins_per_channel * 3)
        raw_histogram = rgb_image.histogram()

        for channel_index in range(3):
            channel = raw_histogram[channel_index * 256 : (channel_index + 1) * 256]
            for value, count in enumerate(channel):
                bucket = min(bins_per_channel - 1, int(value * bins_per_channel / 256))
                histogram[(channel_index * bins_per_channel) + bucket] += float(count)

        total = sum(histogram)
        if total <= 0:
            return tuple(0.0 for _ in histogram)
        return tuple(bucket / total for bucket in histogram)

    @staticmethod
    def _hash_window_titles(titles: tuple[str, ...]) -> str:
        joined = "\n".join(titles)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _normalize_landmark_position(
        landmark: UILandmark,
        *,
        width: int,
        height: int,
    ) -> tuple[float, float] | None:
        x: float | None = None
        y: float | None = None

        if landmark.center is not None:
            x, y = landmark.center
        elif landmark.bounds is not None:
            left, top, right, bottom = landmark.bounds
            x = (left + right) / 2.0
            y = (top + bottom) / 2.0

        if x is None or y is None or width <= 0 or height <= 0:
            return None

        normalized_x = max(0.0, min(1.0, x / width))
        normalized_y = max(0.0, min(1.0, y / height))
        return (normalized_x, normalized_y)

    @staticmethod
    def _get_image_size(image) -> tuple[int, int]:
        size = getattr(image, "size", None)
        if isinstance(size, tuple) and len(size) == 2:
            return int(size[0]), int(size[1])
        width = getattr(image, "width", 0)
        height = getattr(image, "height", 0)
        return int(width), int(height)

    @staticmethod
    def _compare_titles(first: UIStateFingerprint, second: UIStateFingerprint) -> float:
        if first.window_title_hash == second.window_title_hash:
            count_gap = abs(first.window_count - second.window_count)
            return 1.0 if count_gap == 0 else max(0.8, 1.0 - (0.1 * count_gap))

        maximum = max(first.window_count, second.window_count, 1)
        count_similarity = 1.0 - (abs(first.window_count - second.window_count) / maximum)
        return max(0.0, min(0.5, count_similarity * 0.5))

    @staticmethod
    def _compare_landmarks(first: UIStateFingerprint, second: UIStateFingerprint) -> float:
        keys = set(first.landmark_positions) | set(second.landmark_positions)
        if not keys:
            return 1.0

        max_distance = math.sqrt(2.0)
        similarities: list[float] = []
        for key in keys:
            left = first.landmark_positions.get(key)
            right = second.landmark_positions.get(key)
            if left is None or right is None:
                similarities.append(0.0)
                continue
            distance = math.hypot(left[0] - right[0], left[1] - right[1])
            similarities.append(max(0.0, 1.0 - (distance / max_distance)))
        return sum(similarities) / len(similarities)

    @staticmethod
    def _compare_histograms(first: UIStateFingerprint, second: UIStateFingerprint) -> float:
        if not first.pixel_histogram and not second.pixel_histogram:
            return 1.0
        if len(first.pixel_histogram) != len(second.pixel_histogram):
            return 0.0
        return sum(min(left, right) for left, right in zip(first.pixel_histogram, second.pixel_histogram))
