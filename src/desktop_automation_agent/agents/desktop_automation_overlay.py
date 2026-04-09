try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None
    ttk = None

import threading
try:
    import speech_recognition as sr
except ImportError:
    sr = None
from typing import Callable, Optional

class DesktopAutomationOverlay:
    """
    A priority overlay window for interacting with the Desktop Automation Agent.
    Includes text input and voice command capabilities.
    """
    def __init__(self, on_command_received: Callable[[str], None]):
        self.on_command_received = on_command_received
        self.root = None
        self.status_var = None
        self.entry = None
        self._setup_done = threading.Event()
        self.recognizer = sr.Recognizer() if sr is not None else None

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
        self.root.title("Agent Control")
        self.root.attributes("-topmost", True)
        self.root.geometry("400x150+10+10")  # Positioned at top-left

        # Transparent background effect (some OS support)
        # self.root.attributes("-alpha", 0.9)

        frame = ttk.Frame(self.root, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Agent Instruction:").pack(anchor=tk.W)

        self.entry = ttk.Entry(frame, width=50)
        self.entry.pack(fill=tk.X, pady=5)
        self.entry.bind("<Return>", lambda e: self._send_text_command())

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        self.send_btn = ttk.Button(btn_frame, text="Send", command=self._send_text_command)
        self.send_btn.pack(side=tk.LEFT, padx=2)

        self.voice_btn = ttk.Button(btn_frame, text="🎤 Speak", command=self._listen_voice)
        self.voice_btn.pack(side=tk.LEFT, padx=2)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(frame, textvariable=self.status_var, foreground="blue").pack(anchor=tk.W, pady=5)

        self._setup_done.set()
        self.root.mainloop()

    def _send_text_command(self):
        command = self.entry.get()
        if command.strip():
            self.status_var.set(f"Executing: {command[:30]}...")
            self.entry.delete(0, tk.END)
            # Run execution in background thread to avoid UI freeze
            threading.Thread(target=self.on_command_received, args=(command,), daemon=True).start()

    def _listen_voice(self):
        if sr is None:
            self.status_var.set("SpeechRecognition not installed")
            return
        self.status_var.set("Listening...")
        threading.Thread(target=self._voice_recognition_task, daemon=True).start()

    def _voice_recognition_task(self):
        if sr is None: return
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source)
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)

            self.status_var.set("Processing speech...")
            command = self.recognizer.recognize_google(audio)

            # Update UI from thread
            self.root.after(0, lambda: self.entry.insert(0, command))
            self.root.after(0, self._send_text_command)
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f"Voice Error: {str(e)[:30]}"))

    def update_status(self, message: str):
        if self.root and self.status_var:
            self.root.after(0, lambda: self.status_var.set(message))
