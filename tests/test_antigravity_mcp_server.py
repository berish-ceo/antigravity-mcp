from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import AsyncMock, Mock, call, patch


SERVER_PATH = Path(__file__).parents[1] / "antigravity_mcp_server.py"
SPEC = importlib.util.spec_from_file_location("antigravity_mcp_server_under_test", SERVER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {SERVER_PATH}")
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)
DESKTOP_RPC = server.desktop_rpc


class AntigravityMcpServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.cli_root = root / "cli"
        self.desktop_root = root / "desktop"
        self.archive_root = self.cli_root / "archive"
        for base in (self.cli_root, self.desktop_root):
            for name in ("conversations", "annotations", "brain"):
                (base / name).mkdir(parents=True, exist_ok=True)

        self.fake_agy = root / "agy"
        self.fake_agy.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys
                import time

                args = sys.argv[1:]
                if args == ["models"]:
                    print("gemini-3.8-flash-low\\tGemini Flash Low")
                    print("gemini-3.8-flash-medium\\tGemini Flash Medium")
                    print("gemini-3.8-flash-high\\tGemini Flash High")
                    raise SystemExit(0)

                stream_mode = "--input-format" in args and args[args.index("--input-format") + 1] == "stream-json"
                if stream_mode:
                    request = json.loads(sys.stdin.readline())
                    prompt = request["message"]["content"]
                else:
                    prompt = next((arg.removeprefix("--print=") for arg in args if arg.startswith("--print=")), "")
                conversation = "11111111-1111-4111-8111-111111111111"
                if "--conversation" in args:
                    conversation = args[args.index("--conversation") + 1]
                if stream_mode:
                    print(json.dumps({"event": "init", "conversation_id": conversation}), flush=True)
                    print(json.dumps({"event": "step_update", "step_update": {
                        "conversation_id": conversation,
                        "step_index": 0,
                        "state": "DONE",
                        "step_type": "user_input",
                    }}), flush=True)
                    if prompt == "SLOW" or "TRIGGER_SLOW" in prompt:
                        time.sleep(10)
                    if prompt == "DELAY":
                        time.sleep(1)
                    if prompt == "MALFORMED":
                        print("not-json", flush=True)
                        raise SystemExit(0)
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
                    print(json.dumps({"event": "result", "result": result}), flush=True)
                else:
                    if prompt == "SLOW":
                        time.sleep(10)
                    if prompt == "DELAY":
                        time.sleep(1)
                    if prompt == "MALFORMED":
                        print("not-json")
                        raise SystemExit(0)
                    print(json.dumps({"status": "SUCCESS", "conversation_id": conversation, "response": "done"}))
                """
            ),
            encoding="utf-8",
        )
        self.fake_agy.chmod(self.fake_agy.stat().st_mode | stat.S_IXUSR)

        self.storage_patch = patch.multiple(
            server,
            CLI_STORAGE_ROOT=str(self.cli_root),
            DESKTOP_STORAGE_ROOT=str(self.desktop_root),
            ARCHIVE_STORAGE_ROOT=str(self.archive_root),
        )
        self.storage_patch.start()
        self.agy_patch = patch.object(server, "get_agy_path", return_value=str(self.fake_agy))
        self.agy_patch.start()
        self.desktop_rpc_patch = patch.object(server, "desktop_rpc")
        self.desktop_rpc = self.desktop_rpc_patch.start()
        self.desktop_rpc.return_value = b'{"numTotalSteps":2}'
        server.managed_subagent_runs.clear()
        server.model_registry._cached_models = []

    async def asyncTearDown(self) -> None:
        for managed_run in server.managed_subagent_runs.values():
            if managed_run.task_handle and not managed_run.task_handle.done():
                await server.antigravity_subagent_stop(managed_run.run_id)
                await managed_run.task_handle
        server.managed_subagent_runs.clear()
        self.agy_patch.stop()
        self.desktop_rpc_patch.stop()
        self.storage_patch.stop()
        self.temp_dir.cleanup()

    async def test_managed_run_completes_with_session_identity(self) -> None:
        managed_run = await server.start_managed_subagent(
            task="SMALL",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=5,
            sync_to_desktop=False,
        )

        await managed_run.task_handle

        self.assertEqual(managed_run.status, "COMPLETED")
        self.assertEqual(managed_run.conversation_id, "11111111-1111-4111-8111-111111111111")
        self.assertEqual(managed_run.result["response"], "done")

    async def test_malformed_json_is_failed(self) -> None:
        result = await server.antigravity_subagent_run(
            task="MALFORMED",
            project_dir=self.temp_dir.name,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=5,
            sync_to_desktop=False,
        )

        self.assertEqual(result["status"], "FAILED")
        self.assertIn("malformed JSON", result["response"])

    async def test_continue_reuses_conversation(self) -> None:
        conversation_id = "33333333-3333-4333-8333-333333333333"

        started = await server.antigravity_subagent_continue(
            conversation_id=conversation_id,
            task="SMALL",
            project_dir=self.temp_dir.name,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=5,
            sync_to_desktop=False,
        )
        managed_run = await server.get_managed_run(started["run_id"])
        await managed_run.task_handle

        self.assertEqual(managed_run.status, "COMPLETED")
        self.assertEqual(managed_run.conversation_id, conversation_id)

    async def test_stop_cancels_running_process(self) -> None:
        managed_run = await server.start_managed_subagent(
            task="SLOW",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=20,
            sync_to_desktop=False,
        )
        await asyncio.sleep(0.05)

        result = await server.antigravity_subagent_stop(managed_run.run_id)

        self.assertEqual(result["status"], "CANCELLED")
        self.assertIsNone(managed_run.process)

    async def test_concurrent_turn_for_same_conversation_is_rejected(self) -> None:
        conversation_id = "44444444-4444-4444-8444-444444444444"
        managed_run = await server.start_managed_subagent(
            task="SLOW",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=conversation_id,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=20,
            sync_to_desktop=False,
        )

        with self.assertRaisesRegex(ValueError, "active managed run"):
            await server.start_managed_subagent(
                task="SMALL",
                project_dir=self.temp_dir.name,
                mode="accept-edits",
                conversation_id=conversation_id,
                model="gemini-3.8-flash-low",
                effort="low",
                timeout=5,
                sync_to_desktop=False,
            )

        await server.antigravity_subagent_stop(managed_run.run_id)

    async def test_invalid_mode_and_model_effort_fail_before_launch(self) -> None:
        with self.assertRaisesRegex(ValueError, "mode"):
            await server.resolve_subagent_request(
                self.temp_dir.name,
                "unknown",
                None,
                "gemini-3.8-flash-low",
                "low",
                5,
            )
        with self.assertRaisesRegex(ValueError, "must match"):
            await server.resolve_subagent_request(
                self.temp_dir.name,
                "plan",
                None,
                "gemini-3.8-flash-low",
                "medium",
                5,
            )

    async def test_archive_preserves_cli_session_and_hides_desktop_artifacts(self) -> None:
        conversation_id = "22222222-2222-4222-8222-222222222222"
        cli_db = self.cli_root / "conversations" / f"{conversation_id}.db"
        desktop_db = self.desktop_root / "conversations" / f"{conversation_id}.db"
        desktop_annotation = self.desktop_root / "annotations" / f"{conversation_id}.pbtxt"
        desktop_brain = self.desktop_root / "brain" / conversation_id
        cli_db.write_bytes(b"cli")
        desktop_db.write_bytes(b"desktop")
        desktop_annotation.write_text('title: "Small test"\n', encoding="utf-8")
        desktop_brain.mkdir()
        (desktop_brain / "result.txt").write_text("ok", encoding="utf-8")

        result = await server.antigravity_archive_session(conversation_id)
        listed = await server.antigravity_list_sessions(include_archived=True)
        repeated = await server.antigravity_archive_session(conversation_id)

        self.assertEqual(result["status"], "ARCHIVED")
        self.assertTrue(cli_db.exists())
        self.assertFalse(desktop_db.exists())
        self.assertFalse(desktop_annotation.exists())
        self.assertFalse(desktop_brain.exists())
        self.assertTrue(server.is_conversation_archived(conversation_id))
        self.assertEqual(server.read_conversation_title(conversation_id), "Small test")
        self.assertEqual(listed["sessions"][0]["conversation_id"], conversation_id)
        self.assertTrue(listed["sessions"][0]["archived"])
        self.assertEqual(repeated["status"], "ARCHIVED")

    async def test_run_store_crud_and_status(self) -> None:
        store = server.RunStore(str(self.cli_root / "custom_runs.db"))
        run_id = "test-run-123"
        store.save_run(
            run_id=run_id,
            project_dir="/some/dir",
            model="gemini-3.8-flash-low",
            effort="low",
            mode="accept-edits",
            created_at="2026-09-12T12:00:00Z",
            status="RUNNING",
            pid=9999999,
            conversation_id="conv-123",
        )
        saved = store.get_run(run_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], "RUNNING")
        self.assertEqual(saved["conversation_id"], "conv-123")
        self.assertFalse(saved["synced_to_desktop"])

        # Update run to COMPLETED
        store.save_run(
            run_id=run_id,
            project_dir="/some/dir",
            model="gemini-3.8-flash-low",
            effort="low",
            mode="accept-edits",
            created_at="2026-09-12T12:00:00Z",
            status="COMPLETED",
            response="all done",
            duration_seconds=12.5,
            synced_to_desktop=True,
        )
        updated = store.get_run(run_id)
        self.assertEqual(updated["status"], "COMPLETED")
        self.assertEqual(updated["response"], "all done")
        self.assertEqual(updated["duration_seconds"], 12.5)
        self.assertTrue(updated["synced_to_desktop"])

        all_runs = store.list_runs(limit=10)
        self.assertEqual(len(all_runs), 1)
        self.assertEqual(all_runs[0]["run_id"], run_id)

    async def test_merge_and_remove_desktop_hub_summary(self) -> None:
        conversation_id = "55555555-5555-5555-5555-555555555555"
        conv_db = self.cli_root / "conversations" / f"{conversation_id}.db"
        import sqlite3
        con = sqlite3.connect(str(conv_db))
        con.execute("CREATE TABLE trajectory_meta (trajectory_id TEXT);")
        con.execute("INSERT INTO trajectory_meta VALUES ('traj-555');")
        con.execute("CREATE TABLE steps (idx INTEGER, step_type TEXT);")
        con.execute("INSERT INTO steps VALUES (0, 'PROMPT'), (1, 'RESPONSE');")
        con.commit()
        con.close()

        summary = b"\x0a\x04Test"
        with patch.object(server, "get_raw_summary_from_cli", return_value=summary):
            ok = server.merge_summary_into_desktop_hub(conversation_id)
        self.assertTrue(ok)
        hub_path = self.desktop_root / "agyhub_summaries_proto.pb"
        self.assertFalse(hub_path.exists(), "Desktop owns the hub file; MCP must not rewrite it")
        identity = b"\x0a\x24" + conversation_id.encode()
        self.assertEqual(self.desktop_rpc.call_args_list[:2], [
            call("LoadTrajectory", identity),
            call("JetboxWriteSummary", identity + b"\x12\x06" + summary),
        ])

        removed = server.remove_summary_from_desktop_hub(conversation_id)
        self.assertTrue(removed)
        self.desktop_rpc.assert_called_with("JetboxDeleteSummary", identity)

    async def test_silent_timeout_detection(self) -> None:
        c1 = "11111111-1111-4111-8111-111111111111"
        c2 = "22222222-2222-4222-8222-222222222222"
        cmd1 = {
            "status": "COMPLETED",
            "stdout": json.dumps({"status": "SUCCESS", "conversation_id": c1, "response": "done"}),
            "stderr": "",
            "duration_seconds": 1.0,
            "exit_code": 0,
        }
        res1 = server.parse_subagent_command_result(
            command=cmd1,
            run_id="run-1",
            project_dir="/dir",
            model="gemini-3.8-flash-low",
            conversation_id=c1,
            sync_to_desktop=False,
        )
        self.assertEqual(res1["status"], "COMPLETED")
        self.assertEqual(res1["response"], "done")

        cmd2 = {
            "status": "COMPLETED",
            "stdout": json.dumps({"status": "SUCCESS", "conversation_id": c2, "response": ""}),
            "stderr": "[agy] print timeout after 180s reached",
            "duration_seconds": 180.0,
            "exit_code": 0,
        }
        res2 = server.parse_subagent_command_result(
            command=cmd2,
            run_id="run-2",
            project_dir="/dir",
            model="gemini-3.8-flash-low",
            conversation_id=c2,
            sync_to_desktop=False,
        )
        self.assertEqual(res2["status"], "TIMED_OUT")
        self.assertIn("timed out", res2["response"])

    async def test_managed_run_persists_in_run_store(self) -> None:
        managed_run = await server.start_managed_subagent(
            task="SMALL",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=5,
            sync_to_desktop=True,
        )
        await managed_run.task_handle

        stored = server.run_store.get_run(managed_run.run_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "COMPLETED")
        self.assertEqual(stored["response"], "done")
        self.assertEqual(stored["conversation_id"], "11111111-1111-4111-8111-111111111111")
        self.assertFalse(stored["synced_to_desktop"], "Fake CLI created no conversation database")

    async def test_sync_all_desktop_sessions(self) -> None:
        conversation_id = "66666666-6666-6666-6666-666666666666"
        conv_db = self.cli_root / "conversations" / f"{conversation_id}.db"
        import sqlite3
        con = sqlite3.connect(str(conv_db))
        con.execute("CREATE TABLE trajectory_meta (trajectory_id TEXT);")
        con.execute("INSERT INTO trajectory_meta VALUES ('traj-666');")
        con.execute("CREATE TABLE steps (idx INTEGER, step_type TEXT);")
        con.execute("INSERT INTO steps VALUES (0, 'PROMPT');")
        con.commit()
        con.close()

        with patch.object(server, "get_raw_summary_from_cli", return_value=b"\x0a\x04Test"):
            self.desktop_rpc.return_value = b'{"numTotalSteps":1}'
            res = await server.antigravity_sync_all_desktop_sessions(limit=10)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreaterEqual(res["synced_count"], 1)
        self.assertIn(conversation_id, res["synced_conversations"])

        hub_path = self.desktop_root / "agyhub_summaries_proto.pb"
        self.assertFalse(hub_path.exists())
        self.assertEqual(self.desktop_rpc.call_args_list[-1].args[0], "GetCascadeTrajectory")

    async def test_stale_desktop_messages_are_not_reported_as_synced(self) -> None:
        cid = "77777777-7777-4777-8777-777777777777"
        with patch.object(server, "get_raw_summary_from_cli", return_value=b"\x0a\x04Test"), patch.object(
            server, "get_conversation_step_progress", return_value={"step_count":5}
        ):
            self.desktop_rpc.return_value = b'{"numTotalSteps":2}'
            self.assertFalse(server.merge_summary_into_desktop_hub(cid))

    async def test_desktop_registration_failure_is_not_reported_as_synced(self) -> None:
        cid = "77777777-7777-4777-8777-777777777777"
        (self.cli_root / "conversations" / f"{cid}.db").write_bytes(b"fixture")
        self.desktop_rpc.side_effect = OSError("Desktop unavailable")
        with patch.object(server, "get_raw_summary_from_cli", return_value=b"\x0a\x04Test"):
            self.assertFalse(server.sync_conversation_to_desktop(cid))
        self.assertTrue((self.desktop_root / "conversations" / f"{cid}.db").exists())

    async def test_missing_summary_does_not_fabricate_desktop_identity(self) -> None:
        cid = "77777777-7777-4777-8777-777777777777"
        self.assertFalse(server.merge_summary_into_desktop_hub(cid))
        self.desktop_rpc.assert_not_called()

    async def test_desktop_rpc_discovers_https_and_authenticates_request(self) -> None:
        log = Path(self.temp_dir.name) / "desktop.log"
        log.write_text("listening on random port at 12345 for HTTPS\n"
                       "listening on random port at 12346 for HTTP\n")
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(b'<script>window.__APP_CONFIG__ = {"productName":"antigravity","csrfToken":"test-token"};</script>'),
            io.BytesIO(b""),
        ]
        with patch.object(server, "DESKTOP_LOG_PATH", str(log)), patch.object(
            server.urllib.request, "build_opener", return_value=opener
        ):
            DESKTOP_RPC("JetboxWriteSummary", b"request")
        request = opener.open.call_args_list[1].args[0]
        self.assertEqual(request.full_url, "https://127.0.0.1:12345/exa.language_server_pb.LanguageServerService/JetboxWriteSummary")
        self.assertEqual(request.data, b"request")
        self.assertEqual(request.get_header("X-codeium-csrf-token"), "test-token")

    async def test_desktop_rpc_rejects_unrecognized_service(self) -> None:
        log = Path(self.temp_dir.name) / "desktop.log"
        log.write_text("listening on random port at 12345 for HTTPS\n")
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"unrelated server")
        with patch.object(server, "DESKTOP_LOG_PATH", str(log)), patch.object(
            server.urllib.request, "build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(RuntimeError, "identity"):
                DESKTOP_RPC("JetboxWriteSummary", b"request")
        self.assertEqual(opener.open.call_count, 1)

    async def test_worker_completes_after_launching_process_exits(self) -> None:
        code = f'''
import asyncio, json, os, sys
sys.path.insert(0, {str(SERVER_PATH.parent)!r})
import antigravity_mcp_server as s
s.CLI_STORAGE_ROOT = {str(self.cli_root)!r}
s.DESKTOP_STORAGE_ROOT = {str(self.desktop_root)!r}
s.ARCHIVE_STORAGE_ROOT = {str(self.archive_root)!r}
s.get_agy_path = lambda: {str(self.fake_agy)!r}
async def launch():
    r = await s.start_managed_subagent("DELAY", {self.temp_dir.name!r}, "plan", None, "gemini-3.8-flash-low", "low", 5, False)
    print(r.run_id, flush=True)
    os._exit(0)
asyncio.run(launch())
'''
        launcher = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        run_id = launcher.stdout.strip()
        result = await server.get_managed_run_snapshot(run_id)
        self.assertNotEqual(result["status"], "NOT_FOUND")
        for _ in range(100):
            result = await server.get_managed_run_snapshot(run_id)
            if result["status"] in server.TERMINAL_RUN_STATUSES:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["response"], "done")
        self.assertEqual(result["model_used"], "gemini-3.8-flash-low")


    async def test_wait_invalid_run_id_and_not_found(self) -> None:
        res_bad_uuid = await server.antigravity_subagent_wait("not-a-uuid")
        self.assertEqual(res_bad_uuid["status"], "FAILED")
        self.assertIn("UUID", res_bad_uuid["error"])

        res_not_found = await server.antigravity_subagent_wait("99999999-9999-4999-8999-999999999999")
        self.assertEqual(res_not_found["status"], "NOT_FOUND")
        self.assertEqual(res_not_found["run_id"], "99999999-9999-4999-8999-999999999999")

    async def test_wait_returns_immediately_if_already_terminal(self) -> None:
        managed_run = await server.start_managed_subagent(
            task="SMALL",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=5,
            sync_to_desktop=False,
        )
        await managed_run.task_handle

        start_time = asyncio.get_running_loop().time()
        result = await server.antigravity_subagent_wait(managed_run.run_id)
        elapsed = asyncio.get_running_loop().time() - start_time

        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["wait_status"], "TERMINAL")
        self.assertEqual(result["response"], "done")

    async def test_wait_completes_when_task_finishes(self) -> None:
        managed_run = await server.start_managed_subagent(
            task="DELAY",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=10,
            sync_to_desktop=False,
        )

        result = await server.antigravity_subagent_wait(managed_run.run_id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["wait_status"], "TERMINAL")
        self.assertEqual(result["response"], "done")

    async def test_wait_client_timeout_without_cancellation(self) -> None:
        managed_run = await server.start_managed_subagent(
            task="SLOW",
            project_dir=self.temp_dir.name,
            mode="accept-edits",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=20,
            sync_to_desktop=False,
        )

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(
                server.antigravity_subagent_wait(managed_run.run_id),
                timeout=0.3,
            )

        # Verify task is not cancelled and process is still running
        self.assertFalse(managed_run.task_handle.done())
        self.assertIsNotNone(managed_run.pid)
        self.assertTrue(server.is_pid_alive(managed_run.pid))

        # Cleanup
        await server.antigravity_subagent_stop(managed_run.run_id)

    async def test_wait_persisted_fallback_without_task_handle(self) -> None:
        # Simulate run that exists in SQLite and memory has no task_handle (e.g. after restart)
        run_id = "55555555-5555-4555-8555-555555555555"
        server.run_store.save_run(
            run_id=run_id,
            project_dir=self.temp_dir.name,
            model="gemini-3.8-flash-low",
            effort="low",
            mode="accept-edits",
            created_at="2026-09-12T12:00:00Z",
            status="RUNNING",
        )

        # Background coroutine that sets terminal state after short delay
        async def mark_done_later():
            await asyncio.sleep(0.1)
            server.run_store.save_run(
                run_id=run_id,
                project_dir=self.temp_dir.name,
                model="gemini-3.8-flash-low",
                effort="low",
                mode="accept-edits",
                created_at="2026-09-12T12:00:00Z",
                status="COMPLETED",
                response="persisted done",
            )

        asyncio.create_task(mark_done_later())

        result = await server.antigravity_subagent_wait(run_id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["wait_status"], "TERMINAL")
        self.assertEqual(result["response"], "persisted done")

    def test_stream_command_keeps_prompt_out_of_process_arguments(self) -> None:
        command = server.build_agy_stream_command(
            model="gemini-3.8-flash-low",
            effort="low",
            allow_tools=True,
            conversation_id=None,
            timeout=60,
            extra_flags=["--mode", "plan"],
        )

        self.assertIn("stream-json", command)
        self.assertFalse(any("private prompt" in argument for argument in command))
        self.assertFalse(any(argument.startswith("--print=") for argument in command))

        compatibility_command = server.build_agy_command(
            prompt="private prompt",
            model="gemini-3.8-flash-low",
            effort="low",
            allow_tools=True,
            conversation_id=None,
            output_format="json",
            timeout=60,
            extra_flags=None,
        )
        self.assertFalse(any("private prompt" in argument for argument in compatibility_command))
        with self.assertRaises(ValueError):
            server.build_agy_stream_command(
                model="gemini-pro",
                effort="low",
                allow_tools=True,
                conversation_id=None,
                timeout=60,
                extra_flags=None,
            )

    async def test_proxy_applies_to_model_discovery_and_turns_only_when_configured(self) -> None:
        spawn = asyncio.create_subprocess_exec
        for proxy in ("", "socks5://127.0.0.1:1088"):
            with patch.dict(os.environ, {"AGY_PROXY_URL": proxy, "https_proxy": "http://parent.invalid"}), \
                    patch.object(asyncio, "create_subprocess_exec", wraps=spawn) as calls:
                await server.ModelRegistry().get_flash_models()
                await server.run_agy("probe", cwd=self.temp_dir.name, model="gemini-3.8-flash-low", timeout=5)
                self.assertEqual(os.environ["https_proxy"], "http://parent.invalid")
            self.assertEqual([c.args[1] for c in calls.await_args_list], ["models", "--model"])
            for c in calls.await_args_list:
                env = c.kwargs["env"]
                if not proxy:
                    self.assertIsNone(env)
                    continue
                for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                    self.assertEqual(env[name], proxy)
                    self.assertEqual(env[name.lower()], proxy)
                self.assertEqual(env["NO_PROXY"], env["no_proxy"])
                self.assertIn("127.0.0.1", env["NO_PROXY"].split(","))

    async def test_run_agy_uses_stream_transport_and_preserves_json_contract(self) -> None:
        output = await server.run_agy(
            "private prompt",
            cwd=self.temp_dir.name,
            model="gemini-3.8-flash-low",
            effort="low",
            output_format="json",
            timeout=5,
        )

        payload = json.loads(output)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(payload["response"], "done")
        self.assertEqual(payload["usage"], {"total_tokens": 3})

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

    async def test_fetch_url_rejects_http_error_and_supports_medium_model(self) -> None:
        failed = await server.antigravity_fetch_url(
            "https://example.invalid",
            prompt="TRIGGER_FETCH_ERROR",
        )
        self.assertIn("FALLBACK_REQUIRED", failed)
        self.assertIn("HTTP 403 Forbidden", failed)

        succeeded = await server.antigravity_fetch_url(
            "https://example.com",
            prompt="TRIGGER_STRUCTURED_ONLY",
            model="gemini-3.8-flash-medium",
        )
        self.assertEqual(succeeded, "done")

    async def test_large_structured_result_survives_stream_transport(self) -> None:
        result = await server.antigravity_fetch_url("https://example.com", "TRIGGER_LARGE")
        self.assertEqual(result, "x" * 200000)

    async def test_research_timeout_covers_model_discovery_and_reaps_process(self) -> None:
        processes = []
        original = asyncio.create_subprocess_exec

        async def record(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(server.asyncio, "create_subprocess_exec", side_effect=record):
            result = await server.antigravity_fetch_url("https://example.com", "TRIGGER_SLOW", timeout=1)
        self.assertIn("timeout=1s", result)
        self.assertTrue(processes)
        self.assertTrue(all(process.returncode is not None for process in processes))

        async def slow_models(*args, **kwargs):
            await asyncio.sleep(10)
        with patch.object(server.model_registry, "get_recommended_model", side_effect=slow_models):
            with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock) as run:
                result = await server.antigravity_grounded_search("query", timeout=1)
                self.assertIn("timeout=1s", result)
                run.assert_not_called()

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

    async def test_model_discovery_timeout_reaps_its_subprocess(self) -> None:
        processes = []
        original = asyncio.create_subprocess_exec

        async def slow_model_process(*args, **kwargs):
            self.assertEqual(args[-1], "models")
            process = await original(sys.executable, "-c", "import time; time.sleep(10)", **kwargs)
            processes.append(process)
            return process

        with patch.object(server.asyncio, "create_subprocess_exec", side_effect=slow_model_process):
            result = await server.antigravity_grounded_search("query", timeout=1)
        self.assertIn("timeout=1s", result)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)

    def test_invalid_agent_results_cannot_be_reported_as_success(self) -> None:
        for response in ("not-json", "[]", {}, {"status": "success", "content": ""},
                         {"status": "success", "content": "plausible", "error": "Missing article"}):
            content, error = server.parse_research_result(response)
            self.assertIsNone(content)
            self.assertTrue(error)

    async def test_all_research_tools_forward_timeout_and_invalid_values_do_not_launch(self) -> None:
        result = {"status": "COMPLETED", "response": "", "structured_output": {
            "status": "success", "content": "done", "error": None,
        }, "conversation_id": "11111111-1111-4111-8111-111111111111"}
        calls = [
            (server.antigravity_fetch_url, {"url": "https://example.com"}),
            (server.antigravity_grounded_search, {"query": "query"}),
            (server.antigravity_grounded_dialogue, {"query": "query"}),
            (server.antigravity_deep_research, {"topic": "topic"}),
            (server.ask_antigravity, {"prompt": "prompt"}),
            (server.antigravity_review, {"code_or_diff": "code"}),
        ]
        with patch.object(server, "run_agy_stream_turn", new_callable=AsyncMock, return_value=result) as run:
            for function, args in calls:
                await function(**args)
                self.assertEqual(run.call_args.kwargs["timeout"], 300)
                await function(**args, timeout=450)
                self.assertEqual(run.call_args.kwargs["timeout"], 450)
            run.reset_mock()
            for value in (0, -1, 86401, True, 1.5):
                with self.assertRaises(ValueError):
                    await server.antigravity_fetch_url("https://example.com", timeout=value)
            run.assert_not_called()

    async def test_turn_lifecycle_persists_items_and_commands(self) -> None:
        started = await server.antigravity_turn_start(
            message="DELAY",
            project_dir=self.temp_dir.name,
            mode="plan",
            model="gemini-3.8-flash-low",
            effort="low",
            execution_timeout_seconds=10,
            sync_to_desktop=False,
        )
        active = await server.antigravity_turn_status(started["turn_id"])
        for _ in range(30):
            if active["status"] != "CREATED" and active["session_id"]:
                break
            await asyncio.sleep(0.05)
            active = await server.antigravity_turn_status(started["turn_id"])
        self.assertEqual(active["status"], "RUNNING")
        self.assertEqual(active["session_id"], "11111111-1111-4111-8111-111111111111")

        terminal = await server.antigravity_turn_wait(started["turn_id"])
        self.assertEqual(terminal["status"], "COMPLETED")
        self.assertEqual(terminal["response"], "done")
        self.assertEqual(terminal["usage"], {"total_tokens": 3})

        items = await server.antigravity_turn_items(started["turn_id"], limit=20)
        self.assertEqual(items["status"], "SUCCESS")
        self.assertEqual([item["event_type"] for item in items["items"]], [
            "init", "step_update", "step_update", "step_update", "result",
        ])
        commands = await server.antigravity_turn_commands(started["turn_id"], limit=20)
        self.assertEqual(len(commands["commands"]), 2)
        self.assertEqual(commands["commands"][-1]["payload"]["step_update"]["state"], "DONE")
        self.assertNotIn("items", commands)

    async def test_turn_start_is_idempotent(self) -> None:
        first = await server.antigravity_turn_start(
            message="DELAY",
            project_dir=self.temp_dir.name,
            mode="plan",
            model="gemini-3.8-flash-low",
            effort="low",
            execution_timeout_seconds=10,
            sync_to_desktop=False,
            idempotency_key="same-request",
        )
        repeated = await server.antigravity_turn_start(
            message="must not launch",
            project_dir=self.temp_dir.name,
            mode="plan",
            model="gemini-3.8-flash-low",
            effort="low",
            execution_timeout_seconds=10,
            sync_to_desktop=False,
            idempotency_key="same-request",
        )

        self.assertEqual(repeated["turn_id"], first["turn_id"])
        await server.antigravity_turn_wait(first["turn_id"])
        self.assertEqual(len(server.run_store.list_runs(limit=10)), 1)

    async def test_session_read_lists_durable_turns(self) -> None:
        started = await server.antigravity_turn_start(
            message="SMALL",
            project_dir=self.temp_dir.name,
            mode="plan",
            model="gemini-3.8-flash-low",
            effort="low",
            execution_timeout_seconds=10,
            sync_to_desktop=False,
        )
        terminal = await server.antigravity_turn_wait(started["turn_id"])
        session = await server.antigravity_session_read(terminal["session_id"])

        self.assertEqual(session["status"], "SUCCESS")
        self.assertEqual(session["session_id"], terminal["session_id"])
        self.assertEqual(session["turns"][0]["turn_id"], terminal["turn_id"])

        inventory = await server.antigravity_session_list(limit=10)
        self.assertEqual(inventory["status"], "SUCCESS")
        self.assertEqual(inventory["sessions"][0]["session_id"], terminal["session_id"])
        self.assertEqual(inventory["turns"][0]["turn_id"], terminal["turn_id"])

    async def test_execution_timeout_allows_explicit_long_turns(self) -> None:
        resolved = await server.resolve_subagent_request(
            project_dir=self.temp_dir.name,
            mode="plan",
            conversation_id=None,
            model="gemini-3.8-flash-low",
            effort="low",
            timeout=7200,
        )

        self.assertEqual(resolved[0], self.temp_dir.name)

    def test_run_store_is_private(self) -> None:
        store = server.RunStore(str(self.cli_root / "private.db"))
        store.init_db()

        self.assertEqual((self.cli_root / "private.db").stat().st_mode & 0o777, 0o600)

    async def test_pid_reuse_does_not_keep_turn_running(self) -> None:
        run_id = "88888888-8888-4888-8888-888888888888"
        server.run_store.save_run(
            run_id=run_id,
            project_dir=self.temp_dir.name,
            model="gemini-3.8-flash-low",
            effort="low",
            mode="plan",
            created_at="2026-09-12T12:00:00Z",
            status="RUNNING",
            pid=123,
        )

        with patch.object(server, "is_pid_alive", return_value=True), patch.object(
            server.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, stdout="unrelated process", stderr=""),
        ):
            snapshot = await server.get_managed_run_snapshot(run_id)

        self.assertEqual(snapshot["status"], "FAILED")
        self.assertEqual(snapshot["response"], "Process terminated unexpectedly.")


if __name__ == "__main__":
    unittest.main()
