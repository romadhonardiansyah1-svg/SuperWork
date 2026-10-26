"""Select a local 9router model and run the existing SuperWork workflows."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "http://127.0.0.1:20128/v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def local_url(value):
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError("Invalid local 9router URL.") from None
    if (url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1")
            or url.username is not None or url.password is not None or url.query or url.fragment
            or url.path.rstrip("/") != "/v1" or port == 0):
        raise ValueError("Use a loopback HTTP(S) endpoint ending in /v1, without credentials or query parameters.")
    return value.rstrip("/")


def get_models(base_url, api_key=None):
    base_url = local_url(base_url)
    headers = {"Accept": "application/json"}
    if api_key:
        if not api_key.isascii() or re.search(r"\s|[\x00-\x1f\x7f]", api_key):
            raise ValueError("Invalid SUPERWORK_9ROUTER_API_KEY; use a single ASCII token.")
        headers["Authorization"] = "Bearer " + api_key
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            Request(base_url + "/models", headers=headers), timeout=10
        ) as response:
            body = response.read(1_000_001)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        hint = " Set SUPERWORK_9ROUTER_API_KEY in your environment." if code in (401, 403) else ""
        raise ValueError(f"9router returned HTTP {code}.{hint}") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Cannot reach 9router. Start the local server and check --base-url.") from None
    try:
        doc = json.loads(body) if len(body) <= 1_000_000 else None
        rows = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError
        models = []
        for row in rows:
            model = row.get("id") if isinstance(row, dict) else None
            if not isinstance(model, str) or not model or len(model) > 200 or re.search(r"\s|[\x00-\x1f\x7f]", model):
                raise ValueError
            models.append({key: row[key] for key in ("id", "context_length", "max_completion_tokens") if key in row})
        return models
    except (ValueError, UnicodeError):
        raise ValueError("9router returned an invalid or oversized model list.") from None


def runtime_config(base_url, models, model):
    if model not in {row["id"] for row in models}:
        raise ValueError("Choose an exact model ID from the models command, or set SUPERWORK_MODEL.")
    available = {}
    for row in models:
        entry = {"name": row["id"]}
        limits = {target: row[source] for source, target in
                  (("context_length", "context"), ("max_completion_tokens", "output"))
                  if type(row.get(source)) is int and row[source] > 0}
        if len(limits) == 2:
            entry["limit"] = limits
        available[row["id"]] = entry
    return {
        "$schema": "https://opencode.ai/config.json", "model": "9router/" + model, "small_model": "9router/" + model,
        "enabled_providers": ["9router"], "share": "disabled", "autoupdate": False,
        "provider": {"9router": {"npm": "@ai-sdk/openai-compatible", "name": "9router local",
            "options": {"baseURL": local_url(base_url), "apiKey": "{env:SUPERWORK_9ROUTER_API_KEY}"},
            "models": available}},
    }


def workspace_config(root):
    return {
        "instructions": ["AGENTS.md", "CLAUDE.md"],
        "permission": {"bash": "ask", "edit": "ask", "external_directory": "ask",
                       "read": {"**/.env": "deny", "**/.env.*": "deny"}},
        "command": {path.stem: {"template": "{file:.claude/commands/" + path.name + "}\n\n$ARGUMENTS"}
                    for path in sorted((root / ".claude" / "commands").glob("*.md"))},
    }


def opencode_binary():
    native = shutil.which("opencode.exe" if os.name == "nt" else "opencode")
    if native:
        return native
    shim = shutil.which("opencode")
    if shim:
        bundled = Path(shim).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if bundled.is_file():
            return str(bundled)
    raise ValueError("OpenCode executable not found. Install OpenCode and put it on PATH.")


def router_key(base_url):
    explicit = os.environ.get("SUPERWORK_9ROUTER_API_KEY")
    if explicit:
        return explicit
    shim = shutil.which("9router")
    node = shutil.which("node")
    message = "Set SUPERWORK_9ROUTER_API_KEY or use the installed 9router CLI with an active local key."
    if not shim or not node:
        raise ValueError(message)
    candidates = (Path(shim).resolve().parent / "src" / "cli" / "api" / "client.js",
                  Path(shim).parent / "node_modules" / "9router" / "src" / "cli" / "api" / "client.js")
    client = next((path for path in candidates if path.is_file()), None)
    if client is None:
        raise ValueError(message)
    # Delegate to 9router's authenticated local CLI; never read its account database.
    script = ("const api=require(process.argv[1]);const u=new URL(process.argv[2]);"
              "api.configure({host:u.hostname.replaceAll('[','').replaceAll(']',''),port:Number(u.port)||(u.protocol==='https:'?443:80),protocol:u.protocol});"
              "api.getApiKeys().then(r=>{const k=r.success?(r.data.keys||[]).find(k=>k.isActive!==false):null;"
              "if(k?.key)process.stdout.write(k.key);else process.exitCode=1;}).catch(()=>{process.exitCode=1;});")
    try:
        result = subprocess.run([node, "-e", script, str(client), local_url(base_url)],
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
    except (subprocess.TimeoutExpired, OSError, UnicodeError):
        raise ValueError(message) from None
    if result.returncode or not result.stdout.strip():
        raise ValueError(message)
    return result.stdout.strip()


def isolated_environment(root, original):
    env = {key: value for key, value in original.items() if not key.startswith("OPENCODE_")}
    env.update({"OPENCODE_TEST_HOME": str(root / "home"), "OPENCODE_PURE": "1",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
                "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1"})
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        env[f"XDG_{name}_HOME"] = str(root / name.lower())
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


def check_evidence(stdout, fixture, expected):
    read_completed = False
    text = []
    finish = None
    try:
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") == "error":
                raise ValueError
            part = event.get("part", {})
            if event.get("type") == "tool_use":
                state = part.get("state", {})
                if (part.get("tool") != "read" or state.get("status") != "completed"
                        or Path(state.get("input", {}).get("filePath", "")).resolve() != fixture.resolve()):
                    raise ValueError
                read_completed = True
                text = []
            elif event.get("type") == "text":
                text.append(part["text"])
            elif event.get("type") == "step_finish":
                finish = part.get("reason")
        output = json.loads("".join(text))
        if not read_completed or finish != "stop" or output != expected or output.get("profile_complete") is not False:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        raise ValueError("Check failed: require a completed fixture read and exact valid JSON, without tool/runtime errors.") from None
    return {"tool_calling": True, "json_output": True, "output": output}


def smoke_check(config, binary, api_key=None):
    with tempfile.TemporaryDirectory(prefix="superwork-9router-") as tmp:
        root = Path(tmp).resolve()
        fixture = root / "fixture.json"
        expected = {"fixture_id": "SUPERWORK_TEST", "probe": uuid4().hex,
                    "matched_skills": ["Python"], "missing_skills": ["SQL"],
                    "language_gate": "FLAG", "profile_complete": False}
        fixture.write_text(json.dumps({"fixture_id": expected["fixture_id"], "probe": expected["probe"],
            "candidate": {"skills": ["Python"], "profile_complete": False},
            "job": {"required_skills": ["Python", "SQL"]}}, indent=2), encoding="utf-8")
        # OpenCode checks read permissions relative to its Git worktree.
        # A temporary repository makes this rule cover precisely one fixture.
        try:
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True, timeout=15)
        except (subprocess.SubprocessError, OSError):
            raise ValueError("Git is required to isolate the fixture check; no PASS recorded.") from None
        isolated = dict(config, permission={"*": "deny", "read": {fixture.name: "allow"}})
        env = isolated_environment(root, os.environ)
        if api_key:
            env["SUPERWORK_9ROUTER_API_KEY"] = api_key
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(isolated)
        prompt = (f"Use the read tool to read {fixture.as_posix()}. This is synthetic test data. "
                  "Your final message must be a raw JSON object, beginning with { and ending with }. "
                  "Never wrap it in backticks or markdown. Required fields: fixture_id and probe (strings copied "
                  "exactly from the file), matched_skills and missing_skills (arrays of strings, comparing "
                  "candidate.skills with job.required_skills), profile_complete (boolean copied from candidate), "
                  'and language_gate (the literal STRING "FLAG", never a boolean, because the profile is incomplete). '
                  "Do not guess the probe, add extra fields, or use any other tools.")
        try:
            result = subprocess.run([binary, "run", "--pure", "--format", "json", "--model", config["model"], prompt],
                                    cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=120)
        except subprocess.TimeoutExpired:
            raise ValueError("OpenCode check timed out after 120 seconds; no PASS recorded.") from None
        if result.returncode:
            raise ValueError("OpenCode check failed. Check model access and local 9router authentication.")
        return {"ok": True, "model": config["model"], "fixture_only": True,
                **check_evidence(result.stdout, fixture, expected)}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("models", "start", "check"))
    parser.add_argument("--base-url", default=os.environ.get("SUPERWORK_9ROUTER_URL", DEFAULT_URL))
    parser.add_argument("--model", default=os.environ.get("SUPERWORK_MODEL"))
    args = parser.parse_args(argv)
    try:
        if args.command != "models" and not args.model:
            raise ValueError("Choose --model ID or set SUPERWORK_MODEL; list IDs with the models command.")
        base_url = local_url(args.base_url)
        api_key = os.environ.get("SUPERWORK_9ROUTER_API_KEY") if args.command == "models" else router_key(base_url)
        models = get_models(base_url, api_key)
        if args.command == "models":
            result = {"base_url": base_url, "models": [row["id"] for row in models]}
        else:
            config = runtime_config(base_url, models, args.model)
            binary = opencode_binary()
            if args.command == "check":
                result = smoke_check(config, binary, api_key)
            else:
                config.update(workspace_config(ROOT))
                env = dict(os.environ, OPENCODE_CONFIG_CONTENT=json.dumps(config), SUPERWORK_9ROUTER_API_KEY=api_key)
                return subprocess.call([binary, "--model", config["model"]], cwd=ROOT, env=env)
    except (ValueError, OSError) as exc:
        # Expected validation errors are safe; OS error text may contain private paths.
        result = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "Cannot start OpenCode."}
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
