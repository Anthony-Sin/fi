from __future__ import annotations

import ctypes
import ctypes.wintypes
from dataclasses import dataclass

from desktop_automation_agent.contracts import AccessibilityBackend, RawWindowBackend
from desktop_automation_agent.models import (
    AccessibilityElement,
    AccessibilityElementState,
    AccessibilityQueryResult,
    AccessibilityTree,
)


@dataclass(slots=True)
class PyWinAutoAccessibilityBackend:
    def get_active_application_tree(self) -> AccessibilityTree | None:
        try:
            from pywinauto import Desktop
        except ImportError:
            return None

        try:
            window = Desktop(backend="uia").get_active()
        except Exception:
            return None

        return AccessibilityTree(
            application_name=self._safe_value(window, "friendly_class_name"),
            root=self._convert_element(window),
        )

    def _convert_element(self, element, depth: int = 0, max_depth: int = 5) -> AccessibilityElement:
        info = getattr(element, "element_info", None)
        name = getattr(info, "name", None)
        control_type = getattr(info, "control_type", None)
        handle = getattr(info, "handle", None)
        rectangle = getattr(info, "rectangle", None)
        bounds = None
        if rectangle is not None:
            bounds = (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)

        state = AccessibilityElementState(
            text=self._safe_window_text(element),
            enabled=self._safe_value(element, "is_enabled"),
            selected=self._safe_selection(element),
        )

        children: list[AccessibilityElement] = []
        if depth < max_depth:
            try:
                child_elements = element.children()
            except Exception:
                child_elements = []
            children = [self._convert_element(child, depth + 1, max_depth=max_depth) for child in child_elements]

        return AccessibilityElement(
            element_id=f"uia:{handle or id(element)}",
            name=name,
            role=control_type,
            value=self._safe_value(info, "value"),
            state=state,
            bounds=bounds,
            children=children,
            source="accessibility",
            handle=int(handle) if handle else None,
        )

    def _safe_value(self, target, attribute: str):
        if target is None:
            return None
        try:
            value = getattr(target, attribute)
            return value() if callable(value) else value
        except Exception:
            return None

    def _safe_window_text(self, element) -> str | None:
        try:
            value = element.window_text()
            return value or None
        except Exception:
            return None

    def _safe_selection(self, element) -> bool | None:
        for attribute in ("is_selected", "is_checked"):
            try:
                value = getattr(element, attribute)
                return bool(value() if callable(value) else value)
            except Exception:
                continue
        return None


@dataclass(slots=True)
class Win32RawWindowBackend:
    def get_active_window_handle(self) -> int | None:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    def inspect_window(self, handle: int) -> AccessibilityElement | None:
        if not ctypes.windll.user32.IsWindow(handle):
            return None
        return self._build_element(handle)

    def inspect_children(self, handle: int) -> list[AccessibilityElement]:
        user32 = ctypes.windll.user32
        children: list[AccessibilityElement] = []
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def callback(hwnd: int, lparam: int) -> bool:
            element = self._build_element(int(hwnd))
            if element is not None:
                children.append(element)
            return True

        user32.EnumChildWindows(handle, enum_proc(callback), 0)
        return children

    def get_application_name(self, handle: int) -> str | None:
        process_id = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if not process_id.value:
            return None

        kernel32 = ctypes.windll.kernel32
        process_handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not process_handle:
            return None
        try:
            size = ctypes.wintypes.DWORD(260)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(process_handle, 0, buffer, ctypes.byref(size)):
                return buffer.value.split("\\")[-1]
        finally:
            kernel32.CloseHandle(process_handle)
        return None

    def _build_element(self, handle: int) -> AccessibilityElement | None:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, title_buffer, length + 1)

        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, class_buffer, 256)

        rect = ctypes.wintypes.RECT()
        has_rect = bool(user32.GetWindowRect(handle, ctypes.byref(rect)))
        bounds = (rect.left, rect.top, rect.right, rect.bottom) if has_rect else None

        enabled = bool(user32.IsWindowEnabled(handle))
        selected = bool(user32.SendMessageW(handle, 0x00F0, 0, 0))
        text = title_buffer.value or None

        return AccessibilityElement(
            element_id=f"win32:{handle}",
            name=text,
            role=class_buffer.value or None,
            value=text,
            state=AccessibilityElementState(
                text=text,
                enabled=enabled,
                selected=selected,
            ),
            bounds=bounds,
            children=[],
            source="raw_window",
            handle=handle,
        )


@dataclass(slots=True)
class AccessibilityTreeReader:
    accessibility_backend: AccessibilityBackend | None = None
    raw_window_backend: RawWindowBackend | None = None

    def __post_init__(self) -> None:
        if self.accessibility_backend is None:
            self.accessibility_backend = PyWinAutoAccessibilityBackend()
        if self.raw_window_backend is None:
            self.raw_window_backend = Win32RawWindowBackend()

    def read_active_application_tree(self) -> AccessibilityTree:
        tree = self.accessibility_backend.get_active_application_tree() if self.accessibility_backend else None
        if tree is not None and tree.root is not None:
            return tree
        return self._read_fallback_tree()

    def find_elements(
        self,
        *,
        name: str | None = None,
        role: str | None = None,
        value: str | None = None,
    ) -> AccessibilityQueryResult:
        tree = self.read_active_application_tree()
        used_fallback = tree.root.source == "raw_window" if tree.root is not None else False
        if tree.root is None:
            return AccessibilityQueryResult(matches=[], used_fallback=used_fallback)

        matches = [
            element
            for element in self._walk(tree.root)
            if self._matches(element, name=name, role=role, value=value)
        ]
        return AccessibilityQueryResult(matches=matches, used_fallback=used_fallback)

    def enumerate_children(self, element: AccessibilityElement) -> list[AccessibilityElement]:
        if element.children:
            return list(element.children)
        if element.handle is None or self.raw_window_backend is None:
            return []
        return self.raw_window_backend.inspect_children(element.handle)

    def get_element_text(self, element: AccessibilityElement) -> str | None:
        return element.state.text or element.value or element.name

    def is_element_enabled(self, element: AccessibilityElement) -> bool | None:
        return element.state.enabled

    def is_element_selected(self, element: AccessibilityElement) -> bool | None:
        return element.state.selected

    def _read_fallback_tree(self) -> AccessibilityTree:
        handle = self.raw_window_backend.get_active_window_handle() if self.raw_window_backend else None
        if handle is None or self.raw_window_backend is None:
            return AccessibilityTree(application_name=None, root=None)

        root = self.raw_window_backend.inspect_window(handle)
        if root is None:
            return AccessibilityTree(application_name=None, root=None)
        root.children = self.raw_window_backend.inspect_children(handle)
        return AccessibilityTree(
            application_name=self.raw_window_backend.get_application_name(handle),
            root=root,
        )

    def _walk(self, element: AccessibilityElement):
        yield element
        for child in element.children:
            yield from self._walk(child)

    def _matches(
        self,
        element: AccessibilityElement,
        *,
        name: str | None,
        role: str | None,
        value: str | None,
    ) -> bool:
        if name is not None and (element.name or "").casefold() != name.casefold():
            return False
        if role is not None and (element.role or "").casefold() != role.casefold():
            return False
        if value is not None and (element.value or "").casefold() != value.casefold():
            return False
        return True
