from __future__ import annotations

from typing import Any, Protocol

from desktop_automation_agent.models import (
    AccessibilityElement,
    ClipboardContent,
    DisplayConfigurationChangeResult,
    DisplayConfigurationSnapshot,
    InputAction,
    MonitorDescriptor,
    PromptInjectionMethod,
    PromptInjectionTarget,
    ScreenBounds,
    UILandmark,
    ScreenVerificationCheck,
    TemplateSearchRequest,
    WindowState,
)


class InputRunner(Protocol):
    def run(self, actions: list[InputAction]) -> Any:
        raise NotImplementedError("InputRunner.run must be implemented.")


class PromptInjector(Protocol):
    def inject_prompt(
        self,
        *,
        prompt: str,
        target: PromptInjectionTarget,
        method: PromptInjectionMethod = PromptInjectionMethod.TYPE,
        ocr_language: str = "eng",
        minimum_ocr_confidence: float = 0.0,
    ) -> Any:
        raise NotImplementedError("PromptInjector.inject_prompt must be implemented.")


class AccessibilityReader(Protocol):
    def read_active_application_tree(self) -> Any:
        raise NotImplementedError("AccessibilityReader.read_active_application_tree must be implemented.")

    def find_elements(
        self,
        *,
        name: str | None = None,
        role: str | None = None,
        value: str | None = None,
    ) -> Any:
        raise NotImplementedError("AccessibilityReader.find_elements must be implemented.")

    def get_element_text(self, element: AccessibilityElement) -> str | None:
        raise NotImplementedError("AccessibilityReader.get_element_text must be implemented.")

    def is_element_enabled(self, element: AccessibilityElement) -> bool | None:
        raise NotImplementedError("AccessibilityReader.is_element_enabled must be implemented.")

    def is_element_selected(self, element: AccessibilityElement) -> bool | None:
        raise NotImplementedError("AccessibilityReader.is_element_selected must be implemented.")


class OCRExtractor(Protocol):
    def extract_text(
        self,
        *,
        screenshot_path: str | None = None,
        region_of_interest: tuple[int, int, int, int] | None = None,
        language: str = "eng",
        minimum_confidence: float = 0.0,
    ) -> Any:
        raise NotImplementedError("OCRExtractor.extract_text must be implemented.")

    def find_text(
        self,
        *,
        target: str,
        screenshot_path: str | None = None,
        region_of_interest: tuple[int, int, int, int] | None = None,
        language: str = "eng",
        minimum_confidence: float = 0.0,
    ) -> Any:
        raise NotImplementedError("OCRExtractor.find_text must be implemented.")


class TemplateMatcher(Protocol):
    def search(
        self,
        *,
        screenshot_path: str,
        requests: list[TemplateSearchRequest],
    ) -> Any:
        raise NotImplementedError("TemplateMatcher.search must be implemented.")


class ScreenshotBackend(Protocol):
    def capture_screenshot_to_path(self, path: str | None = None, monitor_id: str | None = None) -> str:
        raise NotImplementedError("ScreenshotBackend.capture_screenshot_to_path must be implemented.")


class WindowManager(Protocol):
    def list_windows(self) -> Any:
        raise NotImplementedError("WindowManager.list_windows must be implemented.")

    def focus_window(self, title: str | None = None, process_name: str | None = None) -> Any:
        raise NotImplementedError("WindowManager.focus_window must be implemented.")

    def get_focused_window(self) -> WindowState | None:
        raise NotImplementedError("WindowManager.get_focused_window must be implemented.")


class ApplicationLauncherBackend(Protocol):
    def launch_executable(self, executable_path: str, arguments: tuple[str, ...]) -> bool:
        raise NotImplementedError("ApplicationLauncherBackend.launch_executable must be implemented.")

    def launch_start_menu(self, query: str, arguments: tuple[str, ...]) -> bool:
        raise NotImplementedError("ApplicationLauncherBackend.launch_start_menu must be implemented.")

    def launch_url(self, url: str) -> bool:
        raise NotImplementedError("ApplicationLauncherBackend.launch_url must be implemented.")


class BrowserLauncher(Protocol):
    def launch(self, browser_executable: str, profile_directory: str, application: str | None = None) -> Any:
        raise NotImplementedError("BrowserLauncher.launch must be implemented.")

    def close(self, process_id: int | None, profile_directory: str) -> bool:
        raise NotImplementedError("BrowserLauncher.close must be implemented.")


class ScreenCaptureBackend(Protocol):
    def capture(
        self,
        region_of_interest: tuple[int, int, int, int] | None = None,
        monitor_id: str | None = None,
    ) -> Any:
        raise NotImplementedError("ScreenCaptureBackend.capture must be implemented.")

    def save(self, image: Any, path: str) -> str:
        raise NotImplementedError("ScreenCaptureBackend.save must be implemented.")


class DifferenceBackend(Protocol):
    def compute_difference(self, previous_image: Any, current_image: Any) -> float:
        raise NotImplementedError("DifferenceBackend.compute_difference must be implemented.")


class ClipboardBackend(Protocol):
    def read(self) -> ClipboardContent:
        raise NotImplementedError("ClipboardBackend.read must be implemented.")

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        raise NotImplementedError("ClipboardBackend.write_text must be implemented.")

    def write_image(self, image_bytes: bytes) -> None:
        raise NotImplementedError("ClipboardBackend.write_image must be implemented.")


class TextReader(Protocol):
    def read_text(self) -> str:
        raise NotImplementedError("TextReader.read_text must be implemented.")


class StateVerifier(Protocol):
    def verify(
        self,
        checks: list[ScreenVerificationCheck],
        screenshot_path: str | None = None,
    ) -> Any:
        raise NotImplementedError("StateVerifier.verify must be implemented.")


class DatabaseExporter(Protocol):
    def insert_records(self, *, table: str, records: list[dict[str, Any]]) -> int:
        raise NotImplementedError("DatabaseExporter.insert_records must be implemented.")


class APIExporter(Protocol):
    def push_records(self, *, endpoint: str, records: list[dict[str, Any]]) -> int:
        raise NotImplementedError("APIExporter.push_records must be implemented.")


class AuditLogger(Protocol):
    def log_action(self, **kwargs: Any) -> Any:
        raise NotImplementedError("AuditLogger.log_action must be implemented.")


class ApplicationLauncher(Protocol):
    def launch(self, request: Any) -> Any:
        raise NotImplementedError("ApplicationLauncher.launch must be implemented.")


class ClipboardManager(Protocol):
    def write_text(self, text: str, *, delay_seconds: float = 0.0, encoding: str = "utf-8") -> Any:
        raise NotImplementedError("ClipboardManager.write_text must be implemented.")

    def read_clipboard(self) -> Any:
        raise NotImplementedError("ClipboardManager.read_clipboard must be implemented.")


class ApplicationNavigator(Protocol):
    def navigate(self, step: Any) -> Any:
        raise NotImplementedError("ApplicationNavigator.navigate must be implemented.")


class PromptTemplateRenderer(Protocol):
    def render_template(self, name: str, variables: dict[str, str]) -> Any:
        raise NotImplementedError("PromptTemplateRenderer.render_template must be implemented.")


class AIInterfaceNavigationExecutor(Protocol):
    def navigate(self, *, prompt: str, interface: Any, injection_method: Any) -> Any:
        raise NotImplementedError("AIInterfaceNavigationExecutor.navigate must be implemented.")


class FailureClassifier(Protocol):
    def classify(self, error: Exception | object) -> Any:
        raise NotImplementedError("FailureClassifier.classify must be implemented.")


class ClipboardWriter(Protocol):
    def write_text(self, text: str, *, delay_seconds: float = 0.0, encoding: str = "utf-8") -> Any:
        raise NotImplementedError("ClipboardWriter.write_text must be implemented.")


class PlatformTextInputBackend(Protocol):
    def inject_text(self, text: str) -> None:
        raise NotImplementedError("PlatformTextInputBackend.inject_text must be implemented.")


class ImageMatcherBackend(Protocol):
    def load_image(self, path: str) -> Any:
        raise NotImplementedError("ImageMatcherBackend.load_image must be implemented.")

    def load_screenshot(self, screenshot_path: str | None = None) -> Any:
        raise NotImplementedError("ImageMatcherBackend.load_screenshot must be implemented.")

    def crop_image(self, image: Any, bounds: tuple[int, int, int, int]) -> Any:
        raise NotImplementedError("ImageMatcherBackend.crop_image must be implemented.")

    def save_image(self, image: Any, path: str) -> None:
        raise NotImplementedError("ImageMatcherBackend.save_image must be implemented.")

    def resize_image(self, image: Any, scale_factor: float) -> Any:
        raise NotImplementedError("ImageMatcherBackend.resize_image must be implemented.")

    def get_image_size(self, image: Any) -> tuple[int, int]:
        raise NotImplementedError("ImageMatcherBackend.get_image_size must be implemented.")

    def find_matches(
        self,
        screenshot: Any,
        template: Any,
        threshold: float,
        region_of_interest: tuple[int, int, int, int] | None = None,
    ) -> Any:
        raise NotImplementedError("ImageMatcherBackend.find_matches must be implemented.")


class WindowBackend(Protocol):
    def enumerate_windows(self) -> list[Any]:
        raise NotImplementedError("WindowBackend.enumerate_windows must be implemented.")

    def focus_window(self, handle: int) -> bool:
        raise NotImplementedError("WindowBackend.focus_window must be implemented.")

    def move_resize_window(self, handle: int, x: int, y: int, width: int, height: int) -> bool:
        raise NotImplementedError("WindowBackend.move_resize_window must be implemented.")

    def show_window(self, handle: int, command: int) -> bool:
        raise NotImplementedError("WindowBackend.show_window must be implemented.")

    def get_foreground_window_handle(self) -> int | None:
        raise NotImplementedError("WindowBackend.get_foreground_window_handle must be implemented.")

    def get_window_by_handle(self, handle: int) -> Any:
        raise NotImplementedError("WindowBackend.get_window_by_handle must be implemented.")


class AccessibilityBackend(Protocol):
    def get_active_application_tree(self) -> Any:
        raise NotImplementedError("AccessibilityBackend.get_active_application_tree must be implemented.")


class RawWindowBackend(Protocol):
    def get_active_window_handle(self) -> int | None:
        raise NotImplementedError("RawWindowBackend.get_active_window_handle must be implemented.")

    def inspect_window(self, handle: int) -> AccessibilityElement | None:
        raise NotImplementedError("RawWindowBackend.inspect_window must be implemented.")

    def inspect_children(self, handle: int) -> list[AccessibilityElement]:
        raise NotImplementedError("RawWindowBackend.inspect_children must be implemented.")

    def get_application_name(self, handle: int) -> str | None:
        raise NotImplementedError("RawWindowBackend.get_application_name must be implemented.")


class ScreenInspector(Protocol):
    def get_screen_bounds(self) -> ScreenBounds:
        raise NotImplementedError("ScreenInspector.get_screen_bounds must be implemented.")

    def get_monitor_bounds(self, monitor_id: str | None = None) -> ScreenBounds:
        raise NotImplementedError("ScreenInspector.get_monitor_bounds must be implemented.")


class UILandmarkProvider(Protocol):
    def list_landmarks(self) -> list[UILandmark]:
        raise NotImplementedError("UILandmarkProvider.list_landmarks must be implemented.")


class DisplayManager(Protocol):
    def list_monitors(self) -> list[MonitorDescriptor]:
        raise NotImplementedError("DisplayManager.list_monitors must be implemented.")

    def get_monitor(self, monitor_id: str | None = None) -> MonitorDescriptor | None:
        raise NotImplementedError("DisplayManager.get_monitor must be implemented.")

    def get_screen_bounds(self, monitor_id: str | None = None) -> ScreenBounds:
        raise NotImplementedError("DisplayManager.get_screen_bounds must be implemented.")

    def start_session(self) -> DisplayConfigurationSnapshot:
        raise NotImplementedError("DisplayManager.start_session must be implemented.")

    def detect_configuration_change(self) -> DisplayConfigurationChangeResult:
        raise NotImplementedError("DisplayManager.detect_configuration_change must be implemented.")


class InputBackend(Protocol):
    def click(self, x: int, y: int, button: str) -> None:
        raise NotImplementedError("InputBackend.click must be implemented.")

    def press(self, key: str) -> None:
        raise NotImplementedError("InputBackend.press must be implemented.")

    def write(self, text: str) -> None:
        raise NotImplementedError("InputBackend.write must be implemented.")

    def scroll(self, clicks: int) -> None:
        raise NotImplementedError("InputBackend.scroll must be implemented.")

    def hotkey(self, *keys: str) -> None:
        raise NotImplementedError("InputBackend.hotkey must be implemented.")


class OCRBackend(Protocol):
    def capture_screenshot(self, region: tuple[int, int, int, int] | None = None) -> Any:
        raise NotImplementedError("OCRBackend.capture_screenshot must be implemented.")

    def load_image(self, screenshot_path: str) -> Any:
        raise NotImplementedError("OCRBackend.load_image must be implemented.")

    def extract_blocks(self, image: Any, language: str) -> Any:
        raise NotImplementedError("OCRBackend.extract_blocks must be implemented.")
