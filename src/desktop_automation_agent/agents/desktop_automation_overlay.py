import sys
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    tk = None
    ttk = None

import threading
import time
try:
    import speech_recognition as sr
except ImportError:
    sr = None
from typing import Callable, Optional, Dict, Any, List
from desktop_automation_agent.models import OrchestratorTaskPlan

class DesktopAutomationOverlay:
    """
    A Cyberpunk-themed priority overlay sidebar for interacting with the Desktop Automation Agent.
    """
    def __init__(self, on_command_received: Callable[[str], None], on_settings_changed: Optional[Callable[[str, str], None]] = None):
        self.on_command_received = on_command_received
        self.on_settings_changed = on_settings_changed
        self.root = None
        self._setup_done = threading.Event()
        self.recognizer = sr.Recognizer() if sr is not None else None

        # UI Colors (Cyberpunk)
        self.BG_COLOR = "#0a0a0a"
        self.FG_COLOR = "#00f0ff" # Neon Cyan
        self.ACCENT_COLOR = "#ff003c" # Neon Magenta/Red
        self.SUCCESS_COLOR = "#39ff14" # Neon Green
        self.FONT_MAIN = ("Consolas", 10)
        self.FONT_BOLD = ("Consolas", 10, "bold")
        self.FONT_TITLE = ("Consolas", 14, "bold")

        # State
        self.is_collapsed = False
        self.active_subtasks: Dict[str, tk.Label] = {}
        self.history_items: List[Dict[str, Any]] = []

    def launch(self):
        """Launches the overlay in a separate thread."""
        thread = threading.Thread(target=self._run_ui, daemon=True)
        thread.start()
        self._setup_done.wait()

    def _run_ui(self):
        if tk is None:
            print("Tkinter not available. Overlay cannot be launched.")
            self._setup_done.set()
            return

        self.root = tk.Tk()
        self.root.title("AGENT_OVERLAY_v3.1")
        self.root.attributes("-topmost", True)
        self.root.geometry("400x700+10+10")
        self.root.configure(bg=self.BG_COLOR)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Style configuration
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Cyber.TFrame", background=self.BG_COLOR)
        style.configure("Cyber.TLabel", background=self.BG_COLOR, foreground=self.FG_COLOR, font=self.FONT_MAIN)
        style.configure("Cyber.TButton", background=self.BG_COLOR, foreground=self.FG_COLOR, bordercolor=self.FG_COLOR, font=self.FONT_BOLD)
        style.map("Cyber.TButton", background=[('active', self.FG_COLOR)], foreground=[('active', self.BG_COLOR)])

        # Main Layout
        self.main_container = tk.Frame(self.root, bg=self.BG_COLOR)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # Header
        header = tk.Frame(self.main_container, bg=self.BG_COLOR, height=40)
        header.pack(fill=tk.X)
        tk.Label(header, text="> AGENT CONTROL", bg=self.BG_COLOR, fg=self.ACCENT_COLOR, font=self.FONT_TITLE).pack(side=tk.LEFT, padx=10)

        self.collapse_btn = tk.Button(header, text="[ - ]", bg=self.BG_COLOR, fg=self.FG_COLOR, relief=tk.FLAT, command=self.toggle_collapse, font=self.FONT_BOLD)
        self.collapse_btn.pack(side=tk.RIGHT, padx=10)

        # Scrollable area for content
        self.canvas = tk.Canvas(self.main_container, bg=self.BG_COLOR, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.main_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=self.BG_COLOR)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self._build_sections()

        # Footer / Resource Metrics
        footer = tk.Frame(self.root, bg=self.BG_COLOR, height=50, bd=1, relief=tk.RAISED)
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        self.resource_var = tk.StringVar(value="TOKENS: 0 | COST: $0.0000")
        tk.Label(footer, textvariable=self.resource_var, bg=self.BG_COLOR, fg=self.SUCCESS_COLOR, font=self.FONT_BOLD).pack(side=tk.LEFT, padx=10)

        self.status_var = tk.StringVar(value="STATUS: READY")
        tk.Label(footer, textvariable=self.status_var, bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_MAIN).pack(side=tk.RIGHT, padx=10)

        self._setup_done.set()
        self.root.mainloop()

    def _build_sections(self):
        # 1. Input Section
        input_frame = tk.LabelFrame(self.scrollable_frame, text=" SYSTEM_INPUT ", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_BOLD, padx=10, pady=10)
        input_frame.pack(fill=tk.X, padx=10, pady=5)

        self.entry = tk.Entry(input_frame, bg=self.BG_COLOR, fg=self.FG_COLOR, insertbackground=self.FG_COLOR, font=self.FONT_MAIN, borderwidth=1)
        self.entry.pack(fill=tk.X, pady=5)
        self.entry.bind("<Return>", lambda e: self._send_text_command())

        btn_row = tk.Frame(input_frame, bg=self.BG_COLOR)
        btn_row.pack(fill=tk.X)
        tk.Button(btn_row, text="EXECUTE", bg=self.BG_COLOR, fg=self.SUCCESS_COLOR, font=self.FONT_BOLD, command=self._send_text_command).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_row, text="VOICE", bg=self.BG_COLOR, fg=self.ACCENT_COLOR, font=self.FONT_BOLD, command=self._listen_voice).pack(side=tk.LEFT, padx=2)

        # 2. Active Tasks Section
        self.tasks_frame = tk.LabelFrame(self.scrollable_frame, text=" ACTIVE_OPERATIONS ", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_BOLD, padx=10, pady=10)
        self.tasks_frame.pack(fill=tk.X, padx=10, pady=5)
        self.tasks_list = tk.Frame(self.tasks_frame, bg=self.BG_COLOR)
        self.tasks_list.pack(fill=tk.X)
        tk.Label(self.tasks_list, text="No active tasks.", bg=self.BG_COLOR, fg="#444", font=self.FONT_MAIN).pack(anchor=tk.W)

        # 3. History Section
        history_frame = tk.LabelFrame(self.scrollable_frame, text=" MISSION_LOG ", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_BOLD, padx=10, pady=10)
        history_frame.pack(fill=tk.X, padx=10, pady=5)
        self.history_list = tk.Frame(history_frame, bg=self.BG_COLOR)
        self.history_list.pack(fill=tk.X)
        tk.Label(self.history_list, text="Log is empty.", bg=self.BG_COLOR, fg="#444", font=self.FONT_MAIN).pack(anchor=tk.W)

        # 4. Settings Section
        settings_frame = tk.LabelFrame(self.scrollable_frame, text=" COGNITIVE_CONFIG ", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_BOLD, padx=10, pady=10)
        settings_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(settings_frame, text="API_KEY:", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_MAIN).pack(anchor=tk.W)
        self.api_key_entry = tk.Entry(settings_frame, bg=self.BG_COLOR, fg=self.FG_COLOR, insertbackground=self.FG_COLOR, show="*", borderwidth=1)
        self.api_key_entry.pack(fill=tk.X, pady=2)

        tk.Label(settings_frame, text="MODEL_SELECT:", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_MAIN).pack(anchor=tk.W)
        self.model_var = tk.StringVar(value="gemini-3.1-flash-lite-preview")
        models = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview"]
        self.model_dropdown = tk.OptionMenu(settings_frame, self.model_var, *models)
        self.model_dropdown.config(bg=self.BG_COLOR, fg=self.FG_COLOR, activebackground=self.FG_COLOR, activeforeground=self.BG_COLOR, font=self.FONT_MAIN, highlightthickness=0)
        self.model_dropdown.pack(fill=tk.X, pady=2)

        tk.Button(settings_frame, text="UPDATE_CONFIG", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_BOLD, command=self._save_settings).pack(fill=tk.X, pady=5)

    def toggle_collapse(self):
        if self.is_collapsed:
            self.root.geometry("400x700")
            self.collapse_btn.config(text="[ - ]")
        else:
            self.root.geometry("400x40")
            self.collapse_btn.config(text="[ + ]")
        self.is_collapsed = not self.is_collapsed

    def _send_text_command(self):
        command = self.entry.get()
        if command.strip():
            self.update_status(f"INITIATING: {command[:20]}...")
            self.entry.delete(0, tk.END)
            threading.Thread(target=self.on_command_received, args=(command,), daemon=True).start()

    def _listen_voice(self):
        if sr is None:
            self.update_status("SPEECH_LIB_MISSING")
            return
        self.update_status("LISTENING...")
        threading.Thread(target=self._voice_recognition_task, daemon=True).start()

    def _voice_recognition_task(self):
        if sr is None: return
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source)
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)

            self.update_status("PROCESSING_AUDIO...")
            command = self.recognizer.recognize_google(audio)
            self.root.after(0, lambda: self.entry.insert(0, command))
            self.root.after(0, self._send_text_command)
        except Exception as e:
            self.update_status(f"VOICE_ERR: {str(e)[:15]}")

    def _save_settings(self):
        if self.on_settings_changed:
            api_key = self.api_key_entry.get()
            model = self.model_var.get()
            self.on_settings_changed(api_key, model)
            messagebox.showinfo("SYSTEM", "Configuration updated successfully.")

    def update_status(self, message: str):
        if self.root and self.status_var:
            self.root.after(0, lambda: self.status_var.set(f"STATUS: {message.upper()}"))

    def update_resource_usage(self, tokens: int, cost: float):
        if self.root and self.resource_var:
            text = f"TOKENS: {tokens} | COST: ${cost:.6f}"
            self.root.after(0, lambda: self.resource_var.set(text))

    def set_active_plan(self, plan: OrchestratorTaskPlan):
        if self.root:
            self.root.after(0, lambda: self._ui_set_active_plan(plan))

    def _ui_set_active_plan(self, plan: OrchestratorTaskPlan):
        for widget in self.tasks_list.winfo_children():
            widget.destroy()

        self.active_subtasks = {}
        for subtask in plan.subtasks:
            lbl = tk.Label(self.tasks_list, text=f"[WAIT] {subtask.description[:40]}", bg=self.BG_COLOR, fg=self.FG_COLOR, font=self.FONT_MAIN, anchor=tk.W)
            lbl.pack(fill=tk.X)
            self.active_subtasks[subtask.subtask_id] = lbl

    def update_subtask_status(self, subtask_id: str, status: str):
        if self.root:
            self.root.after(0, lambda: self._ui_update_subtask_status(subtask_id, status))

    def _ui_update_subtask_status(self, subtask_id: str, status: str):
        if subtask_id in self.active_subtasks:
            lbl = self.active_subtasks[subtask_id]
            color = self.FG_COLOR
            if status == "RUNNING": color = self.ACCENT_COLOR
            elif status == "COMPLETED": color = self.SUCCESS_COLOR
            elif status == "FAILED": color = "red"

            current_text = lbl.cget("text")
            desc = current_text.split("] ", 1)[1] if "] " in current_text else current_text
            lbl.config(text=f"[{status}] {desc}", fg=color)

    def add_history_entry(self, entry: Dict[str, Any]):
        self.history_items.append(entry)
        if self.root:
            self.root.after(0, lambda: self._ui_add_history_entry(entry))

    def _ui_add_history_entry(self, entry: Dict[str, Any]):
        # Clear "empty" label if present
        if len(self.history_items) == 1:
            for widget in self.history_list.winfo_children():
                widget.destroy()

        color = self.SUCCESS_COLOR if entry["succeeded"] else "red"
        status_char = "✓" if entry["succeeded"] else "✗"

        txt = f"{status_char} {entry['command'][:30]} (${entry['cost']:.4f})"
        tk.Label(self.history_list, text=txt, bg=self.BG_COLOR, fg=color, font=self.FONT_MAIN, anchor=tk.W).pack(fill=tk.X)

    def _on_close(self):
        """Handle UI closure and cleanup."""
        if messagebox.askokcancel("QUIT", "Terminate Agent Session?"):
            self.root.destroy()
            sys.exit(0)
