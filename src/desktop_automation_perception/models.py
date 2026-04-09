from __future__ import annotations

from desktop_automation_perception._time import utc_now

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PerceptionSource(str, Enum):
    ACCESSIBILITY = "accessibility"
    OCR = "ocr"
    TEMPLATE_MATCH = "template_match"
    AI_VISION = "ai_vision"


class LocatorStrategy(str, Enum):
    ACCESSIBILITY = "accessibility"
    OCR = "ocr"
    TEMPLATE_MATCH = "template_match"


class TargetKind(str, Enum):
    TEXT = "text"
    TEMPLATE = "template"
    ELEMENT_TYPE = "element_type"


class InputActionType(str, Enum):
    CLICK = "click"
    KEYPRESS = "keypress"
    TYPE_TEXT = "type_text"
    SCROLL = "scroll"
    HOTKEY = "hotkey"


@dataclass(slots=True)
class PerceptionArtifact:
    kind: str
    confidence: float
    payload: dict[str, Any] = field(default_factory=dict)
    bounds: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class PerceptionResult:
    source: PerceptionSource
    confidence: float
    artifacts: list[PerceptionArtifact] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    succeeded: bool = True
    error: str | None = None


@dataclass(slots=True)
class DesktopState:
    captured_at: datetime
    results: list[PerceptionResult]

    @classmethod
    def empty(cls) -> "DesktopState":
        return cls(captured_at=datetime.now(timezone.utc), results=[])

    def best_result(self) -> PerceptionResult | None:
        successful = [result for result in self.results if result.succeeded]
        if not successful:
            return None
        return max(successful, key=lambda result: result.confidence)

    def best_summary(self) -> dict[str, Any]:
        best = self.best_result()
        if best is None:
            return {
                "captured_at": self.captured_at.isoformat(),
                "source": None,
                "confidence": 0.0,
                "artifacts": [],
            }
        return {
            "captured_at": self.captured_at.isoformat(),
            "source": best.source.value,
            "confidence": best.confidence,
            "artifacts": [
                {
                    "kind": artifact.kind,
                    "confidence": artifact.confidence,
                    "bounds": artifact.bounds,
                    "payload": artifact.payload,
                }
                for artifact in best.artifacts
            ],
        }


@dataclass(slots=True)
class LocatorTarget:
    text: str | None = None
    template_name: str | None = None
    element_type: str | None = None
    monitor_id: str | None = None

    def requested_kinds(self) -> tuple[TargetKind, ...]:
        kinds: list[TargetKind] = []
        if self.text:
            kinds.append(TargetKind.TEXT)
        if self.template_name:
            kinds.append(TargetKind.TEMPLATE)
        if self.element_type:
            kinds.append(TargetKind.ELEMENT_TYPE)
        return tuple(kinds)


@dataclass(slots=True)
class LocatorCandidate:
    strategy: LocatorStrategy
    confidence: float
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]
    artifact: PerceptionArtifact
    monitor_id: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class LocatorResult:
    succeeded: bool
    confidence: float
    threshold: float
    strategy: LocatorStrategy | None = None
    bounds: tuple[int, int, int, int] | None = None
    center: tuple[int, int] | None = None
    best_candidate: LocatorCandidate | None = None
    monitor_id: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class ScreenBounds:
    width: int
    height: int
    dpi: int = 96
    origin_x: int = 0
    origin_y: int = 0

    def contains_bounds(self, bounds: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = bounds
        return (
            left >= self.origin_x
            and top >= self.origin_y
            and right <= self.origin_x + self.width
            and bottom <= self.origin_y + self.height
        )

    def contains_point(self, point: tuple[int, int]) -> bool:
        x, y = point
        return (
            x >= self.origin_x
            and y >= self.origin_y
            and x <= self.origin_x + self.width
            and y <= self.origin_y + self.height
        )


@dataclass(slots=True)
class WindowReference:
    title: str | None = None
    handle: int | None = None


@dataclass(slots=True)
class WindowState:
    reference: WindowReference
    focused: bool
    bounds: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class InputTarget:
    window: WindowReference | None = None
    element_bounds: tuple[int, int, int, int] | None = None
    monitor_id: str | None = None


@dataclass(slots=True)
class InputAction:
    action_type: InputActionType
    target: InputTarget | None = None
    position: tuple[int, int] | None = None
    button: str = "left"
    key: str | None = None
    text: str | None = None
    scroll_amount: int | None = None
    hotkey: tuple[str, ...] = ()
    monitor_id: str | None = None
    context_tags: tuple[str, ...] = ()


@dataclass(slots=True)
class ActionLogEntry:
    action: InputAction
    executed: bool
    delay_seconds: float
    reason: str | None = None


@dataclass(slots=True)
class PacingProfile:
    profile_id: str
    step_delay_range_seconds: tuple[float, float] = (0.1, 0.3)
    typing_key_delay_range_seconds: tuple[float, float] = (0.03, 0.12)
    typing_pause_probability: float = 0.15
    typing_pause_range_seconds: tuple[float, float] = (0.12, 0.35)
    pre_click_delay_range_seconds: tuple[float, float] = (0.04, 0.12)
    post_page_load_delay_range_seconds: tuple[float, float] = (1.0, 2.5)


@dataclass(slots=True)
class PacingDecision:
    profile_id: str
    delay_seconds: float
    reason: str


@dataclass(slots=True)
class PacingContext:
    action: InputAction | None = None
    account_name: str | None = None
    application_name: str | None = None


@dataclass(slots=True)
class PacingAssignment:
    assignment_type: str
    assignment_key: str
    profile_id: str


@dataclass(slots=True)
class InputSimulationResult:
    succeeded: bool
    logs: list[ActionLogEntry] = field(default_factory=list)
    failure_reason: str | None = None


@dataclass(slots=True)
class WindowContext:
    handle: int
    title: str
    process_name: str | None = None
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)
    focused: bool = False
    minimized: bool = False
    maximized: bool = False
    monitor_id: str | None = None


@dataclass(slots=True)
class MonitorDescriptor:
    monitor_id: str
    bounds: tuple[int, int, int, int]
    work_area: tuple[int, int, int, int] | None = None
    resolution: tuple[int, int] = (0, 0)
    primary: bool = False
    device_name: str | None = None


@dataclass(slots=True)
class DisplayConfigurationSnapshot:
    monitors: list[MonitorDescriptor] = field(default_factory=list)
    captured_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class DisplayConfigurationChangeResult:
    changed: bool
    baseline: DisplayConfigurationSnapshot | None = None
    current: DisplayConfigurationSnapshot | None = None
    reason: str | None = None


class WindowZoneType(str, Enum):
    TOOLBAR = "toolbar"
    CONTENT = "content"
    STATUS_BAR = "status_bar"
    SIDEBAR = "sidebar"
    FULL_WINDOW = "full_window"


@dataclass(slots=True)
class WindowZone:
    zone_type: WindowZoneType
    bounds: tuple[int, int, int, int]
    confidence: float = 1.0


@dataclass(slots=True)
class DynamicRegionOfInterest:
    window_handle: int | None
    bounds: tuple[int, int, int, int]
    zone_type: WindowZoneType
    window_bounds: tuple[int, int, int, int]
    confidence: float = 1.0
    detail: str | None = None


@dataclass(slots=True)
class DynamicRegionResult:
    succeeded: bool
    roi: DynamicRegionOfInterest | None = None
    zones: list[WindowZone] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class WindowOperationResult:
    succeeded: bool
    window: WindowContext | None = None
    reason: str | None = None


class ClipboardContentType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    EMPTY = "empty"


class ClipboardPasteMode(str, Enum):
    TYPE = "type"
    PASTE = "paste"


class ClipboardBridgeFormat(str, Enum):
    TEXT = "text"
    JSON = "json"
    FORMATTED_VALUES = "formatted_values"


class UITheme(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    HIGH_CONTRAST = "high_contrast"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ClipboardContent:
    content_type: ClipboardContentType
    text: str | None = None
    image_bytes: bytes | None = None
    encoding: str = "utf-8"


@dataclass(slots=True)
class ClipboardEvent:
    operation: str
    content_type: ClipboardContentType
    timestamp: datetime
    detail: str | None = None


@dataclass(slots=True)
class ClipboardOperationResult:
    succeeded: bool
    content: ClipboardContent | None = None
    reason: str | None = None


@dataclass(slots=True)
class ClipboardVerificationResult:
    succeeded: bool
    expected: str
    actual: str | None = None
    mode: ClipboardPasteMode = ClipboardPasteMode.PASTE
    reason: str | None = None


@dataclass(slots=True)
class ClipboardBridgeResult:
    succeeded: bool
    clipboard_written: ClipboardOperationResult | None = None
    window_result: WindowOperationResult | None = None
    verification: ClipboardVerificationResult | None = None
    rendered_text: str | None = None
    retry_count: int = 0
    conflict_detected: bool = False
    reason: str | None = None


@dataclass(slots=True)
class TemplateMatch:
    template_name: str
    confidence: float
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]


@dataclass(slots=True)
class TemplateSearchRequest:
    template_name: str
    template_path: str
    threshold: float = 0.8
    region_of_interest: tuple[int, int, int, int] | None = None
    scale_factor: float = 1.0
    theme: UITheme | None = None


@dataclass(slots=True)
class TemplateSearchResult:
    template_name: str
    matches: list[TemplateMatch] = field(default_factory=list)
    threshold: float = 0.8
    region_of_interest: tuple[int, int, int, int] | None = None
    scale_factor: float = 1.0


@dataclass(slots=True)
class ReferenceTemplateMetadata:
    name: str
    image_path: str
    metadata_path: str
    timestamp: datetime
    screen_resolution: tuple[int, int]
    application_name: str | None = None
    bounds: tuple[int, int, int, int] | None = None
    baseline_dpi: int = 96
    theme: UITheme = UITheme.UNKNOWN


@dataclass(slots=True)
class ThemeDetectionResult:
    theme: UITheme
    detected_with: str
    confidence: float = 1.0
    reason: str | None = None


@dataclass(slots=True)
class ThemeTemplateReferenceSet:
    theme: UITheme
    root_directory: str
    aliases: tuple[str, ...] = ()


@dataclass(slots=True)
class ResolutionReferenceElement:
    name: str
    expected_bounds: tuple[int, int, int, int]
    template_path: str
    threshold: float = 0.8
    region_of_interest: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class ResolutionCalibrationProfile:
    baseline_resolution: tuple[int, int]
    baseline_dpi: int = 96
    scale_tolerance: float = 0.15
    multi_scale_steps: tuple[float, ...] = (0.9, 1.0, 1.1)
    reference_elements: list[ResolutionReferenceElement] = field(default_factory=list)


@dataclass(slots=True)
class ResolutionVerificationReference:
    name: str
    expected_center: tuple[int, int]
    actual_center: tuple[int, int]
    scale_x: float
    scale_y: float
    confidence: float


@dataclass(slots=True)
class ResolutionVerificationResult:
    succeeded: bool
    references: list[ResolutionVerificationReference] = field(default_factory=list)
    current_resolution: tuple[int, int] | None = None
    current_dpi: int | None = None
    expected_scale_x: float = 1.0
    expected_scale_y: float = 1.0
    actual_scale_x: float = 1.0
    actual_scale_y: float = 1.0
    reason: str | None = None


@dataclass(slots=True)
class CoordinateAdaptationResult:
    succeeded: bool
    original_point: tuple[int, int] | None = None
    adapted_point: tuple[int, int] | None = None
    original_bounds: tuple[int, int, int, int] | None = None
    adapted_bounds: tuple[int, int, int, int] | None = None
    scale_x: float = 1.0
    scale_y: float = 1.0
    reason: str | None = None


@dataclass(slots=True)
class TemplateCaptureResult:
    succeeded: bool
    reference: ReferenceTemplateMetadata | None = None
    reason: str | None = None


@dataclass(slots=True)
class OCRTextBlock:
    text: str
    confidence: float
    bounds: tuple[int, int, int, int]


@dataclass(slots=True)
class OCRExtractionResult:
    blocks: list[OCRTextBlock] = field(default_factory=list)
    language: str = "eng"
    region_of_interest: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class OCRTextMatchResult:
    succeeded: bool
    target: str
    bounds: tuple[int, int, int, int] | None = None
    confidence: float = 0.0
    matched_text: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class AccessibilityElementState:
    text: str | None = None
    enabled: bool | None = None
    selected: bool | None = None


@dataclass(slots=True)
class AccessibilityElement:
    element_id: str
    name: str | None = None
    role: str | None = None
    value: str | None = None
    state: AccessibilityElementState = field(default_factory=AccessibilityElementState)
    bounds: tuple[int, int, int, int] | None = None
    children: list["AccessibilityElement"] = field(default_factory=list)
    source: str = "accessibility"
    handle: int | None = None


@dataclass(slots=True)
class AccessibilityTree:
    application_name: str | None
    root: AccessibilityElement | None


@dataclass(slots=True)
class AccessibilityQueryResult:
    matches: list[AccessibilityElement] = field(default_factory=list)
    used_fallback: bool = False


class ScreenCheckType(str, Enum):
    TEXT_PRESENT = "text_present"
    IMAGE_PRESENT = "image_present"
    ACTIVE_WINDOW = "active_window"
    ELEMENT_VALUE = "element_value"
    LOADING_ABSENT = "loading_absent"
    MODAL_ABSENT = "modal_absent"


@dataclass(slots=True)
class ScreenVerificationCheck:
    check_id: str
    check_type: ScreenCheckType
    timeout_seconds: float = 3.0
    polling_interval_seconds: float = 0.25
    target_text: str | None = None
    template_name: str | None = None
    template_path: str | None = None
    threshold: float = 0.8
    window_title: str | None = None
    process_name: str | None = None
    element_name: str | None = None
    element_role: str | None = None
    expected_value: str | None = None
    region_of_interest: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class ScreenVerificationCheckResult:
    check_id: str
    check_type: ScreenCheckType
    passed: bool
    detail: str | None = None


@dataclass(slots=True)
class ScreenVerificationResult:
    passed_checks: list[ScreenVerificationCheckResult] = field(default_factory=list)
    failed_checks: list[ScreenVerificationCheckResult] = field(default_factory=list)
    screenshot_path: str | None = None


@dataclass(slots=True)
class ScreenChangeEvent:
    difference_metric: float
    threshold: float
    screenshot_path: str | None = None
    region_of_interest: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class ScreenChangeResult:
    changed: bool
    event: ScreenChangeEvent | None = None
    reason: str | None = None


@dataclass(slots=True)
class UILandmark:
    name: str
    bounds: tuple[int, int, int, int] | None = None
    center: tuple[int, int] | None = None
    confidence: float = 1.0


@dataclass(slots=True)
class UIStateFingerprint:
    window_title_hash: str
    landmark_positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    pixel_histogram: tuple[float, ...] = ()
    screen_size: tuple[int, int] = (0, 0)
    window_count: int = 0
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AccountRecord:
    name: str
    credential_reference: str
    account_type: str
    application: str
    pacing_profile_id: str | None = None
    last_used_at: datetime | None = None
    active: bool = True
    health_score: float = 1.0


@dataclass(slots=True)
class AccountUsageEvent:
    account_name: str
    action: str
    timestamp: datetime
    detail: str | None = None


@dataclass(slots=True)
class AccountRegistrySnapshot:
    accounts: list[AccountRecord] = field(default_factory=list)
    usage_history: list[AccountUsageEvent] = field(default_factory=list)


@dataclass(slots=True)
class AccountRegistryResult:
    succeeded: bool
    account: AccountRecord | None = None
    reason: str | None = None


class CredentialKind(str, Enum):
    USERNAME = "username"
    PASSWORD = "password"
    TOKEN = "token"
    COOKIE = "cookie"


@dataclass(slots=True)
class SecureCredentialValue:
    buffer: bytearray = field(default_factory=bytearray, repr=False)
    encoding: str = "utf-8"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    zeroized: bool = False

    @classmethod
    def from_plaintext(cls, value: str, *, encoding: str = "utf-8") -> "SecureCredentialValue":
        return cls(buffer=bytearray(value.encode(encoding)), encoding=encoding)

    def reveal(self) -> str:
        if self.zeroized:
            raise ValueError("Credential buffer has been zeroized.")
        return bytes(self.buffer).decode(self.encoding)

    def is_available(self) -> bool:
        return not self.zeroized and len(self.buffer) > 0

    def zeroize(self) -> None:
        for index in range(len(self.buffer)):
            self.buffer[index] = 0
        self.buffer.clear()
        self.zeroized = True


@dataclass(slots=True)
class CredentialRecord:
    account_identifier: str
    kind: CredentialKind
    encrypted_value: str
    expires_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class CredentialAccessEvent:
    account_identifier: str
    kind: CredentialKind
    action: str
    timestamp: datetime
    detail: str | None = None


@dataclass(slots=True)
class CredentialVaultSnapshot:
    credentials: list[CredentialRecord] = field(default_factory=list)
    access_log: list[CredentialAccessEvent] = field(default_factory=list)


@dataclass(slots=True)
class CredentialVaultResult:
    succeeded: bool
    value: str | None = None
    expires_at: datetime | None = None
    reason: str | None = None


@dataclass(slots=True)
class VaultCredentialCacheEntry:
    secret_name: str
    credential: SecureCredentialValue
    expires_at: datetime | None = None
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: str | None = None


@dataclass(slots=True)
class VaultCredentialAccessEvent:
    secret_name: str
    action: str
    timestamp: datetime
    cache_hit: bool = False
    expires_at: datetime | None = None
    detail: str | None = None


@dataclass(slots=True)
class VaultCredentialResult:
    succeeded: bool
    secret_name: str | None = None
    credential: SecureCredentialValue | None = None
    expires_at: datetime | None = None
    cached: bool = False
    context_key: str | None = None
    access_event: VaultCredentialAccessEvent | None = None
    reason: str | None = None


@dataclass(slots=True)
class BrowserProfileRecord:
    account_name: str
    profile_directory: str
    browser_executable: str
    application: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_launched_at: datetime | None = None
    persistent_session: bool = False


@dataclass(slots=True)
class BrowserSessionRecord:
    account_name: str
    profile_directory: str
    launched_at: datetime
    browser_process_id: int | None = None
    active: bool = True


@dataclass(slots=True)
class BrowserProfileSnapshot:
    profiles: list[BrowserProfileRecord] = field(default_factory=list)
    sessions: list[BrowserSessionRecord] = field(default_factory=list)


@dataclass(slots=True)
class BrowserProfileResult:
    succeeded: bool
    profile: BrowserProfileRecord | None = None
    session: BrowserSessionRecord | None = None
    reason: str | None = None


class SessionState(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class SessionHealthEvent:
    state: SessionState
    timestamp: datetime
    detail: str | None = None
    screenshot_path: str | None = None


@dataclass(slots=True)
class SessionTrackerSnapshot:
    current_state: SessionState = SessionState.UNKNOWN
    health_log: list[SessionHealthEvent] = field(default_factory=list)


@dataclass(slots=True)
class SessionValidationResult:
    succeeded: bool
    state: SessionState
    reason: str | None = None
    screenshot_path: str | None = None


class AccountExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkerExecutionMode(str, Enum):
    THREAD = "thread"
    PROCESS = "process"


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    CRASHED = "crashed"
    RESTARTING = "restarting"
    STOPPED = "stopped"


class RateLimitScope(str, Enum):
    ACCOUNT = "account"
    APPLICATION = "application"
    ACTION_TYPE = "action_type"


class RateLimitWindow(str, Enum):
    MINUTE = "minute"
    HOUR = "hour"
    CUSTOM = "custom"


class ThrottlingEventType(str, Enum):
    SLOWED = "slowed"
    QUEUED = "queued"
    RESUMED = "resumed"


class WorkflowTriggerType(str, Enum):
    CRON = "cron"
    ONE_TIME = "one_time"
    EVENT = "event"


class MissedExecutionPolicy(str, Enum):
    RUN_IMMEDIATELY = "run_immediately"
    SKIP = "skip"


class WorkflowEventType(str, Enum):
    FILE_APPEARED = "file_appeared"
    QUEUE_DEPTH_THRESHOLD = "queue_depth_threshold"


@dataclass(slots=True)
class RotationTask:
    task_id: str
    required_account: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RotationExecutionEvent:
    task_id: str
    account_name: str
    timestamp: datetime
    status: str
    detail: str | None = None


@dataclass(slots=True)
class RotationExecutionSnapshot:
    events: list[RotationExecutionEvent] = field(default_factory=list)


@dataclass(slots=True)
class AccountRotationResult:
    succeeded: bool
    mode: AccountExecutionMode
    scheduled_batches: list[list[RotationTask]] = field(default_factory=list)
    executed_events: list[RotationExecutionEvent] = field(default_factory=list)
    skipped_tasks: list[RotationExecutionEvent] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class AutomationTask:
    task_id: str
    priority: TaskPriority
    required_module: str
    required_account: str | None = None
    required_account_type: str | None = None
    required_application: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    deadline: datetime | None = None
    max_retry_count: int = 0
    retry_count: int = 0
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class TaskQueueDepthMetric:
    timestamp: datetime
    depth: int
    threshold_exceeded: bool = False
    exceeded_thresholds: list[int] = field(default_factory=list)


@dataclass(slots=True)
class TaskQueueAlert:
    timestamp: datetime
    depth: int
    threshold: int
    message: str


@dataclass(slots=True)
class TaskQueueSnapshot:
    tasks: list[AutomationTask] = field(default_factory=list)
    depth_metrics: list[TaskQueueDepthMetric] = field(default_factory=list)
    alerts: list[TaskQueueAlert] = field(default_factory=list)
    last_dequeued_account: str | None = None


@dataclass(slots=True)
class TaskQueueOperationResult:
    succeeded: bool
    task: AutomationTask | None = None
    tasks: list[AutomationTask] = field(default_factory=list)
    metric: TaskQueueDepthMetric | None = None
    alert: TaskQueueAlert | None = None
    removed_tasks: list[AutomationTask] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class WorkerSessionContext:
    worker_id: str
    session_id: str
    execution_mode: WorkerExecutionMode
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkerAssignment:
    worker_id: str
    task_id: str
    account_name: str
    module_name: str
    assigned_at: datetime


@dataclass(slots=True)
class WorkerRecord:
    worker_id: str
    status: WorkerStatus
    session_context: WorkerSessionContext
    last_heartbeat_at: datetime | None = None
    current_task_id: str | None = None
    current_account: str | None = None
    restart_count: int = 0


@dataclass(slots=True)
class WorkerPoolSnapshot:
    workers: list[WorkerRecord] = field(default_factory=list)
    pending_tasks: list[AutomationTask] = field(default_factory=list)
    active_tasks: list[AutomationTask] = field(default_factory=list)
    active_assignments: list[WorkerAssignment] = field(default_factory=list)
    completed_task_ids: list[str] = field(default_factory=list)
    failed_task_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkerPoolOperationResult:
    succeeded: bool
    worker: WorkerRecord | None = None
    task: AutomationTask | None = None
    workers: list[WorkerRecord] = field(default_factory=list)
    tasks: list[AutomationTask] = field(default_factory=list)
    assignments: list[WorkerAssignment] = field(default_factory=list)
    snapshot: WorkerPoolSnapshot | None = None
    reason: str | None = None


class SandboxIsolationMode(str, Enum):
    OS_USER = "os_user"
    VIRTUAL_ENVIRONMENT = "virtual_environment"


class SandboxAccessType(str, Enum):
    NETWORK = "network"
    FILE_WRITE = "file_write"
    RESOURCE = "resource"


@dataclass(slots=True)
class SandboxIsolationPolicy:
    hostname_allowlist: tuple[str, ...] = ()
    writable_directories: tuple[str, ...] = ()
    restricted_resources: tuple[str, ...] = ()
    os_user_accounts: tuple[str, ...] = ()
    virtualenv_paths: tuple[str, ...] = ()
    default_isolation_mode: SandboxIsolationMode = SandboxIsolationMode.VIRTUAL_ENVIRONMENT


@dataclass(slots=True)
class SandboxWorkerLaunchSpec:
    worker_id: str
    session_id: str
    execution_mode: WorkerExecutionMode
    isolation_mode: SandboxIsolationMode
    os_user_account: str | None = None
    virtualenv_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SandboxAccessAttempt:
    worker_id: str
    session_id: str
    access_type: SandboxAccessType
    target: str
    timestamp: datetime
    allowed: bool
    detail: str | None = None


@dataclass(slots=True)
class SandboxViolationRecord:
    worker_id: str
    session_id: str
    access_type: SandboxAccessType
    target: str
    timestamp: datetime
    action_taken: str = "terminated"
    detail: str | None = None


@dataclass(slots=True)
class SandboxIsolationResult:
    succeeded: bool
    spec: SandboxWorkerLaunchSpec | None = None
    policy: SandboxIsolationPolicy | None = None
    runtime: object | None = None
    attempt: SandboxAccessAttempt | None = None
    violation: SandboxViolationRecord | None = None
    violations: list[SandboxViolationRecord] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class RateLimitRule:
    scope: RateLimitScope
    key: str
    limit: int
    window: RateLimitWindow = RateLimitWindow.MINUTE
    window_seconds: float | None = None
    slowdown_threshold_ratio: float = 0.8
    slowdown_delay_seconds: float = 1.0


@dataclass(slots=True)
class RateLimitRequest:
    request_id: str
    account_name: str | None = None
    application_name: str | None = None
    action_type: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class RateLimitExecutionRecord:
    request_id: str
    account_name: str | None = None
    application_name: str | None = None
    action_type: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class ThrottlingEvent:
    event_type: ThrottlingEventType
    request_id: str
    timestamp: datetime
    scope: RateLimitScope
    key: str
    detail: str | None = None
    delay_seconds: float = 0.0


@dataclass(slots=True)
class RateUsageMetric:
    scope: RateLimitScope
    key: str
    window_seconds: float
    limit: int
    used_count: int
    remaining_count: int
    approaching_limit: bool = False
    limit_reached: bool = False
    reset_at: datetime | None = None


@dataclass(slots=True)
class RateLimiterSnapshot:
    rules: list[RateLimitRule] = field(default_factory=list)
    execution_history: list[RateLimitExecutionRecord] = field(default_factory=list)
    queued_requests: list[RateLimitRequest] = field(default_factory=list)
    throttling_events: list[ThrottlingEvent] = field(default_factory=list)


@dataclass(slots=True)
class RateLimiterResult:
    succeeded: bool
    allowed: bool = False
    queued: bool = False
    request: RateLimitRequest | None = None
    requests: list[RateLimitRequest] = field(default_factory=list)
    metrics: list[RateUsageMetric] = field(default_factory=list)
    events: list[ThrottlingEvent] = field(default_factory=list)
    delay_seconds: float = 0.0
    reason: str | None = None


@dataclass(slots=True)
class WorkflowEventTrigger:
    event_type: WorkflowEventType
    file_path: str | None = None
    queue_name: str | None = None
    depth_threshold: int | None = None


@dataclass(slots=True)
class WorkflowSchedule:
    schedule_id: str
    workflow_id: str
    trigger_type: WorkflowTriggerType
    cron_expression: str | None = None
    run_at: datetime | None = None
    event_trigger: WorkflowEventTrigger | None = None
    missed_execution_policy: MissedExecutionPolicy = MissedExecutionPolicy.RUN_IMMEDIATELY
    active: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    last_checked_at: datetime | None = None
    last_triggered_at: datetime | None = None


@dataclass(slots=True)
class WorkflowSchedulerEvent:
    event_type: WorkflowEventType
    file_path: str | None = None
    queue_name: str | None = None
    queue_depth: int | None = None
    previous_queue_depth: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class WorkflowRunRecord:
    schedule_id: str
    workflow_id: str
    trigger_type: WorkflowTriggerType
    trigger_detail: str
    triggered_at: datetime
    workflow_version_number: int | None = None
    scheduled_for: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowSchedulerSnapshot:
    schedules: list[WorkflowSchedule] = field(default_factory=list)
    run_history: list[WorkflowRunRecord] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowSchedulerResult:
    succeeded: bool
    schedule: WorkflowSchedule | None = None
    schedules: list[WorkflowSchedule] = field(default_factory=list)
    run: WorkflowRunRecord | None = None
    runs: list[WorkflowRunRecord] = field(default_factory=list)
    reason: str | None = None


class CICDReportMode(str, Enum):
    API = "api"
    OUTPUT_FILE = "output_file"
    BOTH = "both"


@dataclass(slots=True)
class CICDWorkflowSpecification:
    workflow_id: str
    workflow_name: str
    steps: list["WorkflowStep"] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CICDTriggerPayload:
    build_id: str
    specification: CICDWorkflowSpecification
    report_mode: CICDReportMode = CICDReportMode.API
    callback_endpoint: str | None = None
    output_path: str | None = None
    pipeline_parameters: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CICDStepOutcome:
    step_id: str
    application_name: str
    status: str
    reason: str | None = None


@dataclass(slots=True)
class CICDRunResult:
    succeeded: bool
    build_id: str
    workflow_id: str
    workflow_name: str
    status: str
    skipped_steps: list[str] = field(default_factory=list)
    step_outcomes: list[CICDStepOutcome] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    report_payload: dict[str, Any] = field(default_factory=dict)
    callback_result: "RESTAPIExecutorResult" | None = None
    output_path: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class AccountLoadMetric:
    account_name: str
    account_type: str
    application_name: str
    healthy: bool
    active: bool
    current_load: int
    capacity: int
    available_capacity: int
    load_ratio: float
    rate_limit_used: int = 0
    rate_limit_limit: int = 0
    rate_limit_utilization: float = 0.0
    assigned_worker_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LoadBalancerDecisionRecord:
    task_id: str
    selected_account: str | None = None
    selected_worker_id: str | None = None
    queued: bool = False
    reason: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class LoadBalancerSnapshot:
    queued_tasks: list[AutomationTask] = field(default_factory=list)
    decision_history: list[LoadBalancerDecisionRecord] = field(default_factory=list)


@dataclass(slots=True)
class LoadBalancerResult:
    succeeded: bool
    task: AutomationTask | None = None
    tasks: list[AutomationTask] = field(default_factory=list)
    metrics: list[AccountLoadMetric] = field(default_factory=list)
    decision: LoadBalancerDecisionRecord | None = None
    decisions: list[LoadBalancerDecisionRecord] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class PromptTemplateVersion:
    version: int
    body: str
    timestamp: datetime


@dataclass(slots=True)
class PromptTemplateRecord:
    name: str
    description: str
    body: str
    target_context: str
    current_version: int = 1
    version_history: list[PromptTemplateVersion] = field(default_factory=list)


@dataclass(slots=True)
class PromptTemplateSnapshot:
    templates: list[PromptTemplateRecord] = field(default_factory=list)


@dataclass(slots=True)
class PromptTemplateResult:
    succeeded: bool
    template: PromptTemplateRecord | None = None
    rendered_prompt: str | None = None
    reason: str | None = None


class PromptInjectionMethod(str, Enum):
    TYPE = "type"
    CLIPBOARD = "clipboard"
    PLATFORM_API = "platform_api"


class PromptReadbackMethod(str, Enum):
    ACCESSIBILITY = "accessibility"
    OCR = "ocr"


class LineEndingStyle(str, Enum):
    AUTO = "auto"
    LF = "lf"
    CRLF = "crlf"


@dataclass(slots=True)
class PromptInjectionTarget:
    window_title: str | None = None
    process_name: str | None = None
    element_name: str | None = None
    element_role: str | None = None
    element_bounds: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class PromptFieldContext:
    bounds: tuple[int, int, int, int] | None = None
    center: tuple[int, int] | None = None
    element: AccessibilityElement | None = None
    window: WindowReference | None = None
    used_fallback: bool = False


@dataclass(slots=True)
class PromptInjectionResult:
    succeeded: bool
    method: PromptInjectionMethod
    target: PromptInjectionTarget
    field_context: PromptFieldContext | None = None
    normalized_prompt: str | None = None
    expected_text: str | None = None
    actual_text: str | None = None
    verification_method: PromptReadbackMethod | None = None
    reason: str | None = None


class SelectorStrategy(str, Enum):
    ACCESSIBILITY = "accessibility"
    OCR = "ocr"
    TEMPLATE_MATCH = "template_match"
    DIRECT_BOUNDS = "direct_bounds"


class AIInterfaceSubmitMode(str, Enum):
    AUTO = "auto"
    ENTER = "enter"
    BUTTON = "button"


class AIInterfaceStatus(str, Enum):
    READY = "ready"
    SUBMITTED = "submitted"
    STREAMING = "streaming"
    COMPLETED = "completed"
    ERROR = "error"
    SESSION_TIMEOUT = "session_timeout"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class AIInterfaceElementSelector:
    name: str | None = None
    role: str | None = None
    value: str | None = None
    target_text: str | None = None
    template_name: str | None = None
    template_path: str | None = None
    bounds: tuple[int, int, int, int] | None = None
    region_of_interest: tuple[int, int, int, int] | None = None
    window_title: str | None = None
    process_name: str | None = None
    strategies: tuple[SelectorStrategy, ...] = (
        SelectorStrategy.ACCESSIBILITY,
        SelectorStrategy.OCR,
        SelectorStrategy.TEMPLATE_MATCH,
    )
    threshold: float = 0.8
    required: bool = True


@dataclass(slots=True)
class AIInterfaceConfiguration:
    interface_name: str
    input_selector: AIInterfaceElementSelector
    submit_mode: AIInterfaceSubmitMode = AIInterfaceSubmitMode.AUTO
    submit_button_selector: AIInterfaceElementSelector | None = None
    response_selector: AIInterfaceElementSelector | None = None
    streaming_indicator_selectors: list[AIInterfaceElementSelector] = field(default_factory=list)
    loading_state_selectors: list[AIInterfaceElementSelector] = field(default_factory=list)
    error_dialog_selectors: list[AIInterfaceElementSelector] = field(default_factory=list)
    session_timeout_selectors: list[AIInterfaceElementSelector] = field(default_factory=list)
    submit_settle_seconds: float = 0.35
    response_timeout_seconds: float = 60.0
    polling_interval_seconds: float = 0.5
    stable_polls_required: int = 2
    ocr_language: str = "eng"
    minimum_ocr_confidence: float = 0.0


@dataclass(slots=True)
class AIInterfaceElementMatch:
    selector: AIInterfaceElementSelector
    strategy: SelectorStrategy
    bounds: tuple[int, int, int, int] | None = None
    center: tuple[int, int] | None = None
    text: str | None = None
    confidence: float = 0.0
    element: AccessibilityElement | None = None
    detail: str | None = None


@dataclass(slots=True)
class AIInterfaceObservation:
    status: AIInterfaceStatus
    detail: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AIInterfaceNavigationResult:
    succeeded: bool
    interface_name: str
    status: AIInterfaceStatus
    prompt: str
    response_text: str | None = None
    input_match: AIInterfaceElementMatch | None = None
    submit_match: AIInterfaceElementMatch | None = None
    response_match: AIInterfaceElementMatch | None = None
    observations: list[AIInterfaceObservation] = field(default_factory=list)
    reason: str | None = None


class PipelineResponseAction(str, Enum):
    STORE_AS = "store_as"
    APPEND_TO_VARIABLE = "append_to_variable"
    REPLACE_VARIABLES = "replace_variables"
    NOOP = "noop"


class PipelinePauseDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PipelineStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass(slots=True)
class PromptPipelineStep:
    step_id: str
    interface: AIInterfaceConfiguration
    template_name: str
    template_variables: dict[str, str] = field(default_factory=dict)
    expected_response_pattern: str | None = None
    response_action: PipelineResponseAction = PipelineResponseAction.STORE_AS
    output_variable_name: str = "previous_response"
    action_target_variable: str | None = None
    allow_human_review: bool = False
    injection_method: PromptInjectionMethod = PromptInjectionMethod.CLIPBOARD


@dataclass(slots=True)
class PipelineReviewRequest:
    step_id: str
    prompt: str
    rendered_variables: dict[str, str] = field(default_factory=dict)
    response_text: str | None = None


@dataclass(slots=True)
class PipelineReviewResult:
    decision: PipelinePauseDecision
    reason: str | None = None


@dataclass(slots=True)
class PromptPipelineStepLog:
    step_id: str
    interface_name: str
    template_name: str
    prompt: str
    response: str | None = None
    execution_time_seconds: float = 0.0
    succeeded: bool = False
    matched_expected_pattern: bool | None = None
    review_requested: bool = False
    review_decision: PipelinePauseDecision | None = None
    reason: str | None = None


@dataclass(slots=True)
class PromptPipelineResult:
    succeeded: bool
    status: PipelineStatus
    logs: list[PromptPipelineStepLog] = field(default_factory=list)
    final_variables: dict[str, str] = field(default_factory=dict)
    failed_step_id: str | None = None
    reason: str | None = None


class ResponseProcessingMode(str, Enum):
    RAW = "raw"
    STRIP_FORMATTING = "strip_formatting"
    EXTRACT_JSON_BLOCK = "extract_json_block"
    SPLIT_SECTIONS = "split_sections"


class ResponseValidationMode(str, Enum):
    NONE = "none"
    REGEX = "regex"
    JSON = "json"
    JSON_SCHEMA_LITE = "json_schema_lite"


@dataclass(slots=True)
class ResponseExtractionConfiguration:
    interface_name: str
    response_selector: AIInterfaceElementSelector
    ocr_language: str = "eng"
    minimum_ocr_confidence: float = 0.0
    processing_modes: tuple[ResponseProcessingMode, ...] = (ResponseProcessingMode.RAW,)
    section_delimiters: tuple[str, ...] = ()
    validation_mode: ResponseValidationMode = ResponseValidationMode.NONE
    expected_pattern: str | None = None
    expected_schema: dict[str, str] = field(default_factory=dict)
    retry_on_validation_failure: bool = False
    max_retry_attempts: int = 0
    retry_instruction_suffix: str = (
        " Please regenerate the response strictly in the requested format."
    )


@dataclass(slots=True)
class ResponseValidationResult:
    succeeded: bool
    mode: ResponseValidationMode
    reason: str | None = None
    parsed_payload: object | None = None


@dataclass(slots=True)
class ResponseExtractionAttempt:
    attempt_number: int
    prompt: str | None = None
    raw_response: str | None = None
    processed_response: str | None = None
    sections: list[str] = field(default_factory=list)
    validation: ResponseValidationResult | None = None
    retried: bool = False
    reason: str | None = None


@dataclass(slots=True)
class ResponseExtractionResult:
    succeeded: bool
    interface_name: str
    raw_response: str | None = None
    processed_response: str | None = None
    sections: list[str] = field(default_factory=list)
    parsed_payload: object | None = None
    validation: ResponseValidationResult | None = None
    attempts: list[ResponseExtractionAttempt] = field(default_factory=list)
    reason: str | None = None


class ApplicationLaunchMode(str, Enum):
    EXECUTABLE = "executable"
    START_MENU = "start_menu"
    URL = "url"


class ApplicationLaunchStatus(str, Enum):
    STARTED = "started"
    FAILED = "failed"
    ESCALATED = "escalated"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class ApplicationStartupSignature:
    window_title: str | None = None
    process_name: str | None = None
    element_selector: AIInterfaceElementSelector | None = None


@dataclass(slots=True)
class ApplicationLaunchRequest:
    application_name: str
    launch_mode: ApplicationLaunchMode
    executable_path: str | None = None
    start_menu_query: str | None = None
    url: str | None = None
    arguments: tuple[str, ...] = ()
    url_parameters: dict[str, str] = field(default_factory=dict)
    startup_signature: ApplicationStartupSignature | None = None
    timeout_seconds: float = 15.0
    retry_attempts: int = 1
    retry_delay_seconds: float = 1.0
    escalate_on_failure: bool = True


@dataclass(slots=True)
class KnownApplicationRecord:
    name: str
    launch_mode: ApplicationLaunchMode
    executable_path: str | None = None
    start_menu_query: str | None = None
    url: str | None = None
    default_arguments: tuple[str, ...] = ()
    default_url_parameters: dict[str, str] = field(default_factory=dict)
    startup_signature: ApplicationStartupSignature | None = None
    pacing_profile_id: str | None = None


@dataclass(slots=True)
class ApplicationRegistrySnapshot:
    applications: list[KnownApplicationRecord] = field(default_factory=list)


@dataclass(slots=True)
class ApplicationLaunchAttempt:
    attempt_number: int
    command: tuple[str, ...] = ()
    launched: bool = False
    verified: bool = False
    reason: str | None = None


@dataclass(slots=True)
class ApplicationLaunchResult:
    succeeded: bool
    status: ApplicationLaunchStatus
    application: KnownApplicationRecord | None = None
    attempts: list[ApplicationLaunchAttempt] = field(default_factory=list)
    launched_command: tuple[str, ...] = ()
    reason: str | None = None


class NavigationStepActionType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    NAVIGATE = "navigate"
    SCROLL = "scroll"
    WAIT = "wait"
    VERIFY = "verify"


class NavigationSequenceMode(str, Enum):
    STRICT = "strict"
    LENIENT = "lenient"


@dataclass(slots=True)
class NavigationStep:
    step_id: str
    action_type: NavigationStepActionType
    target_description: str
    input_data: dict[str, Any] = field(default_factory=dict)
    preconditions: list[ScreenVerificationCheck] = field(default_factory=list)
    expected_post_action_state: list[ScreenVerificationCheck] = field(default_factory=list)
    timeout_seconds: float = 5.0
    optional: bool = False


@dataclass(slots=True)
class NavigationStepOutcome:
    step_id: str
    action_type: NavigationStepActionType
    succeeded: bool
    skipped: bool = False
    replayable: bool = True
    execution_time_seconds: float = 0.0
    reason: str | None = None
    action_result: Any | None = None
    precondition_result: ScreenVerificationResult | None = None
    postcondition_result: ScreenVerificationResult | None = None


@dataclass(slots=True)
class NavigationSequenceResult:
    succeeded: bool
    mode: NavigationSequenceMode
    outcomes: list[NavigationStepOutcome] = field(default_factory=list)
    failed_step_id: str | None = None
    reason: str | None = None


class DialogResponse(str, Enum):
    ACCEPT = "accept"
    CANCEL = "cancel"
    CUSTOM = "custom"
    DISMISS = "dismiss"


class DialogClassification(str, Enum):
    CONFIRMATION = "confirmation"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class MenuNavigationRequest:
    menu_label: str
    window_title: str | None = None
    process_name: str | None = None
    menu_selector: AIInterfaceElementSelector | None = None
    custom_menu_selector: AIInterfaceElementSelector | None = None
    timeout_seconds: float = 5.0


@dataclass(slots=True)
class DialogHandlingRequest:
    dialog_selector: AIInterfaceElementSelector
    response: DialogResponse = DialogResponse.ACCEPT
    custom_response_selector: AIInterfaceElementSelector | None = None
    timeout_seconds: float = 5.0


@dataclass(slots=True)
class UnexpectedDialogPolicy:
    default_response: DialogResponse = DialogResponse.CANCEL
    response_by_classification: dict[DialogClassification, DialogResponse] = field(default_factory=dict)
    custom_response_selector: AIInterfaceElementSelector | None = None


@dataclass(slots=True)
class InteractionStateSnapshot:
    screenshot_path: str | None = None
    active_window_title: str | None = None
    dialog_text: str | None = None


@dataclass(slots=True)
class MenuDialogInteractionLog:
    interaction_type: str
    target_label: str
    succeeded: bool
    classification: DialogClassification | None = None
    response: DialogResponse | None = None
    reason: str | None = None
    before_state: InteractionStateSnapshot | None = None
    after_state: InteractionStateSnapshot | None = None


@dataclass(slots=True)
class MenuDialogNavigationResult:
    succeeded: bool
    logs: list[MenuDialogInteractionLog] = field(default_factory=list)
    reason: str | None = None


class FormFieldType(str, Enum):
    TEXT = "text"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    DATE = "date"


@dataclass(slots=True)
class FormFieldValue:
    label: str
    value: str | bool
    field_type: FormFieldType | None = None
    placeholder: str | None = None
    accessibility_name: str | None = None
    field_selector: AIInterfaceElementSelector | None = None
    option_selector: AIInterfaceElementSelector | None = None
    optional: bool = False


@dataclass(slots=True)
class FormFieldContext:
    field: AccessibilityElement | None = None
    label_element: AccessibilityElement | None = None
    bounds: tuple[int, int, int, int] | None = None
    center: tuple[int, int] | None = None
    field_type: FormFieldType | None = None
    selector_used: str | None = None


@dataclass(slots=True)
class FormFieldResult:
    label: str
    succeeded: bool
    field_type: FormFieldType | None = None
    expected_value: str | bool | None = None
    actual_value: str | bool | None = None
    context: FormFieldContext | None = None
    reason: str | None = None


@dataclass(slots=True)
class FormAutomationResult:
    succeeded: bool
    field_results: list[FormFieldResult] = field(default_factory=list)
    reason: str | None = None


class WorkflowDataExchangeMode(str, Enum):
    CLIPBOARD = "clipboard"
    FILE = "file"


@dataclass(slots=True)
class WorkflowContext:
    current_application: str | None = None
    step_number: int = 0
    shared_data: dict[str, str] = field(default_factory=dict)
    secure_data: dict[str, SecureCredentialValue] = field(default_factory=dict)
    active_applications: list[str] = field(default_factory=list)
    application_signatures: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowExchangeRequest:
    mode: WorkflowDataExchangeMode
    data_key: str
    value: str | None = None
    file_path: str | None = None


@dataclass(slots=True)
class WorkflowStep:
    step_id: str
    application_name: str
    launch_request: ApplicationLaunchRequest | None = None
    required_window_title: str | None = None
    required_process_name: str | None = None
    focus_required: bool = True
    incoming_exchange: WorkflowExchangeRequest | None = None
    outgoing_exchange: WorkflowExchangeRequest | None = None
    optional: bool = False


@dataclass(slots=True)
class WorkflowStepResult:
    step_id: str
    application_name: str
    succeeded: bool
    dry_run: bool = False
    context_snapshot: WorkflowContext | None = None
    reason: str | None = None


@dataclass(slots=True)
class WorkflowCoordinatorResult:
    succeeded: bool
    context: WorkflowContext
    step_results: list[WorkflowStepResult] = field(default_factory=list)
    reason: str | None = None


class WorkflowGraphNodeType(str, Enum):
    STEP = "step"
    MERGE = "merge"


class WorkflowGraphEdgeType(str, Enum):
    SEQUENTIAL = "sequential"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"
    RETRY = "retry"


class WorkflowGraphConditionOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"
    TRUTHY = "truthy"
    FALSY = "falsy"
    CONTAINS = "contains"
    IN = "in"


@dataclass(slots=True)
class WorkflowGraphCondition:
    output_key: str | None = None
    operator: WorkflowGraphConditionOperator = WorkflowGraphConditionOperator.EQUALS
    expected_value: Any = None


@dataclass(slots=True)
class WorkflowGraphNode:
    node_id: str
    step_name: str
    node_type: WorkflowGraphNodeType = WorkflowGraphNodeType.STEP
    application_name: str | None = None
    step_payload: dict[str, Any] = field(default_factory=dict)
    wait_for_all_predecessors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowGraphEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: WorkflowGraphEdgeType = WorkflowGraphEdgeType.SEQUENTIAL
    condition: WorkflowGraphCondition | None = None
    loop_id: str | None = None
    max_iterations: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowGraphDefinition:
    workflow_id: str
    version: int = 1
    entry_node_ids: tuple[str, ...] = ()
    nodes: list[WorkflowGraphNode] = field(default_factory=list)
    edges: list[WorkflowGraphEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowGraphState:
    ready_node_ids: tuple[str, ...] = ()
    completed_node_ids: tuple[str, ...] = ()
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    execution_counts: dict[str, int] = field(default_factory=dict)
    loop_iterations: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowGraphPlanResult:
    succeeded: bool
    ready_nodes: list[WorkflowGraphNode] = field(default_factory=list)
    state: WorkflowGraphState | None = None
    reason: str | None = None


class WorkflowVersionTag(str, Enum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"


@dataclass(slots=True)
class WorkflowDefinitionVersion:
    workflow_id: str
    version_number: int
    author: str
    timestamp: datetime
    change_description: str
    workflow_graph: WorkflowGraphDefinition
    tag: WorkflowVersionTag = WorkflowVersionTag.EXPERIMENTAL


@dataclass(slots=True)
class WorkflowVersionDiff:
    workflow_id: str
    from_version_number: int
    to_version_number: int
    added_node_ids: list[str] = field(default_factory=list)
    removed_node_ids: list[str] = field(default_factory=list)
    changed_node_ids: list[str] = field(default_factory=list)
    added_edge_ids: list[str] = field(default_factory=list)
    removed_edge_ids: list[str] = field(default_factory=list)
    changed_edge_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowVersionSnapshot:
    workflow_id: str
    active_version_number: int | None = None
    versions: list[WorkflowDefinitionVersion] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowVersionControlResult:
    succeeded: bool
    version: WorkflowDefinitionVersion | None = None
    versions: list[WorkflowDefinitionVersion] = field(default_factory=list)
    snapshot: WorkflowVersionSnapshot | None = None
    diff: WorkflowVersionDiff | None = None
    reason: str | None = None


@dataclass(slots=True)
class WorkflowTemplateParameter:
    name: str
    description: str
    default_value: Any = None
    required: bool = False


@dataclass(slots=True)
class WorkflowTemplateVersion:
    version_number: int
    author: str
    timestamp: datetime
    change_description: str
    workflow_graph: WorkflowGraphDefinition
    parameters: list[WorkflowTemplateParameter] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowTemplateDocument:
    template_id: str
    name: str
    description: str
    application: str | None = None
    task_type: str | None = None
    keywords: list[str] = field(default_factory=list)
    current_version_number: int = 1
    versions: list[WorkflowTemplateVersion] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class WorkflowTemplateSearchMatch:
    template_id: str
    score: float
    template: WorkflowTemplateDocument


@dataclass(slots=True)
class WorkflowTemplateCompositionComponent:
    template_id: str
    version_number: int | None = None
    parameter_values: dict[str, Any] = field(default_factory=dict)
    node_prefix: str | None = None


@dataclass(slots=True)
class WorkflowTemplateLibrarySnapshot:
    templates: list[WorkflowTemplateDocument] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowTemplateLibraryResult:
    succeeded: bool
    template: WorkflowTemplateDocument | None = None
    templates: list[WorkflowTemplateDocument] = field(default_factory=list)
    matches: list[WorkflowTemplateSearchMatch] = field(default_factory=list)
    workflow_graph: WorkflowGraphDefinition | None = None
    reason: str | None = None


class BranchValueSource(str, Enum):
    STEP_OUTPUT = "step_output"
    SCREEN_OBSERVATION = "screen_observation"
    WORKFLOW_DATA = "workflow_data"


class BranchConditionType(str, Enum):
    STRING_MATCH = "string_match"
    NUMERIC_COMPARISON = "numeric_comparison"
    ELEMENT_PRESENCE = "element_presence"
    CUSTOM_PREDICATE = "custom_predicate"


class BranchComparisonOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"


@dataclass(slots=True)
class BranchConditionSpecification:
    condition_id: str
    condition_type: BranchConditionType
    source: BranchValueSource = BranchValueSource.STEP_OUTPUT
    field_path: str | None = None
    operator: BranchComparisonOperator = BranchComparisonOperator.EQUALS
    expected_value: Any = None
    predicate_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BranchOption:
    branch_id: str
    next_step_id: str
    condition: BranchConditionSpecification | None = None
    default: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BranchEvaluationContext:
    step_output: dict[str, Any] = field(default_factory=dict)
    screen_observations: dict[str, Any] = field(default_factory=dict)
    workflow_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BranchEvaluationRecord:
    condition_id: str | None
    condition_type: BranchConditionType | None
    source: BranchValueSource | None
    field_path: str | None
    actual_value: Any = None
    expected_value: Any = None
    matched: bool = False
    selected_branch_id: str | None = None
    selected_next_step_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    detail: str | None = None


@dataclass(slots=True)
class BranchEvaluationResult:
    succeeded: bool
    selected_branch: BranchOption | None = None
    records: list[BranchEvaluationRecord] = field(default_factory=list)
    reason: str | None = None


class AntiLoopTriggerType(str, Enum):
    STEP_EXECUTION_LIMIT = "step_execution_limit"
    PIPELINE_TIMEOUT = "pipeline_timeout"


@dataclass(slots=True)
class AntiLoopStepExecution:
    step_id: str
    timestamp: datetime
    execution_count: int
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AntiLoopEventRecord:
    workflow_id: str
    trigger_type: AntiLoopTriggerType
    step_id: str | None = None
    detail: str | None = None
    max_step_execution_count: int = 0
    max_pipeline_duration_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    step_execution_count: int = 0
    step_history: list[AntiLoopStepExecution] = field(default_factory=list)
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AntiLoopDetectionResult:
    succeeded: bool
    triggered: bool = False
    record: AntiLoopEventRecord | None = None
    escalation_result: object | None = None
    reason: str | None = None


class AllowlistScope(str, Enum):
    ACTION_TYPE = "action_type"
    APPLICATION = "application"
    URL = "url"
    FILE_PATH = "file_path"


@dataclass(slots=True)
class AllowlistRuleSet:
    action_types: tuple[str, ...] = ()
    applications: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()
    loaded_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AllowlistCheckRequest:
    action_type: str
    workflow_id: str | None = None
    step_name: str | None = None
    application_name: str | None = None
    url: str | None = None
    file_path: str | None = None
    context_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AllowlistCheckResult:
    succeeded: bool
    request: AllowlistCheckRequest
    allowed: bool
    rules: AllowlistRuleSet | None = None
    violated_scopes: list[AllowlistScope] = field(default_factory=list)
    escalation_result: object | None = None
    reason: str | None = None


@dataclass(slots=True)
class SensitiveAccessEvent:
    location: str
    action: str
    timestamp: datetime
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SensitiveProtectionResult:
    succeeded: bool
    text: str | None = None
    file_path: str | None = None
    violations: list[str] = field(default_factory=list)
    events: list[SensitiveAccessEvent] = field(default_factory=list)
    reason: str | None = None


class FailSafeTriggerType(str, Enum):
    MOUSE_CORNER = "mouse_corner"
    HOTKEY = "hotkey"
    MANUAL = "manual"


@dataclass(slots=True)
class FailSafeResourceReleaseResult:
    resource_name: str
    succeeded: bool
    detail: str | None = None


@dataclass(slots=True)
class FailSafeActivationRecord:
    workflow_id: str
    trigger_type: FailSafeTriggerType
    detail: str | None = None
    screenshot_path: str | None = None
    checkpoint_saved: bool = False
    checkpoint_storage_path: str | None = None
    cancelled_task_ids: list[str] = field(default_factory=list)
    released_resources: list[FailSafeResourceReleaseResult] = field(default_factory=list)
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class FailSafeActivationResult:
    succeeded: bool
    activated: bool = False
    record: FailSafeActivationRecord | None = None
    checkpoint: WorkflowCheckpoint | None = None
    released_resources: list[FailSafeResourceReleaseResult] = field(default_factory=list)
    reason: str | None = None


class RetryDisposition(str, Enum):
    RETRY = "retry"
    FAIL = "fail"


@dataclass(slots=True)
class RetryExceptionRule:
    exception_type_name: str
    disposition: RetryDisposition
    message_contains: str | None = None


@dataclass(slots=True)
class RetryConfiguration:
    max_retry_count: int = 3
    initial_delay_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0
    exception_rules: list[RetryExceptionRule] = field(default_factory=list)
    default_disposition: RetryDisposition = RetryDisposition.RETRY


@dataclass(slots=True)
class RetryAttemptLog:
    attempt_number: int
    delay_seconds: float = 0.0
    exception_type: str | None = None
    exception_message: str | None = None
    disposition: RetryDisposition | None = None


@dataclass(slots=True)
class RetryFailureResult:
    succeeded: bool = False
    attempts: list[RetryAttemptLog] = field(default_factory=list)
    final_exception_type: str | None = None
    final_exception_message: str | None = None
    reason: str | None = None


class CheckpointDecision(str, Enum):
    RESUME = "resume"
    RESTART = "restart"


class CheckpointResumePolicy(str, Enum):
    AUTO_RESUME = "auto_resume"
    AUTO_RESTART = "auto_restart"
    CALLBACK = "callback"


@dataclass(slots=True)
class WorkflowCheckpoint:
    workflow_id: str
    saved_at: datetime
    step_index: int
    workflow_context: WorkflowContext
    account_context: dict[str, str] = field(default_factory=dict)
    collected_data: dict[str, str] = field(default_factory=dict)
    step_outcomes: list[WorkflowStepResult] = field(default_factory=list)
    ui_state_fingerprint: UIStateFingerprint | None = None


@dataclass(slots=True)
class CheckpointRestoreResult:
    succeeded: bool
    decision: CheckpointDecision
    checkpoint: WorkflowCheckpoint | None = None
    reason: str | None = None


class ValidationPhase(str, Enum):
    PRE_CONDITION = "pre_condition"
    POST_CONDITION = "post_condition"


class ValidationDisposition(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    RETRY = "retry"
    ESCALATE = "escalate"


class ApprovalRiskLevel(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class ApprovalTimeoutPolicy(str, Enum):
    REJECT = "reject"
    ESCALATE = "escalate"
    PROCEED_WITH_CAUTION = "proceed_with_caution"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    PROCEED_WITH_CAUTION = "proceed_with_caution"


@dataclass(slots=True)
class ConditionDescription:
    condition_id: str
    description: str
    checks: list[ScreenVerificationCheck] = field(default_factory=list)


@dataclass(slots=True)
class ConditionValidationResult:
    succeeded: bool
    phase: ValidationPhase
    disposition: ValidationDisposition
    condition: ConditionDescription
    verification: ScreenVerificationResult | None = None
    retry_failure: RetryFailureResult | None = None
    reason: str | None = None


@dataclass(slots=True)
class ApprovalGateAction:
    workflow_id: str
    step_id: str
    action_type: str
    description: str
    application_name: str | None = None
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.REVERSIBLE
    blast_radius: int = 1
    context_data: dict[str, Any] = field(default_factory=dict)
    expected_consequences: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApprovalRequest:
    request_id: str
    action: ApprovalGateAction
    reviewer_channel: str
    created_at: datetime
    expires_at: datetime
    proposed_effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApprovalResponse:
    request_id: str
    decision: ApprovalDecision
    reviewer_id: str | None = None
    responded_at: datetime = field(default_factory=utc_now)
    reason: str | None = None
    modified_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ApprovalGateResult:
    succeeded: bool
    action: ApprovalGateAction
    triggered_gate: bool
    request: ApprovalRequest | None = None
    response: ApprovalResponse | None = None
    notification_result: NotificationDispatcherResult | None = None
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.REVERSIBLE
    timed_out: bool = False
    reason: str | None = None


class ConfidenceRoutingDisposition(str, Enum):
    AUTO_PROCEED = "auto_proceed"
    ROUTE_TO_APPROVAL = "route_to_approval"


@dataclass(slots=True)
class ConfidenceThresholdRule:
    action_type: str
    minimum_confidence: float
    observed_error_rate: float = 0.0


@dataclass(slots=True)
class ConfidenceRoutingDecision:
    action_type: str
    confidence_score: float
    threshold_used: float
    disposition: ConfidenceRoutingDisposition
    routed_to_approval: bool
    observed_error_rate: float = 0.0
    timestamp: datetime = field(default_factory=utc_now)
    reason: str | None = None


@dataclass(slots=True)
class ConfidenceRoutingSnapshot:
    threshold_rules: list[ConfidenceThresholdRule] = field(default_factory=list)
    decisions: list[ConfidenceRoutingDecision] = field(default_factory=list)


@dataclass(slots=True)
class ConfidenceRoutingResult:
    succeeded: bool
    decision: ConfidenceRoutingDecision | None = None
    approval_result: ApprovalGateResult | None = None
    snapshot: ConfidenceRoutingSnapshot | None = None
    reason: str | None = None


class EscalationTriggerType(str, Enum):
    REPEATED_STEP_FAILURE = "repeated_step_failure"
    CAPTCHA_DETECTED = "captcha_detected"
    SECURITY_VERIFICATION = "security_verification"
    NOVEL_UI_STATE = "novel_ui_state"
    APPROVAL_TIMEOUT = "approval_timeout"
    ALLOWLIST_VIOLATION = "allowlist_violation"
    LOOP_DETECTED = "loop_detected"
    PIPELINE_TIMEOUT = "pipeline_timeout"


class EscalationResolution(str, Enum):
    RESUME = "resume"
    ABORT = "abort"


@dataclass(slots=True)
class EscalationRequest:
    escalation_id: str
    workflow_id: str
    step_id: str | None
    trigger_type: EscalationTriggerType
    created_at: datetime
    expires_at: datetime
    context_data: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None


@dataclass(slots=True)
class EscalationResponse:
    escalation_id: str
    resolution: EscalationResolution
    operator_id: str | None = None
    responded_at: datetime = field(default_factory=utc_now)
    reason: str | None = None


@dataclass(slots=True)
class EscalationRecord:
    escalation_id: str
    workflow_id: str
    step_id: str | None
    trigger_type: EscalationTriggerType
    paused: bool
    resolved: bool = False
    resolution: EscalationResolution | None = None
    operator_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    responded_at: datetime | None = None
    detail: str | None = None


@dataclass(slots=True)
class EscalationSnapshot:
    active_requests: list[EscalationRequest] = field(default_factory=list)
    records: list[EscalationRecord] = field(default_factory=list)


@dataclass(slots=True)
class EscalationManagerResult:
    succeeded: bool
    request: EscalationRequest | None = None
    response: EscalationResponse | None = None
    record: EscalationRecord | None = None
    snapshot: EscalationSnapshot | None = None
    paused: bool = False
    resumed: bool = False
    aborted: bool = False
    timed_out: bool = False
    reason: str | None = None


class HumanReviewDecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


@dataclass(slots=True)
class HumanReviewPendingItem:
    request: ApprovalRequest
    workflow_context: dict[str, Any] = field(default_factory=dict)
    action_summary: str | None = None


@dataclass(slots=True)
class HumanReviewDecisionRecord:
    request_id: str
    reviewer_id: str
    decision: HumanReviewDecisionType
    decided_at: datetime
    reason: str | None = None
    modified_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HumanReviewInterfaceSnapshot:
    pending_items: list[HumanReviewPendingItem] = field(default_factory=list)
    decision_history: list[HumanReviewDecisionRecord] = field(default_factory=list)


@dataclass(slots=True)
class HumanReviewInterfaceResult:
    succeeded: bool
    item: HumanReviewPendingItem | None = None
    items: list[HumanReviewPendingItem] = field(default_factory=list)
    decision_record: HumanReviewDecisionRecord | None = None
    response: ApprovalResponse | None = None
    snapshot: HumanReviewInterfaceSnapshot | None = None
    rendered_view: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class IdempotencyRecord:
    action_id: str
    completed_at: datetime
    result_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IdempotencySnapshot:
    completed_actions: list[IdempotencyRecord] = field(default_factory=list)


@dataclass(slots=True)
class IdempotencyResult:
    succeeded: bool
    action_id: str
    executed: bool
    cached: bool = False
    result_payload: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


@dataclass(slots=True)
class DeadLetterItem:
    item_id: str
    action_type: str
    inputs: dict[str, Any] = field(default_factory=dict)
    retry_failure: RetryFailureResult | None = None
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class DeadLetterSnapshot:
    items: list[DeadLetterItem] = field(default_factory=list)


@dataclass(slots=True)
class DeadLetterOperationResult:
    succeeded: bool
    item: DeadLetterItem | None = None
    items: list[DeadLetterItem] = field(default_factory=list)
    report_path: str | None = None
    reason: str | None = None


class ErrorCategory(str, Enum):
    UI_ELEMENT_NOT_FOUND = "ui_element_not_found"
    APPLICATION_NOT_RESPONDING = "application_not_responding"
    SESSION_EXPIRED = "session_expired"
    NETWORK_TIMEOUT = "network_timeout"
    UNEXPECTED_DIALOG_APPEARED = "unexpected_dialog_appeared"
    SCREEN_STATE_MISMATCH = "screen_state_mismatch"
    UNRECOGNIZED_ERROR = "unrecognized_error"


class RecoveryStrategy(str, Enum):
    RETRY = "retry"
    REFRESH = "refresh"
    REAUTHENTICATE = "re_authenticate"
    DISMISS_DIALOG = "dismiss_dialog"
    SCROLL_TO_FIND = "scroll_to_find"
    WAIT_FOR_LOADING = "wait_for_loading"
    ESCALATE = "escalate"
    ABORT = "abort"


@dataclass(slots=True)
class ErrorClassificationRecord:
    category: ErrorCategory
    recovery_strategy: RecoveryStrategy
    exception_type: str | None = None
    message: str | None = None
    screenshot_path: str | None = None
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ErrorClassificationResult:
    succeeded: bool
    category: ErrorCategory
    recovery_strategy: RecoveryStrategy
    record: ErrorClassificationRecord
    reason: str | None = None


@dataclass(slots=True)
class SelfHealingRecoveryRequest:
    error: Exception | object
    target_checks: list[ScreenVerificationCheck] = field(default_factory=list)
    target_description: str | None = None
    input_target: InputTarget | None = None
    strategy_override: RecoveryStrategy | None = None
    scroll_amount: int = -800
    max_scroll_attempts: int = 3
    loading_timeout_seconds: float = 10.0
    loading_poll_interval_seconds: float = 0.5


@dataclass(slots=True)
class SelfHealingRecoveryResult:
    succeeded: bool
    classification: ErrorClassificationResult
    strategy: RecoveryStrategy
    initial_screenshot_path: str | None = None
    recovery_action_result: object | None = None
    verification: ScreenVerificationResult | None = None
    step_result: object | None = None
    reason: str | None = None


class AnomalyCategory(str, Enum):
    SLOW_STEP_EXECUTION = "slow_step_execution"
    REPEATED_STEP_FAILURE = "repeated_step_failure"
    APPLICATION_CRASH = "application_crash"
    UI_STRUCTURE_CHANGE = "ui_structure_change"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


@dataclass(slots=True)
class ResourceUsageSnapshot:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    available_memory_mb: float | None = None
    process_name: str | None = None


@dataclass(slots=True)
class AnomalyRecord:
    category: AnomalyCategory
    step_id: str | None = None
    application_name: str | None = None
    detail: str | None = None
    execution_time_seconds: float | None = None
    baseline_seconds: float | None = None
    observed_value: float | None = None
    threshold_value: float | None = None
    pause_requested: bool = False
    alert_sent: bool = False
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AnomalyDetectionResult:
    succeeded: bool
    anomalies: list[AnomalyRecord] = field(default_factory=list)
    pause_requested: bool = False
    reason: str | None = None


@dataclass(slots=True)
class FailureArchiveRecord:
    record_id: str
    workflow_id: str
    step_name: str
    timestamp: datetime
    screenshot_path: str | None = None
    accessibility_tree_path: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    last_actions: list[str] = field(default_factory=list)
    application_name: str | None = None


@dataclass(slots=True)
class FailureArchiveQuery:
    workflow_id: str | None = None
    step_name: str | None = None
    exception_type: str | None = None


@dataclass(slots=True)
class FailureArchiveResult:
    succeeded: bool
    record: FailureArchiveRecord | None = None
    records: list[FailureArchiveRecord] = field(default_factory=list)
    reason: str | None = None


class WatchdogEventType(str, Enum):
    STALL = "stall"
    RESOURCE_ALERT = "resource_alert"


@dataclass(slots=True)
class WatchdogEvent:
    event_type: WatchdogEventType
    workflow_id: str
    detail: str | None = None
    screenshot_path: str | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    heartbeat_age_seconds: float | None = None
    graceful_termination_attempted: bool = False
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class WatchdogStatus:
    running: bool
    workflow_id: str | None = None
    last_heartbeat_at: datetime | None = None
    heartbeat_timeout_seconds: float = 0.0
    monitoring_interval_seconds: float = 0.0
    last_event: WatchdogEvent | None = None


@dataclass(slots=True)
class WorkflowSkillStep:
    step_name: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowSkillVersion:
    version: int
    description: str
    steps: list[WorkflowSkillStep] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    contextual_notes: str | None = None
    task_description: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    deprecated: bool = False


@dataclass(slots=True)
class WorkflowSkillDocument:
    workflow_name: str
    description: str
    current_version: int = 1
    versions: list[WorkflowSkillVersion] = field(default_factory=list)
    deprecated: bool = False
    deprecated_reason: str | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class WorkflowSkillSearchMatch:
    workflow_name: str
    score: float
    skill: WorkflowSkillDocument


@dataclass(slots=True)
class WorkflowSkillStoreResult:
    succeeded: bool
    skill: WorkflowSkillDocument | None = None
    matches: list[WorkflowSkillSearchMatch] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class AutomationEpisode:
    episode_id: str
    task_description: str
    task_type: str
    applications: list[str] = field(default_factory=list)
    steps_executed: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    errors_encountered: list[str] = field(default_factory=list)
    recovery_actions_taken: list[str] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    succeeded: bool = False
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class EpisodicMemorySearchMatch:
    episode: AutomationEpisode
    score: float


@dataclass(slots=True)
class EpisodicMemoryResult:
    succeeded: bool
    episode: AutomationEpisode | None = None
    episodes: list[AutomationEpisode] = field(default_factory=list)
    matches: list[EpisodicMemorySearchMatch] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class ActionHistorySequencePattern:
    sequence: list[str] = field(default_factory=list)
    count: int = 0


@dataclass(slots=True)
class ActionStepDurationSummary:
    step_type: str
    average_duration_seconds: float
    sample_count: int = 0


@dataclass(slots=True)
class ActionFailurePoint:
    step_type: str
    failure_count: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ActionRetryRateSummary:
    step_type: str
    retry_count: int = 0
    sample_count: int = 0
    retry_rate: float = 0.0


@dataclass(slots=True)
class ActionOptimizationHint:
    step_type: str
    recommendation: str


@dataclass(slots=True)
class ActionHistoryAnalysisReport:
    frequent_sequences: list[ActionHistorySequencePattern] = field(default_factory=list)
    common_failure_points: list[ActionFailurePoint] = field(default_factory=list)
    average_durations: list[ActionStepDurationSummary] = field(default_factory=list)
    high_retry_steps: list[ActionRetryRateSummary] = field(default_factory=list)
    optimization_hints: list[ActionOptimizationHint] = field(default_factory=list)


@dataclass(slots=True)
class ActionHistoryAnalysisResult:
    succeeded: bool
    report: ActionHistoryAnalysisReport | None = None
    reason: str | None = None


@dataclass(slots=True)
class PromptPerformanceRecord:
    record_id: str
    template_name: str
    template_version: int | None = None
    variables: dict[str, str] = field(default_factory=dict)
    response_text: str | None = None
    expected_format_met: bool = False
    execution_time_seconds: float = 0.0
    succeeded: bool = False
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PromptTemplatePerformanceSummary:
    template_name: str
    submission_count: int = 0
    success_count: int = 0
    expected_format_success_count: int = 0
    success_rate: float = 0.0
    expected_format_rate: float = 0.0
    average_execution_time_seconds: float = 0.0
    flagged_low_success: bool = False


@dataclass(slots=True)
class PromptPerformanceReport:
    template_summaries: list[PromptTemplatePerformanceSummary] = field(default_factory=list)
    most_reliable_templates: list[PromptTemplatePerformanceSummary] = field(default_factory=list)
    least_reliable_templates: list[PromptTemplatePerformanceSummary] = field(default_factory=list)


@dataclass(slots=True)
class PromptPerformanceResult:
    succeeded: bool
    record: PromptPerformanceRecord | None = None
    report: PromptPerformanceReport | None = None
    reason: str | None = None


class ImprovementTargetType(str, Enum):
    STEP = "step"
    WORKFLOW = "workflow"
    PROMPT_TEMPLATE = "prompt_template"


class ImprovementProposalStatus(str, Enum):
    PROPOSED = "proposed"
    REVIEW_PENDING = "review_pending"
    APPLIED = "applied"
    REJECTED = "rejected"


@dataclass(slots=True)
class ImprovementProposalRecord:
    proposal_id: str
    target_type: ImprovementTargetType
    target_identifier: str
    workflow_id: str | None = None
    failure_count: int = 0
    failure_summary: str | None = None
    proposed_modification: str | None = None
    status: ImprovementProposalStatus = ImprovementProposalStatus.PROPOSED
    human_review_required: bool = False
    baseline_success_count: int = 0
    baseline_failure_count: int = 0
    post_apply_success_count: int = 0
    post_apply_failure_count: int = 0
    created_at: datetime = field(default_factory=utc_now)
    applied_at: datetime | None = None
    review_note: str | None = None


@dataclass(slots=True)
class SelfCritiqueResult:
    succeeded: bool
    proposal: ImprovementProposalRecord | None = None
    proposals: list[ImprovementProposalRecord] = field(default_factory=list)
    reason: str | None = None


class FeedbackEventType(str, Enum):
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_MODIFIED = "approval_modified"
    HUMAN_REVIEW_REJECTED = "human_review_rejected"
    HUMAN_REVIEW_MODIFIED = "human_review_modified"


@dataclass(slots=True)
class FeedbackEventRecord:
    feedback_id: str
    workflow_id: str
    step_id: str | None
    action_type: str
    event_type: FeedbackEventType
    reviewer_id: str | None = None
    original_action: dict[str, Any] = field(default_factory=dict)
    modified_action: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    context_data: dict[str, Any] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class FeedbackPatternSummary:
    pattern_key: str
    event_count: int
    workflow_ids: list[str] = field(default_factory=list)
    action_type: str | None = None
    event_type: FeedbackEventType | None = None
    common_reasons: list[str] = field(default_factory=list)
    suggested_change: str | None = None


@dataclass(slots=True)
class FeedbackLoopResult:
    succeeded: bool
    event: FeedbackEventRecord | None = None
    events: list[FeedbackEventRecord] = field(default_factory=list)
    patterns: list[FeedbackPatternSummary] = field(default_factory=list)
    proposals: list[ImprovementProposalRecord] = field(default_factory=list)
    reason: str | None = None


class TaskDecompositionLevel(str, Enum):
    PHASE = "phase"
    TASK = "task"
    STEP = "step"


@dataclass(slots=True)
class TaskDecompositionNode:
    node_id: str
    title: str
    description: str
    level: TaskDecompositionLevel
    depth: int
    abstract: bool = False
    children: list["TaskDecompositionNode"] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskDecompositionTree:
    task_description: str
    max_depth: int
    fully_decomposed: bool = False
    root_nodes: list[TaskDecompositionNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskDecompositionResult:
    succeeded: bool
    tree: TaskDecompositionTree | None = None
    reason: str | None = None


class OrchestratorSubtaskStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    REROUTED = "rerouted"


@dataclass(slots=True)
class OrchestratorSubtask:
    subtask_id: str
    description: str
    responsible_module: str
    required_inputs: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    dependency_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OrchestratorSubtaskResult:
    subtask_id: str
    status: OrchestratorSubtaskStatus
    responsible_module: str
    produced_outputs: dict[str, str] = field(default_factory=dict)
    rerouted_to: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class OrchestratorTaskPlan:
    task_description: str
    subtasks: list[OrchestratorSubtask] = field(default_factory=list)
    decomposition_tree: TaskDecompositionTree | None = None


@dataclass(slots=True)
class OrchestratorExecutionSummary:
    succeeded: bool
    completed_subtasks: list[str] = field(default_factory=list)
    failed_subtasks: list[str] = field(default_factory=list)
    skipped_subtasks: list[str] = field(default_factory=list)
    rerouted_subtasks: list[str] = field(default_factory=list)
    final_outputs: dict[str, str] = field(default_factory=dict)
    reason: str | None = None


@dataclass(slots=True)
class OrchestratorAgentResult:
    succeeded: bool
    plan: OrchestratorTaskPlan | None = None
    subtask_results: list[OrchestratorSubtaskResult] = field(default_factory=list)
    summary: OrchestratorExecutionSummary | None = None
    reason: str | None = None


@dataclass(slots=True)
class SpecialistAgentRecord:
    agent_name: str
    capabilities: list[str] = field(default_factory=list)
    module_reference: str | None = None


@dataclass(slots=True)
class RoutingDecisionRecord:
    subtask_id: str
    selected_agent: str | None = None
    selected_module: str | None = None
    matched_capability: str | None = None
    escalated: bool = False
    reason: str | None = None
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SpecialistRouterResult:
    succeeded: bool
    decision: RoutingDecisionRecord | None = None
    decisions: list[RoutingDecisionRecord] = field(default_factory=list)
    subtask_result: OrchestratorSubtaskResult | None = None
    reason: str | None = None


class BusRecipientKind(str, Enum):
    TOPIC = "topic"
    DIRECT = "direct"


@dataclass(slots=True)
class AgentBusMessage:
    message_id: str
    sender_id: str
    recipient_kind: BusRecipientKind
    recipient_id: str
    message_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    correlation_sequence: int = 1
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AgentBusSubscription:
    agent_id: str
    topic: str
    subscribed_at: datetime = field(default_factory=utc_now)
    last_delivered_message_id: str | None = None


@dataclass(slots=True)
class AgentBusDeliveryResult:
    succeeded: bool
    message: AgentBusMessage | None = None
    messages: list[AgentBusMessage] = field(default_factory=list)
    reason: str | None = None


class SharedStateConflictPolicy(str, Enum):
    LAST_WRITE_WINS = "last_write_wins"
    PRIORITY_BASED = "priority_based"
    MANUAL_RESOLUTION = "manual_resolution"


@dataclass(slots=True)
class SharedStateField:
    field_name: str
    field_type: str
    value: Any = None
    read_agents: list[str] = field(default_factory=list)
    write_agents: list[str] = field(default_factory=list)
    updated_by: str | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class SharedStateWriteLog:
    field_name: str
    agent_id: str
    value: Any = None
    timestamp: datetime = field(default_factory=utc_now)
    accepted: bool = True
    reason: str | None = None


@dataclass(slots=True)
class SharedStateSnapshot:
    fields: list[SharedStateField] = field(default_factory=list)
    captured_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SharedStateResult:
    succeeded: bool
    state_field: SharedStateField | None = None
    fields: list[SharedStateField] = field(default_factory=list)
    snapshot: SharedStateSnapshot | None = None
    write_log: SharedStateWriteLog | None = None
    reason: str | None = None


class AgentHandoffReason(str, Enum):
    COMPLETION = "completion"
    ERROR = "error"
    CAPABILITY_MISMATCH = "capability_mismatch"


@dataclass(slots=True)
class AgentHandoffContext:
    current_step: str | None = None
    collected_data: dict[str, str] = field(default_factory=dict)
    plan_description: str | None = None


@dataclass(slots=True)
class AgentHandoffRecord:
    handoff_id: str
    sender_agent_id: str
    receiver_agent_id: str
    context: AgentHandoffContext
    reason: AgentHandoffReason
    special_instructions: str | None = None
    overlap_until: datetime | None = None
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AgentHandoffResult:
    succeeded: bool
    handoff: AgentHandoffRecord | None = None
    handoffs: list[AgentHandoffRecord] = field(default_factory=list)
    reason: str | None = None


class StructuredDataExtractionMode(str, Enum):
    TABLE = "table"
    FORM = "form"
    TEXT_BLOCK = "text_block"


class StructuredDataFieldType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"


class PaginationAdvanceMode(str, Enum):
    CLICK = "click"
    HOTKEY = "hotkey"


@dataclass(slots=True)
class StructuredDataFieldSchema:
    field_name: str
    field_type: StructuredDataFieldType = StructuredDataFieldType.STRING
    source_name: str | None = None
    aliases: tuple[str, ...] = ()
    selector: AIInterfaceElementSelector | None = None
    required: bool = False
    column_index: int | None = None


@dataclass(slots=True)
class StructuredDataSchema:
    schema_name: str
    fields: list[StructuredDataFieldSchema] = field(default_factory=list)


@dataclass(slots=True)
class PaginationConfiguration:
    next_page_selector: AIInterfaceElementSelector
    disabled_selector: AIInterfaceElementSelector | None = None
    max_pages: int = 1
    advance_mode: PaginationAdvanceMode = PaginationAdvanceMode.CLICK
    advance_hotkey: tuple[str, ...] = ("pagedown",)


@dataclass(slots=True)
class StructuredDataRecord:
    values: dict[str, Any] = field(default_factory=dict)
    page_number: int = 1
    source_mode: StructuredDataExtractionMode = StructuredDataExtractionMode.TEXT_BLOCK
    validation_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StructuredDataPageResult:
    page_number: int
    records: list[StructuredDataRecord] = field(default_factory=list)
    raw_text: str | None = None
    advanced_to_next_page: bool = False
    reason: str | None = None


@dataclass(slots=True)
class StructuredDataExtractionConfiguration:
    mode: StructuredDataExtractionMode
    schema: StructuredDataSchema
    table_selector: AIInterfaceElementSelector | None = None
    form_selector: AIInterfaceElementSelector | None = None
    text_block_selector: AIInterfaceElementSelector | None = None
    pagination: PaginationConfiguration | None = None
    ocr_language: str = "eng"
    minimum_ocr_confidence: float = 0.0
    has_header_row: bool = True
    max_rows_per_page: int | None = None
    row_merge_tolerance: int = 12


@dataclass(slots=True)
class StructuredDataExtractionResult:
    succeeded: bool
    records: list[StructuredDataRecord] = field(default_factory=list)
    page_results: list[StructuredDataPageResult] = field(default_factory=list)
    reason: str | None = None


class WorkflowAuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(slots=True)
class WorkflowAuditLogEntry:
    timestamp: datetime
    workflow_id: str
    step_name: str
    action_type: str
    workflow_version_number: int | None = None
    target_element: str | None = None
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    outcome: WorkflowAuditOutcome = WorkflowAuditOutcome.SUCCESS
    success: bool = True


@dataclass(slots=True)
class WorkflowAuditQuery:
    workflow_id: str | None = None
    workflow_version_number: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    action_type: str | None = None
    outcome: WorkflowAuditOutcome | None = None


@dataclass(slots=True)
class WorkflowAuditResult:
    succeeded: bool
    entry: WorkflowAuditLogEntry | None = None
    entries: list[WorkflowAuditLogEntry] = field(default_factory=list)
    export_path: str | None = None
    reason: str | None = None


class DatabaseLogOperationType(str, Enum):
    INSERT_WORKFLOW = "insert_workflow"
    INSERT_EVENT = "insert_event"
    INSERT_STEP = "insert_step"
    INSERT_ERROR = "insert_error"
    INSERT_CHECKPOINT = "insert_checkpoint"
    INSERT_EXTRACTED_DATA = "insert_extracted_data"
    INSERT_METRIC = "insert_metric"
    UPDATE_STEP_STATUS = "update_step_status"


@dataclass(slots=True)
class DatabaseWorkflowRecord:
    workflow_id: str
    workflow_name: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    status: str = "running"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatabaseWorkflowEventRecord:
    workflow_id: str
    event_type: str
    recorded_at: datetime = field(default_factory=utc_now)
    step_id: str | None = None
    detail: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatabaseStepRecord:
    workflow_id: str
    step_id: str
    application_name: str
    recorded_at: datetime = field(default_factory=utc_now)
    status: str = "pending"
    succeeded: bool | None = None
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatabaseErrorRecord:
    workflow_id: str
    error_type: str
    message: str
    recorded_at: datetime = field(default_factory=utc_now)
    step_id: str | None = None
    detail: str | None = None


@dataclass(slots=True)
class DatabaseCheckpointRecord:
    workflow_id: str
    step_index: int
    saved_at: datetime
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    account_context: dict[str, Any] = field(default_factory=dict)
    collected_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatabaseExtractedDataRecord:
    workflow_id: str
    data_key: str
    data_value: str
    recorded_at: datetime = field(default_factory=utc_now)
    step_id: str | None = None
    source_application: str | None = None


@dataclass(slots=True)
class DatabaseMetricRecord:
    workflow_id: str
    metric_name: str
    metric_value: float
    recorded_at: datetime = field(default_factory=utc_now)
    step_id: str | None = None
    dimensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatabaseBufferedOperation:
    operation_type: DatabaseLogOperationType
    payload: dict[str, Any] = field(default_factory=dict)
    buffered_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class DatabaseLoggingSnapshot:
    workflows: list[DatabaseWorkflowRecord] = field(default_factory=list)
    events: list[DatabaseWorkflowEventRecord] = field(default_factory=list)
    steps: list[DatabaseStepRecord] = field(default_factory=list)
    errors: list[DatabaseErrorRecord] = field(default_factory=list)
    checkpoints: list[DatabaseCheckpointRecord] = field(default_factory=list)
    extracted_data: list[DatabaseExtractedDataRecord] = field(default_factory=list)
    metrics: list[DatabaseMetricRecord] = field(default_factory=list)
    buffered_operations: list[DatabaseBufferedOperation] = field(default_factory=list)


@dataclass(slots=True)
class DatabaseLoggingResult:
    succeeded: bool
    snapshot: DatabaseLoggingSnapshot | None = None
    workflow: DatabaseWorkflowRecord | None = None
    event: DatabaseWorkflowEventRecord | None = None
    step: DatabaseStepRecord | None = None
    error: DatabaseErrorRecord | None = None
    checkpoint: DatabaseCheckpointRecord | None = None
    extracted_data_record: DatabaseExtractedDataRecord | None = None
    metric: DatabaseMetricRecord | None = None
    buffered_count: int = 0
    flushed_count: int = 0
    deleted_count: int = 0
    updated_count: int = 0
    reason: str | None = None


class MetricsDegradationDirection(str, Enum):
    HIGHER_IS_WORSE = "higher_is_worse"
    LOWER_IS_WORSE = "lower_is_worse"


@dataclass(slots=True)
class StepLatencySummary:
    step_name: str
    sample_count: int = 0
    mean_seconds: float = 0.0
    p95_seconds: float = 0.0
    p99_seconds: float = 0.0


@dataclass(slots=True)
class StepRetryRateMetric:
    step_name: str
    retry_count: int = 0
    execution_count: int = 0
    retry_rate: float = 0.0


@dataclass(slots=True)
class WorkflowSuccessMetric:
    workflow_id: str
    run_count: int = 0
    success_count: int = 0
    success_rate: float = 0.0
    average_steps_per_run: float = 0.0


@dataclass(slots=True)
class MetricsDegradationRecord:
    metric_name: str
    current_value: float
    baseline_value: float
    delta_ratio: float
    direction: MetricsDegradationDirection
    threshold_ratio: float


@dataclass(slots=True)
class PerformanceMetricsSnapshot:
    generated_at: datetime = field(default_factory=utc_now)
    window_seconds: float = 3600.0
    step_latencies: list[StepLatencySummary] = field(default_factory=list)
    retry_rates: list[StepRetryRateMetric] = field(default_factory=list)
    workflow_success_rates: list[WorkflowSuccessMetric] = field(default_factory=list)
    dlq_depth: int = 0
    session_count: int = 0
    average_steps_per_workflow: float = 0.0
    degradations: list[MetricsDegradationRecord] = field(default_factory=list)


@dataclass(slots=True)
class PerformanceMetricsResult:
    succeeded: bool
    snapshot: PerformanceMetricsSnapshot | None = None
    endpoint_payload: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class ResourceUsageRunRecord:
    workflow_id: str
    workflow_type: str
    account_name: str | None = None
    cpu_time_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    api_call_count: int = 0
    llm_token_count: int = 0
    run_duration_seconds: float = 0.0
    screenshot_count: int = 0
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ResourceUsageAggregate:
    group_key: str
    run_count: int = 0
    average_cpu_time_seconds: float = 0.0
    average_peak_memory_mb: float = 0.0
    average_api_call_count: float = 0.0
    average_llm_token_count: float = 0.0
    average_run_duration_seconds: float = 0.0
    average_screenshot_count: float = 0.0
    total_cpu_time_seconds: float = 0.0
    total_api_call_count: int = 0
    total_llm_token_count: int = 0
    total_run_duration_seconds: float = 0.0
    total_screenshot_count: int = 0
    peak_memory_mb: float = 0.0


@dataclass(slots=True)
class ResourceUsageTrendSnapshot:
    generated_at: datetime = field(default_factory=utc_now)
    window_seconds: float = 3600.0
    workflow_type_aggregates: list[ResourceUsageAggregate] = field(default_factory=list)
    account_aggregates: list[ResourceUsageAggregate] = field(default_factory=list)
    workflow_type_degradations: dict[str, list[MetricsDegradationRecord]] = field(default_factory=dict)


@dataclass(slots=True)
class ResourceUsageTrendReport:
    report_date: str
    generated_at: datetime = field(default_factory=utc_now)
    snapshot: ResourceUsageTrendSnapshot | None = None
    body_text: str | None = None


@dataclass(slots=True)
class ResourceUsageTrackerResult:
    succeeded: bool
    run_record: ResourceUsageRunRecord | None = None
    runs: list[ResourceUsageRunRecord] = field(default_factory=list)
    aggregate: ResourceUsageAggregate | None = None
    snapshot: ResourceUsageTrendSnapshot | None = None
    report: ResourceUsageTrendReport | None = None
    reason: str | None = None


@dataclass(slots=True)
class AutomationPluginDeclaration:
    name: str
    supported_action_types: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    initialize_entry_point: str = "initialize"
    execute_entry_point: str = "execute"
    teardown_entry_point: str = "teardown"
    version: str | None = None
    description: str | None = None


@dataclass(slots=True)
class AutomationPluginRecord:
    declaration: AutomationPluginDeclaration
    module_path: str
    module_name: str
    loaded_at: datetime = field(default_factory=utc_now)
    initialized: bool = False
    last_reloaded_at: datetime | None = None
    file_mtime: float | None = None


@dataclass(slots=True)
class PluginActionRoute:
    action_type: str
    plugin_name: str
    module_path: str


@dataclass(slots=True)
class AutomationPluginExecutionResult:
    succeeded: bool
    plugin_name: str | None = None
    action_type: str | None = None
    result_payload: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


@dataclass(slots=True)
class AutomationPluginLoaderResult:
    succeeded: bool
    plugin: AutomationPluginRecord | None = None
    plugins: list[AutomationPluginRecord] = field(default_factory=list)
    route: PluginActionRoute | None = None
    routes: list[PluginActionRoute] = field(default_factory=list)
    execution_result: AutomationPluginExecutionResult | None = None
    reloaded_plugin_names: list[str] = field(default_factory=list)
    removed_plugin_names: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class SLAWorkflowConfiguration:
    workflow_type: str
    expected_completion_time_seconds: float
    compliance_threshold: float = 0.95
    description: str | None = None


@dataclass(slots=True)
class SLARecordedStepDuration:
    step_name: str
    duration_seconds: float


@dataclass(slots=True)
class SLARunRecord:
    workflow_id: str
    workflow_type: str
    completion_time_seconds: float
    met_sla: bool
    timestamp: datetime = field(default_factory=utc_now)
    step_durations: list[SLARecordedStepDuration] = field(default_factory=list)


@dataclass(slots=True)
class SLASlowestStepContribution:
    step_name: str
    miss_count: int = 0
    average_duration_seconds: float = 0.0
    total_duration_seconds: float = 0.0


@dataclass(slots=True)
class SLAAlertRecord:
    workflow_type: str
    compliance_rate: float
    threshold: float
    run_count: int
    triggered_at: datetime = field(default_factory=utc_now)
    notification_sent: bool = False


@dataclass(slots=True)
class SLAComplianceSnapshot:
    workflow_type: str
    expected_completion_time_seconds: float
    compliance_threshold: float
    run_count: int = 0
    met_sla_count: int = 0
    compliance_rate: float = 0.0
    average_completion_time_seconds: float = 0.0
    sla_miss_count: int = 0
    slowest_miss_steps: list[SLASlowestStepContribution] = field(default_factory=list)
    latest_alert: SLAAlertRecord | None = None
    alert_needed: bool = False


@dataclass(slots=True)
class SLADailyPerformanceReport:
    report_date: str
    generated_at: datetime = field(default_factory=utc_now)
    workflow_summaries: list[SLAComplianceSnapshot] = field(default_factory=list)
    alerts: list[SLAAlertRecord] = field(default_factory=list)
    body_text: str | None = None


@dataclass(slots=True)
class SLAMonitorResult:
    succeeded: bool
    configuration: SLAWorkflowConfiguration | None = None
    configurations: list[SLAWorkflowConfiguration] = field(default_factory=list)
    run_record: SLARunRecord | None = None
    snapshot: SLAComplianceSnapshot | None = None
    snapshots: list[SLAComplianceSnapshot] = field(default_factory=list)
    alert: SLAAlertRecord | None = None
    alerts: list[SLAAlertRecord] = field(default_factory=list)
    report: SLADailyPerformanceReport | None = None
    reason: str | None = None


@dataclass(slots=True)
class ActiveWorkflowStatus:
    workflow_id: str
    workflow_version_number: int | None = None
    current_step_name: str | None = None
    total_steps: int = 0
    completed_steps: int = 0
    percent_complete: float = 0.0
    active_task_ids: list[str] = field(default_factory=list)
    assigned_worker_ids: list[str] = field(default_factory=list)
    status: str = "active"


@dataclass(slots=True)
class QueueDepthStatus:
    task_queue_depth: int = 0
    dead_letter_queue_depth: int = 0


@dataclass(slots=True)
class DashboardAccountStatus:
    account_name: str
    account_type: str
    application_name: str
    healthy: bool
    active: bool
    current_load: int
    capacity: int
    available_capacity: int
    load_ratio: float
    assigned_worker_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepExecutionRateStatus:
    step_name: str
    success_count: int = 0
    failure_count: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0


@dataclass(slots=True)
class DashboardWorkerStatus:
    worker_id: str
    status: str
    current_task_id: str | None = None
    current_account: str | None = None
    last_heartbeat_at: datetime | None = None
    restart_count: int = 0


@dataclass(slots=True)
class DashboardSnapshot:
    generated_at: datetime
    active_workflows: list[ActiveWorkflowStatus] = field(default_factory=list)
    queue_depths: QueueDepthStatus = field(default_factory=QueueDepthStatus)
    account_statuses: list[DashboardAccountStatus] = field(default_factory=list)
    step_rates_last_hour: list[StepExecutionRateStatus] = field(default_factory=list)
    worker_statuses: list[DashboardWorkerStatus] = field(default_factory=list)


@dataclass(slots=True)
class DashboardDataProviderResult:
    succeeded: bool
    snapshot: DashboardSnapshot | None = None
    sse_event: str | None = None
    websocket_payload: dict[str, Any] | None = None
    reason: str | None = None


class ExecutionTraceEventType(str, Enum):
    STEP_STATE = "step_state"
    PERCEPTION = "perception"
    ACTION_DECISION = "action_decision"
    ACTION_EXECUTED = "action_executed"
    BRANCH_DECISION = "branch_decision"
    HUMAN_INTERACTION = "human_interaction"
    FINAL_OUTCOME = "final_outcome"


@dataclass(slots=True)
class ExecutionTraceEvent:
    sequence_number: int
    timestamp: datetime
    event_type: ExecutionTraceEventType
    step_id: str | None = None
    step_index: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None


@dataclass(slots=True)
class ExecutionTraceRecord:
    trace_id: str
    workflow_id: str
    workflow_version_number: int | None = None
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    succeeded: bool | None = None
    final_outcome: str | None = None
    manifest_path: str | None = None
    archive_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[ExecutionTraceEvent] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionTraceResult:
    succeeded: bool
    trace: ExecutionTraceRecord | None = None
    traces: list[ExecutionTraceRecord] = field(default_factory=list)
    event: ExecutionTraceEvent | None = None
    replay_events: list[ExecutionTraceEvent] = field(default_factory=list)
    manifest_path: str | None = None
    archive_path: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class ComplianceActionRecord:
    timestamp: datetime
    actor: str
    action_type: str
    step_name: str
    outcome: str
    target_element: str | None = None
    workflow_version_number: int | None = None
    detail: str | None = None


@dataclass(slots=True)
class ComplianceApprovalRecord:
    request_id: str
    step_id: str
    action_type: str
    reviewer_channel: str
    requested_at: datetime
    decision: str | None = None
    reviewer_id: str | None = None
    responded_at: datetime | None = None
    reason: str | None = None


@dataclass(slots=True)
class ComplianceEscalationRecord:
    escalation_id: str
    step_id: str | None
    trigger_type: str
    created_at: datetime
    resolved: bool = False
    resolution: str | None = None
    operator_id: str | None = None
    responded_at: datetime | None = None
    detail: str | None = None


@dataclass(slots=True)
class ComplianceFailureRecord:
    step_name: str
    timestamp: datetime
    exception_type: str | None = None
    exception_message: str | None = None
    recovery_actions: list[str] = field(default_factory=list)
    screenshot_path: str | None = None


@dataclass(slots=True)
class ComplianceAccountSessionRecord:
    account_name: str
    application_name: str | None = None
    launched_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    active: bool = False


@dataclass(slots=True)
class ComplianceSensitiveAccessRecord:
    source_type: str
    identifier: str
    action: str
    timestamp: datetime
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ComplianceAuditReport:
    report_id: str
    workflow_id: str
    workflow_name: str
    generated_at: datetime
    generated_by: str
    signature: str
    signature_algorithm: str
    actions: list[ComplianceActionRecord] = field(default_factory=list)
    approvals: list[ComplianceApprovalRecord] = field(default_factory=list)
    escalations: list[ComplianceEscalationRecord] = field(default_factory=list)
    failures: list[ComplianceFailureRecord] = field(default_factory=list)
    account_sessions: list[ComplianceAccountSessionRecord] = field(default_factory=list)
    sensitive_access_events: list[ComplianceSensitiveAccessRecord] = field(default_factory=list)
    body_text: str | None = None
    json_export_path: str | None = None
    pdf_export_path: str | None = None


@dataclass(slots=True)
class ComplianceAuditReportResult:
    succeeded: bool
    report: ComplianceAuditReport | None = None
    export_path: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class WorkflowReportTimelineItem:
    timestamp: datetime
    step_name: str
    action_type: str
    outcome: WorkflowAuditOutcome
    duration_seconds: float = 0.0
    detail: str | None = None


@dataclass(slots=True)
class WorkflowReportSummary:
    workflow_name: str
    workflow_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    total_steps_executed: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_seconds: float = 0.0
    highest_retry_steps: list[ActionRetryRateSummary] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    dlq_items: list[str] = field(default_factory=list)
    unusual_patterns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowRunReport:
    summary: WorkflowReportSummary
    timeline: list[WorkflowReportTimelineItem] = field(default_factory=list)
    failure_screenshot_links: list[str] = field(default_factory=list)
    body_text: str | None = None


@dataclass(slots=True)
class WorkflowReportResult:
    succeeded: bool
    report: WorkflowRunReport | None = None
    export_path: str | None = None
    reason: str | None = None


class DataExportDestinationType(str, Enum):
    FILE = "file"
    DATABASE = "database"
    API = "api"


class DataExportFileFormat(str, Enum):
    CSV = "csv"
    JSON = "json"
    XML = "xml"


@dataclass(slots=True)
class DataExportDestination:
    destination_type: DataExportDestinationType
    destination_name: str
    file_path: str | None = None
    file_format: DataExportFileFormat | None = None
    database_table: str | None = None
    api_endpoint: str | None = None
    include_workflow_id: bool = True


@dataclass(slots=True)
class DataExportValidationFailure:
    record_index: int
    field_name: str | None = None
    reason: str | None = None
    record_values: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DataExportLogEntry:
    timestamp: datetime
    destination_name: str
    destination_type: DataExportDestinationType
    exported_count: int = 0
    validation_failure_count: int = 0
    success: bool = True
    detail: str | None = None


@dataclass(slots=True)
class DataExportResult:
    succeeded: bool
    buffered_count: int = 0
    deduplicated_count: int = 0
    exported_count: int = 0
    validation_failures: list[DataExportValidationFailure] = field(default_factory=list)
    destination_results: list[DataExportLogEntry] = field(default_factory=list)
    retry_failure: RetryFailureResult | None = None
    reason: str | None = None


@dataclass(slots=True)
class FileDataExchangeResult:
    succeeded: bool
    file_path: str | None = None
    file_format: DataExportFileFormat | None = None
    encoding: str = "utf-8"
    import_result: object | None = None
    verification: ScreenVerificationResult | None = None
    cleaned_up: bool = False
    payload_preview: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class COMAutomationSession:
    application_name: str
    programmatic_identifier: str
    visible: bool = False
    connected: bool = True


@dataclass(slots=True)
class COMAutomationResult:
    succeeded: bool
    session: COMAutomationSession | None = None
    value: object | None = None
    retry_failure: RetryFailureResult | None = None
    released_objects: int = 0
    reason: str | None = None


@dataclass(slots=True)
class HybridAutomationSession:
    browser_process_id: int | None = None
    web_session_id: str | None = None
    current_url: str | None = None
    active_window_title: str | None = None
    active_process_name: str | None = None
    web_state: dict[str, Any] = field(default_factory=dict)
    desktop_state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HybridAutomationResult:
    succeeded: bool
    session: HybridAutomationSession
    web_result: object | None = None
    desktop_result: object | None = None
    verification: ScreenVerificationResult | None = None
    reason: str | None = None


class IPCChannelType(str, Enum):
    NAMED_PIPE = "named_pipe"
    LOCAL_SOCKET = "local_socket"


@dataclass(slots=True)
class IPCMessage:
    message_id: str
    sender_id: str
    recipient_id: str
    message_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class IPCConnectionRecord:
    process_id: str
    endpoint: str
    channel_type: IPCChannelType
    connected: bool = True
    last_connected_at: datetime | None = None
    reconnect_attempts: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class IPCSnapshot:
    connections: list[IPCConnectionRecord] = field(default_factory=list)
    sent_messages: list[IPCMessage] = field(default_factory=list)
    received_messages: list[IPCMessage] = field(default_factory=list)


@dataclass(slots=True)
class IPCOperationResult:
    succeeded: bool
    connection: IPCConnectionRecord | None = None
    message: IPCMessage | None = None
    connections: list[IPCConnectionRecord] = field(default_factory=list)
    messages: list[IPCMessage] = field(default_factory=list)
    snapshot: IPCSnapshot | None = None
    retry_failure: RetryFailureResult | None = None
    reason: str | None = None


class RESTAPIMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class APIAuthType(str, Enum):
    NONE = "none"
    BEARER = "bearer"
    API_KEY = "api_key"


@dataclass(slots=True)
class RESTAPIRequest:
    endpoint: str
    method: RESTAPIMethod
    headers: dict[str, str] = field(default_factory=dict)
    payload: dict[str, Any] | None = None
    auth_type: APIAuthType = APIAuthType.NONE
    auth_value: str | None = None
    api_key_header_name: str = "X-API-Key"
    expected_schema: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0


@dataclass(slots=True)
class RESTAPICallLog:
    timestamp: datetime
    endpoint: str
    method: RESTAPIMethod
    status_code: int | None = None
    latency_seconds: float = 0.0
    success: bool = False
    detail: str | None = None


@dataclass(slots=True)
class RESTAPIResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body_text: str | None = None
    parsed_body: object | None = None


@dataclass(slots=True)
class RESTAPIExecutorResult:
    succeeded: bool
    request: RESTAPIRequest | None = None
    response: RESTAPIResponse | None = None
    validation: ResponseValidationResult | None = None
    log_entry: RESTAPICallLog | None = None
    retry_failure: RetryFailureResult | None = None
    reason: str | None = None


class NotificationChannelType(str, Enum):
    EMAIL = "email"
    SLACK_WEBHOOK = "slack_webhook"
    GENERIC_WEBHOOK = "generic_webhook"


class NotificationEventType(str, Enum):
    STEP_FAILURE = "step_failure"
    ESCALATION = "escalation"
    COMPLETION = "completion"
    ANOMALY = "anomaly"


class WaitType(str, Enum):
    ELEMENT_APPEARS = "element_appears"
    ELEMENT_DISAPPEARS = "element_disappears"
    SCREEN_CHANGES = "screen_changes"
    TEXT_VISIBLE = "text_visible"
    NETWORK_IDLE = "network_idle"


class AnimationCompletionSignal(str, Enum):
    SETTLED = "settled"
    SPINNER_GONE = "spinner_gone"
    PROGRESS_COMPLETE = "progress_complete"


@dataclass(slots=True)
class AnimationWaitRequest:
    wait_id: str
    timeout_seconds: float = 5.0
    sampling_interval_seconds: float = 0.1
    settle_threshold: float = 0.01
    consecutive_stable_frames: int = 2
    region_of_interest: tuple[int, int, int, int] | None = None
    monitor_id: str | None = None
    screenshot_path: str | None = None
    spinner_template_name: str | None = None
    spinner_template_path: str | None = None
    spinner_text: str | None = None
    spinner_element_name: str | None = None
    spinner_element_role: str | None = None
    progress_complete_text: str | None = "100%"
    progress_element_name: str | None = None
    progress_element_role: str | None = None
    progress_expected_value: str | None = "100"
    required_signals: tuple[AnimationCompletionSignal, ...] = (AnimationCompletionSignal.SETTLED,)


@dataclass(slots=True)
class AnimationWaitLogEntry:
    wait_id: str
    elapsed_seconds: float
    succeeded: bool
    attempts: int
    detail: str | None = None
    completed_signals: tuple[AnimationCompletionSignal, ...] = ()


@dataclass(slots=True)
class AnimationWaitResult:
    succeeded: bool
    request: AnimationWaitRequest
    elapsed_seconds: float
    attempts: int
    detail: str | None = None
    screenshot_path: str | None = None
    completed_signals: tuple[AnimationCompletionSignal, ...] = ()
    last_change_rate: float = 0.0
    log_entry: AnimationWaitLogEntry | None = None


@dataclass(slots=True)
class SmartWaitRequest:
    wait_id: str
    wait_type: WaitType
    timeout_seconds: float = 5.0
    polling_interval_seconds: float = 0.25
    template_name: str | None = None
    template_path: str | None = None
    threshold: float = 0.8
    target_text: str | None = None
    element_name: str | None = None
    element_role: str | None = None
    expected_value: str | None = None
    region_of_interest: tuple[int, int, int, int] | None = None
    monitor_id: str | None = None
    screenshot_path: str | None = None
    network_indicator_text: str | None = None


@dataclass(slots=True)
class SmartWaitLogEntry:
    wait_id: str
    wait_type: WaitType
    elapsed_seconds: float
    succeeded: bool
    attempts: int
    detail: str | None = None


@dataclass(slots=True)
class SmartWaitResult:
    succeeded: bool
    request: SmartWaitRequest
    elapsed_seconds: float
    attempts: int
    detail: str | None = None
    screenshot_path: str | None = None
    log_entry: SmartWaitLogEntry | None = None


@dataclass(slots=True)
class AdaptiveTimingBaselineRecord:
    benchmark_id: str
    measured_at: datetime
    launch_seconds: float
    window_wait_seconds: float
    click_seconds: float


@dataclass(slots=True)
class AdaptiveTimingSessionProfile:
    benchmark_id: str
    system_speed_factor: float = 1.0
    baseline: AdaptiveTimingBaselineRecord | None = None
    current_measurement: AdaptiveTimingBaselineRecord | None = None


@dataclass(slots=True)
class AdaptiveTimingCalibrationResult:
    succeeded: bool
    profile: AdaptiveTimingSessionProfile | None = None
    reason: str | None = None


class TriggerType(str, Enum):
    NEW_WINDOW = "new_window"
    FILE_CHANGED = "file_changed"
    CLIPBOARD_CHANGED = "clipboard_changed"
    TIMER = "timer"


@dataclass(slots=True)
class EventTriggerDefinition:
    trigger_id: str
    trigger_type: TriggerType
    title_pattern: str | None = None
    directory_path: str | None = None
    include_subdirectories: bool = False
    timer_interval_seconds: float | None = None
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventTriggerRecord:
    trigger_id: str
    trigger_type: TriggerType
    timestamp: datetime
    detail: str | None = None
    window_title: str | None = None
    window_handle: int | None = None
    file_path: str | None = None
    clipboard_text: str | None = None
    clipboard_content_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventTriggerListenerStatus:
    running: bool
    trigger_count: int = 0
    last_event: EventTriggerRecord | None = None


@dataclass(slots=True)
class EventTriggerListenerResult:
    succeeded: bool
    trigger: EventTriggerDefinition | None = None
    triggers: list[EventTriggerDefinition] = field(default_factory=list)
    event: EventTriggerRecord | None = None
    events: list[EventTriggerRecord] = field(default_factory=list)
    status: EventTriggerListenerStatus | None = None
    reason: str | None = None


@dataclass(slots=True)
class NotificationChannel:
    channel_id: str
    channel_type: NotificationChannelType
    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    batch_non_urgent: bool = True
    urgent_event_types: tuple[NotificationEventType, ...] = (
        NotificationEventType.STEP_FAILURE,
        NotificationEventType.ESCALATION,
    )


@dataclass(slots=True)
class NotificationMessage:
    notification_id: str
    workflow_id: str
    event_type: NotificationEventType
    description: str
    context_data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class NotificationDispatchRecord:
    notification_id: str
    channel_id: str
    channel_type: NotificationChannelType
    status_code: int | None = None
    batched: bool = False
    succeeded: bool = False
    detail: str | None = None
    dispatched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class NotificationDispatcherSnapshot:
    queued_notifications: list[NotificationMessage] = field(default_factory=list)
    dispatch_history: list[NotificationDispatchRecord] = field(default_factory=list)


@dataclass(slots=True)
class NotificationDispatcherResult:
    succeeded: bool
    notification: NotificationMessage | None = None
    notifications: list[NotificationMessage] = field(default_factory=list)
    dispatch_records: list[NotificationDispatchRecord] = field(default_factory=list)
    snapshot: NotificationDispatcherSnapshot | None = None
    reason: str | None = None


