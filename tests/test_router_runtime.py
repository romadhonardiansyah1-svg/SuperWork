"""Process ownership and safe router lifecycle, without touching the real router."""
import json
from pathlib import Path
import subprocess
import socket
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from tools import router_runtime


class RouterRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = router_runtime.RouterRuntime(root=self.root)

    def test_external_router_is_reused_and_never_stopped_or_restarted(self):
        with mock.patch.object(self.runtime, "_probe", return_value=True), \
                mock.patch.object(router_runtime.subprocess, "Popen") as spawn:
            for result in (self.runtime.start(), self.runtime.stop(), self.runtime.restart()):
                self.assertEqual(result["status"], "ready")
                self.assertFalse(result["managed"])
                self.assertFalse(result["can_stop"])
            spawn.assert_not_called()

    def test_port_owned_by_other_service_is_not_replaced(self):
        with mock.patch.object(self.runtime, "_probe", return_value=False), \
                mock.patch.object(self.runtime, "_port_open", return_value=True), \
                mock.patch.object(router_runtime.subprocess, "Popen") as spawn:
            result = self.runtime.start()
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["can_stop"])
            self.assertIn("dipakai", result["message"])
            spawn.assert_not_called()

    def test_start_uses_direct_bundled_server_not_cli_kill_helpers(self):
        command = ["node.exe", "C:/9router/app/server.js"]
        process = mock.Mock(pid=9876)
        process.poll.return_value = None
        identity = {"pid": 9876, "created": "12345", "executable": "C:/node.exe"}
        with mock.patch.object(self.runtime, "_probe", side_effect=[False, True, True]), \
                mock.patch.object(self.runtime, "_port_open", return_value=False), \
                mock.patch.object(router_runtime, "router_command", return_value=command), \
                mock.patch.object(router_runtime, "router_environment", return_value=dict(router_runtime.os.environ)), \
                mock.patch.object(router_runtime, "process_identity", return_value=identity), \
                mock.patch.object(router_runtime.subprocess, "Popen", return_value=process) as spawn:
            result = self.runtime.start()
            self.assertEqual(result["status"], "ready")
            self.assertTrue(result["managed"])
            self.assertTrue(result["can_stop"])
            args, kwargs = spawn.call_args
            self.assertEqual(args[0], command)
            self.assertEqual(kwargs["env"]["HOSTNAME"], "127.0.0.1")
            self.assertEqual(kwargs["env"]["PORT"], "20128")
            self.assertFalse(kwargs.get("shell", False))
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            saved = json.loads(self.runtime.record_path.read_text())
            self.assertEqual(saved, identity)

    def test_stops_only_own_child_and_clears_record(self):
        process = mock.Mock(pid=9876)
        process.poll.return_value = None
        self.runtime._process = process
        self.runtime.record_path.parent.mkdir(parents=True)
        self.runtime.record_path.write_text('{"pid":9876}')
        with mock.patch.object(self.runtime, "_probe", return_value=False):
            result = self.runtime.stop()
        self.assertEqual(result["status"], "stopped")
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once()
        self.assertFalse(self.runtime.record_path.exists())

    def test_pid_reuse_or_forged_record_does_not_grant_ownership(self):
        record = {"pid": 4321, "created": "old-time", "executable": "C:/node.exe"}
        self.runtime.record_path.parent.mkdir(parents=True)
        self.runtime.record_path.write_text(json.dumps(record))
        current = {"pid": 4321, "created": "new-time", "executable": "C:/node.exe"}
        with mock.patch.object(router_runtime, "process_identity", return_value=current), \
                mock.patch.object(self.runtime, "_probe", return_value=True), \
                mock.patch.object(router_runtime, "terminate_record") as terminate:
            result = self.runtime.stop()
        self.assertFalse(result["managed"])
        self.assertFalse(result["can_stop"])
        terminate.assert_not_called()

    def test_verified_persisted_process_can_be_stopped(self):
        record = {"pid": 4321, "created": "same-time", "executable": "C:/node.exe"}
        self.runtime.record_path.parent.mkdir(parents=True)
        self.runtime.record_path.write_text(json.dumps(record))
        with mock.patch.object(router_runtime, "process_identity", return_value=record), \
                mock.patch.object(self.runtime, "_probe", return_value=False), \
                mock.patch.object(router_runtime, "terminate_record", return_value=True) as terminate:
            result = self.runtime.stop()
        terminate.assert_called_once_with(record)
        self.assertEqual(result["status"], "stopped")

    def test_stopped_router_is_reported_without_spawning(self):
        with mock.patch.object(self.runtime, "_probe", return_value=False), \
                mock.patch.object(router_runtime.subprocess, "Popen") as spawn:
            self.assertEqual(self.runtime.status()["status"], "stopped")
        spawn.assert_not_called()

    def test_failure_message_and_logs_do_not_contain_subprocess_exception(self):
        with mock.patch.object(self.runtime, "_probe", return_value=False), \
                mock.patch.object(self.runtime, "_port_open", return_value=False), \
                mock.patch.object(router_runtime, "router_command", return_value=["node", "server.js"]), \
                mock.patch.object(router_runtime, "router_environment", return_value=dict(router_runtime.os.environ)), \
                mock.patch.object(router_runtime.subprocess, "Popen", side_effect=OSError("secret-fixture-key")):
            result = self.runtime.start()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-fixture-key", json.dumps([result, self.runtime.logs()]))

    def test_slow_live_startup_stays_starting_instead_of_reporting_a_crash(self):
        process = mock.Mock(pid=9876)
        process.poll.return_value = None
        with mock.patch.object(self.runtime, '_probe', return_value=False), \
                mock.patch.object(self.runtime, '_port_open', return_value=False), \
                mock.patch.object(router_runtime, 'router_command', return_value=['node', 'server.js']), \
                mock.patch.object(router_runtime, 'router_environment', return_value=dict(router_runtime.os.environ)), \
                mock.patch.object(router_runtime, 'process_identity', return_value=None), \
                mock.patch.object(router_runtime.subprocess, 'Popen', return_value=process), \
                mock.patch.object(router_runtime.time, 'monotonic', side_effect=[0, 26]):
            result = self.runtime.start()
        self.assertEqual(result['status'], 'starting')
        self.assertTrue(result['can_stop'])

    def test_constructor_rejects_nonlocal_or_nonfixed_host(self):
        for endpoint in ("http://example.org:20128/v1", "http://0.0.0.0:20128/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                router_runtime.RouterRuntime(root=self.root, base_url=endpoint)

    def fixture_router(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        runtime = router_runtime.RouterRuntime(root=self.root, base_url=f"http://127.0.0.1:{port}/v1")
        script = ("import os;from http.server import BaseHTTPRequestHandler,HTTPServer;"
                  "exec(\"class Handler(BaseHTTPRequestHandler):\\n"
                  " def do_GET(self):\\n  self.send_response(200);self.end_headers();self.wfile.write(b'{\\\"data\\\":[]}')\\n"
                  " def log_message(self,*args): pass\");"
                  "HTTPServer((os.environ['HOSTNAME'],int(os.environ['PORT'])),Handler).serve_forever()")
        return runtime, [sys.executable, "-u", "-c", script]

    def test_real_isolated_child_lifecycle_and_loopback_readiness(self):
        runtime, command = self.fixture_router()
        self.addCleanup(runtime.stop)
        with mock.patch.object(router_runtime, "router_command", return_value=command), \
                mock.patch.object(router_runtime, "router_environment", return_value=dict(router_runtime.os.environ)):
            result = runtime.start()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["can_stop"])
        self.assertEqual(runtime.status()["status"], "ready")
        self.assertEqual(runtime.stop()["status"], "stopped")
        self.assertFalse(runtime.record_path.exists())

    def test_recovered_real_fixture_process_requires_identity_and_can_stop(self):
        runtime, command = self.fixture_router()
        self.addCleanup(runtime.stop)
        with mock.patch.object(router_runtime, "router_command", return_value=command), \
                mock.patch.object(router_runtime, "router_environment", return_value=dict(router_runtime.os.environ)):
            self.assertEqual(runtime.start()["status"], "ready")
        process = runtime._process
        recovered = router_runtime.RouterRuntime(root=self.root, base_url=runtime.base_url)
        self.assertTrue(recovered.status()["can_stop"])
        self.assertEqual(recovered.stop()["status"], "stopped")
        process.wait(timeout=3)

    def test_failed_child_does_not_block_a_new_start_with_stale_record(self):
        process = mock.Mock(pid=9876)
        process.poll.return_value = 1
        self.runtime._process = process
        self.runtime.record_path.parent.mkdir(parents=True)
        self.runtime.record_path.write_text('{"pid":9876}')
        with mock.patch.object(self.runtime, "_probe", return_value=False):
            result = self.runtime.status()
        self.assertFalse(result["managed"])
        self.assertFalse(self.runtime.record_path.exists())

    @unittest.skipUnless(shutil.which('node'), 'Node.js installed package entry regression')
    def test_installed_entry_runs_as_main_and_official_node_path_applies_at_startup(self):
        package = self.root / 'node_modules' / '9router'
        (package / 'app').mkdir(parents=True)
        (package / 'hooks').mkdir()
        dependency = self.root / 'runtime-modules' / 'runtime-probe'
        dependency.mkdir(parents=True)
        (dependency / 'index.js').write_text('module.exports="MAIN_ENTRY_OK";', encoding='utf-8')
        (package / 'package.json').write_text('{"name":"9router","version":"0.5.59"}', encoding='utf-8')
        (package / 'hooks' / 'sqliteRuntime.js').write_text(
            'exports.buildEnvWithRuntime=env=>({...env,NODE_PATH:' + json.dumps(str(dependency.parent)) + '});', encoding='utf-8')
        (package / 'app' / 'custom-server.js').write_text(
            'if(require.main===module){process.stdout.write(require("runtime-probe"));}', encoding='utf-8')
        native_node = shutil.which('node')
        with mock.patch.object(router_runtime.shutil, 'which', side_effect=lambda name: native_node if name == 'node' else str(self.root / '9router.cmd')):
            command = router_runtime.router_command()
        environment = router_runtime.router_environment(command)
        result = subprocess.run(command, env=environment, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if router_runtime.os.name == 'nt' else 0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'MAIN_ENTRY_OK')


if __name__ == "__main__":
    unittest.main()
