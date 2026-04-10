# FI_NEURAL_LINK: Desktop Automation Agent

FI_NEURAL_LINK is your personal autonomous desktop agent. It allows you to control your entire computer through simple natural language commands. From switching accounts and filling forms to complex multi-app workflows, FI_NEURAL_LINK handles the "grind" so you can focus on what matters.

## 🚀 Key Features

- **🗣️ Natural Language Control**: Tell your computer what to do in plain English.
- **🧠 Autonomous Brain**: Decomposes complex goals into logical steps.
- **👁️ Vision & Perception**: Sees your screen like a human using OCR and AI Vision.
- **⚡ Cyberpunk Dashboard**: A high-fidelity, always-on-top overlay for real-time control.
- **🛡️ Safety First**: Emergency stop, rate limiting, and secure credential handling.
- **🤖 Gemini Integration**: Powered by Google Gemini for high-reliability problem solving.

## 🛠️ Getting Started

### Prerequisites
- Windows OS (recommended)
- Python 3.11+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) installed and in PATH.

### Installation
1. Clone the repository.
2. Install dependencies:
   ```powershell
   python -m pip install -e .
   ```

### Launching the Agent
Run the main entry point:
```powershell
python start.py
```
This will launch the Cyberpunk dashboard.

## 🎮 Using the Dashboard

1. **Configure API**: Click on **COGNITIVE_CONFIG**, enter your Gemini API key, and select your preferred model.
2. **Issue Commands**: Type your command in the **SYSTEM_INPUT** field (e.g., *"Open Chrome and search for the latest news"*) and hit **EXECUTE**.
3. **Voice Control**: Click **VOICE** to issue commands using your microphone.
4. **Emergency Stop**: If the agent is doing something unexpected, hit the **EMERGENCY STOP** button immediately.

## 🧠 Core Specialist Modules

FI_NEURAL_LINK uses specialized "agents" for different tasks:
- **Application Launcher**: Opens any installed app or URL.
- **Form Automation**: Automatically fills out complex web and desktop forms.
- **AI Navigator**: Interacts directly with LLM interfaces like ChatGPT or Claude.
- **Navigation Sequencer**: Handles precise clicking, scrolling, and verification.

## 🛡️ Safety & Reliability

- **Anti-Loop Detection**: Prevents the agent from getting stuck in repetitive failures.
- **AI Vision Fallback**: If standard OCR fails, the agent uses Gemini Vision to "see" the UI elements.
- **Rate Limiting**: Protects your API quota by limiting calls per minute.

## 🤝 Contribution

We welcome contributions! Please see our development guidelines for more information on how to add new specialists or improve the core brain.

---
*Built for the future. Controlled by you.*
