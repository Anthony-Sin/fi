import unittest.mock as mock
from desktop_automation_agent.automation.application_launcher import SubprocessApplicationLauncherBackend

def test_launcher_subprocess_calls_popen_correctly():
    """Verifies that SubprocessApplicationLauncherBackend.launch_executable correctly
    calls subprocess.Popen with the expected executable path and arguments."""
    backend = SubprocessApplicationLauncherBackend()

    with mock.patch("subprocess.Popen") as mock_popen:
        result = backend.launch_executable("notepad.exe", ("test.txt",))

        assert result is True
        mock_popen.assert_called_once_with(["notepad.exe", "test.txt"])

def test_launcher_url_calls_webbrowser_open_correctly():
    """Verifies that SubprocessApplicationLauncherBackend.launch_url correctly
    calls webbrowser.open with the expected URL."""
    backend = SubprocessApplicationLauncherBackend()

    with mock.patch("webbrowser.open") as mock_open:
        mock_open.return_value = True
        result = backend.launch_url("https://example.com")

        assert result is True
        mock_open.assert_called_once_with("https://example.com")

def test_launcher_start_menu_calls_powershell_correctly():
    """Verifies that SubprocessApplicationLauncherBackend.launch_start_menu correctly
    calls subprocess.Popen with a PowerShell command to launch a Shell AppsFolder item."""
    backend = SubprocessApplicationLauncherBackend()

    with mock.patch("subprocess.Popen") as mock_popen:
        result = backend.launch_start_menu("Calculator", ())

        assert result is True
        expected_cmd = ["powershell", "-Command", "Start-Process shell:AppsFolder\\Calculator"]
        mock_popen.assert_called_once_with(expected_cmd)
