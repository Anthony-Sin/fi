from __future__ import annotations

import ctypes
import ctypes.wintypes
from dataclasses import dataclass, field
from pathlib import Path

from desktop_automation_perception.models import (
    ThemeDetectionResult,
    ThemeTemplateReferenceSet,
    UITheme,
)


@dataclass(slots=True)
class Win32ThemeBackend:
    apps_use_light_theme_path: str = (
        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    )
    high_contrast_path: str = r"Control Panel\Accessibility\HighContrast"

    def detect_theme(self) -> ThemeDetectionResult | None:
        high_contrast = self._read_registry_value(self.high_contrast_path, "Flags")
        if high_contrast not in (None, "", "0"):
            return ThemeDetectionResult(
                theme=UITheme.HIGH_CONTRAST,
                detected_with="win32_registry",
                confidence=1.0,
                reason="Windows high contrast mode is enabled.",
            )

        light_mode = self._read_registry_value(self.apps_use_light_theme_path, "AppsUseLightTheme")
        if light_mode == 0:
            return ThemeDetectionResult(
                theme=UITheme.DARK,
                detected_with="win32_registry",
                confidence=1.0,
                reason="Windows app theme is configured for dark mode.",
            )
        if light_mode == 1:
            return ThemeDetectionResult(
                theme=UITheme.LIGHT,
                detected_with="win32_registry",
                confidence=1.0,
                reason="Windows app theme is configured for light mode.",
            )
        return None

    def _read_registry_value(self, subkey: str, value_name: str):
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as handle:
                value, _ = winreg.QueryValueEx(handle, value_name)
                return value
        except OSError:
            return None


@dataclass(slots=True)
class ScreenBrightnessThemeHeuristic:
    capture_backend: object
    dark_threshold: float = 0.40
    high_contrast_threshold: float = 0.92

    def detect_theme(self) -> ThemeDetectionResult:
        image = self.capture_backend.capture()
        grayscale = image.convert("L").resize((64, 64))
        histogram = grayscale.histogram()
        total = float(sum(histogram)) or 1.0
        brightness = sum(index * count for index, count in enumerate(histogram)) / (255.0 * total)
        low_band = sum(histogram[:32]) / total
        high_band = sum(histogram[224:]) / total

        if low_band + high_band >= self.high_contrast_threshold:
            return ThemeDetectionResult(
                theme=UITheme.HIGH_CONTRAST,
                detected_with="screen_heuristic",
                confidence=min(0.95, low_band + high_band),
                reason="Screen luminance is concentrated in extreme light/dark bands.",
            )
        if brightness <= self.dark_threshold:
            return ThemeDetectionResult(
                theme=UITheme.DARK,
                detected_with="screen_heuristic",
                confidence=max(0.55, 1.0 - brightness),
                reason="Average screen brightness is consistent with a dark UI theme.",
            )
        return ThemeDetectionResult(
            theme=UITheme.LIGHT,
            detected_with="screen_heuristic",
            confidence=max(0.55, brightness),
            reason="Average screen brightness is consistent with a light UI theme.",
        )


@dataclass(slots=True)
class ThemeAdaptationModule:
    os_theme_backend: object | None = None
    heuristic_detector: object | None = None
    reference_sets: dict[UITheme, ThemeTemplateReferenceSet] = field(default_factory=dict)
    active_detection: ThemeDetectionResult = field(
        default_factory=lambda: ThemeDetectionResult(
            theme=UITheme.UNKNOWN,
            detected_with="unset",
            confidence=0.0,
        )
    )

    def detect_theme_on_startup(self) -> ThemeDetectionResult:
        if self.os_theme_backend is not None:
            detected = self.os_theme_backend.detect_theme()
            if detected is not None:
                self.active_detection = detected
                return detected

        if self.heuristic_detector is not None:
            detected = self.heuristic_detector.detect_theme()
            self.active_detection = detected
            return detected

        self.active_detection = ThemeDetectionResult(
            theme=UITheme.UNKNOWN,
            detected_with="default",
            confidence=0.0,
            reason="No theme detector is configured.",
        )
        return self.active_detection

    def active_theme(self) -> UITheme:
        return self.active_detection.theme

    def resolve_template_path(self, template_path: str, theme: UITheme | None = None) -> str:
        requested_theme = theme or self.active_theme()
        candidate = self._resolve_from_reference_set(template_path, requested_theme)
        if candidate is not None:
            return candidate

        if requested_theme is not UITheme.UNKNOWN:
            fallback = self._resolve_from_reference_set(template_path, UITheme.LIGHT)
            if fallback is not None:
                return fallback

        return template_path

    def register_reference_set(
        self,
        *,
        theme: UITheme,
        root_directory: str,
        aliases: tuple[str, ...] = (),
    ) -> None:
        self.reference_sets[theme] = ThemeTemplateReferenceSet(
            theme=theme,
            root_directory=root_directory,
            aliases=aliases,
        )

    def _resolve_from_reference_set(self, template_path: str, theme: UITheme) -> str | None:
        reference_set = self.reference_sets.get(theme)
        if reference_set is None:
            return None

        source = Path(template_path)
        candidate = Path(reference_set.root_directory) / source.name
        if candidate.exists():
            return str(candidate)

        for alias in reference_set.aliases:
            alias_candidate = Path(alias) / source.name
            if alias_candidate.exists():
                return str(alias_candidate)
        return None
