import sys
import os
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
    A high-fidelity Cyberpunk-themed priority overlay sidebar.
    Strictly follows the design spec for colors, geometry, and layout.
    Provides real-time mission logs, resource usage tracking, and active operation status.

    Inputs:
        - on_command_received: Callback function when a new command is issued.
        - on_settings_changed: Optional callback for configuration updates.
    """
    def __init__(self, on_command_received: Callable[[str], None], on_settings_changed: Optional[Callable[[str, str], None]] = None):
        self.on_command_received = on_command_received
        self.on_settings_changed = on_settings_changed
        self.root = None
        self._setup_done = threading.Event()
        self.recognizer = sr.Recognizer() if sr is not None else None

        # Color Palette
        self.CYBER_YELLOW = "#fcee0a"
        self.CYBER_BLACK = "#000000"
        self.CYBER_PINK = "#ff003c"
        self.CYBER_BLUE = "#00f0ff"
        self.CYBER_GREEN = "#00ff41"
        self.CYBER_GRAY = "#1a1a1a"
        self.CYBER_DARK_GRAY = "#0d0d0d"

        # Fonts - Updated weights to "bold italic"
        self.FONT_LOGS = ("JetBrains Mono", 10, "bold")
        self.FONT_INPUT = ("JetBrains Mono", 11)
        self.FONT_BUTTON = ("JetBrains Mono", 12, "bold italic")
        self.FONT_TITLE = ("Inter", 12, "bold italic")
        self.FONT_LOGO = ("Inter", 24, "bold italic")
        self.FONT_VERSION = ("Inter", 10, "bold")

        # Fallbacks for fonts
        self.PRIMARY_MONO = "JetBrains Mono" if self._font_exists("JetBrains Mono") else "Consolas"
        self.DISPLAY_SANS = "Inter" if self._font_exists("Inter") else "Arial"

        # State
        self.is_collapsed = False
        self.active_subtasks: Dict[str, tk.Label] = {}
        self.history_items: List[Dict[str, Any]] = []

    def _font_exists(self, name):
        if tk is None: return False
        try:
            from tkinter import font
            return name in font.families()
        except:
            return False

    def run(self):
        """Alias for launch to satisfy standard entry method requirement."""
        return self.launch()

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
        self.root.title("FI_NEURAL_LINK")
        self.root.attributes("-topmost", True)
        self.root.geometry("400x850+10+10")
        self.root.configure(bg=self.CYBER_BLACK)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Main Container
        self.main_container = tk.Frame(self.root, bg=self.CYBER_BLACK)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        self._build_header()

        # Status Line
        status_line = tk.Frame(self.main_container, bg=self.CYBER_BLACK)
        status_line.pack(fill=tk.X, padx=20, pady=(0, 20))
        tk.Label(status_line, text="((o))", fg=self.CYBER_BLUE, bg=self.CYBER_BLACK, font=(self.PRIMARY_MONO, 12, "bold")).pack(side=tk.LEFT)
        tk.Label(status_line, text=" AI PERSONA ACTIVE", fg=self.CYBER_BLUE, bg=self.CYBER_BLACK, font=(self.PRIMARY_MONO, 11, "bold italic")).pack(side=tk.LEFT)

        # Scrollable area for content
        self.canvas = tk.Canvas(self.main_container, bg=self.CYBER_BLACK, highlightthickness=0)
        self.scrollable_frame = tk.Frame(self.canvas, bg=self.CYBER_BLACK)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw", width=400)
        self.canvas.pack(fill="both", expand=True)

        self._build_sections()

        self._setup_done.set()
        self.root.mainloop()

    def _build_header(self):
        header = tk.Frame(self.main_container, bg=self.CYBER_BLACK, pady=20, padx=20)

        # Resource Usage Overlay in header
        self.usage_lbl = tk.Label(header, text="TOKENS: 0 | COST: $0.000000", fg=self.CYBER_BLUE, bg=self.CYBER_BLACK, font=(self.PRIMARY_MONO, 7, "bold"))
        self.usage_lbl.place(relx=1.0, x=-5, y=0, anchor="ne")

        header.pack(fill=tk.X)

        # Profile Picture Placeholder
        pic_frame = tk.Frame(header, bg=self.CYBER_YELLOW, width=80, height=80)
        pic_frame.pack_propagate(False)
        pic_frame.pack(side=tk.LEFT)
        inner_pic = tk.Frame(pic_frame, bg=self.CYBER_DARK_GRAY)
        inner_pic.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        blue_box = tk.Frame(pic_frame, bg=self.CYBER_BLUE, width=15, height=15)
        blue_box.place(relx=1.0, rely=1.0, x=-5, y=-5, anchor="se")

        # Text Side
        text_side = tk.Frame(header, bg=self.CYBER_BLACK, padx=15)
        text_side.pack(side=tk.LEFT, fill=tk.Y)

        # "FI" Logo with glitch effect (updated fonts)
        logo_frame = tk.Frame(text_side, bg=self.CYBER_BLACK)
        logo_frame.pack(anchor=tk.W)

        # Shadow effects for glitch - All fonts updated to bold italic
        tk.Label(logo_frame, text="FI", fg=self.CYBER_BLUE, bg=self.CYBER_BLACK, font=(self.DISPLAY_SANS, 26, "bold italic")).place(x=2, y=2)
        tk.Label(logo_frame, text="FI", fg=self.CYBER_PINK, bg=self.CYBER_BLACK, font=(self.DISPLAY_SANS, 26, "bold italic")).place(x=-2, y=-1)
        tk.Label(logo_frame, text="FI", fg=self.CYBER_YELLOW, bg=self.CYBER_BLACK, font=(self.DISPLAY_SANS, 26, "bold italic")).pack()

        # Version and Neural line
        version_line = tk.Frame(text_side, bg=self.CYBER_BLACK)
        version_line.pack(anchor=tk.W, pady=2)
        v_tag = tk.Label(version_line, text=" V2.0.77 ", bg=self.CYBER_PINK, fg=self.CYBER_YELLOW, font=(self.DISPLAY_SANS, 9, "bold"))
        v_tag.pack(side=tk.LEFT)
        tk.Label(version_line, text=" NEURAL INTERCONNECT V4", fg=self.CYBER_YELLOW, bg=self.CYBER_BLACK, font=(self.PRIMARY_MONO, 8, "bold")).pack(side=tk.LEFT, padx=5)

    def _create_cyber_section(self, parent, title):
        frame = tk.Frame(parent, bg=self.CYBER_BLACK, padx=20, pady=10)
        frame.pack(fill=tk.X)

        header_frame = tk.Frame(frame, bg=self.CYBER_BLACK)
        header_frame.pack(fill=tk.X)

        tk.Frame(header_frame, bg=self.CYBER_YELLOW, width=30, height=2).pack(side=tk.LEFT, pady=8)
        tk.Label(header_frame, text=f" {title} ", bg=self.CYBER_BLACK, fg=self.CYBER_YELLOW, font=(self.PRIMARY_MONO, 10, "bold")).pack(side=tk.LEFT)
        tk.Frame(header_frame, bg=self.CYBER_YELLOW, height=2).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=8)

        content = tk.Frame(frame, bg=self.CYBER_BLACK, bd=1, highlightbackground="#333", highlightthickness=1)
        content.pack(fill=tk.X, pady=5)
        return content

    def _build_sections(self):
        # 1. SYSTEM_INPUT
        input_container = self._create_cyber_section(self.scrollable_frame, "SYSTEM_INPUT")
        input_container.configure(padx=15, pady=15)

        entry_frame = tk.Frame(input_container, bg=self.CYBER_GRAY, padx=10, pady=10)
        entry_frame.pack(fill=tk.X)

        self.entry = tk.Entry(entry_frame, bg=self.CYBER_GRAY, fg="#666", insertbackground=self.CYBER_YELLOW,
                             font=(self.PRIMARY_MONO, 11, "italic"), borderwidth=0)
        self.entry.insert(0, "ENTER COMMAND...")
        self.entry.bind("<FocusIn>", lambda e: self._on_entry_focus_in())
        self.entry.bind("<FocusOut>", lambda e: self._on_entry_focus_out())
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(entry_frame, text="⌘", fg="#444", bg=self.CYBER_GRAY, font=(self.PRIMARY_MONO, 14)).pack(side=tk.RIGHT)
        self.entry.bind("<Return>", lambda e: self._send_text_command())

        btn_frame = tk.Frame(input_container, bg=self.CYBER_BLACK, pady=15)
        btn_frame.pack(fill=tk.X)

        self._create_poly_button(btn_frame, "EXECUTE", self.CYBER_YELLOW, self.CYBER_BLACK, self._send_text_command, side=tk.LEFT)
        self._create_poly_button(btn_frame, "VOICE", self.CYBER_PINK, self.CYBER_YELLOW, self._listen_voice, side=tk.RIGHT)

        self._create_poly_button(input_container, "EMERGENCY STOP", self.CYBER_PINK, self.CYBER_YELLOW, self._emergency_stop)

        # 2. ACTIVE_OPERATIONS
        ops_container = self._create_cyber_section(self.scrollable_frame, "ACTIVE_OPERATIONS")
        ops_container.configure(padx=15, pady=15)
        self.tasks_list = tk.Frame(ops_container, bg=self.CYBER_BLACK)
        self.tasks_list.pack(fill=tk.X)
        self.empty_ops_lbl = tk.Label(self.tasks_list, text="No active tasks.", bg=self.CYBER_BLACK, fg="#444", font=(self.PRIMARY_MONO, 10, "italic"))
        self.empty_ops_lbl.pack(anchor=tk.W)

        # 3. MISSION_LOG
        log_container = self._create_cyber_section(self.scrollable_frame, "MISSION_LOG")
        log_container.configure(padx=15, pady=15)
        log_box = tk.Frame(log_container, bg=self.CYBER_BLACK, bd=1, highlightbackground="#222", highlightthickness=1, padx=10, pady=10)
        log_box.pack(fill=tk.BOTH, expand=True)
        self.history_list = tk.Frame(log_box, bg=self.CYBER_BLACK)
        self.history_list.pack(fill=tk.X)
        self._add_log_line("SYSTEM STATUS: OPTIMAL")

        # 4. CONFIG
        self.settings_frame = self._create_cyber_section(self.scrollable_frame, "COGNITIVE_CONFIG")
        self.settings_frame.configure(padx=15, pady=15)

        tk.Label(self.settings_frame, text="API_KEY:", bg=self.CYBER_BLACK, fg=self.CYBER_YELLOW, font=(self.PRIMARY_MONO, 8)).pack(anchor=tk.W)
        self.api_key_entry = tk.Entry(self.settings_frame, bg=self.CYBER_GRAY, fg=self.CYBER_YELLOW, show="*", borderwidth=0)
        self.api_key_entry.pack(fill=tk.X, pady=(0, 10))

        tk.Label(self.settings_frame, text="MODEL:", bg=self.CYBER_BLACK, fg=self.CYBER_YELLOW, font=(self.PRIMARY_MONO, 8)).pack(anchor=tk.W)
        self.model_var = tk.StringVar(value="gemini-3.1-flash-lite-preview")
        models = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview"]
        self.model_dropdown = tk.OptionMenu(self.settings_frame, self.model_var, *models)
        self.model_dropdown.config(bg=self.CYBER_GRAY, fg=self.CYBER_YELLOW, activebackground=self.CYBER_YELLOW, activeforeground=self.CYBER_BLACK, font=(self.PRIMARY_MONO, 8), highlightthickness=0, borderwidth=0)
        self.model_dropdown.pack(fill=tk.X, pady=(0, 10))

        self._create_poly_button(self.settings_frame, "UPDATE_CONFIG", self.CYBER_GRAY, self.CYBER_YELLOW, self._save_settings)

    def _create_poly_button(self, parent, text, bg, fg, command, side=None):
        if side is None:
            side = tk.TOP if tk else 0
        c = tk.Canvas(parent, bg=self.CYBER_BLACK, width=170, height=45, highlightthickness=0)
        c.pack(side=side, padx=5)

        points = [0, 0, 170, 0, 170, 30, 155, 45, 0, 45]
        poly = c.create_polygon(points, fill=bg, outline="")

        # Text weight updated to bold italic
        txt = c.create_text(80, 22, text=text, fill=fg, font=(self.PRIMARY_MONO, 12, "bold italic"))

        def on_click(e):
            command()
            c.configure(bg=self.CYBER_BLUE)
            self.root.after(100, lambda: c.configure(bg=self.CYBER_BLACK))

        c.tag_bind(poly, "<Button-1>", on_click)
        c.tag_bind(txt, "<Button-1>", on_click)

    def _on_entry_focus_in(self):
        if self.entry.get() == "ENTER COMMAND...":
            self.entry.delete(0, tk.END)
            self.entry.config(fg=self.CYBER_YELLOW, font=(self.PRIMARY_MONO, 11))

    def _on_entry_focus_out(self):
        if not self.entry.get():
            self.entry.insert(0, "ENTER COMMAND...")
            self.entry.config(fg="#666", font=(self.PRIMARY_MONO, 11, "italic"))

    def _add_log_line(self, text, color=None):
        if color is None: color = self.CYBER_YELLOW
        row = tk.Frame(self.history_list, bg=self.CYBER_BLACK)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=" » ", fg=self.CYBER_PINK, bg=self.CYBER_BLACK, font=self.FONT_LOGS).pack(side=tk.LEFT)
        tk.Label(row, text=text.upper(), fg=color, bg=self.CYBER_BLACK, font=self.FONT_LOGS, anchor=tk.W).pack(side=tk.LEFT)

    def toggle_collapse(self):
        """Toggles the sidebar between collapsed and expanded states."""
        if not self.root:
            return

        if self.is_collapsed:
            self.root.geometry("400x850")
            self.is_collapsed = False
        else:
            self.root.geometry("60x850")
            self.is_collapsed = True

    def _send_text_command(self):
        command = self.entry.get()
        if command.strip() and command != "ENTER COMMAND...":
            self._add_log_line(f"INITIATING: {command[:20]}...")
            self.entry.delete(0, tk.END)
            threading.Thread(target=self.on_command_received, args=(command,), daemon=True).start()

    def _listen_voice(self):
        if sr is None:
            self._add_log_line("SPEECH_LIB_MISSING", "red")
            return
        self._add_log_line("LISTENING...", self.CYBER_BLUE)
        threading.Thread(target=self._voice_recognition_task, daemon=True).start()

    def _voice_recognition_task(self):
        if sr is None: return
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source)
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)

            self._add_log_line("PROCESSING_AUDIO...", self.CYBER_BLUE)
            command = self.recognizer.recognize_google(audio)
            self.root.after(0, lambda: self.entry.delete(0, tk.END))
            self.root.after(0, lambda: self.entry.insert(0, command))
            self.root.after(0, self._send_text_command)
        except Exception as e:
            self._add_log_line(f"VOICE_ERR: {str(e)[:15]}", "red")

    def _save_settings(self):
        if self.on_settings_changed:
            api_key = self.api_key_entry.get()
            model = self.model_var.get()
            self.on_settings_changed(api_key, model)
            self._add_log_line("CONFIG UPDATED", self.CYBER_BLUE)

    def _emergency_stop(self):
        self._add_log_line("EMERGENCY STOP TRIGGERED", self.CYBER_PINK)
        # Notify agent to stop all tasks
        if hasattr(self, 'agent') and self.agent:
            self.agent.stop_all_tasks()
        else:
            # Fallback if agent reference not yet established
            print("EMERGENCY STOP: Terminating process.")
            os._exit(1)

    def update_status(self, message: str):
        if self.root:
            self.root.after(0, lambda: self._add_log_line(message))

    def update_resource_usage(self, tokens: int, cost: float):
        if self.root:
            self.root.after(0, lambda: self._ui_update_resource_usage(tokens, cost))

    def _ui_update_resource_usage(self, tokens: int, cost: float):
        if hasattr(self, 'usage_lbl'):
            self.usage_lbl.config(text=f"TOKENS: {tokens} | COST: ${cost:.6f}")

    def set_active_plan(self, plan: OrchestratorTaskPlan):
        if self.root:
            self.root.after(0, lambda: self._ui_set_active_plan(plan))

    def _ui_set_active_plan(self, plan: OrchestratorTaskPlan):
        self.empty_ops_lbl.pack_forget()
        for widget in self.tasks_list.winfo_children():
            widget.destroy()

        self.active_subtasks = {}
        for subtask in plan.subtasks:
            lbl = tk.Label(self.tasks_list, text=f"[WAIT] {subtask.description[:40]}", bg=self.CYBER_BLACK, fg="#666", font=(self.PRIMARY_MONO, 10), anchor=tk.W)
            lbl.pack(fill=tk.X)
            self.active_subtasks[subtask.subtask_id] = lbl

    def update_subtask_status(self, subtask_id: str, status: str):
        if self.root:
            self.root.after(0, lambda: self._ui_update_subtask_status(subtask_id, status))

    def _ui_update_subtask_status(self, subtask_id: str, status: str):
        if subtask_id in self.active_subtasks:
            lbl = self.active_subtasks[subtask_id]
            color = "#666"
            if status == "RUNNING": color = self.CYBER_BLUE
            elif status == "COMPLETED": color = self.CYBER_GREEN
            elif status == "FAILED": color = self.CYBER_PINK

            current_text = lbl.cget("text")
            desc = current_text.split("] ", 1)[1] if "] " in current_text else current_text
            lbl.config(text=f"[{status}] {desc}", fg=color)

    def add_history_entry(self, entry: Dict[str, Any]):
        color = self.CYBER_GREEN if entry["succeeded"] else self.CYBER_PINK
        status_prefix = "COMPLETED" if entry["succeeded"] else "FAILED"
        if self.root:
            self.root.after(0, lambda: self._add_log_line(f"{status_prefix}: {entry['command'][:20]}", color))

    def _on_close(self):
        if messagebox.askokcancel("QUIT", "Terminate Neural Link?"):
            self.root.destroy()
            sys.exit(0)