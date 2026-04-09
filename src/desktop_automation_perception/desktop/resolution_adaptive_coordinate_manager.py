from __future__ import annotations

from dataclasses import dataclass, field

from desktop_automation_perception.models import (
    CoordinateAdaptationResult,
    ResolutionCalibrationProfile,
    ResolutionVerificationReference,
    ResolutionVerificationResult,
    ScreenBounds,
    TemplateSearchRequest,
)


@dataclass(slots=True)
class ResolutionAdaptiveCoordinateManager:
    calibration_profile: ResolutionCalibrationProfile
    screen_inspector: object
    template_matcher: object | None = None
    _verified_scale_x: float | None = field(default=None, init=False, repr=False)
    _verified_scale_y: float | None = field(default=None, init=False, repr=False)
    _last_verification: ResolutionVerificationResult | None = field(default=None, init=False, repr=False)

    def current_screen_bounds(self) -> ScreenBounds:
        return self.screen_inspector.get_screen_bounds()

    def scale_factors(self) -> tuple[float, float]:
        expected_scale_x, expected_scale_y = self.expected_scale_factors()
        return (
            self._verified_scale_x if self._verified_scale_x is not None else expected_scale_x,
            self._verified_scale_y if self._verified_scale_y is not None else expected_scale_y,
        )

    def expected_scale_factors(self) -> tuple[float, float]:
        current = self.current_screen_bounds()
        baseline_width, baseline_height = self.calibration_profile.baseline_resolution
        dpi_ratio = current.dpi / max(1, self.calibration_profile.baseline_dpi)
        return (
            (current.width / baseline_width) * dpi_ratio,
            (current.height / baseline_height) * dpi_ratio,
        )

    def adapt_point(self, point: tuple[int, int]) -> CoordinateAdaptationResult:
        scale_x, scale_y = self.scale_factors()
        adapted_point = (int(round(point[0] * scale_x)), int(round(point[1] * scale_y)))
        return CoordinateAdaptationResult(
            succeeded=True,
            original_point=point,
            adapted_point=adapted_point,
            scale_x=scale_x,
            scale_y=scale_y,
        )

    def adapt_bounds(self, bounds: tuple[int, int, int, int]) -> CoordinateAdaptationResult:
        scale_x, scale_y = self.scale_factors()
        left, top, right, bottom = bounds
        adapted_bounds = (
            int(round(left * scale_x)),
            int(round(top * scale_y)),
            int(round(right * scale_x)),
            int(round(bottom * scale_y)),
        )
        return CoordinateAdaptationResult(
            succeeded=True,
            original_bounds=bounds,
            adapted_bounds=adapted_bounds,
            scale_x=scale_x,
            scale_y=scale_y,
        )

    def multi_scale_factors(self) -> tuple[float, ...]:
        scale_x, scale_y = self.scale_factors()
        nominal = (scale_x + scale_y) / 2.0
        factors = {
            round(nominal * step, 4)
            for step in self.calibration_profile.multi_scale_steps
            if nominal * step > 0
        }
        return tuple(sorted(factors))

    def adapt_search_request(self, request: TemplateSearchRequest) -> TemplateSearchRequest:
        region = request.region_of_interest
        adapted_region = None
        if region is not None:
            adapted_region = self.adapt_bounds(region).adapted_bounds
        return TemplateSearchRequest(
            template_name=request.template_name,
            template_path=request.template_path,
            threshold=request.threshold,
            region_of_interest=adapted_region,
            scale_factor=request.scale_factor,
        )

    def build_startup_reference_requests(self) -> list[TemplateSearchRequest]:
        requests: list[TemplateSearchRequest] = []
        for reference in self.calibration_profile.reference_elements:
            adapted_region = None
            if reference.region_of_interest is not None:
                adapted_region = self.adapt_bounds(reference.region_of_interest).adapted_bounds
            requests.append(
                TemplateSearchRequest(
                    template_name=reference.name,
                    template_path=reference.template_path,
                    threshold=reference.threshold,
                    region_of_interest=adapted_region,
                )
            )
        return requests

    def verify_scale(self, *, screenshot_path: str | None = None) -> ResolutionVerificationResult:
        if self.template_matcher is None:
            result = ResolutionVerificationResult(
                succeeded=False,
                reason="Template matcher is required to verify resolution scale.",
            )
            self._last_verification = result
            return result
        references = self.calibration_profile.reference_elements
        if not references:
            result = ResolutionVerificationResult(
                succeeded=False,
                reason="At least one reference element is required to verify resolution scale.",
            )
            self._last_verification = result
            return result

        expected_scale_x, expected_scale_y = self.expected_scale_factors()
        results = self.template_matcher.search(
            screenshot_path=screenshot_path,
            requests=self.build_startup_reference_requests(),
        )
        measured: list[ResolutionVerificationReference] = []
        for reference, match_result in zip(references, results):
            if not getattr(match_result, "matches", None):
                continue
            match = match_result.matches[0]
            expected_center = self._center(reference.expected_bounds)
            actual_center = match.center
            scale_x = actual_center[0] / max(1, expected_center[0])
            scale_y = actual_center[1] / max(1, expected_center[1])
            measured.append(
                ResolutionVerificationReference(
                    name=reference.name,
                    expected_center=expected_center,
                    actual_center=actual_center,
                    scale_x=scale_x,
                    scale_y=scale_y,
                    confidence=match.confidence,
                )
            )

        current = self.current_screen_bounds()
        if not measured:
            result = ResolutionVerificationResult(
                succeeded=False,
                current_resolution=(current.width, current.height),
                current_dpi=current.dpi,
                expected_scale_x=expected_scale_x,
                expected_scale_y=expected_scale_y,
                reason="Unable to locate any startup reference elements for scale verification.",
            )
            self._last_verification = result
            return result

        actual_scale_x = sum(item.scale_x for item in measured) / len(measured)
        actual_scale_y = sum(item.scale_y for item in measured) / len(measured)
        within_tolerance = (
            abs(actual_scale_x - expected_scale_x) <= self.calibration_profile.scale_tolerance
            and abs(actual_scale_y - expected_scale_y) <= self.calibration_profile.scale_tolerance
        )
        self._verified_scale_x = actual_scale_x
        self._verified_scale_y = actual_scale_y
        result = ResolutionVerificationResult(
            succeeded=within_tolerance,
            references=measured,
            current_resolution=(current.width, current.height),
            current_dpi=current.dpi,
            expected_scale_x=expected_scale_x,
            expected_scale_y=expected_scale_y,
            actual_scale_x=actual_scale_x,
            actual_scale_y=actual_scale_y,
            reason=None if within_tolerance else "Measured scale differs from expected baseline ratio.",
        )
        self._last_verification = result
        return result

    def last_verification(self) -> ResolutionVerificationResult | None:
        return self._last_verification

    def _center(self, bounds: tuple[int, int, int, int]) -> tuple[int, int]:
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)
