"""Own a CLI turn independently of the MCP transport process."""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys

import antigravity_mcp_server as server


async def main(request_path: Path) -> None:
    request = json.loads(request_path.read_text())
    server.CLI_STORAGE_ROOT = request["cli_storage"]
    server.DESKTOP_STORAGE_ROOT = request["desktop_storage"]
    server.ARCHIVE_STORAGE_ROOT = request["archive_storage"]
    server.run_store = server.RunStore(request["store_path"])
    server.get_agy_path = lambda: request["agy_path"]
    run = server.ManagedSubagentRun(**request["run"], pid=os.getpid(), detached=True)
    run.agent = request.get("agent")
    run.result_schema = request.get("result_schema")
    # The request contains the prompt. Retain only the final result after loading it.
    request_path.unlink()
    task = asyncio.create_task(server.execute_managed_subagent(
        run, request["task"], request["timeout"], request["sync_to_desktop"]
    ))
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    await task


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
