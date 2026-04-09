from desktop_automation_perception.models import (
    AIInterfaceConfiguration,
    AIInterfaceElementSelector,
    AIInterfaceStatus,
    PipelinePauseDecision,
    PipelineResponseAction,
    PipelineStatus,
    PromptPipelineStep,
)
from desktop_automation_perception.prompt_pipeline_runner import PromptPipelineRunner


class FakeTemplateManager:
    def __init__(self, templates):
        self.templates = templates

    def render_template(self, name, variables):
        template = self.templates[name]
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace("${" + key + "}", value)
        if "${" in rendered:
            missing = rendered.split("${", 1)[1].split("}", 1)[0]
            return type("RenderResult", (), {"succeeded": False, "rendered_prompt": None, "reason": f"Missing template variable: {missing}"})()
        return type("RenderResult", (), {"succeeded": True, "rendered_prompt": rendered, "reason": None})()


class FakeNavigator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def navigate(self, *, prompt, interface, injection_method):
        self.calls.append((prompt, interface.interface_name, injection_method))
        response_text, succeeded, reason = self.responses.pop(0)
        return type(
            "NavigationResult",
            (),
            {
                "succeeded": succeeded,
                "response_text": response_text,
                "reason": reason,
                "status": AIInterfaceStatus.COMPLETED if succeeded else AIInterfaceStatus.ERROR,
            },
        )()


def make_interface(name):
    return AIInterfaceConfiguration(
        interface_name=name,
        input_selector=AIInterfaceElementSelector(name="Message", role="edit"),
        stable_polls_required=1,
        response_timeout_seconds=1.0,
    )


def test_prompt_pipeline_runner_chains_response_into_next_step_and_logs_timings():
    monotonic_values = iter([0.0, 0.5, 1.0, 1.8]).__next__
    runner = PromptPipelineRunner(
        template_manager=FakeTemplateManager(
            {
                "step-1": "Draft a title for ${topic}",
                "step-2": "Expand this title into a summary: ${title}",
            }
        ),
        navigator=FakeNavigator(
            [
                ("Edge Automation", True, None),
                ("Edge Automation summary", True, None),
            ]
        ),
        monotonic_fn=monotonic_values,
    )

    result = runner.run(
        [
            PromptPipelineStep(
                step_id="s1",
                interface=make_interface("chatgpt"),
                template_name="step-1",
                template_variables={"topic": "desktop AI"},
                output_variable_name="title",
            ),
            PromptPipelineStep(
                step_id="s2",
                interface=make_interface("claude"),
                template_name="step-2",
                response_action=PipelineResponseAction.STORE_AS,
                output_variable_name="summary",
            ),
        ]
    )

    assert result.succeeded is True
    assert result.status is PipelineStatus.COMPLETED
    assert result.final_variables["title"] == "Edge Automation"
    assert result.final_variables["summary"] == "Edge Automation summary"
    assert runner.navigator.calls[1][0] == "Expand this title into a summary: Edge Automation"
    assert result.logs[0].execution_time_seconds == 0.5
    assert result.logs[1].execution_time_seconds == 0.8


def test_prompt_pipeline_runner_fails_when_response_pattern_does_not_match():
    runner = PromptPipelineRunner(
        template_manager=FakeTemplateManager({"step": "Return JSON for ${subject}"}),
        navigator=FakeNavigator([("plain text only", True, None)]),
        monotonic_fn=iter([0.0, 0.3]).__next__,
    )

    result = runner.run(
        [
            PromptPipelineStep(
                step_id="s1",
                interface=make_interface("chatgpt"),
                template_name="step",
                template_variables={"subject": "report"},
                expected_response_pattern=r"^\{.+\}$",
            )
        ]
    )

    assert result.succeeded is False
    assert result.status is PipelineStatus.FAILED
    assert result.reason == "Response did not match the expected pattern."
    assert result.logs[0].matched_expected_pattern is False


def test_prompt_pipeline_runner_pauses_for_human_review():
    review_requests = []
    runner = PromptPipelineRunner(
        template_manager=FakeTemplateManager({"step": "Review ${item}"}),
        navigator=FakeNavigator([("Needs approval", True, None)]),
        review_callback=lambda request: review_requests.append(request) or type(
            "ReviewResult",
            (),
            {"decision": PipelinePauseDecision.REJECTED, "reason": "Waiting for analyst approval."},
        )(),
        monotonic_fn=iter([0.0, 0.2]).__next__,
    )

    result = runner.run(
        [
            PromptPipelineStep(
                step_id="s1",
                interface=make_interface("chatgpt"),
                template_name="step",
                template_variables={"item": "draft"},
                allow_human_review=True,
            )
        ]
    )

    assert result.succeeded is False
    assert result.status is PipelineStatus.PAUSED
    assert result.reason == "Waiting for analyst approval."
    assert review_requests[0].response_text == "Needs approval"
    assert result.logs[0].review_decision is PipelinePauseDecision.REJECTED


def test_prompt_pipeline_runner_supports_append_action():
    runner = PromptPipelineRunner(
        template_manager=FakeTemplateManager({"step": "Append ${seed}"}),
        navigator=FakeNavigator([("second line", True, None)]),
        monotonic_fn=iter([0.0, 0.1]).__next__,
    )

    result = runner.run(
        [
            PromptPipelineStep(
                step_id="s1",
                interface=make_interface("chatgpt"),
                template_name="step",
                template_variables={"seed": "value"},
                response_action=PipelineResponseAction.APPEND_TO_VARIABLE,
                action_target_variable="history",
            )
        ],
        initial_variables={"history": "first line"},
    )

    assert result.succeeded is True
    assert result.final_variables["history"] == "first line\nsecond line"
