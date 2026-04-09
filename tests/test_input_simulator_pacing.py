from desktop_automation_agent.input_simulator import SafeInputSimulator
from desktop_automation_agent.models import InputAction, InputActionType, PacingContext, ScreenBounds


class FakeBackend:
    def __init__(self):
        self.writes = []
        self.clicks = []

    def click(self, x, y, button):
        self.clicks.append((x, y, button))

    def press(self, key):
        return None

    def write(self, text):
        self.writes.append(text)

    def scroll(self, clicks):
        return None

    def hotkey(self, *keys):
        return None


class FakeWindowManager:
    def get_focused_window(self):
        return None


class FakeScreenInspector:
    def get_screen_bounds(self):
        return ScreenBounds(width=1920, height=1080)


class FakePacingController:
    def before_action(self, context: PacingContext):
        if context.action is not None and context.action.action_type is InputActionType.CLICK:
            return type("Decision", (), {"delay_seconds": 0.05})()
        return type("Decision", (), {"delay_seconds": 0.1})()

    def after_action(self, context: PacingContext):
        if context.action is not None and "page_load" in context.action.context_tags:
            return type("Decision", (), {"delay_seconds": 0.5})()
        return type("Decision", (), {"delay_seconds": 0.2})()

    def typing_delays(self, text, *, account_name=None, application_name=None):
        return [type("Decision", (), {"delay_seconds": 0.03})()]


def test_safe_input_simulator_uses_pacing_controller_for_typing_and_clicks():
    backend = FakeBackend()
    sleeps = []
    simulator = SafeInputSimulator(
        backend=backend,
        window_manager=FakeWindowManager(),
        screen_inspector=FakeScreenInspector(),
        sleep_fn=sleeps.append,
        pacing_controller=FakePacingController(),
        account_name="acct-1",
        application_name="crm",
    )

    result = simulator.run(
        [
            InputAction(action_type=InputActionType.TYPE_TEXT, text="hi"),
            InputAction(action_type=InputActionType.CLICK, position=(100, 200), context_tags=("page_load",)),
        ]
    )

    assert result.succeeded is True
    assert backend.writes == ["h", "i"]
    assert backend.clicks == [(100, 200, "left")]
    assert sleeps == [0.1, 0.03, 0.03, 0.2, 0.05, 0.5]
    assert result.logs[0].delay_seconds == 0.36
    assert result.logs[1].delay_seconds == 0.55
