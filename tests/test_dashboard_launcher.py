"""The one-click launcher is bounded, hidden, and never kills existing services."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import launch_dashboard


class DashboardLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "tools").mkdir()
        (self.root / "tools" / "dashboard_server.py").write_text("# fixture")

    def test_running_superwork_is_reused_without_new_process(self):
        with mock.patch.object(launch_dashboard, "dashboard_ready", return_value=True), \
                mock.patch.object(launch_dashboard, "ensure_router", return_value=True), \
                mock.patch.object(launch_dashboard.subprocess, "Popen") as spawn, \
                mock.patch.object(launch_dashboard.webbrowser, "open", return_value=True) as browser:
            result = launch_dashboard.launch_dashboard(self.root)
        self.assertTrue(result["reused"])
        self.assertEqual(result["url"], "http://127.0.0.1:4175/#beranda")
        spawn.assert_not_called()
        browser.assert_called_once()

    def test_conflicting_listener_is_left_running(self):
        with mock.patch.object(launch_dashboard, "dashboard_ready", return_value=False), \
                mock.patch.object(launch_dashboard, "port_open", return_value=True), \
                mock.patch.object(launch_dashboard.subprocess, "Popen") as spawn, \
                self.assertRaisesRegex(ValueError, "4175.*dipakai"):
            launch_dashboard.launch_dashboard(self.root)
        spawn.assert_not_called()

    def test_starts_fixed_server_and_opens_only_after_readiness(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(launch_dashboard, "dashboard_ready", side_effect=[False, False, True]), \
                mock.patch.object(launch_dashboard, "port_open", return_value=False), \
                mock.patch.object(launch_dashboard, "ensure_router", return_value=True), \
                mock.patch.object(launch_dashboard.subprocess, "Popen", return_value=process) as spawn, \
                mock.patch.object(launch_dashboard.webbrowser, "open", return_value=True) as browser, \
                mock.patch.object(launch_dashboard.time, "sleep"):
            result = launch_dashboard.launch_dashboard(self.root)
        self.assertFalse(result["reused"])
        args, kwargs = spawn.call_args
        self.assertEqual(args[0][-3:], [str(self.root / "tools" / "dashboard_server.py"), "--port", "4175"])
        self.assertEqual(kwargs["cwd"], str(self.root))
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse(kwargs.get("shell", False))
        browser.assert_called_once()

    def test_router_failure_still_opens_dashboard_for_recovery(self):
        with mock.patch.object(launch_dashboard, "dashboard_ready", return_value=True), \
                mock.patch.object(launch_dashboard, "ensure_router", return_value=False), \
                mock.patch.object(launch_dashboard.webbrowser, "open", return_value=True) as browser:
            result = launch_dashboard.launch_dashboard(self.root)
        self.assertFalse(result["router_ready"])
        browser.assert_called_once()

    def test_server_early_exit_has_readable_error_and_no_browser(self):
        process = mock.Mock()
        process.poll.return_value = 1
        with mock.patch.object(launch_dashboard, "dashboard_ready", return_value=False), \
                mock.patch.object(launch_dashboard, "port_open", return_value=False), \
                mock.patch.object(launch_dashboard.subprocess, "Popen", return_value=process), \
                mock.patch.object(launch_dashboard.webbrowser, "open") as browser, \
                self.assertRaisesRegex(ValueError, "Dashboard gagal"):
            launch_dashboard.launch_dashboard(self.root)
        browser.assert_not_called()

    def test_server_readiness_timeout_is_bounded(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(launch_dashboard, "dashboard_ready", return_value=False), \
                mock.patch.object(launch_dashboard, "port_open", return_value=False), \
                mock.patch.object(launch_dashboard.subprocess, "Popen", return_value=process), \
                mock.patch.object(launch_dashboard.time, "monotonic", side_effect=[0, 0, 31]), \
                mock.patch.object(launch_dashboard.time, "sleep"), \
                self.assertRaisesRegex(ValueError, "belum siap"):
            launch_dashboard.launch_dashboard(self.root)

    def test_invalid_port_is_rejected_without_commands(self):
        for value in (0, 65536, True, "4175"):
            with self.subTest(port=value), self.assertRaises(ValueError):
                launch_dashboard.launch_dashboard(self.root, port=value)

    def test_browser_error_produces_usable_local_url(self):
        with mock.patch.object(launch_dashboard, "dashboard_ready", return_value=True), \
                mock.patch.object(launch_dashboard, "ensure_router", return_value=True), \
                mock.patch.object(launch_dashboard.webbrowser, "open", side_effect=launch_dashboard.webbrowser.Error), \
                self.assertRaisesRegex(ValueError, "http://127.0.0.1:4175"):
            launch_dashboard.launch_dashboard(self.root)

    def test_shortcut_vbs_and_installer_quote_paths_and_reject_conflict(self):
        root = Path(__file__).resolve().parent.parent
        vbs = (root / "Start-SuperWork.vbs").read_text()
        installer = (root / "tools" / "install_dashboard_shortcut.ps1").read_text()
        self.assertIn("launch_dashboard.py", vbs)
        self.assertIn(", 0, False", vbs)
        self.assertIn('SpecialFolders.Item("Desktop")', installer)
        self.assertIn("wscript.exe", installer)
        self.assertIn("throw", installer)
        self.assertNotIn("ExecutionPolicy Bypass", vbs)

    @unittest.skipUnless(os.name == "nt", "Windows shortcut integration")
    def test_shortcut_install_is_idempotent_and_preserves_conflicting_target(self):
        root = Path(__file__).resolve().parent.parent
        installer = root / "tools" / "install_dashboard_shortcut.ps1"
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(installer),
                   "-DesktopDirectory", str(self.root)]
        kwargs = {"capture_output": True, "text": True, "timeout": 15,
                  "creationflags": subprocess.CREATE_NO_WINDOW}
        first = subprocess.run(command, **kwargs)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((self.root / "SuperWork.lnk").is_file())
        second = subprocess.run(command, **kwargs)
        self.assertEqual(second.returncode, 0, second.stderr)
        path = str(self.root / "SuperWork.lnk").replace("'", "''")
        change = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');"
                  "$s.TargetPath=Join-Path $env:SystemRoot 'System32\\notepad.exe';$s.Save()")
        self.assertEqual(subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", change], **kwargs).returncode, 0)
        conflict = subprocess.run(command, **kwargs)
        self.assertNotEqual(conflict.returncode, 0)
        inspect = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{path}').TargetPath"
        retained = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", inspect], **kwargs)
        self.assertTrue(retained.stdout.strip().lower().endswith("notepad.exe"))


if __name__ == "__main__":
    unittest.main()
