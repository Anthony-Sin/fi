import sys
import os
import time

# Ensure src is in PYTHONPATH
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

try:
    from desktop_automation_agent import DesktopAutomationAgent
except ImportError:
    # Fallback for different execution contexts
    from src.desktop_automation_agent import DesktopAutomationAgent

def main():
    print("--- DEKTOP AUTOMATION AGENT v3.1 ---")
    print("Initializing core brain...")

    agent = DesktopAutomationAgent()

    print("Launching Cyberpunk Interactive Dashboard...")
    agent.run_interactive()

    print("\n[SYSTEM] Dashboard is active.")
    print("[SYSTEM] Use the overlay to issue commands or configure settings.")
    print("[SYSTEM] Press Ctrl+C in this terminal to terminate the agent session.")

    try:
        # Keep the main thread alive while the overlay runs in background
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Shutting down agent...")
        # Optional: Add cleanup logic here if needed

if __name__ == "__main__":
    main()
