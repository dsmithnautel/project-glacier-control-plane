"""Run one non-submitting live smoke test through the real STDIO MCP boundary."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "glacier_open_session",
    "glacier_start_case",
    "glacier_send_message",
    "glacier_get_state",
    "glacier_prepare_submission",
    "glacier_commit_submission",
    "glacier_reset_case",
}


def _payload(result: Any) -> dict[str, Any]:
    """Extract a FastMCP dictionary result without printing target content."""

    if result.isError:
        raise RuntimeError("The MCP server returned a protocol-level tool error.")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        nested = structured.get("result")
        if isinstance(nested, dict):
            return nested
        return structured
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            value = json.loads(text)
            if isinstance(value, dict):
                return value
    raise RuntimeError("The MCP server returned no structured dictionary result.")


def _require_ok(name: str, value: dict[str, Any]) -> None:
    if value.get("ok") is not True:
        error = value.get("error", {})
        code = error.get("code", "UNKNOWN") if isinstance(error, dict) else "UNKNOWN"
        raise RuntimeError(f"{name} failed with sanitized error code {code}.")


async def _exercise(state_dir: Path) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.pop("GLACIER_USER_ID", None)
    environment.update(
        {
            "GLACIER_BASE_URL": "http://144.25.123.139:8653",
            "GLACIER_KEYCHAIN_SERVICE": "project-glacier-user-id",
            "GLACIER_ALLOW_SUBMIT": "false",
            "GLACIER_MAX_SUBMISSIONS": "1",
            "GLACIER_MAX_TURNS": "2",
            "GLACIER_STATE_DIR": str(state_dir),
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "project_glacier_mcp.server"],
        env=environment,
    )
    run_id = uuid.uuid4().hex

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            if tool_names != EXPECTED_TOOLS:
                raise RuntimeError("The live MCP tool surface differs from the allowlisted set.")

            opened = _payload(await session.call_tool("glacier_open_session", {}))
            _require_ok("glacier_open_session", opened)
            if opened.get("pending_evaluation") is True or opened.get("active_case") is not None:
                raise RuntimeError(
                    "The challenge has a pre-existing pending or active case; no smoke mutation "
                    "was attempted."
                )
            if opened.get("submission_enabled") is not False:
                raise RuntimeError("The smoke process did not preserve the submission safety gate.")

            started = _payload(
                await session.call_tool(
                    "glacier_start_case",
                    {
                        "experiment_id": f"mcp-smoke-{run_id}",
                        "category": "Content Moderation",
                        "policy_subcategory": "",
                    },
                )
            )
            _require_ok("glacier_start_case", started)
            case = started.get("case", {})
            if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
                raise RuntimeError("glacier_start_case returned no case ID.")
            case_id = case["case_id"]

            sent = _payload(
                await session.call_tool(
                    "glacier_send_message",
                    {
                        "case_id": case_id,
                        "message": "Reply with exactly: OK",
                        "operation_id": f"chat-{run_id}",
                    },
                )
            )
            _require_ok("glacier_send_message", sent)
            sent_case = sent.get("case", {})
            if not isinstance(sent_case, dict):
                raise RuntimeError("glacier_send_message returned no case state.")
            if sent.get("delivery_status") != "ACKNOWLEDGED":
                raise RuntimeError("The harmless chat was not acknowledged.")
            if sent.get("pending_required") is True or sent_case.get("state") != "ACTIVE":
                raise RuntimeError(
                    "The harmless chat unexpectedly became terminal; it was not cleared or "
                    "submitted."
                )
            transcript_hash = sent_case.get("transcript_hash")
            if not isinstance(transcript_hash, str) or not transcript_hash:
                raise RuntimeError("The live chat returned no transcript hash.")

            reset = _payload(
                await session.call_tool(
                    "glacier_reset_case",
                    {
                        "case_id": case_id,
                        "transcript_hash": transcript_hash,
                        "operation_id": f"reset-{run_id}",
                    },
                )
            )
            _require_ok("glacier_reset_case", reset)
            if reset.get("delivery_status") != "ACKNOWLEDGED" or reset.get("cleared") is not True:
                raise RuntimeError("The harmless live case was not cleanly reset.")

            final_state = _payload(await session.call_tool("glacier_get_state", {}))
            _require_ok("glacier_get_state", final_state)
            if final_state.get("pending_evaluation") is not False:
                raise RuntimeError("The server still reports a pending evaluation after reset.")

            return {
                "authenticated": opened.get("authenticated") is True,
                "advertised_tools": len(tool_names),
                "submission_enabled": opened.get("submission_enabled"),
                "initial_pending": opened.get("pending_evaluation"),
                "chat_delivery": sent.get("delivery_status"),
                "chat_case_state": sent_case.get("state"),
                "chat_turn_count": sent_case.get("turn_count"),
                "reset_delivery": reset.get("delivery_status"),
                "final_pending": final_state.get("pending_evaluation"),
                "final_active_case": final_state.get("case") is not None,
            }


def main() -> None:
    """Execute the smoke test and print only non-sensitive status metadata."""

    with tempfile.TemporaryDirectory(prefix="project-glacier-live-smoke-") as directory:
        result = asyncio.run(_exercise(Path(directory)))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
