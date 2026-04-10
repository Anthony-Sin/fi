from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from desktop_automation_agent._time import utc_now
from desktop_automation_agent.agents.hierarchical_task_decomposer import HierarchicalTaskDecomposer
from desktop_automation_agent.agents.desktop_automation_overlay import DesktopAutomationOverlay
from desktop_automation_agent.agents.orchestrator_agent_core import OrchestratorAgentCore
from desktop_automation_agent.agents.specialist_agent_router import SpecialistAgentRouter
from desktop_automation_agent.models import (
    OrchestratorAgentResult,
    OrchestratorSubtask,
    OrchestratorSubtaskResult,
    OrchestratorSubtaskStatus,
    SpecialistAgentRecord,
)


from desktop_automation_agent.observability.session_logger import SessionLogger

@dataclass(slots=True)
class DesktopAutomationAgent:
    """
    Unified agent that orchestrates complex desktop automation tasks.
    It decomposes high-level requests into subtasks and routes them to specialized modules.
    This agent acts as the central brain, coordinating perception, action, and account management.

    Inputs:
        - orchestrator: Core engine for planning and execution.
        - router: Component for routing subtasks to specialists.
        - overlay: UI component for real-time interaction and status.
    Outputs:
        - OrchestratorAgentResult: Comprehensive result of the task execution.
    """
    orchestrator: OrchestratorAgentCore = field(default_factory=OrchestratorAgentCore)
    router: Optional[SpecialistAgentRouter] = None
    specialists: Dict[str, Any] = field(default_factory=dict)
    overlay: Optional[DesktopAutomationOverlay] = None
    command_history: List[Dict[str, Any]] = field(default_factory=list)
    api_key: str = ""
    selected_model: str = "gemini-3.1-flash-lite-preview"
    total_tokens: int = 0
    total_cost: float = 0.0
    _stop_requested: bool = False
    _ai_provider: Optional[Any] = None
    _logger: SessionLogger = field(default_factory=SessionLogger)

    def __post_init__(self):
        if self.router is None:
            # Internal router state
            self.router = SpecialistAgentRouter(storage_path="data/agent_routing.json")

        if self.orchestrator.task_decomposer is None:
            self.orchestrator.task_decomposer = HierarchicalTaskDecomposer()

        if self.overlay is None:
            self.overlay = DesktopAutomationOverlay(
                on_command_received=self.execute_from_overlay,
                on_settings_changed=self.update_settings
            )
        self._load_config() # FI_NEURAL_LINK_VERIFIED

    def _load_config(self):
        """Loads API key and model selection from secure local storage."""
        import json
        from pathlib import Path
        from desktop_automation_agent.accounts.credential_vault import CredentialVault
        from desktop_automation_agent.models import CredentialKind

        vault = CredentialVault(storage_path="data/vault.json")
        config_path = Path("data/cognitive_config.json")

        # Load non-sensitive model selection
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                self.selected_model = data.get("selected_model", "gemini-3.1-flash-lite-preview")
            except Exception as e:
                print(f"DEBUG: Failed to load cognitive config: {e}")

        # Load sensitive API Key from vault
        try:
            res = vault.retrieve_credential("system", CredentialKind.TOKEN)
            if res.succeeded and res.value:
                self.api_key = res.value
                self.update_settings(self.api_key, self.selected_model)
        except Exception as e:
            print(f"DEBUG: Failed to load API Key from vault: {e}")

    def _save_config(self):
        """Saves API key securely and model selection to local storage."""
        import json
        from pathlib import Path
        from desktop_automation_agent.accounts.credential_vault import CredentialVault
        from desktop_automation_agent.models import CredentialKind

        vault = CredentialVault(storage_path="data/vault.json")
        config_path = Path("data/cognitive_config.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Store sensitive API Key in vault
        if self.api_key:
            vault.store_credential(account_identifier="system", kind=CredentialKind.TOKEN, value=self.api_key)

        # Store non-sensitive model selection
        data = {
            "selected_model": self.selected_model
        }
        config_path.write_text(json.dumps(data, indent=2))

    def register_specialist(
        self,
        module_name: str,
        instance: Any,
        capabilities: Optional[List[str]] = None
    ):
        """
        Registers a specialist module instance and its capabilities.

        Common modules include:
        - application_launcher
        - account_rotation_orchestrator
        - ai_interface_navigator
        - multi_application_workflow_coordinator
        - navigation_step_sequencer
        - form_automation
        """
        self.specialists[module_name] = instance
        if self.router:
            self.router.register_agent(
                agent_name=module_name,
                capabilities=capabilities or [module_name],
                module_reference=module_name
            )

    def run(self, task_description: str) -> OrchestratorAgentResult:
        """Alias for execute to satisfy standard entry method requirement."""
        return self.execute(task_description)

    def handle(self, task_description: str) -> OrchestratorAgentResult:
        """Alias for execute to satisfy standard entry method requirement."""
        return self.execute(task_description)

    def execute(self, task_description: str) -> OrchestratorAgentResult: # FI_NEURAL_LINK_VERIFIED
        """
        Decomposes and executes a high-level task description.

        Example: "Switch to my work account, open ChatGPT, and ask 'What is the weather in Tokyo?'"
        """
        self._logger.log("TASK_START", {"description": task_description})
        self._stop_requested = False
        self.overlay.update_status("Decomposing task...")

        # Inject API key and model into task decomposer if it's AI-based (future enhancement)
        # For now, it uses the hardcoded regex-based decomposer

        plan = self.orchestrator.create_plan(task_description)

        # Notify overlay about the plan
        self.overlay.set_active_plan(plan)

        def monitored_executor(subtask: OrchestratorSubtask, context: Dict[str, str]) -> Any:
            if self._stop_requested:
                return OrchestratorSubtaskResult(
                    subtask_id=subtask.subtask_id,
                    status=OrchestratorSubtaskStatus.FAILED,
                    responsible_module=subtask.responsible_module,
                    reason="Task stopped by user."
                )

            self.overlay.update_subtask_status(subtask.subtask_id, "RUNNING")
            res = self._dispatch_with_router(subtask, context)

            # Calculate token usage and cost for the subtask
            tokens_used = 0
            if self.api_key and self._ai_provider:
                try:
                    tokens_used = self._ai_provider.get_token_count(subtask.description)
                except Exception:
                    tokens_used = len(subtask.description) // 4 # Fallback estimate

            cost_per_token = 0.0000001 if "lite" in self.selected_model.lower() else 0.0000005
            subtask_cost = tokens_used * cost_per_token

            self.total_tokens += tokens_used
            self.total_cost += subtask_cost

            self.overlay.update_resource_usage(self.total_tokens, self.total_cost)

            status = "COMPLETED" if getattr(res, 'succeeded', True) else "FAILED"
            self.overlay.update_subtask_status(subtask.subtask_id, status)
            return res

        start_tokens = self.total_tokens
        start_cost = self.total_cost

        result = self.orchestrator.execute_plan(plan, executor=monitored_executor)

        history_entry = {
            "timestamp": str(utc_now()),
            "command": task_description,
            "succeeded": result.succeeded,
            "reason": result.reason,
            "tokens": self.total_tokens - start_tokens,
            "cost": self.total_cost - start_cost
        }
        self.command_history.append(history_entry)
        self.overlay.add_history_entry(history_entry)

        if result.succeeded:
            self.overlay.update_status("Task completed successfully")
        else:
            self.overlay.update_status(f"Failed: {result.reason[:30]}")

        self._logger.log("TASK_END", {"succeeded": result.succeeded, "reason": result.reason})
        return result

    def update_settings(self, api_key: str, model_name: str):
        """Updates agent settings from the overlay."""
        self.api_key = api_key
        self.selected_model = model_name
        self._save_config() # FI_NEURAL_LINK_VERIFIED

        from desktop_automation_agent.ai.gemini_provider import GeminiProvider
        if not self._ai_provider:
            self._ai_provider = GeminiProvider(api_key=self.api_key, model_name=self.selected_model)
        else:
            self._ai_provider.update_config(api_key=self.api_key, model_name=self.selected_model)

        # Update specialists with AI fallback reference
        for specialist in self.specialists.values():
            if hasattr(specialist, 'ocr_extractor') and specialist.ocr_extractor:
                specialist.ocr_extractor.ai_fallback = self._ai_provider
            if hasattr(specialist, 'verifier') and specialist.verifier and hasattr(specialist.verifier, 'ocr_extractor'):
                specialist.verifier.ocr_extractor.ai_fallback = self._ai_provider

        if self.overlay:
            self.overlay.api_key_entry.delete(0, 'end')
            self.overlay.api_key_entry.insert(0, self.api_key)
            self.overlay.model_var.set(self.selected_model)

        print(f"DEBUG: Settings updated - Model: {self.selected_model}")

    def execute_from_overlay(self, command: str):
        """Callback for overlay interaction."""
        return self.execute(command)

    def run_interactive(self):
        """Launches the agent with its UI overlay."""
        self.overlay.agent = self
        self.overlay.launch()

    def stop_all_tasks(self):
        """Signals the agent to stop all currently running tasks."""
        self._stop_requested = True
        self.overlay.update_status("Stopping all tasks...")

    def _solve_with_ai(self, subtask: OrchestratorSubtask, context: Dict[str, str]) -> OrchestratorSubtaskResult:
        """Fallback mechanism using Gemini to solve a task when specialists are missing or fail."""
        self.overlay.update_subtask_status(subtask.subtask_id, "RUNNING")
        if not self.api_key:
            return OrchestratorSubtaskResult(
                subtask_id=subtask.subtask_id,
                status=OrchestratorSubtaskStatus.FAILED,
                responsible_module=subtask.responsible_module,
                reason="AI fallback failed: No API key configured."
            )

        from desktop_automation_agent.ai.gemini_provider import GeminiProvider

        self.overlay.update_status(f"AI solving: {subtask.subtask_id}")
        ai = self._ai_provider or GeminiProvider(api_key=self.api_key, model_name=self.selected_model)

        # Capture screen for context
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
        except Exception:
            # Fallback for environments without display (like CI/tests)
            from PIL import Image
            screenshot = Image.new('RGB', (1920, 1080), color = (73, 109, 137))

        prompt = (
            f"You are a desktop automation assistant. Your current subtask is: '{subtask.description}'.\n"
            "The user wants you to execute this task on their desktop.\n"
            "Analyze the provided screenshot and determine the exact steps to take.\n"
            "YOU MUST RETURN A JSON OBJECT ONLY.\n"
            "Supported actions:\n"
            "- {\"type\": \"click\", \"x\": integer, \"y\": integer}\n"
            "- {\"type\": \"type\", \"text\": \"string\"}\n"
            "- {\"type\": \"hotkey\", \"keys\": [\"key1\", \"key2\"]}\n"
            "- {\"type\": \"press\", \"key\": \"string\"}\n"
            "- {\"type\": \"wait\", \"seconds\": float}\n\n"
            "Example response format:\n"
            "{\n"
            "  \"succeeded\": true,\n"
            "  \"actions\": [\n"
            "    {\"type\": \"hotkey\", \"keys\": [\"win\", \"r\"]},\n"
            "    {\"type\": \"wait\", \"seconds\": 0.5},\n"
            "    {\"type\": \"type\", \"text\": \"chrome https://aistudio.google.com/\\n\"}\n"
            "  ],\n"
            "  \"summary\": \"Opened Chrome via Win+R and navigated to URL\"\n"
            "}\n"
            "If the task cannot be done, set succeeded to false and provide a reason."
        )

        try:
            self._logger.log("AI_PROMPT", {"subtask": subtask.subtask_id, "prompt": prompt})
            response_text = ai.analyze_image(prompt, screenshot)
            self._logger.log("AI_RESPONSE", {"subtask": subtask.subtask_id, "response": response_text})
            # Simple attempt to parse JSON from AI response
            import json
            import re

            # Find the outermost JSON object
            start_index = response_text.find('{')
            end_index = response_text.rfind('}')

            if start_index != -1 and end_index != -1 and end_index > start_index:
                json_payload = response_text[start_index:end_index+1]
                try:
                    res_data = json.loads(json_payload)
                    succeeded = res_data.get("succeeded", True)

                    if succeeded and "actions" in res_data:
                        self._execute_ai_actions(res_data["actions"])

                    return OrchestratorSubtaskResult(
                        subtask_id=subtask.subtask_id,
                        status=OrchestratorSubtaskStatus.COMPLETED if succeeded else OrchestratorSubtaskStatus.FAILED,
                        responsible_module="ai_vision_fallback",
                        produced_outputs={f"{subtask.subtask_id}_result": res_data.get("summary", "AI execution completed")},
                        reason=res_data.get("reason")
                    )
                except json.JSONDecodeError as je:
                    self._logger.log("AI_PARSE_ERROR", {"error": str(je), "raw_response": response_text})
                    return OrchestratorSubtaskResult(
                        subtask_id=subtask.subtask_id,
                        status=OrchestratorSubtaskStatus.FAILED,
                        responsible_module="ai_vision_fallback",
                        reason=f"Failed to parse AI response JSON: {str(je)}"
                    )
            else:
                self._logger.log("AI_NO_JSON_ERROR", {"raw_response": response_text})
                return OrchestratorSubtaskResult(
                    subtask_id=subtask.subtask_id,
                    status=OrchestratorSubtaskStatus.FAILED,
                    responsible_module="ai_vision_fallback",
                    reason="AI response did not contain a valid JSON block."
                )
        except Exception as e:
            return OrchestratorSubtaskResult(
                subtask_id=subtask.subtask_id,
                status=OrchestratorSubtaskStatus.FAILED,
                responsible_module="ai_vision_fallback",
                reason=f"AI fallback error: {str(e)}"
            )

        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.FAILED,
            responsible_module="ai_vision_fallback",
            reason="AI fallback could not determine a solution."
        )

    def _execute_ai_actions(self, actions: List[Dict[str, Any]]):
        """Executes a list of actions returned by Gemini."""
        import pyautogui
        import time

        for action in actions:
            if self._stop_requested: break

            action_type = action.get("type")
            self._logger.log("ACTION_EXECUTE", {"action": action})
            try:
                if action_type == "click":
                    pyautogui.click(x=action.get("x"), y=action.get("y"))
                elif action_type == "type":
                    pyautogui.write(action.get("text"))
                elif action_type == "hotkey":
                    keys = action.get("keys", [])
                    if keys: pyautogui.hotkey(*keys)
                elif action_type == "press":
                    pyautogui.press(action.get("key"))
                elif action_type == "wait":
                    time.sleep(float(action.get("seconds", 1.0)))
            except Exception as e:
                print(f"DEBUG: AI Action failed: {e}")

    def _translate_instruction_to_request(self, module_name: str, instruction: str) -> Dict[str, Any]:
        """Uses Gemini to translate a natural language instruction into structured data for a specialist."""
        if not self.api_key or not self._ai_provider:
             # Fallback to simple description if no AI is available
             if module_name == "ai_interface_navigator": return {"prompt": instruction}
             if module_name == "application_launcher":
                 from desktop_automation_agent.models import ApplicationLaunchRequest, ApplicationLaunchMode
                 return {"request": ApplicationLaunchRequest(application_name=instruction, launch_mode=ApplicationLaunchMode.START_MENU)}
             return {"instruction": instruction}

        prompt = (
            f"Translate the following desktop automation instruction for the '{module_name}' module into a JSON request.\n"
            f"Instruction: '{instruction}'\n\n"
            f"Module Schema Hints:\n"
            "- application_launcher: { \"request\": { \"application_name\": \"...\", \"launch_mode\": \"start_menu/url/executable\" } }\n"
            "- ai_interface_navigator: { \"prompt\": \"...\", \"interface\": null }\n"
            "- form_automation: { \"fields\": [ { \"label\": \"...\", \"value\": \"...\" } ] }\n"
            "- navigation_step_sequencer: { \"steps\": [ { \"action_type\": \"click/type/wait\", \"target_description\": \"...\" } ] }\n\n"
            "Return ONLY the JSON object."
        )

        try:
            response = self._ai_provider.generate_text(prompt)
            import json, re

            # Find the outermost JSON object
            start_index = response.find('{')
            end_index = response.rfind('}')

            if start_index != -1 and end_index != -1 and end_index > start_index:
                json_payload = response[start_index:end_index+1]
                try:
                    data = json.loads(json_payload)
                except json.JSONDecodeError as je:
                    self._logger.log("AI_TRANSLATE_PARSE_ERROR", {"error": str(je), "raw_response": response})
                    return {"instruction": instruction}

                # Handle specific model conversions (like Enum strings to Enums)
                if module_name == "application_launcher" and "request" in data:
                    from desktop_automation_agent.models import ApplicationLaunchRequest, ApplicationLaunchMode
                    req = data["request"]

                    # URL Detection Override
                    url = req.get("url")
                    launch_mode_str = req.get("launch_mode", "start_menu")
                    if "http" in instruction and not url:
                        import re
                        urls = re.findall(r'https?://\S+', instruction)
                        if urls:
                            url = urls[0].strip(" ,.;")
                            launch_mode_str = "url"

                    data["request"] = ApplicationLaunchRequest(
                        application_name=req.get("application_name", instruction),
                        launch_mode=ApplicationLaunchMode(launch_mode_str),
                        url=url,
                        executable_path=req.get("executable_path")
                    )
                if module_name == "form_automation" and "fields" in data:
                    from desktop_automation_agent.models import FormFieldValue
                    data["fields"] = [FormFieldValue(**f) for f in data["fields"]]
                if module_name == "navigation_step_sequencer" and "steps" in data:
                    from desktop_automation_agent.models import NavigationStep, NavigationStepActionType
                    data["steps"] = [NavigationStep(
                        step_id=f"step-{i}",
                        action_type=NavigationStepActionType(s.get("action_type", "click")),
                        target_description=s.get("target_description", ""),
                        input_data=s.get("input_data", {})
                    ) for i, s in enumerate(data["steps"])]
                return data
        except Exception as e:
            print(f"DEBUG: Cognitive translation failed: {e}")

        return {"instruction": instruction}

    def _dispatch_with_router(self, subtask: OrchestratorSubtask, context: Dict[str, str]) -> Any:
        """Internal executor that uses the router to find and invoke the correct specialist."""
        if not self.router:
            return self._invoke_specialist(subtask.responsible_module, subtask, context)

        routing_result = self.router.route_subtask(
            subtask,
            context=context,
            dispatcher=self._router_dispatcher
        )

        if routing_result.subtask_result:
            return routing_result.subtask_result

        # If router failed to find a match, try invoking directly (which has fallback logic)
        if not routing_result.succeeded and routing_result.decision and routing_result.decision.escalated:
             return self._invoke_specialist(subtask.responsible_module, subtask, context)

        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.FAILED,
            responsible_module=subtask.responsible_module,
            reason=routing_result.reason or f"Routing failed for {subtask.responsible_module}"
        )

    def _router_dispatcher(
        self,
        agent_record: SpecialistAgentRecord,
        subtask: OrchestratorSubtask,
        context: Dict[str, str]
    ) -> Any:
        """Callback for the router to invoke the specialist instance."""
        module_name = agent_record.module_reference or agent_record.agent_name
        print(f"DEBUG: Router dispatching to {module_name} for subtask {subtask.subtask_id}")
        return self._invoke_specialist(module_name, subtask, context)

    def _invoke_specialist(self, module_name: str, subtask: OrchestratorSubtask, context: Dict[str, str]) -> Any:
        """Invokes the specialist instance based on its type and standard method names."""
        # Normalize module name as it might come from router registry
        specialist = self.specialists.get(module_name)
        if not specialist:
            # Attempt to solve with AI if specialist is missing
            return self._solve_with_ai(subtask, context)

        try:
            # Cognitive Translation: Convert natural language to specialist request objects
            request_data = self._translate_instruction_to_request(module_name, subtask.description)

            if module_name == "ai_interface_navigator" and hasattr(specialist, "navigate"):
                 return specialist.navigate(**request_data)

            if module_name == "application_launcher" and hasattr(specialist, "launch"):
                 return specialist.launch(**request_data)

            if module_name == "form_automation" and hasattr(specialist, "fill_form"):
                 return specialist.fill_form(**request_data)

            if module_name == "navigation_step_sequencer" and hasattr(specialist, "run"):
                 return specialist.run(**request_data)

            if hasattr(specialist, "execute"):
                return specialist.execute(subtask.description)

            if hasattr(specialist, "run"):
                return specialist.run(subtask.description)

            if callable(specialist):
                return specialist(subtask, context)

        except Exception as e:
            return OrchestratorSubtaskResult(
                subtask_id=subtask.subtask_id,
                status=OrchestratorSubtaskStatus.FAILED,
                responsible_module=module_name,
                reason=f"Error invoking specialist '{module_name}': {str(e)}"
            )

        return OrchestratorSubtaskResult(
            subtask_id=subtask.subtask_id,
            status=OrchestratorSubtaskStatus.FAILED,
            responsible_module=module_name,
            reason=f"Specialist module '{module_name}' does not have a recognized execution method."
        )
