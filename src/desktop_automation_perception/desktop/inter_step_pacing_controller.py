from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from desktop_automation_perception.models import (
    InputAction,
    InputActionType,
    PacingAssignment,
    PacingContext,
    PacingDecision,
    PacingProfile,
)


@dataclass(slots=True)
class InterStepPacingController:
    default_profile: PacingProfile = field(default_factory=lambda: PacingProfile(profile_id="default"))
    random_source: random.Random = field(default_factory=random.Random)
    profile_overrides: dict[str, PacingProfile] = field(default_factory=dict)
    account_assignments: dict[str, str] = field(default_factory=dict)
    application_assignments: dict[str, str] = field(default_factory=dict)

    def upsert_profile(self, profile: PacingProfile) -> PacingProfile:
        self.profile_overrides[profile.profile_id] = profile
        return profile

    def assign_profile_to_account(self, account_name: str, profile_id: str) -> PacingAssignment:
        self.account_assignments[account_name.casefold()] = profile_id
        return PacingAssignment("account", account_name, profile_id)

    def assign_profile_to_application(self, application_name: str, profile_id: str) -> PacingAssignment:
        self.application_assignments[application_name.casefold()] = profile_id
        return PacingAssignment("application", application_name, profile_id)

    def resolve_profile(
        self,
        *,
        account_name: str | None = None,
        application_name: str | None = None,
    ) -> PacingProfile:
        if account_name is not None:
            account_profile_id = self.account_assignments.get(account_name.casefold())
            if account_profile_id is not None:
                return self.profile_overrides.get(account_profile_id, self.default_profile)
        if application_name is not None:
            application_profile_id = self.application_assignments.get(application_name.casefold())
            if application_profile_id is not None:
                return self.profile_overrides.get(application_profile_id, self.default_profile)
        return self.default_profile

    def before_action(self, context: PacingContext) -> PacingDecision:
        profile = self.resolve_profile(
            account_name=context.account_name,
            application_name=context.application_name,
        )
        action = context.action
        if action is not None and action.action_type is InputActionType.CLICK:
            return PacingDecision(
                profile_id=profile.profile_id,
                delay_seconds=self._sample_range(profile.pre_click_delay_range_seconds),
                reason="Micro-delay between pointer movement and click.",
            )
        return PacingDecision(
            profile_id=profile.profile_id,
            delay_seconds=self._sample_range(profile.step_delay_range_seconds),
            reason="Human-like inter-step pause.",
        )

    def after_action(self, context: PacingContext) -> PacingDecision:
        profile = self.resolve_profile(
            account_name=context.account_name,
            application_name=context.application_name,
        )
        action = context.action
        if action is not None and "page_load" in action.context_tags:
            return PacingDecision(
                profile_id=profile.profile_id,
                delay_seconds=self._sample_range(profile.post_page_load_delay_range_seconds),
                reason="Longer recovery pause after page-load event.",
            )
        return PacingDecision(
            profile_id=profile.profile_id,
            delay_seconds=self._sample_range(profile.step_delay_range_seconds),
            reason="Human-like inter-step pause.",
        )

    def typing_delays(
        self,
        text: str,
        *,
        account_name: str | None = None,
        application_name: str | None = None,
    ) -> list[PacingDecision]:
        profile = self.resolve_profile(account_name=account_name, application_name=application_name)
        delays: list[PacingDecision] = []
        for _index, _character in enumerate(text):
            delays.append(
                PacingDecision(
                    profile_id=profile.profile_id,
                    delay_seconds=self._sample_range(profile.typing_key_delay_range_seconds),
                    reason="Per-key typing cadence.",
                )
            )
            if self.random_source.random() < profile.typing_pause_probability:
                delays.append(
                    PacingDecision(
                        profile_id=profile.profile_id,
                        delay_seconds=self._sample_range(profile.typing_pause_range_seconds),
                        reason="Natural typing hesitation.",
                    )
                )
        return delays

    def _sample_range(self, bounds: tuple[float, float]) -> float:
        low, high = bounds
        if high < low:
            low, high = high, low
        if low == high:
            return low
        return self.random_source.uniform(low, high)
