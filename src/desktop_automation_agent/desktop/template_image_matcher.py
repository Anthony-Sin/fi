from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from desktop_automation_agent.contracts import ImageMatcherBackend
from desktop_automation_agent.models import (
    ReferenceTemplateMetadata,
    TemplateCaptureResult,
    TemplateMatch,
    TemplateSearchRequest,
    TemplateSearchResult,
    UITheme,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenCVImageMatcherBackend:
    def load_image(self, path: str) -> Any | None:
        try:
            import cv2

            image = cv2.imread(path, cv2.IMREAD_COLOR)
            if image is None:
                logger.warning(f"Unable to load image: {path}")
            return image
        except Exception as e:
            logger.warning(f"Error loading image {path}: {e}")
            return None

    def load_screenshot(self, screenshot_path: str | None = None) -> Any | None:
        if screenshot_path is not None:
            return self.load_image(screenshot_path)

        try:
            import cv2
            import numpy
            import pyautogui

            screenshot = pyautogui.screenshot()
            return cv2.cvtColor(numpy.array(screenshot), cv2.COLOR_RGB2BGR)
        except Exception as e:
            logger.warning(f"Failed to capture screenshot with OpenCV: {e}")
            return None

    def crop_image(self, image: Any, bounds: tuple[int, int, int, int]) -> Any | None:
        if image is None:
            return None
        try:
            left, top, right, bottom = bounds
            return image[top:bottom, left:right]
        except Exception as e:
            logger.warning(f"Failed to crop image: {e}")
            return None

    def save_image(self, image: Any, path: str) -> bool:
        if image is None:
            return False
        try:
            import cv2

            if not cv2.imwrite(path, image):
                logger.warning(f"Unable to save image to {path}")
                return False
            return True
        except Exception as e:
            logger.warning(f"Error saving image to {path}: {e}")
            return False

    def resize_image(self, image: Any, scale_factor: float) -> Any | None:
        if image is None:
            return None
        if scale_factor == 1.0:
            return image
        try:
            import cv2

            width, height = self.get_image_size(image)
            resized_width = max(1, int(round(width * scale_factor)))
            resized_height = max(1, int(round(height * scale_factor)))
            return cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        except Exception as e:
            logger.warning(f"Failed to resize image: {e}")
            return image

    def get_image_size(self, image) -> tuple[int, int]:
        height, width = image.shape[:2]
        return (width, height)

    def find_matches(
        self,
        screenshot,
        template,
        threshold: float,
        region_of_interest: tuple[int, int, int, int] | None = None,
    ) -> list[TemplateMatch]:
        import cv2
        import numpy

        roi_image = screenshot
        x_offset = 0
        y_offset = 0
        if region_of_interest is not None:
            left, top, right, bottom = region_of_interest
            roi_image = screenshot[top:bottom, left:right]
            x_offset = left
            y_offset = top

        result = cv2.matchTemplate(roi_image, template, cv2.TM_CCOEFF_NORMED)
        locations = numpy.where(result >= threshold)
        template_height, template_width = template.shape[:2]
        matches: list[TemplateMatch] = []

        for y, x in zip(locations[0], locations[1]):
            confidence = float(result[y, x])
            left = int(x + x_offset)
            top = int(y + y_offset)
            right = left + int(template_width)
            bottom = top + int(template_height)
            matches.append(
                TemplateMatch(
                    template_name="",
                    confidence=confidence,
                    bounds=(left, top, right, bottom),
                    center=((left + right) // 2, (top + bottom) // 2),
                )
            )

        matches.sort(key=lambda match: match.confidence, reverse=True)
        return self._deduplicate_overlapping_matches(matches)

    def _deduplicate_overlapping_matches(self, matches: list[TemplateMatch]) -> list[TemplateMatch]:
        deduplicated: list[TemplateMatch] = []
        for match in matches:
            if any(self._overlaps(match.bounds, existing.bounds) for existing in deduplicated):
                continue
            deduplicated.append(match)
        return deduplicated

    def _overlaps(
        self,
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> bool:
        left_a, top_a, right_a, bottom_a = first
        left_b, top_b, right_b, bottom_b = second
        return not (right_a <= left_b or right_b <= left_a or bottom_a <= top_b or bottom_b <= top_a)


@dataclass(slots=True)
class TemplateImageMatcher:
    backend: ImageMatcherBackend
    coordinate_manager: object | None = None
    theme_adapter: object | None = None

    def search(
        self,
        screenshot_path: str | None,
        requests: list[TemplateSearchRequest],
    ) -> list[TemplateSearchResult]:
        """Search for multiple templates in a screenshot."""
        try:
            screenshot = self.backend.load_screenshot(screenshot_path)
        except Exception as e:
            logger.warning("Failed to load screenshot for template search: %s", e)
            screenshot = None
        if screenshot is None:
            return [
                TemplateSearchResult(
                    template_name=request.template_name,
                    matches=[],
                    threshold=request.threshold,
                    region_of_interest=request.region_of_interest,
                    scale_factor=request.scale_factor,
                )
                for request in requests
            ]

        results: list[TemplateSearchResult] = []

        for request in requests:
            try:
                prepared_request = self._prepare_request(request)
                resolved_template_path = self._resolve_template_path(prepared_request)
                template = self.backend.load_image(resolved_template_path)
            except Exception as e:
                logger.warning("Failed to prepare or load template %s: %s", request.template_name, e)
                template = None
            if template is None:
                results.append(
                    TemplateSearchResult(
                        template_name=prepared_request.template_name,
                        matches=[],
                        threshold=prepared_request.threshold,
                        region_of_interest=prepared_request.region_of_interest,
                        scale_factor=self._selected_scale_factor(prepared_request),
                    )
                )
                continue

            try:
                matches = self._find_matches_for_request(
                    screenshot=screenshot,
                    template=template,
                    request=prepared_request,
                )
            except Exception as e:
                logger.warning("Matching failed for template %s: %s", prepared_request.template_name, e)
                matches = []
            results.append(
                TemplateSearchResult(
                    template_name=prepared_request.template_name,
                    matches=[
                        TemplateMatch(
                            template_name=prepared_request.template_name,
                            confidence=match.confidence,
                            bounds=match.bounds,
                            center=match.center,
                        )
                        for match in matches
                    ],
                    threshold=prepared_request.threshold,
                    region_of_interest=prepared_request.region_of_interest,
                    scale_factor=self._selected_scale_factor(prepared_request),
                )
            )

        return results

    def capture_reference_template(
        self,
        *,
        name: str,
        output_directory: str,
        bounds: tuple[int, int, int, int],
        application_name: str | None = None,
        screenshot_path: str | None = None,
        theme: UITheme | None = None,
    ) -> TemplateCaptureResult:
        screenshot = self.backend.load_screenshot(screenshot_path)
        if screenshot is None:
            return TemplateCaptureResult(succeeded=False, reason="Failed to load screenshot.")

        cropped = self.backend.crop_image(screenshot, bounds)
        if cropped is None:
            return TemplateCaptureResult(succeeded=False, reason="Failed to crop template image.")

        output_dir = Path(output_directory)
        selected_theme = theme or self._active_theme()
        if selected_theme is not UITheme.UNKNOWN:
            output_dir = output_dir / selected_theme.value
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc)
        safe_name = name.replace(" ", "_")
        image_path = output_dir / f"{safe_name}.png"
        metadata_path = output_dir / f"{safe_name}.json"
        baseline_dpi = 96
        if self.coordinate_manager is not None:
            baseline_dpi = getattr(self.coordinate_manager.current_screen_bounds(), "dpi", 96)

        if not self.backend.save_image(cropped, str(image_path)):
            return TemplateCaptureResult(succeeded=False, reason=f"Failed to save template image to {image_path}")

        metadata = ReferenceTemplateMetadata(
            name=name,
            image_path=str(image_path),
            metadata_path=str(metadata_path),
            timestamp=timestamp,
            screen_resolution=self.backend.get_image_size(screenshot),
            application_name=application_name,
            bounds=bounds,
            baseline_dpi=baseline_dpi,
            theme=selected_theme,
        )

        metadata_path.write_text(
            json.dumps(
                {
                    "name": metadata.name,
                    "image_path": metadata.image_path,
                    "timestamp": metadata.timestamp.isoformat(),
                    "screen_resolution": list(metadata.screen_resolution),
                    "application_name": metadata.application_name,
                    "bounds": list(metadata.bounds) if metadata.bounds is not None else None,
                    "baseline_dpi": metadata.baseline_dpi,
                    "theme": metadata.theme.value,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        return TemplateCaptureResult(succeeded=True, reference=metadata)

    def _prepare_request(self, request: TemplateSearchRequest) -> TemplateSearchRequest:
        if self.coordinate_manager is None:
            return request
        return self.coordinate_manager.adapt_search_request(request)

    def _selected_scale_factor(self, request: TemplateSearchRequest) -> float:
        if self.coordinate_manager is None:
            return request.scale_factor
        factors = self.coordinate_manager.multi_scale_factors()
        return factors[0] if len(factors) == 1 else request.scale_factor

    def _resolve_template_path(self, request: TemplateSearchRequest) -> str:
        if self.theme_adapter is None:
            return request.template_path
        return self.theme_adapter.resolve_template_path(
            request.template_path,
            theme=request.theme,
        )

    def _active_theme(self) -> UITheme:
        if self.theme_adapter is None:
            return UITheme.UNKNOWN
        return self.theme_adapter.active_theme()

    def _find_matches_for_request(self, *, screenshot, template, request: TemplateSearchRequest) -> list[TemplateMatch]:
        search_variants: list[tuple[float, object]] = [(request.scale_factor, template)]
        if self.coordinate_manager is not None and hasattr(self.backend, "resize_image"):
            search_variants = []
            for scale_factor in self.coordinate_manager.multi_scale_factors():
                resized = self.backend.resize_image(template, scale_factor)
                search_variants.append((scale_factor, resized))

        all_matches: list[TemplateMatch] = []
        for _, variant in search_variants:
            matches = self.backend.find_matches(
                screenshot=screenshot,
                template=variant,
                threshold=request.threshold,
                region_of_interest=request.region_of_interest,
            )
            for match in matches:
                all_matches.append(
                    TemplateMatch(
                        template_name=request.template_name,
                        confidence=match.confidence,
                        bounds=match.bounds,
                        center=match.center,
                    )
                )
        all_matches.sort(key=lambda item: item.confidence, reverse=True)
        deduplicated: list[TemplateMatch] = []
        seen: set[tuple[int, int, int, int]] = set()
        for match in all_matches:
            if match.bounds in seen:
                continue
            seen.add(match.bounds)
            deduplicated.append(match)
        return deduplicated
