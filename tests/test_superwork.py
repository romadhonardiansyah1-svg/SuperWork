"""The 9router bridge uses local endpoints and preserves exact model IDs."""
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools import superwork


class RouterBridgeTests(unittest.TestCase):
    def server(self, status=200, payload=None, headers=None):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization")))
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1", received

    def test_loopback_endpoint_and_model_discovery(self):
        base, received = self.server(payload={"data": [
            {"id": "kr/test-model", "context_length": 12345, "max_completion_tokens": 1234},
            {"id": "custom/nested/model:free"},
        ]})
        models = superwork.get_models(base, "test-key")
        self.assertEqual([row["id"] for row in models], ["kr/test-model", "custom/nested/model:free"])
        self.assertEqual(received, [("/v1/models", "Bearer test-key")])
        config = superwork.runtime_config(base, models, "custom/nested/model:free")
        self.assertEqual(config["model"], "9router/custom/nested/model:free")
        self.assertEqual(config["enabled_providers"], ["9router"])
        provider = config["provider"]["9router"]
        self.assertEqual(provider["options"]["baseURL"], base)
        self.assertEqual(provider["options"]["apiKey"], "{env:SUPERWORK_9ROUTER_API_KEY}")
        self.assertEqual(provider["models"]["kr/test-model"]["limit"], {"context": 12345, "output": 1234})

    def test_only_local_plain_endpoints_are_allowed(self):
        self.assertEqual(superwork.local_url("http://localhost:20128/v1/"), "http://localhost:20128/v1")
        self.assertEqual(superwork.local_url("http://[::1]:20128/v1"), "http://[::1]:20128/v1")
        for value in ("https://example.com/v1", "http://127.0.0.1.evil/v1",
                      "http://user:secret@localhost/v1", "http://localhost/v1?token=x",
                      "http://localhost/v1#fragment", "file:///v1", "http://localhost/other",
                      "http://localhost:99999/v1", "http://localhost:0/v1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                superwork.local_url(value)

    def test_authentication_error_does_not_echo_router_response(self):
        base, _ = self.server(status=401, payload={"error": "private-router-secret"})
        with self.assertRaises(ValueError) as raised:
            superwork.get_models(base)
        self.assertIn("SUPERWORK_9ROUTER_API_KEY", str(raised.exception))
        self.assertNotIn("private-router-secret", str(raised.exception))

    def test_malformed_key_is_rejected_without_echoing_or_sending_it(self):
        base, received = self.server(payload={"data": [{"id": "test/model"}]})
        for key in ("private-key\nextra-header", "private-key\x00"):
            with self.subTest(key=key), self.assertRaises(ValueError) as raised:
                superwork.get_models(base, key)
            self.assertNotIn("private-key", str(raised.exception))
        self.assertEqual(received, [])

    def test_redirect_is_rejected_without_contacting_target(self):
        target, received = self.server(payload={"data": [{"id": "wrong/model"}]})
        base, _ = self.server(status=302, headers={"Location": target + "/models"})
        with self.assertRaises(ValueError):
            superwork.get_models(base, "test-key")
        self.assertEqual(received, [])

    def test_invalid_model_data_and_unknown_selection_are_rejected(self):
        for payload in ({"data": []}, {"data": [{"id": ""}]}, {"data": [{"id": "bad\nmodel"}]}, [],
                        {"data": "not a list"}):
            with self.subTest(payload=payload):
                base, _ = self.server(payload=payload)
                with self.assertRaises(ValueError):
                    superwork.get_models(base)
        with self.assertRaises(ValueError):
            superwork.runtime_config("http://localhost:20128/v1", [{"id": "known"}], "unknown")

    def test_canonical_workflows_are_referenced_without_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = root / ".claude" / "commands"
            commands.mkdir(parents=True)
            (commands / "rank.md").write_text("canonical fixture rule", encoding="utf-8")
            config = superwork.workspace_config(root)
        self.assertIn(".claude/commands/rank.md", config["command"]["rank"]["template"])
        self.assertNotIn("canonical fixture rule", json.dumps(config))
        self.assertEqual(config["permission"]["bash"], "ask")
        self.assertEqual(config["permission"]["edit"], "ask")

    def test_models_command_prints_json_without_starting_inference(self):
        base, received = self.server(payload={"data": [{"id": "custom/nested/model:free"}]})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = superwork.main(["models", "--base-url", base])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["models"], ["custom/nested/model:free"])
        self.assertEqual(len(received), 1)

    def test_missing_model_fails_before_network_or_inference(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"SUPERWORK_MODEL": ""}), contextlib.redirect_stdout(output):
            code = superwork.main(["check", "--base-url", "http://127.0.0.1:1/v1"])
        self.assertEqual(code, 1)
        self.assertIn("--model", json.loads(output.getvalue())["error"])

    def test_invalid_base_url_error_does_not_echo_credentials(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = superwork.main(["models", "--base-url", "http://user:private-key@localhost/v1"])
        self.assertEqual(code, 1)
        self.assertNotIn("private-key", output.getvalue())

    def test_explicit_key_takes_priority_over_local_cli_credentials(self):
        with mock.patch.dict(os.environ, {"SUPERWORK_9ROUTER_API_KEY": "test-explicit-key"}), \
                mock.patch.object(superwork.subprocess, "run") as run:
            self.assertEqual(superwork.router_key(superwork.DEFAULT_URL), "test-explicit-key")
        run.assert_not_called()

    def test_local_cli_key_is_captured_without_writing_config(self):
        completed = subprocess.CompletedProcess([], 0, stdout="test-memory-key", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            npm = Path(tmp)
            client = npm / "node_modules" / "9router" / "src" / "cli" / "api" / "client.js"
            client.parent.mkdir(parents=True)
            client.write_text("// fake installed client", encoding="utf-8")
            def which(name):
                return str(npm / "9router.cmd") if name == "9router" else "node"
            with mock.patch.dict(os.environ, {"SUPERWORK_9ROUTER_API_KEY": ""}), \
                    mock.patch.object(superwork.shutil, "which", side_effect=which), \
                    mock.patch.object(superwork.subprocess, "run", return_value=completed) as run:
                self.assertEqual(superwork.router_key(superwork.DEFAULT_URL), "test-memory-key")
                argv = run.call_args.args[0]
                self.assertIn(str(client), argv)
                self.assertNotIn("test-memory-key", str(argv))
                self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_cli_auth_failure_does_not_echo_private_error_text(self):
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="private-detail")
        with mock.patch.dict(os.environ, {"SUPERWORK_9ROUTER_API_KEY": ""}), \
                mock.patch.object(superwork.shutil, "which", return_value="node"), \
                mock.patch.object(Path, "is_file", return_value=True), \
                mock.patch.object(superwork.subprocess, "run", return_value=completed):
            with self.assertRaises(ValueError) as raised:
                superwork.router_key(superwork.DEFAULT_URL)
        self.assertNotIn("private-detail", str(raised.exception))


class SmokeEvidenceTests(unittest.TestCase):
    def events(self, payload=None, tool_status="completed", tool="read", path="fixture.json"):
        self.expected = {"fixture_id": "SUPERWORK_TEST", "probe": "random-test-nonce",
                         "matched_skills": ["Python"], "missing_skills": ["SQL"],
                         "language_gate": "FLAG", "profile_complete": False}
        return "\n".join(json.dumps(row) for row in [
            {"type": "tool_use", "part": {"tool": tool, "state": {
                "status": tool_status, "input": {"filePath": path}, "output": "fixture contents"}}},
            {"type": "text", "part": {"text": json.dumps(payload if payload is not None else self.expected)}},
            {"type": "step_finish", "part": {"reason": "stop"}},
        ])

    def test_completed_fixture_read_and_valid_json_are_required(self):
        events = self.events()
        result = superwork.check_evidence(events, Path("fixture.json"), self.expected)
        self.assertEqual(result["output"], self.expected)
        self.assertTrue(result["tool_calling"])
        self.assertTrue(result["json_output"])

    def test_missing_wrong_or_failed_tool_cannot_pass(self):
        for status, tool, path in (("error", "read", "fixture.json"), ("completed", "bash", "fixture.json"),
                                   ("completed", "read", "other.json")):
            with self.subTest(status=status, tool=tool, path=path):
                events = self.events(tool_status=status, tool=tool, path=path)
                with self.assertRaises(ValueError):
                    superwork.check_evidence(events, Path("fixture.json"), self.expected)

    def test_error_event_invalid_json_or_wrong_nonce_cannot_pass(self):
        valid = self.events()
        cases = [valid + '\n{"type":"error","error":{"message":"private-detail"}}',
                 valid.replace('random-test-nonce', 'invented-nonce'),
                 valid.replace('"type": "text"', '"type": "reasoning"'),
                 valid + '\nnot-json']
        for events in cases:
            with self.subTest(events=events), self.assertRaises(ValueError):
                superwork.check_evidence(events, Path("fixture.json"), self.expected)

    def test_json_boolean_cannot_be_replaced_by_zero(self):
        self.events()
        wrong = dict(self.expected, profile_complete=0)
        events = self.events(payload=wrong)
        with self.assertRaises(ValueError):
            superwork.check_evidence(events, Path("fixture.json"), self.expected)

    def test_test_environment_does_not_load_global_config_or_skills(self):
        env = superwork.isolated_environment(Path("/test-workspace"), {
            "OPENCODE_CONFIG": "private-config", "OPENCODE_PERMISSION": '{"*":"allow"}',
            "SUPERWORK_9ROUTER_API_KEY": "test-key", "PATH": "runtime-path"})
        self.assertNotIn("OPENCODE_CONFIG", env)
        self.assertNotIn("OPENCODE_PERMISSION", env)
        self.assertEqual(env["OPENCODE_PURE"], "1")
        self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE"], "1")
        self.assertTrue(env["XDG_CONFIG_HOME"].startswith(str(Path("/test-workspace"))))
        self.assertEqual(env["SUPERWORK_9ROUTER_API_KEY"], "test-key")


if __name__ == "__main__":
    unittest.main()
