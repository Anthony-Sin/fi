from desktop_automation_agent.inter_step_pacing_controller import InterStepPacingController
from desktop_automation_agent.models import (
    InputAction,
    InputActionType,
    PacingContext,
    PacingProfile,
)


class DeterministicRandom:
    def __init__(self, uniform_values, random_values):
        self._uniform_values = list(uniform_values)
        self._random_values = list(random_values)

    def uniform(self, low, high):
        value = self._uniform_values.pop(0)
        return max(low, min(high, value))

    def random(self):
        return self._random_values.pop(0)


def test_inter_step_pacing_controller_resolves_account_profile_before_application_profile():
    controller = InterStepPacingController(
        default_profile=PacingProfile(profile_id="default", step_delay_range_seconds=(0.1, 0.1)),
    )
    controller.upsert_profile(PacingProfile(profile_id="account-fast", step_delay_range_seconds=(0.2, 0.2)))
    controller.upsert_profile(PacingProfile(profile_id="app-slow", step_delay_range_seconds=(0.8, 0.8)))
    controller.assign_profile_to_account("acct-1", "account-fast")
    controller.assign_profile_to_application("crm", "app-slow")

    decision = controller.before_action(
        PacingContext(
            action=InputAction(action_type=InputActionType.KEYPRESS, key="enter"),
            account_name="acct-1",
            application_name="crm",
        )
    )

    assert decision.profile_id == "account-fast"
    assert decision.delay_seconds == 0.2


def test_inter_step_pacing_controller_generates_typing_cadence_and_page_load_pause():
    controller = InterStepPacingController(
        default_profile=PacingProfile(
            profile_id="default",
            step_delay_range_seconds=(0.1, 0.1),
            typing_key_delay_range_seconds=(0.05, 0.05),
            typing_pause_probability=0.5,
            typing_pause_range_seconds=(0.2, 0.2),
            post_page_load_delay_range_seconds=(1.5, 1.5),
        ),
        random_source=DeterministicRandom(
            uniform_values=[0.05, 0.2, 0.05, 1.5],
            random_values=[0.4, 0.8],
        ),
    )

    typing = controller.typing_delays("ok")
    after = controller.after_action(
        PacingContext(
            action=InputAction(action_type=InputActionType.CLICK, context_tags=("page_load",)),
        )
    )

    assert [decision.delay_seconds for decision in typing] == [0.05, 0.2, 0.05]
    assert after.delay_seconds == 1.5
    assert "page-load" in after.reason.lower()
