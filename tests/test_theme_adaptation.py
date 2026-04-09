from desktop_automation_perception.models import ThemeDetectionResult, TemplateSearchRequest, UITheme
from desktop_automation_perception.template_image_matcher import TemplateImageMatcher
from desktop_automation_perception.theme_adaptation import ThemeAdaptationModule


class FakeOSThemeBackend:
    def __init__(self, result):
        self.result = result

    def detect_theme(self):
        return self.result


class FakeHeuristicDetector:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def detect_theme(self):
        self.calls += 1
        return self.result


class FakeTemplateBackend:
    def __init__(self):
        self.loaded_paths = []

    def load_image(self, path: str):
        self.loaded_paths.append(path)
        return {"path": path}

    def load_screenshot(self, screenshot_path: str | None = None):
        return {"size": (100, 100)}

    def crop_image(self, image, bounds):
        return {"image": image, "bounds": bounds}

    def save_image(self, image, path: str) -> None:
        pass

    def resize_image(self, image, scale_factor: float):
        return image

    def get_image_size(self, image):
        return image["size"]

    def find_matches(self, screenshot, template, threshold: float, region_of_interest=None):
        return []


def test_theme_adaptation_prefers_os_theme_detection():
    heuristic = FakeHeuristicDetector(
        ThemeDetectionResult(theme=UITheme.LIGHT, detected_with="heuristic", confidence=0.7)
    )
    module = ThemeAdaptationModule(
        os_theme_backend=FakeOSThemeBackend(
            ThemeDetectionResult(theme=UITheme.DARK, detected_with="win32_registry", confidence=1.0)
        ),
        heuristic_detector=heuristic,
    )

    result = module.detect_theme_on_startup()

    assert result.theme is UITheme.DARK
    assert result.detected_with == "win32_registry"
    assert heuristic.calls == 0


def test_theme_adaptation_falls_back_to_heuristic_when_os_detection_is_unavailable():
    heuristic = FakeHeuristicDetector(
        ThemeDetectionResult(theme=UITheme.HIGH_CONTRAST, detected_with="screen_heuristic", confidence=0.93)
    )
    module = ThemeAdaptationModule(
        os_theme_backend=FakeOSThemeBackend(None),
        heuristic_detector=heuristic,
    )

    result = module.detect_theme_on_startup()

    assert result.theme is UITheme.HIGH_CONTRAST
    assert result.detected_with == "screen_heuristic"
    assert heuristic.calls == 1


def test_template_image_matcher_uses_theme_specific_reference_set(tmp_path):
    light_dir = tmp_path / "light"
    dark_dir = tmp_path / "dark"
    light_dir.mkdir()
    dark_dir.mkdir()
    (light_dir / "send.png").write_text("light", encoding="utf-8")
    (dark_dir / "send.png").write_text("dark", encoding="utf-8")

    module = ThemeAdaptationModule(
        os_theme_backend=FakeOSThemeBackend(
            ThemeDetectionResult(theme=UITheme.DARK, detected_with="win32_registry")
        )
    )
    module.register_reference_set(theme=UITheme.LIGHT, root_directory=str(light_dir))
    module.register_reference_set(theme=UITheme.DARK, root_directory=str(dark_dir))
    module.detect_theme_on_startup()

    backend = FakeTemplateBackend()
    matcher = TemplateImageMatcher(backend=backend, theme_adapter=module)
    matcher.search(
        screenshot_path="screen.png",
        requests=[TemplateSearchRequest(template_name="send_button", template_path="templates/send.png")],
    )

    assert backend.loaded_paths == [str(dark_dir / "send.png")]


def test_template_image_matcher_can_capture_theme_specific_reference_metadata(tmp_path):
    module = ThemeAdaptationModule(
        os_theme_backend=FakeOSThemeBackend(
            ThemeDetectionResult(theme=UITheme.DARK, detected_with="win32_registry")
        )
    )
    module.detect_theme_on_startup()

    matcher = TemplateImageMatcher(backend=FakeTemplateBackend(), theme_adapter=module)
    result = matcher.capture_reference_template(
        name="send button",
        output_directory=str(tmp_path),
        bounds=(1, 2, 3, 4),
        application_name="Chat App",
        screenshot_path="screen.png",
    )

    assert result.succeeded is True
    assert result.reference is not None
    assert result.reference.theme is UITheme.DARK
    assert "dark" in result.reference.image_path
