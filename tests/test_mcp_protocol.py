from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from project_glacier_mcp.journal import OperationJournal


def _clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GLACIER_")
    }


def test_stdio_server_initializes_and_advertises_only_narrow_tools(tmp_path):
    async def exercise() -> None:
        environment = _clean_environment()
        environment["GLACIER_USER_ID"] = "123456789"
        environment["GLACIER_STATE_DIR"] = str(tmp_path / "stdio-state")
        environment["GLACIER_ALLOW_SUBMIT"] = "false"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "project_glacier_mcp.server"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()

        by_name = {tool.name: tool for tool in tools.tools}
        assert set(by_name) == {
            "glacier_open_session",
            "glacier_start_case",
            "glacier_send_message",
            "glacier_get_state",
            "glacier_prepare_submission",
            "glacier_commit_submission",
            "glacier_reset_case",
        }
        assert by_name["glacier_get_state"].annotations.readOnlyHint is True
        assert by_name["glacier_reset_case"].annotations.destructiveHint is True
        assert by_name["glacier_commit_submission"].annotations.readOnlyHint is False
        assert set(by_name["glacier_reset_case"].inputSchema["properties"]) == {
            "case_id",
            "transcript_hash",
            "operation_id",
        }

    asyncio.run(exercise())


def test_stdio_handshake_survives_an_unavailable_broker(tmp_path):
    state_dir = tmp_path / "locked-state"
    journal = OperationJournal(state_dir)

    async def exercise() -> None:
        environment = _clean_environment()
        environment["GLACIER_USER_ID"] = "123456789"
        environment["GLACIER_STATE_DIR"] = str(state_dir)
        environment["GLACIER_ALLOW_SUBMIT"] = "false"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "project_glacier_mcp.server"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                opened = await session.call_tool("glacier_open_session", {})

        assert len(tools.tools) == 7
        assert opened.isError is False
        structured = opened.structuredContent
        assert isinstance(structured, dict)
        payload = structured.get("result", structured)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "BROKER_ALREADY_RUNNING"

    try:
        asyncio.run(exercise())
    finally:
        journal.close()


def test_stdio_handshake_does_not_require_credential_access(tmp_path):
    async def exercise() -> None:
        environment = _clean_environment()
        environment["GLACIER_KEYCHAIN_SERVICE"] = "project-glacier-test-missing"
        environment["GLACIER_STATE_DIR"] = str(tmp_path / "credential-free-handshake")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "project_glacier_mcp.server"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()

        assert initialized.serverInfo.name == "Project Glacier"
        assert len(tools.tools) == 7

    asyncio.run(exercise())
